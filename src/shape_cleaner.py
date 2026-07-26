import math
import pandas as pd
from shapely.geometry import LineString, Point

class ShapeCleaner:
    def __init__(self):
        pass
        
    def filter_perpendicular_stubs(self, shape_df: pd.DataFrame, max_stub_meters: float = 40.0) -> pd.DataFrame:
        """
        Pre-matching filter that removes single-point or small perpendicular side-stubs
        (e.g., a 20-meter detour poking off a main corridor onto a side street for a bus stop).
        
        Args:
            shape_df: DataFrame with 'shape_pt_lon' and 'shape_pt_lat'.
            max_stub_meters: Maximum perpendicular distance in meters to classify a point as a stub.
            
        Returns:
            Filtered DataFrame with perpendicular stubs removed.
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
            
            for i in range(1, len(df) - 1):
                p_prev = (lons[i-1], lats[i-1])
                p_curr = (lons[i], lats[i])
                p_next = (lons[i+1], lats[i+1])
                
                # Approximate degree to meters conversion based on mean latitude
                mean_lat = math.radians((p_prev[1] + p_curr[1] + p_next[1]) / 3.0)
                meters_per_deg_lat = 111000.0
                meters_per_deg_lon = 111000.0 * math.cos(mean_lat)
                
                # Convert coords to local metric projection relative to p_prev
                def to_metric(p):
                    return (
                        (p[0] - p_prev[0]) * meters_per_deg_lon,
                        (p[1] - p_prev[1]) * meters_per_deg_lat
                    )
                    
                m_prev = (0.0, 0.0)
                m_curr = to_metric(p_curr)
                m_next = to_metric(p_next)
                
                # Segment connecting prev and next
                seg = LineString([m_prev, m_next])
                pt = Point(m_curr)
                
                # Perpendicular distance from current point to segment between prev and next
                dist = seg.distance(pt)
                
                # Angle at p_curr
                v1 = (m_prev[0] - m_curr[0], m_prev[1] - m_curr[1])
                v2 = (m_next[0] - m_curr[0], m_next[1] - m_curr[1])
                dot = v1[0]*v2[0] + v1[1]*v2[1]
                mag1 = math.hypot(v1[0], v1[1])
                mag2 = math.hypot(v2[0], v2[1])
                
                if mag1 > 0 and mag2 > 0:
                    cos_angle = max(-1.0, min(1.0, dot / (mag1 * mag2)))
                    angle_deg = math.degrees(math.acos(cos_angle))
                else:
                    angle_deg = 180.0
                    
                # If distance is within stub threshold AND the point juts out (< 165 deg)
                if dist <= max_stub_meters and angle_deg < 165.0:
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
