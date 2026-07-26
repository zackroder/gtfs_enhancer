import requests
import pandas as pd
from shapely.geometry import LineString, shape
import urllib.parse
import json

class OSRMMapMatcher:
    def __init__(self, base_url: str = "http://localhost:5000", profile: str = "driving"):
        """
        Initializes the MapMatcher.
        
        Args:
            base_url: The base URL of the OSRM instance.
            profile: The routing profile (e.g., 'driving', 'bus').
        """
        self.base_url = base_url.rstrip('/')
        self.profile = profile

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
            
        # OSRM expects coordinates in the path as {longitude},{latitude};{longitude},{latitude}...
        # Chunking: Public OSRM API has a limit of 100 coordinates per request. 
        # For local OSRM, the limit can be configured much higher, but it's good practice 
        # to chunk if the shapes are extremely long (e.g., thousands of points).
        # We'll implement a simple one-shot request for now, assuming local config allows it.
        # If chunking is needed, we'd need to stitch the resulting geometries together.
        
        coords_str = ";".join([f"{lon:.5f},{lat:.5f}" for lon, lat in coords])
        
        url = f"{self.base_url}/match/v1/{self.profile}/{coords_str}"
        
        # Parameters
        # geometries=geojson makes it easier to parse into Shapely objects
        # radiuses gives some leeway for the GPS points
        params = {
            "geometries": "geojson",
            "overview": "full",
            "radiuses": ";".join(["25"] * len(coords)), # 25 meters leeway per point
            "annotations": "false"
        }
        
        response = requests.get(url, params=params)
        
        if response.status_code != 200:
            print(f"OSRM Error: {response.status_code} - {response.text}")
            return None
            
        data = response.json()
        
        if data.get("code") != "Ok" or not data.get("matchings"):
            print(f"OSRM Map Matching failed or returned no matchings: {data.get('code')}")
            return None
            
        # Find the matching with the highest confidence, or just take the first one
        best_match = max(data["matchings"], key=lambda m: m.get("confidence", 0))
        
        # Parse geojson into Shapely LineString
        matched_geom = shape(best_match["geometry"])
        
        return matched_geom
