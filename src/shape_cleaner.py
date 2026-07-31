"""Preprocessing for GTFS shapes before OSRM map matching.

The pipeline is intentionally small:

  1. Deduplicate consecutive points.
  2. RDP-simplify to strip GPS jitter and short excursions (default 15m).
  3. Remove stop excursions: short poke-outs where the shape leaves the corridor
     to reach a stop and returns to (nearly) the same spot. A human reads this as
     "the route continues along the road"; feeding OSRM the clean corridor stops
     it from routing the poke as a street detour.
  4. Resample long straightaways and enforce the OSRM point budget.

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
    """RDP simplification. Strips GPS jitter and any excursion poking out less than the tolerance.
    A tolerance <= 0 disables simplification."""
    if tolerance_meters <= 0.0 or len(coords) < 3:
        return coords
    tol_deg = tolerance_meters / 111000.0
    simplified = list(LineString(coords).simplify(tol_deg, preserve_topology=False).coords)
    return simplified if len(simplified) >= 2 else coords


def remove_stop_excursions(
    coords: list[tuple[float, float]],
    stops: list[dict],
    stop_radius_meters: float = 60.0,
    max_return_meters: float = 50.0,
    min_deviation_meters: float = 8.0,
    max_corridor_turn_deg: float = 45.0,
) -> tuple[list[tuple[float, float]], list[dict]]:
    """
    Removes stop excursions: short poke-outs where the shape leaves the corridor
    to reach a stop and returns to (nearly) the same spot.

    An interior vertex B (neighbors A, C) is removed when ALL hold:
      - the excursion returns near its start: dist(A, C) <= max_return_meters
      - B pokes out: perpendicular deviation from the A-C chord >= min_deviation_meters
      - a stop is within stop_radius_meters of B
      - the route continues along the road: the corridor heading before the poke
        matches the corridor heading after it (within max_corridor_turn_deg). This
        is the human discriminator - a 90-degree turn changes the corridor heading,
        a stop poke does not. Exact returns (A == C) are pokes by definition.

    Returns (cleaned_coords, removed_info).
    """
    if not stops or len(coords) < 3:
        return coords, []

    stop_coords = [(s['lon'], s['lat']) for s in stops]
    removed_info = []

    while True:
        n = len(coords)
        if n < 3:
            break
        keep = [True] * n
        removed_this = 0
        for i in range(1, n - 1):
            a, b, c = coords[i - 1], coords[i], coords[i + 1]
            chord = _haversine_meters(a, c)
            if chord > max_return_meters:
                continue
            dev = _point_line_distance_full_meters(b, a, c)
            if dev < min_deviation_meters:
                continue

            # Corridor must continue through the poke (route continues along the road).
            # Exact returns (A == C) are pokes by definition and skip this check.
            if chord >= 2.0:
                if i - 2 < 0 or i + 2 >= n:
                    continue
                h1 = _bearing_deg(coords[i - 2], a)
                h2 = _bearing_deg(c, coords[i + 2])
                if _ang_diff(h1, h2) > max_corridor_turn_deg:
                    continue

            # Require a stop near the poke
            if min(_haversine_meters(b, s) for s in stop_coords) > stop_radius_meters:
                continue

            keep[i] = False
            removed_this += 1

        if removed_this == 0:
            break

        for i in range(1, n - 1):
            if not keep[i]:
                removed_info.append({
                    "index": i,
                    "coord": [round(coords[i][0], 6), round(coords[i][1], 6)],
                    "deviation_meters": round(_point_line_distance_full_meters(
                        coords[i], coords[i - 1], coords[i + 1]), 1),
                })
        coords = _dedupe_coords([c for c, k in zip(coords, keep) if k])

    return coords, removed_info


class ShapeCleaner:
    def preprocess_shape(
        self,
        shape_df: pd.DataFrame,
        stops: list[dict] = None,
        simplify_tolerance_meters: float = 15.0,
        stop_radius_meters: float = 60.0,
        spike_max_return_meters: float = 50.0,
        spike_min_deviation_meters: float = 8.0,
        max_gap_meters: float = 300.0,
        max_points: int = 500,
    ) -> dict:
        """
        Runs the full preprocessing pipeline and returns every intermediate stage
        so the viewer can review RDP simplification and stop-excursion removal.

        Returns:
            {
                "original": raw (lon, lat) coords,
                "simplified": after RDP simplification,
                "stop_removed": after stop-excursion removal,
                "final": after resample / point budget (what is sent to OSRM),
                "removed_stops": list of removed stop-excursion diagnostics,
            }
        """
        original = list(zip(shape_df['shape_pt_lon'], shape_df['shape_pt_lat']))
        deduped = _dedupe_coords(original)
        simplified = simplify_coords(deduped, simplify_tolerance_meters)
        stop_removed, removed_stops = remove_stop_excursions(
            simplified,
            stops or [],
            stop_radius_meters=stop_radius_meters,
            max_return_meters=spike_max_return_meters,
            min_deviation_meters=spike_min_deviation_meters,
        )
        final = _resample_max_gap(stop_removed, max_gap_meters=max_gap_meters, max_points=max_points)
        if len(final) > max_points:
            final = final[:max_points]

        return {
            "original": original,
            "simplified": simplified,
            "stop_removed": stop_removed,
            "final": final,
            "removed_stops": removed_stops,
        }
