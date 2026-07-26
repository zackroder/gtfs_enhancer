from shapely.geometry import LineString

class ShapeCleaner:
    def __init__(self):
        pass
        
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
        # This is a naive 'stop tail' removal.
        cleaned_coords = []
        
        for p in coords:
            if len(cleaned_coords) >= 2:
                # Check if the new point is the same as the point before the last one
                # i.e., A -> B -> A
                if p == cleaned_coords[-2]:
                    # We went back to a previous point, so 'pop' the intermediate point
                    cleaned_coords.pop()
                    continue
            
            # Prevent consecutive duplicate points
            if cleaned_coords and p == cleaned_coords[-1]:
                continue
                
            cleaned_coords.append(p)
            
        if len(cleaned_coords) < 2:
            # Fallback if cleaning destroyed the line
            return geom
            
        return LineString(cleaned_coords)
