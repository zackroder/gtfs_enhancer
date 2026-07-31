import pandas as pd
import zipfile
import os
import tempfile
import requests


def _ensure_local_source(gtfs_path: str) -> tuple[str, str | None]:
    """
    Downloads a URL feed to a temporary zip file. Returns (local_path, temp_zip_to_cleanup).
    For local directory/zip inputs, returns (gtfs_path, None).
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
    return gtfs_path, temp_zip


def _read_gtfs_csv(gtfs_path: str, filename: str) -> pd.DataFrame:
    """Reads a GTFS CSV from a directory or zip path. Returns an empty DataFrame when absent."""
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


def _filter_bus_routes(gtfs_path: str, limit_routes: list[str]) -> tuple[pd.DataFrame, dict, set]:
    """
    Reads routes.txt, filters to bus routes (route_type == 3) plus any limit_routes match,
    and returns (bus_routes_df, route_id->name mapping, bus_route_id_set).
    """
    routes_df = _read_gtfs_csv(gtfs_path, 'routes.txt')
    if not routes_df.empty and 'route_type' in routes_df.columns:
        bus_routes = routes_df[routes_df['route_type'] == 3]
    else:
        print("Warning: routes.txt missing or lacks route_type. Assuming all routes are buses.")
        bus_routes = routes_df

    if limit_routes and not bus_routes.empty:
        limit_routes_set = set(str(r).strip() for r in limit_routes)
        masks = []
        for col in ('route_id', 'route_short_name', 'route_long_name'):
            if col in bus_routes.columns:
                masks.append(bus_routes[col].astype(str).isin(limit_routes_set))
        mask = pd.concat(masks, axis=1).any(axis=1) if masks else pd.Series(False, index=bus_routes.index)
        bus_routes = bus_routes[mask]
        print(f"Debug Mode: Filtered routes down to {len(bus_routes)} matching routes: {limit_routes_set}")

    bus_route_ids = set(bus_routes['route_id']) if not bus_routes.empty else set()

    route_names = {}
    for _, row in bus_routes.iterrows():
        name = str(row.get('route_short_name', row.get('route_long_name', row['route_id'])))
        if name == "nan":
            name = str(row.get('route_long_name', row['route_id']))
        route_names[row['route_id']] = name

    return bus_routes, route_names, bus_route_ids


def parse_shapes(gtfs_path: str, limit_routes: list[str] = None) -> tuple[dict[str, pd.DataFrame], dict[str, list[str]]]:
    """
    Parses GTFS data, filtering shapes to bus routes only (route_type=3),
    and generates a mapping of route names to their shape_ids.
    
    Args:
        gtfs_path: Path to a GTFS .zip file, an extracted GTFS directory, or a URL.
        limit_routes: Optional list of route short names, long names, or IDs to limit parsing to.
        
    Returns:
        A tuple containing:
        - A dictionary mapping shape_id to a pandas DataFrame containing the shape points.
        - A dictionary mapping route names to a list of their unique shape_ids.
    """
    gtfs_path, temp_zip = _ensure_local_source(gtfs_path)

    try:
        _, route_names, bus_route_ids = _filter_bus_routes(gtfs_path, limit_routes)

        # 2. Read trips and link routes to shapes
        trips_df = _read_gtfs_csv(gtfs_path, 'trips.txt')
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
        shapes_df = _read_gtfs_csv(gtfs_path, 'shapes.txt')
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


def parse_stop_usage(gtfs_path: str, limit_routes: list[str] = None) -> dict[str, list[dict]]:
    """
    Parses stop locations and how each shape uses them, ordered by stop_sequence.

    Joins stops.txt through stop_times.txt and trips.txt, grouping by shape_id.
    Only stops belonging to bus routes (and any limit_routes match) are included.

    Args:
        gtfs_path: Path to a GTFS .zip file, an extracted GTFS directory, or a URL.
        limit_routes: Optional list of route short names, long names, or IDs to limit parsing to.

    Returns:
        A dict mapping shape_id to an ordered list of stop records:
        {
            "stop_id": str,
            "stop_name": str,
            "stop_sequence": int,
            "lat": float,
            "lon": float,
            "is_first_stop": bool,   # first stop in every trip using this shape
            "is_last_stop": bool,    # last stop in every trip using this shape
        }
        Returns an empty dict when stops.txt or stop_times.txt is unavailable.
    """
    gtfs_path, temp_zip = _ensure_local_source(gtfs_path)

    try:
        _, _, bus_route_ids = _filter_bus_routes(gtfs_path, limit_routes)

        stops_df = _read_gtfs_csv(gtfs_path, 'stops.txt')
        stop_times_df = _read_gtfs_csv(gtfs_path, 'stop_times.txt')
        trips_df = _read_gtfs_csv(gtfs_path, 'trips.txt')

        if (stops_df.empty or stop_times_df.empty or trips_df.empty
                or 'stop_id' not in stops_df.columns
                or 'trip_id' not in stop_times_df.columns
                or 'stop_id' not in stop_times_df.columns
                or 'shape_id' not in trips_df.columns):
            print("Warning: stops.txt/stop_times.txt/trips.txt incomplete. Stop-aware preprocessing disabled.")
            return {}

        # Filter trips to bus routes
        if bus_route_ids:
            trips_df = trips_df[trips_df['route_id'].isin(bus_route_ids)]

        if trips_df.empty:
            return {}

        # Index stops
        stops_index = stops_df.set_index('stop_id')

        # Join stop_times -> trips -> shape
        merged = stop_times_df.merge(
            trips_df[['trip_id', 'shape_id']], on='trip_id', how='inner'
        )

        shape_to_stops: dict[str, dict[str, dict]] = {}
        shape_to_first_last: dict[str, dict] = {}

        for shape_id, group in merged.groupby('shape_id'):
            shape_key = str(shape_id)
            trips_in_shape = group['trip_id'].unique()

            first_stop_ids = set()
            last_stop_ids = set()
            for trip_id in trips_in_shape:
                trip_rows = group[group['trip_id'] == trip_id].sort_values('stop_sequence')
                if trip_rows.empty:
                    continue
                first_stop_ids.add(str(trip_rows.iloc[0]['stop_id']))
                last_stop_ids.add(str(trip_rows.iloc[-1]['stop_id']))

            # Aggregate stops across trips: minimum stop_sequence per stop_id
            agg = group.groupby('stop_id').agg(
                stop_sequence=('stop_sequence', 'min'),
            ).reset_index()
            agg = agg.sort_values('stop_sequence')

            records = []
            for _, row in agg.iterrows():
                stop_id = str(row['stop_id'])
                if stop_id not in stops_index.index:
                    continue
                stop_row = stops_index.loc[stop_id]
                records.append({
                    "stop_id": stop_id,
                    "stop_name": str(stop_row.get('stop_name', '')) if 'stop_name' in stops_index.columns else '',
                    "stop_sequence": int(row['stop_sequence']),
                    "lat": float(stop_row['stop_lat']),
                    "lon": float(stop_row['stop_lon']),
                    "is_first_stop": stop_id in first_stop_ids,
                    "is_last_stop": stop_id in last_stop_ids,
                })
            shape_to_stops[shape_key] = records
            shape_to_first_last[shape_key] = {"first": first_stop_ids, "last": last_stop_ids}

        return shape_to_stops

    finally:
        if temp_zip and os.path.exists(temp_zip):
            os.remove(temp_zip)
