import math

import pandas as pd
from shapely.geometry import LineString


def _seg_meters(p1: tuple[float, float], p2: tuple[float, float]) -> tuple[float, float]:
    """Project (lon, lat) delta from p1 to p2 into approximate meters (dx, dy)."""
    mean_lat = math.radians((p1[1] + p2[1]) / 2.0)
    dx = (p2[0] - p1[0]) * 111000.0 * math.cos(mean_lat)
    dy = (p2[1] - p1[1]) * 111000.0
    return dx, dy


def _polyline_meters(coords: list[tuple[float, float]]) -> float:
    total = 0.0
    for i in range(len(coords) - 1):
        dx, dy = _seg_meters(coords[i], coords[i + 1])
        total += math.hypot(dx, dy)
    return total


def _point_line_distance_meters(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]):
    """Perpendicular distance in meters from point p to the chord a->b, or None if p projects outside the chord."""
    ax, ay = _seg_meters(a, b)
    chord_len = math.hypot(ax, ay)
    if chord_len < 1e-9:
        return None
    px, py = _seg_meters(a, p)
    t = (px * ax + py * ay) / (chord_len * chord_len)
    if not (0.05 <= t <= 0.95):
        return None
    cross = abs(ax * py - ay * px)
    return cross / chord_len


def _point_line_distance_full_meters(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]):
    """
    Perpendicular distance in meters from point p to the infinite line through a->b.
    Unlike _point_line_distance_meters, no projection-onto-chord guard is applied,
    so a poke-out tip that projects near the chord endpoints is still measured.
    """
    ax, ay = _seg_meters(a, b)
    chord_len = math.hypot(ax, ay)
    if chord_len < 1e-9:
        return _seg_hypot(a, p)
    px, py = _seg_meters(a, p)
    cross = abs(ax * py - ay * px)
    return cross / chord_len


def _seg_hypot(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Straight-line distance in meters between two (lon, lat) points."""
    dx, dy = _seg_meters(p1, p2)
    return math.hypot(dx, dy)


def _project_point_to_segment(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    """
    Projects point p onto segment a->b. Returns (t, distance_meters) where
    t is the fraction along the segment (clamped to [0, 1]).
    """
    ax, ay = _seg_meters(a, b)
    seg_len2 = ax * ax + ay * ay
    px, py = _seg_meters(a, p)
    if seg_len2 < 1e-12:
        return 0.0, math.hypot(px, py)
    t = max(0.0, min(1.0, (px * ax + py * ay) / seg_len2))
    proj_x, proj_y = t * ax, t * ay
    return t, math.hypot(px - proj_x, py - proj_y)


def _merge_spans(spans: list[dict]) -> list[dict]:
    """Merge overlapping candidate spans, keeping the first record's metadata."""
    if not spans:
        return []
    ordered = sorted(spans, key=lambda s: (s["start"], s["end"]))
    merged = [dict(ordered[0])]
    for s in ordered[1:]:
        if s["start"] <= merged[-1]["end"] + 1:
            merged[-1]["end"] = max(merged[-1]["end"], s["end"])
        else:
            merged.append(dict(s))
    return merged


class ShapeCleaner:
    def __init__(self):
        pass

    def find_out_and_back_stubs(
        self,
        shape_df: pd.DataFrame,
        max_stub_meters: float = 100.0,
        min_excursion_meters: float = 10.0,
        reversal_angle_deg: float = 120.0,
        detour_ratio: float = 1.25,
    ) -> tuple[pd.DataFrame, list[dict]]:
        """
        Detects multi-point out-and-back stubs (spurs) in a GTFS shape.

        A true stub requires ALL of the following (this protects legitimate turns,
        terminal loops, and dense GPS corners):
          - The path leaves a junction, travels out to a tip, and returns.
          - The outbound and inbound legs are roughly reversed (> reversal_angle_deg).
          - The tip excursion from the junction chord is within [min_excursion, max_stub] meters.
          - The path through the stub is materially longer than the chord (detour_ratio).
          - The tip projects onto the middle of the chord (not a wrap-around detour).
          - Multiple points exist on the window (j - i >= 4).

        Returns:
            (shape_df, stub_spans) where stub_spans is a list of dicts describing
            each detected spur with its index range and excursion. The DataFrame is
            returned unchanged; callers apply filter_out_and_back_stubs() to remove spans.
        """
        if len(shape_df) < 5:
            return shape_df, []

        lons = shape_df['shape_pt_lon'].values
        lats = shape_df['shape_pt_lat'].values
        coords = list(zip(lons, lats))
        n = len(coords)

        spans: list[tuple[int, int]] = []
        info: list[dict] = []

        for k in range(1, n - 1):
            tip = coords[k]
            for w in range(2, 6):
                i = max(0, k - w)
                j = min(n - 1, k + w)
                if j - i < 4:
                    continue

                a, b = coords[i], coords[j]

                # Reversal check: outbound (a->tip) vs inbound (tip->b) legs
                ox, oy = _seg_meters(a, tip)
                bx, by = _seg_meters(tip, b)
                omag = math.hypot(ox, oy)
                bmag = math.hypot(bx, by)
                if omag < 1e-6 or bmag < 1e-6:
                    continue
                dot = (ox * bx + oy * by) / (omag * bmag)
                angle_deg = math.degrees(math.acos(max(-1.0, min(1.0, dot))))
                if angle_deg < reversal_angle_deg:
                    continue

                # Tip excursion from the junction chord
                excursion = _point_line_distance_meters(tip, a, b)
                if excursion is None:
                    continue
                if not (min_excursion_meters <= excursion <= max_stub_meters):
                    continue

                # Detour ratio: path length vs straight chord
                chord_len = math.hypot(*(_seg_meters(a, b)))
                path_len = _polyline_meters(coords[i:j + 1])
                if chord_len < 1e-6:
                    continue
                detour = path_len / chord_len
                if detour < detour_ratio:
                    continue

                spans.append((i + 1, j - 1))
                info.append({
                    "start": i + 1,
                    "end": j - 1,
                    "tip": k,
                    "excursion_meters": round(excursion, 1),
                    "detour_ratio": round(detour, 2),
                })
                break  # one span per tip point is enough

        # Merge overlapping spans into a minimal set of non-overlapping ranges
        if spans:
            spans.sort()
            merged = [list(spans[0])]
            for start, end in spans[1:]:
                if start <= merged[-1][1] + 1:
                    merged[-1][1] = max(merged[-1][1], end)
                else:
                    merged.append([start, end])
            spans = [(int(s), int(e)) for s, e in merged]

        return shape_df, info

    def filter_out_and_back_stubs(
        self,
        shape_df: pd.DataFrame,
        max_stub_meters: float = 100.0,
        min_excursion_meters: float = 10.0,
        reversal_angle_deg: float = 120.0,
        detour_ratio: float = 1.25,
    ) -> tuple[pd.DataFrame, list[dict]]:
        """
        Removes detected out-and-back stubs, iterating until the shape is stable.
        Returns (filtered_df, stub_spans).
        """
        df = shape_df.copy().reset_index(drop=True)
        all_info: list[dict] = []
        original_len = len(df)

        while True:
            _, info = self.find_out_and_back_stubs(
                df,
                max_stub_meters=max_stub_meters,
                min_excursion_meters=min_excursion_meters,
                reversal_angle_deg=reversal_angle_deg,
                detour_ratio=detour_ratio,
            )
            if not info:
                break
            all_info.extend(info)
            drop_indices = set()
            for span in info:
                drop_indices.update(range(span["start"], span["end"] + 1))
            df = df.drop(index=sorted(drop_indices)).reset_index(drop=True)
            if len(df) < 5 or len(df) == original_len:
                break
            original_len = len(df)

        return df, all_info

    def clean_shape(self, geom: LineString) -> LineString:
        """
        Cleans a map-matched LineString by removing 'stop tail' artifacts
        (e.g., immediate out-and-back spurs where coordinates exactly repeat).
        """
        if not geom or geom.is_empty:
            return geom
            
        coords = list(geom.coords)
        if len(coords) < 3:
            return geom
            
        cleaned_coords = []
        for p in coords:
            if len(cleaned_coords) >= 2:
                if p == cleaned_coords[-2]:
                    cleaned_coords.pop()
                    continue
            if cleaned_coords and p == cleaned_coords[-1]:
                continue
            cleaned_coords.append(p)
            
        if len(cleaned_coords) < 2:
            return geom
            
        return LineString(cleaned_coords)

    # ------------------------------------------------------------------
    # Stop-aware preprocessing
    # ------------------------------------------------------------------

    def project_stops_to_shape(
        self,
        shape_df: pd.DataFrame,
        stops: list[dict],
        association_radius_meters: float = 50.0,
    ) -> list[dict]:
        """
        Projects each stop onto the ordered GTFS shape.

        Args:
            shape_df: DataFrame with 'shape_pt_lon'/'shape_pt_lat', ordered by sequence.
            stops: Stop records from parse_stop_usage().
            association_radius_meters: stops farther than this from the shape are
                retained for diagnostics but marked is_associated=False.

        Returns:
            Projected stop records:
            {
                "stop_id", "stop_name", "stop_sequence",
                "lat", "lon",
                "shape_index": nearest shape vertex index,
                "shape_distance_meters": cumulative distance at projection,
                "distance_to_shape_meters": stop-to-shape distance,
                "is_first_stop", "is_last_stop", "is_terminal",
                "is_associated": bool
            }
        """
        lons = shape_df['shape_pt_lon'].values
        lats = shape_df['shape_pt_lat'].values
        coords = list(zip(lons, lats))
        n = len(coords)

        cum = [0.0]
        for i in range(n - 1):
            cum.append(cum[-1] + _seg_hypot(coords[i], coords[i + 1]))

        projected = []
        for stop in stops:
            p = (float(stop['lon']), float(stop['lat']))
            best_dist = float('inf')
            best_idx = 0
            best_along = 0.0
            for i in range(n - 1):
                t, dist = _project_point_to_segment(p, coords[i], coords[i + 1])
                if dist < best_dist:
                    best_dist = dist
                    best_idx = i if t < 0.5 else i + 1
                    best_along = cum[i] + t * (cum[i + 1] - cum[i])

            projected.append({
                "stop_id": str(stop.get('stop_id', '')),
                "stop_name": str(stop.get('stop_name', '')),
                "stop_sequence": int(stop.get('stop_sequence', 0)),
                "lat": float(stop.get('lat')),
                "lon": float(stop.get('lon')),
                "shape_index": int(best_idx),
                "shape_distance_meters": round(best_along, 1),
                "distance_to_shape_meters": round(best_dist, 1),
                "is_first_stop": bool(stop.get('is_first_stop', False)),
                "is_last_stop": bool(stop.get('is_last_stop', False)),
                "is_terminal": bool(stop.get('is_first_stop', False) or stop.get('is_last_stop', False)),
                "is_associated": best_dist <= association_radius_meters,
            })

        return projected

    def find_stop_tails(
        self,
        shape_df: pd.DataFrame,
        projected_stops: list[dict],
        max_tail_meters: float = 125.0,
        min_tail_meters: float = 10.0,
        stop_radius_meters: float = 25.0,
        return_corridor_meters: float = 20.0,
        min_excursion_meters: float = 8.0,
        max_lookback_points: int = 12,
    ) -> list[dict]:
        """
        Detects short 'stop tails': shape excursions that leave the main corridor,
        reach a stop, and return to the corridor.

        A single shape point poking out to a stop is a valid tail (no minimum
        point count). A candidate requires ALL of:
          - a stop is within stop_radius_meters of the excursion tip
          - the tip deviates from the corridor chord by >= min_excursion_meters
          - the span returns to the corridor (chord <= return_corridor_meters)
            and the excursion exceeds the chord (a poke-out, not a turn/loop)
          - span path length within [min_tail_meters, max_tail_meters]

        Protected cases (never flagged):
          - terminal stops at the very start/end of the shape
          - spans containing more than one stop
          - normal turns / loops, which do not return to the same corridor
        """
        if len(shape_df) < 3 or not projected_stops:
            return []

        lons = shape_df['shape_pt_lon'].values
        lats = shape_df['shape_pt_lat'].values
        coords = list(zip(lons, lats))
        n = len(coords)

        candidates = []
        for stop in projected_stops:
            if not stop.get('is_associated', True):
                continue
            if stop.get('distance_to_shape_meters', 0.0) > stop_radius_meters:
                continue
            k = stop['shape_index']
            # preserve terminal endpoints that sit at the shape start/end
            if stop.get('is_terminal') and (k <= 2 or k >= n - 3):
                continue
            if k <= 0 or k >= n - 1:
                continue

            for i in range(max(0, k - max_lookback_points), k):
                for j in range(k + 1, min(n, k + max_lookback_points)):
                    if j - i < 2:
                        continue
                    path = _polyline_meters(coords[i:j + 1])
                    if path < min_tail_meters or path > max_tail_meters:
                        continue
                    chord = _seg_hypot(coords[i], coords[j])
                    if chord > return_corridor_meters:
                        continue

                    # excursion of the interior tip from the chord line
                    devs = []
                    for t in range(i + 1, j):
                        d = _point_line_distance_full_meters(coords[t], coords[i], coords[j])
                        devs.append((d, t))
                    if not devs:
                        continue
                    dev_max, tip = max(devs, key=lambda x: x[0])
                    if dev_max < min_excursion_meters or not (dev_max > chord):
                        continue

                    # the stop must sit at the poke-out tip
                    if _seg_hypot((stop['lon'], stop['lat']), coords[tip]) > stop_radius_meters:
                        continue

                    # protect branches serving more than one stop: another associated
                    # stop within max_tail_meters of this stop means a real branch
                    other_near = any(
                        o is not stop and o.get('is_associated', True)
                        and _seg_hypot((o['lon'], o['lat']), (stop['lon'], stop['lat'])) <= max_tail_meters
                        for o in projected_stops
                    )
                    if other_near:
                        continue

                    candidates.append({
                        "start": i + 1,
                        "end": j - 1,
                        "tip": tip,
                        "stop_id": stop['stop_id'],
                        "stop_distance_meters": round(stop['distance_to_shape_meters'], 1),
                        "tail_length_meters": round(path, 1),
                        "return_distance_meters": round(chord, 1),
                        "excursion_meters": round(dev_max, 1),
                        "span_points": (j - i - 1),
                        "is_terminal": bool(stop.get('is_terminal', False)),
                    })

        # per tip, keep the smallest span
        best = {}
        for c in candidates:
            key = (c['stop_id'], c['tip'])
            if key not in best or c['span_points'] < best[key]['span_points']:
                best[key] = c

        return _merge_spans(list(best.values()))

    def remove_stop_tails(
        self,
        shape_df: pd.DataFrame,
        tail_candidates: list[dict],
    ) -> tuple[pd.DataFrame, list[dict]]:
        """
        Removes approved stop-tail interior points. Returns (filtered_df, info).
        The original DataFrame is never mutated.
        """
        df = shape_df.copy().reset_index(drop=True)
        if not tail_candidates or len(df) < 3:
            return df, tail_candidates

        spans = sorted((c['start'], c['end']) for c in tail_candidates)
        merged = []
        for s, e in spans:
            if merged and s <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])

        drop = set()
        for s, e in merged:
            drop.update(range(s, e + 1))

        # never leave fewer than 2 points
        if len(df) - len(drop) < 2:
            return df, tail_candidates

        return df.drop(index=sorted(drop)).reset_index(drop=True), tail_candidates
