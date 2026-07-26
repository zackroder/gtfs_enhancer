import math
import requests
import pandas as pd
from shapely.geometry import LineString, shape
import urllib.parse
import json

def _resample_max_gap(coords: list[tuple[float, float]], max_gap_meters: float = 300.0, max_points: int = 500) -> list[tuple[float, float]]:
    """
    Ensures that no two adjacent coordinates in coords are separated by more than effective max_gap_meters,
    while guaranteeing that the total point count never exceeds max_points.
    """
    if len(coords) < 2:
        return coords
        
    # Calculate total length in meters
    total_dist = 0.0
    for i in range(len(coords) - 1):
        p1, p2 = coords[i], coords[i+1]
        mean_lat = math.radians((p1[1] + p2[1]) / 2.0)
        dx = (p2[0] - p1[0]) * 111000.0 * math.cos(mean_lat)
        dy = (p2[1] - p1[1]) * 111000.0
        total_dist += math.hypot(dx, dy)
        
    # Dynamic gap budget so point count never exceeds max_points
    min_required_gap = total_dist / max(1, (max_points - 1))
    effective_gap = max(max_gap_meters, min_required_gap)
    
    resampled = [coords[0]]
    for i in range(len(coords) - 1):
        p1 = coords[i]
        p2 = coords[i+1]
        
        mean_lat = math.radians((p1[1] + p2[1]) / 2.0)
        dx = (p2[0] - p1[0]) * 111000.0 * math.cos(mean_lat)
        dy = (p2[1] - p1[1]) * 111000.0
        dist = math.hypot(dx, dy)
        
        if dist > effective_gap:
            num_segments = int(math.ceil(dist / effective_gap))
            for k in range(1, num_segments):
                fraction = k / float(num_segments)
                interp_lon = p1[0] + fraction * (p2[0] - p1[0])
                interp_lat = p1[1] + fraction * (p2[1] - p1[1])
                resampled.append((interp_lon, interp_lat))
                
        resampled.append(p2)
        
    return resampled[:max_points]

class OSRMMapMatcher:
    def __init__(self, base_url: str = "http://localhost:5000", profile: str = "bus", max_points: int = 500, max_gap_meters: float = 300.0, snap_radius_meters: float = 15.0):
        """
        Initializes the MapMatcher.
        
        Args:
            base_url: The base URL of the OSRM instance.
            profile: The routing profile (e.g., 'bus', 'driving').
            max_points: The maximum number of coordinates allowed per request.
            max_gap_meters: Maximum allowed distance in meters between consecutive points.
            snap_radius_meters: Search radius in meters for snapping GPS points to road network.
        """
        self.base_url = base_url.rstrip('/')
        self.profile = profile
        self.max_points = max_points
        self.max_gap_meters = max_gap_meters
        self.snap_radius_meters = snap_radius_meters

    def match_shape(self, shape_df: pd.DataFrame) -> LineString:
        """
        Takes a DataFrame containing GTFS shape points and returns a map-matched Shapely LineString.
        
        Args:
            shape_df: DataFrame with at least 'shape_pt_lon' and 'shape_pt_lat' columns,
                      ordered by 'shape_pt_sequence'.
                      
        Returns:
            A Shapely LineString representing the matched route, or None if matching failed.
        """
        # Ensure it's sorted
        if 'shape_pt_sequence' in shape_df.columns:
            shape_df = shape_df.sort_values(by='shape_pt_sequence')
            
        # Extract coordinates
        coords = list(zip(shape_df['shape_pt_lon'], shape_df['shape_pt_lat']))
        
        if len(coords) < 2:
            raise ValueError("At least 2 points are required for map matching.")
            
        # Downsample using RDP if point count exceeds max_points
        if len(coords) > self.max_points:
            geom = LineString(coords)
            tolerance = 0.00005 # ~5 meters in degrees
            
            while len(coords) > self.max_points:
                simplified = geom.simplify(tolerance, preserve_topology=False)
                raw_coords = list(simplified.coords)
                
                # Resample straightaways so gaps do not exceed max_gap_meters (e.g. 300m)
                coords = _resample_max_gap(raw_coords, max_gap_meters=self.max_gap_meters, max_points=self.max_points)
                
                tolerance *= 1.5
                if tolerance > 0.01:
                    break
        else:
            # Ensure straightaways are pinned at max_gap_meters max gaps
            coords = _resample_max_gap(coords, max_gap_meters=self.max_gap_meters, max_points=self.max_points)
            if len(coords) > self.max_points:
                coords = coords[:self.max_points]
        
        # OSRM expects coordinates in the path as {longitude},{latitude};{longitude},{latitude}...
        coords_str = ";".join([f"{lon:.5f},{lat:.5f}" for lon, lat in coords])
        
        url = f"{self.base_url}/match/v1/{self.profile}/{coords_str}"
        
        # Parameters
        # geometries=geojson makes it easier to parse into Shapely objects
        # radiuses gives search leeway in meters per point
        # gaps=ignore prevents OSRM from dropping segments when encountering minor gaps
        # annotations=nodes,distance provides detailed OSM node IDs for diagnostics
        radius_str = str(int(self.snap_radius_meters))
        params = {
            "geometries": "geojson",
            "overview": "full",
            "radiuses": ";".join([radius_str] * len(coords)),
            "gaps": "ignore",
            "annotations": "nodes,distance"
        }
        
        response = requests.get(url, params=params)
        
        if response.status_code != 200:
            print(f"OSRM Error: {response.status_code} - {response.text}")
            return None, {}
            
        data = response.json()
        
        if data.get("code") != "Ok" or not data.get("matchings"):
            print(f"OSRM Map Matching failed or returned no matchings: {data.get('code')}")
            return None, {}
            
        # OSRM can return multiple matching segments if there are gaps.
        # Stitch all matching geometries together so long routes are not cut off.
        all_coords = []
        confidences = []
        total_distance = 0.0
        osm_nodes = []
        
        for match in data["matchings"]:
            confidences.append(round(match.get("confidence", 0), 4))
            total_distance += match.get("distance", 0.0)
            
            # Extract OSM nodes from legs annotation if available
            legs = match.get("legs", [])
            for leg in legs:
                annotation = leg.get("annotation", {})
                if "nodes" in annotation:
                    osm_nodes.extend(annotation["nodes"])
                    
            sub_geom = shape(match["geometry"])
            if isinstance(sub_geom, LineString):
                sub_coords = list(sub_geom.coords)
                if not all_coords:
                    all_coords.extend(sub_coords)
                else:
                    if all_coords[-1] == sub_coords[0]:
                        all_coords.extend(sub_coords[1:])
                    else:
                        all_coords.extend(sub_coords)
                        
        if len(all_coords) < 2:
            return None, {}
            
        details = {
            "confidence": confidences,
            "distance_meters": round(total_distance, 1),
            "num_segments": len(data["matchings"]),
            "osm_nodes": osm_nodes,
            "matched_points_count": len(all_coords)
        }
        
        return LineString(all_coords), details
