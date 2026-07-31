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
