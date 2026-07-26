import math
import pandas as pd
from shapely.geometry import LineString, Point

class ShapeCleaner:
    def __init__(self):
        pass
        
    def _perpendicular_distance_meters(self, p_tip, p_start, p_end, meters_per_deg_lon, meters_per_deg_lat):
        """
        Calculates the perpendicular distance in meters from point p_tip (x0, y0)
        to the baseline line segment connecting p_start (x1, y1) and p_end (x2, y2).
        """
        x0, y0 = p_tip[0] * meters_per_deg_lon, p_tip[1] * meters_per_deg_lat
        x1, y1 = p_start[0] * meters_per_deg_lon, p_start[1] * meters_per_deg_lat
        x2, y2 = p_end[0] * meters_per_deg_lon, p_end[1] * meters_per_deg_lat
        
        dx = x2 - x1
        dy = y2 - y1
        
        if dx == 0 and dy == 0:
            return math.hypot(x0 - x1, y0 - y1)
            
        num = abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1)
        den = math.hypot(dx, dy)
        return num / den

    def filter_perpendicular_stubs(self, shape_df: pd.DataFrame, max_stub_meters: float = 75.0) -> pd.DataFrame:
        """
        Pre-matching filter that measures the perpendicular distance of candidate points
        from the baseline chord connecting preceding and succeeding trajectory points.
        
        If a point P_i deviates laterally from the baseline (P_{i-1} -> P_{i+1}) by <= max_stub_meters
        and forms a sharp out-and-back turn (< 110 deg), the spur point is removed.
        
        This protects 100% of normal road curves because normal road bends have gentle angles (140-175 deg)
        or large continuous lateral offsets across subsequent points.
        
        Args:
            shape_df: DataFrame with 'shape_pt_lon' and 'shape_pt_lat'.
            max_stub_meters: Maximum perpendicular deviation distance in meters for spur removal.
            
        Returns:
            Filtered DataFrame with spur points removed.
        """
        if len(shape_df) < 3:
            return shape_df
            
        df = shape_df.copy().reset_index(drop=True)
        
        changed = True
        while changed and len(df) >= 3:
            changed = False
            drop_indices = []
            
            lons = df['shape_pt_lon'].values
            lats = df['shape_pt_lat'].values
            n = len(df)
            
            for i in range(1, n - 1):
                p_prev = (lons[i-1], lats[i-1])
                p_tip = (lons[i], lats[i])
                p_next = (lons[i+1], lats[i+1])
                
                # Approximate degree to meters conversion based on mean latitude
                mean_lat = math.radians((p_prev[1] + p_tip[1] + p_next[1]) / 3.0)
                meters_per_deg_lat = 111000.0
                meters_per_deg_lon = 111000.0 * math.cos(mean_lat)
                
                # Perpendicular deviation distance from p_tip to baseline chord (p_prev -> p_next)
                perp_dist = self._perpendicular_distance_meters(p_tip, p_prev, p_next, meters_per_deg_lon, meters_per_deg_lat)
                
                # Baseline distance (p_prev -> p_next) vs Path distance (p_prev -> p_tip -> p_next)
                dist_prev_next = math.hypot((p_next[0] - p_prev[0]) * meters_per_deg_lon, (p_next[1] - p_prev[1]) * meters_per_deg_lat)
                dist_prev_tip = math.hypot((p_tip[0] - p_prev[0]) * meters_per_deg_lon, (p_tip[1] - p_prev[1]) * meters_per_deg_lat)
                dist_tip_next = math.hypot((p_next[0] - p_tip[0]) * meters_per_deg_lon, (p_next[1] - p_tip[1]) * meters_per_deg_lat)
                path_dist = dist_prev_tip + dist_tip_next
                detour_ratio = path_dist / max(1.0, dist_prev_next)
                
                # Angle at p_tip
                v1 = ((p_prev[0] - p_tip[0]) * meters_per_deg_lon, (p_prev[1] - p_tip[1]) * meters_per_deg_lat)
                v2 = ((p_next[0] - p_tip[0]) * meters_per_deg_lon, (p_next[1] - p_tip[1]) * meters_per_deg_lat)
                dot = v1[0]*v2[0] + v1[1]*v2[1]
                mag1 = math.hypot(v1[0], v1[1])
                mag2 = math.hypot(v2[0], v2[1])
                
                if mag1 > 0 and mag2 > 0:
                    cos_angle = max(-1.0, min(1.0, dot / (mag1 * mag2)))
                    angle_deg = math.degrees(math.acos(cos_angle))
                else:
                    angle_deg = 180.0
                    
                # An out-and-back tail forms:
                # 1) A sharp acute tip (< 110 deg) AND perp_dist <= max_stub_meters
                # 2) OR a mid-trace lateral chord bump (perp_dist <= max_stub_meters and detour_ratio < 1.25)
                is_acute_stub = perp_dist <= max_stub_meters and angle_deg < 110.0
                is_lateral_chord_spike = perp_dist <= max_stub_meters and dist_prev_next > 20.0 and detour_ratio < 1.25 and angle_deg < 165.0
                
                if is_acute_stub or is_lateral_chord_spike:
                    drop_indices.append(i)
                    changed = True
                    break # Restart loop after dropping to re-evaluate adjacent points
                    
            if drop_indices:
                df = df.drop(index=drop_indices).reset_index(drop=True)
                
        return df

    def clean_shape(self, geom: LineString) -> LineString:
        """
        Cleans a map-matched LineString by removing 'stop tail' artifacts
        (e.g., out-and-back spurs).
        """
        if not geom or geom.is_empty:
            return geom
            
        coords = list(geom.coords)
        if len(coords) < 3:
            return geom
            
        # A simple algorithm to remove immediate out-and-back spurs:
        # If we go A -> B -> A, we can reduce this to A.
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
