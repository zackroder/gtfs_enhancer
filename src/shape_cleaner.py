"""Preprocessing for GTFS shapes before OSRM map matching.

The pipeline is intentionally small:

  1. Deduplicate consecutive points.
  2. RDP-simplify to strip GPS jitter and short stop tails (default 15m).
  3. Remove leftover stop-tail "spikes": single vertices that poke out to a stop
     and return to the corridor (RDP keeps these because they deviate more than
     the tolerance, but they are exactly the stubs that make OSRM think there is
     a turn onto a side street).
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


class ShapeCleaner:
    def preprocess_shape(
        self,
        shape_df: pd.DataFrame,
        simplify_tolerance_meters: float = 15.0,
        spike_max_return_meters: float = 20.0,
        spike_min_deviation_meters: float = 15.0,
        max_gap_meters: float = 300.0,
        max_points: int = 500,
    ) -> dict:
        """
        Runs the full preprocessing pipeline and returns every intermediate stage
        so the viewer can review RDP simplification and stop-tail removal.

        Returns:
            {
                "original": raw (lon, lat) coords,
                "simplified": after RDP simplification,
                "spike_removed": after stop-tail spike removal,
                "final": after resample / point budget (what is sent to OSRM),
                "spikes": list of removed spike diagnostics,
            }
        """
        original = list(zip(shape_df['shape_pt_lon'], shape_df['shape_pt_lat']))
        deduped = _dedupe_coords(original)
        simplified = simplify_coords(deduped, simplify_tolerance_meters)
        spike_removed, spikes = remove_spikes(simplified, spike_max_return_meters, spike_min_deviation_meters)
        final = _resample_max_gap(spike_removed, max_gap_meters=max_gap_meters, max_points=max_points)
        if len(final) > max_points:
            final = final[:max_points]

        return {
            "original": original,
            "simplified": simplified,
            "spike_removed": spike_removed,
            "final": final,
            "spikes": spikes,
        }
