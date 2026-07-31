"""Preprocessing for GTFS shapes before OSRM map matching.

The pipeline is intentionally small:

  1. Deduplicate consecutive points.
  2. RDP-simplify to strip GPS jitter and short stop tails (default 15m).
  3. Remove returning "spikes": single vertices that poke out to a stop and
     return to (nearly) the same point on the corridor.
  4. Remove same-corridor "stop triangles": short lateral detours that leave the
     corridor, reach a stop, and rejoin the SAME corridor further along. These
     are the stubs that make OSRM think there is a turn onto a side street.
  5. Resample long straightaways and enforce the OSRM point budget.

Every intermediate stage is returned so the viewer can show the result of each
step for review.
"""

import math

import pandas as pd
from shapely.geometry import LineString


def _seg_meters(p1: tuple[float, float], p2: tuple[float, float]) -> tuple[float, float]:
    mean_lat = math.radians((p1[1] + p2[1]) / 2.0)
    dx = (p2[0] - p1[0]) * 111000.0 * math.cos(mean_lat)
    dy = (p2[1] - p1[1]) * 111000.0
    return dx, dy


def _haversine_meters(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    dx, dy = _seg_meters(p1, p2)
    return math.hypot(dx, dy)


def _polyline_meters(coords: list[tuple[float, float]]) -> float:
    return sum(_haversine_meters(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def _dedupe_coords(coords: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out = []
    for c in coords:
        if not out or c != out[-1]:
            out.append(c)
    return out


def _point_line_distance_full_meters(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    """Perpendicular distance in meters from p to the infinite line through a->b."""
    ax, ay = _seg_meters(a, b)
    chord_len = math.hypot(ax, ay)
    if chord_len < 1e-9:
        return _haversine_meters(a, p)
    px, py = _seg_meters(a, p)
    cross = abs(ax * py - ay * px)
    return cross / chord_len


def _projection_fraction(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    """Fraction [0,1] of p's perpendicular projection along the chord a->b."""
    ax, ay = _seg_meters(a, b)
    seg_len2 = ax * ax + ay * ay
    if seg_len2 < 1e-12:
        return 0.5
    px, py = _seg_meters(a, p)
    return max(0.0, min(1.0, (px * ax + py * ay) / seg_len2))


def _bearing_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Compass bearing (0-360) from point a to point b."""
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _ang_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _cross_sign(a: tuple[float, float], b: tuple[float, float], p: tuple[float, float]) -> int:
    """Side of point p relative to the directed chord a->b (-1, 0, or +1)."""
    ax, ay = _seg_meters(a, b)
    px, py = _seg_meters(a, p)
    cross = ax * py - ay * px
    if abs(cross) < 1e-6:
        return 0
    return 1 if cross > 0 else -1


def _resample_max_gap(coords: list[tuple[float, float]], max_gap_meters: float = 300.0, max_points: int = 500) -> list[tuple[float, float]]:
    """Pin max gap between adjacent points without exceeding max_points."""
    if len(coords) < 2:
        return coords
    total_dist = _polyline_meters(coords)
    min_required_gap = total_dist / max(1, (max_points - 1))
    effective_gap = max(max_gap_meters, min_required_gap)

    resampled = [coords[0]]
    for i in range(len(coords) - 1):
        p1, p2 = coords[i], coords[i + 1]
        dist = _haversine_meters(p1, p2)
        if dist > effective_gap:
            num_segments = int(math.ceil(dist / effective_gap))
            for k in range(1, num_segments):
                frac = k / float(num_segments)
                resampled.append((p1[0] + frac * (p2[0] - p1[0]), p1[1] + frac * (p2[1] - p1[1])))
        resampled.append(p2)
    return resampled[:max_points]


def simplify_coords(coords: list[tuple[float, float]], tolerance_meters: float = 15.0) -> list[tuple[float, float]]:
    """RDP simplification. Strips GPS jitter and any stop tail poking out less than the tolerance.
    A tolerance <= 0 disables simplification."""
    if tolerance_meters <= 0.0 or len(coords) < 3:
        return coords
    tol_deg = tolerance_meters / 111000.0
    simplified = list(LineString(coords).simplify(tol_deg, preserve_topology=False).coords)
    return simplified if len(simplified) >= 2 else coords


def remove_spikes(
    coords: list[tuple[float, float]],
    max_return_meters: float = 20.0,
    min_deviation_meters: float = 15.0,
    min_deviation_ratio: float = 1.5,
) -> tuple[list[tuple[float, float]], list[dict]]:
    """
    Removes stop-tail spikes left behind by RDP.

    A single vertex B (neighbors A, C) is a spike when:
      - A and C are close to each other (the path returns to the corridor)
      - B deviates from the A-C chord by at least min_deviation_meters
      - the deviation clearly exceeds the return distance (a real turn does not
        return, so its A-C chord is large and it is protected)

    Returns (cleaned_coords, removed_info).
    """
    n = len(coords)
    if n < 3:
        return coords, []

    out = []
    removed = []
    for i, c in enumerate(coords):
        if 0 < i < n - 1:
            a, cc = coords[i - 1], coords[i + 1]
            chord = _haversine_meters(a, cc)
            if 0.0 < chord <= max_return_meters:
                dev = _point_line_distance_full_meters(c, a, cc)
                if dev >= min_deviation_meters and dev >= chord * min_deviation_ratio:
                    removed.append({
                        "index": i,
                        "return_meters": round(chord, 1),
                        "deviation_meters": round(dev, 1),
                    })
                    continue
        out.append(c)

    return out, removed


def _find_corridor_detour(
    coords: list[tuple[float, float]],
    i: int,
    max_span_meters: float,
    min_deviation_meters: float,
    max_detour_ratio: float,
    max_heading_deg: float,
    max_corridor_turn_deg: float,
):
    """
    Looks for a same-corridor detour ("stop triangle") starting at vertex i.

    A detour requires ALL of:
      - the corridor direction before i matches the direction after the detour
        (the trace returns to the SAME corridor, not a turn)
      - the detour span (A->D chord) is within max_span_meters
      - interior vertices deviate from the A-D chord by >= min_deviation_meters
      - all interior vertices lie on the same side of the chord
      - the out/in legs are roughly aligned with the corridor heading
      - the excursion tip projects near the middle of the chord
      - the detour path is modestly longer than the chord (not a large loop)

    Returns (j, meta) or None.
    """
    n = len(coords)
    if i <= 0 or i >= n - 1:
        return None

    corridor_in = _bearing_deg(coords[i - 1], coords[i])

    for j in range(i + 2, n):
        if j >= n - 1:
            break  # need a successor after the detour to confirm the corridor
        chord = _haversine_meters(coords[i], coords[j])
        if chord < 1e-9:
            continue  # degenerate span (e.g. out-and-back returns to the same point)
        if chord > max_span_meters:
            break

        corridor_out = _bearing_deg(coords[j], coords[j + 1])
        if _ang_diff(corridor_in, corridor_out) > max_corridor_turn_deg:
            continue

        path = _polyline_meters(coords[i:j + 1])
        if path <= chord:
            continue
        ratio = path / chord
        if ratio > max_detour_ratio:
            continue

        interior = list(range(i + 1, j))
        if not interior:
            continue

        devs = [_point_line_distance_full_meters(coords[t], coords[i], coords[j]) for t in interior]
        dev_max = max(devs)
        if dev_max < min_deviation_meters:
            continue

        sign0 = _cross_sign(coords[i], coords[j], coords[i + 1])
        if sign0 == 0:
            continue
        sides = {_cross_sign(coords[i], coords[j], coords[t]) for t in interior}
        if len(sides) != 1:
            continue

        leg1 = _bearing_deg(coords[i], coords[i + 1])
        leg2 = _bearing_deg(coords[j - 1], coords[j])
        if _ang_diff(leg1, corridor_in) > max_heading_deg or _ang_diff(leg2, corridor_out) > max_heading_deg:
            continue

        tip_t = max((_projection_fraction(coords[t], coords[i], coords[j]), d) for d, t in zip(devs, interior))
        if not (0.25 <= tip_t[0] <= 0.75):
            continue

        return j, {
            "start": i,
            "end": j,
            "span_meters": round(chord, 1),
            "deviation_meters": round(dev_max, 1),
            "path_meters": round(path, 1),
            "detour_ratio": round(ratio, 2),
        }

    return None


def remove_corridor_detours(
    coords: list[tuple[float, float]],
    max_span_meters: float = 100.0,
    min_deviation_meters: float = 12.0,
    max_detour_ratio: float = 2.0,
    max_heading_deg: float = 45.0,
    max_corridor_turn_deg: float = 40.0,
) -> tuple[list[tuple[float, float]], list[dict]]:
    """
    Removes same-corridor "stop triangle" detours, keeping the corridor entry and
    exit points. Real turns and large loops are protected (different corridor
    heading, opposite-side vertices, or large detour ratio).

    Returns (cleaned_coords, detour_info).
    """
    n = len(coords)
    if n < 4:
        return coords, []

    keep = [True] * n
    info = []
    i = 0
    while i < n:
        res = _find_corridor_detour(coords, i, max_span_meters, min_deviation_meters,
                                    max_detour_ratio, max_heading_deg, max_corridor_turn_deg)
        if res is None:
            i += 1
            continue
        j, meta = res
        for t in range(i + 1, j):
            keep[t] = False
        meta["removed_points"] = j - i - 1
        info.append(meta)
        i = j

    cleaned = [c for c, k in zip(coords, keep) if k]
    return cleaned, info


class ShapeCleaner:
    def preprocess_shape(
        self,
        shape_df: pd.DataFrame,
        simplify_tolerance_meters: float = 15.0,
        spike_max_return_meters: float = 20.0,
        spike_min_deviation_meters: float = 15.0,
        detour_max_span_meters: float = 100.0,
        detour_min_deviation_meters: float = 12.0,
        max_gap_meters: float = 300.0,
        max_points: int = 500,
    ) -> dict:
        """
        Runs the full preprocessing pipeline and returns every intermediate stage
        so the viewer can review RDP simplification, stop-tail spike removal, and
        same-corridor stop-triangle removal.

        Returns:
            {
                "original": raw (lon, lat) coords,
                "simplified": after RDP simplification,
                "spike_removed": after returning stop-tail spike removal,
                "detour_removed": after same-corridor stop-triangle removal,
                "final": after resample / point budget (what is sent to OSRM),
                "spikes": list of removed spike diagnostics,
                "detours": list of removed stop-triangle diagnostics,
            }
        """
        original = list(zip(shape_df['shape_pt_lon'], shape_df['shape_pt_lat']))
        deduped = _dedupe_coords(original)
        simplified = simplify_coords(deduped, simplify_tolerance_meters)
        spike_removed, spikes = remove_spikes(simplified, spike_max_return_meters, spike_min_deviation_meters)
        detour_removed, detours = remove_corridor_detours(spike_removed, detour_max_span_meters, detour_min_deviation_meters)
        final = _resample_max_gap(detour_removed, max_gap_meters=max_gap_meters, max_points=max_points)
        if len(final) > max_points:
            final = final[:max_points]

        return {
            "original": original,
            "simplified": simplified,
            "spike_removed": spike_removed,
            "detour_removed": detour_removed,
            "final": final,
            "spikes": spikes,
            "detours": detours,
        }
