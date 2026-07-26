import argparse
import sys
import os
import json

# Add the project root to the python path so 'src' can be imported when running directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import geopandas as gpd
from src.gtfs_parser import parse_shapes
from src.map_matcher import OSRMMapMatcher
from src.shape_cleaner import ShapeCleaner

def process_gtfs_shapes(gtfs_path: str, osrm_url: str, output_path: str, profile: str, max_points: int = 500, routes: list[str] = None, limit_shapes: int = None):
    print(f"Parsing shapes from {gtfs_path}...")
    try:
        shapes, route_mapping = parse_shapes(gtfs_path, limit_routes=routes)
    except Exception as e:
        print(f"Error parsing GTFS: {e}")
        sys.exit(1)
        
    print(f"Found {len(shapes)} unique shapes for bus routes.")
    
    if limit_shapes and limit_shapes > 0:
        shape_keys = list(shapes.keys())[:limit_shapes]
        shapes = {k: shapes[k] for k in shape_keys}
        print(f"Debug Mode: Limited processing to first {len(shapes)} shapes.")
    
    matcher = OSRMMapMatcher(base_url=osrm_url, profile=profile, max_points=max_points)
    cleaner = ShapeCleaner()
    
    results = []
    
    for shape_id, df in shapes.items():
        print(f"Processing shape {shape_id} ({len(df)} points)...")
        
        try:
            # 1. Map Match
            matched_geom = matcher.match_shape(df)
            
            if not matched_geom:
                print(f"  Warning: Map matching failed for {shape_id}. Skipping.")
                continue
                
            # 2. Clean Shape
            cleaned_geom = cleaner.clean_shape(matched_geom)
            
            # Store original geometry for comparison
            # Convert original dataframe to a LineString
            original_coords = list(zip(df['shape_pt_lon'], df['shape_pt_lat']))
            from shapely.geometry import LineString
            if len(original_coords) >= 2:
                original_geom = LineString(original_coords)
                results.append({
                    "shape_id": shape_id,
                    "status": "original",
                    "geometry": original_geom
                })
            
            # Store cleaned result
            results.append({
                "shape_id": shape_id,
                "status": "cleaned",
                "geometry": cleaned_geom
            })
            
        except Exception as e:
            print(f"  Error processing shape {shape_id}: {e}")
            
    if not results:
        print("No shapes were successfully processed.")
        sys.exit(1)
        
    # Export to GeoJSON
    gdf = gpd.GeoDataFrame(results, geometry="geometry", crs="EPSG:4326")
    gdf.to_file(output_path, driver="GeoJSON")
    
    # Inject route_mapping into the GeoJSON root
    with open(output_path, 'r') as f:
        data = json.load(f)
    data['route_mapping'] = route_mapping
    with open(output_path, 'w') as f:
        json.dump(data, f)
        
    print(f"Successfully wrote cleaned shapes and route mapping to {output_path}")

def main():
    parser = argparse.ArgumentParser(description="GTFS Enhancer: Clean route shapes using OSRM map matching.")
    parser.add_argument("gtfs_path", help="Path to GTFS zip file, directory, or HTTP(S) URL")
    parser.add_argument("output_path", help="Path to save the output GeoJSON")
    parser.add_argument("--osrm-url", default="http://localhost:5000", help="Base URL of local OSRM instance (default: http://localhost:5000)")
    parser.add_argument("--profile", default="driving", help="OSRM routing profile to use (default: driving)")
    parser.add_argument("--max-points", type=int, default=500, help="Maximum trace points per OSRM request before downsampling (default: 500)")
    parser.add_argument("--routes", type=str, default=None, help="Comma-separated list of route IDs or names to process (debug mode)")
    parser.add_argument("--limit-shapes", type=int, default=None, help="Limit processing to the first N shapes (debug mode)")
    
    args = parser.parse_args()
    
    is_url = args.gtfs_path.startswith("http://") or args.gtfs_path.startswith("https://")
    if not is_url and not os.path.exists(args.gtfs_path):
        print(f"Error: Path {args.gtfs_path} does not exist.")
        sys.exit(1)
        
    routes_list = [r.strip() for r in args.routes.split(",")] if args.routes else None
    process_gtfs_shapes(args.gtfs_path, args.osrm_url, args.output_path, args.profile, args.max_points, routes_list, args.limit_shapes)

if __name__ == "__main__":
    main()
