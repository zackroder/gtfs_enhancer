import pandas as pd
import zipfile
import os
import tempfile
import requests

def parse_shapes(gtfs_path: str) -> tuple[dict[str, pd.DataFrame], dict[str, list[str]]]:
    """
    Parses GTFS data, filtering shapes to bus routes only (route_type=3),
    and generates a mapping of route names to their shape_ids.
    
    Args:
        gtfs_path: Path to a GTFS .zip file, an extracted GTFS directory, or a URL.
        
    Returns:
        A tuple containing:
        - A dictionary mapping shape_id to a pandas DataFrame containing the shape points.
        - A dictionary mapping route names to a list of their unique shape_ids.
    """
    temp_zip = None
    if gtfs_path.startswith("http://") or gtfs_path.startswith("https://"):
        print(f"Downloading GTFS feed from {gtfs_path}...")
        response = requests.get(gtfs_path, stream=True)
        response.raise_for_status()
        
        fd, temp_zip_path = tempfile.mkstemp(suffix=".zip")
        with os.fdopen(fd, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                
        temp_zip = temp_zip_path
        gtfs_path = temp_zip_path

    try:
        def read_gtfs_csv(filename):
            if os.path.isdir(gtfs_path):
                fpath = os.path.join(gtfs_path, filename)
                if not os.path.exists(fpath):
                    return pd.DataFrame()
                return pd.read_csv(fpath)
            elif zipfile.is_zipfile(gtfs_path):
                with zipfile.ZipFile(gtfs_path) as zf:
                    if filename not in zf.namelist():
                        return pd.DataFrame()
                    with zf.open(filename) as f:
                        return pd.read_csv(f)
            else:
                raise ValueError(f"{gtfs_path} is neither a directory nor a valid zip file")

        # 1. Read routes and filter to bus (route_type == 3)
        routes_df = read_gtfs_csv('routes.txt')
        if not routes_df.empty and 'route_type' in routes_df.columns:
            bus_routes = routes_df[routes_df['route_type'] == 3]
        else:
            print("Warning: routes.txt missing or lacks route_type. Assuming all routes are buses.")
            bus_routes = routes_df
            
        bus_route_ids = set(bus_routes['route_id']) if not bus_routes.empty else set()
        
        # Create a mapping of route_id to a readable name
        route_names = {}
        for _, row in bus_routes.iterrows():
            name = str(row.get('route_short_name', row.get('route_long_name', row['route_id'])))
            if name == "nan":
                 name = str(row.get('route_long_name', row['route_id']))
            route_names[row['route_id']] = name
            
        # 2. Read trips and link routes to shapes
        trips_df = read_gtfs_csv('trips.txt')
        bus_shape_ids = set()
        route_mapping = {}
        
        if not trips_df.empty and 'shape_id' in trips_df.columns:
            for route_id, group in trips_df.groupby('route_id'):
                if route_id in bus_route_ids or not bus_route_ids:
                    r_shape_ids = group['shape_id'].dropna().unique().tolist()
                    r_shape_ids_str = [str(sid) for sid in r_shape_ids]
                    bus_shape_ids.update(r_shape_ids_str)
                    
                    r_name = route_names.get(route_id, str(route_id))
                    route_mapping[r_name] = r_shape_ids_str
        else:
            print("Warning: trips.txt missing or lacks shape_id. Cannot filter by route.")
            
        # 3. Read shapes
        shapes_df = read_gtfs_csv('shapes.txt')
        if shapes_df.empty:
            raise FileNotFoundError(f"shapes.txt not found or empty in {gtfs_path}")
            
        # Filter shapes to only those associated with buses
        if bus_shape_ids:
            shapes_df = shapes_df[shapes_df['shape_id'].astype(str).isin(bus_shape_ids)]
            
        # Ensure it's sorted by shape_pt_sequence
        shapes_df = shapes_df.sort_values(by=['shape_id', 'shape_pt_sequence'])
        
        # Group by shape_id
        shapes = {}
        for shape_id, group in shapes_df.groupby('shape_id'):
            shapes[str(shape_id)] = group.reset_index(drop=True)
            
    finally:
        if temp_zip and os.path.exists(temp_zip):
            os.remove(temp_zip)
            
    return shapes, route_mapping
