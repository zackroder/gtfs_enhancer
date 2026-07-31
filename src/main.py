import argparse
import sys
import os
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import current_thread

# Add the project root to the python path so 'src' can be imported when running directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import geopandas as gpd
from shapely.geometry import LineString
from src.gtfs_parser import parse_shapes
from src.map_matcher import OSRMMapMatcher
from src.shape_cleaner import ShapeCleaner
from src.quality import compute_match_metrics, classify_match

def setup_logging(log_file: str = "execution_debug.log"):
    """Sets up thread-safe logging to both console and log file for race condition diagnosis."""
    logger = logging.getLogger("gtfs_enhancer")
    logger.setLevel(logging.INFO)
    
    # Avoid duplicate handlers if setup_logging is called multiple times
    if logger.handlers:
        return logger
        
    formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] [Thread-%(threadName)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # File handler for diagnosing race conditions and failures
    file_handler = logging.FileHandler(log_file, mode='w')
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

def _feature(shape_id: str, status: str, geometry, **properties) -> dict:
    feature = {"shape_id": shape_id, "status": status, "geometry": geometry}
    feature.update(properties)
    return feature

def _process_single_shape(shape_id: str, df, matcher: OSRMMapMatcher, cleaner: ShapeCleaner, logger: logging.Logger, max_stub_meters: float = 75.0, enable_stub_filter: bool = False, quality_thresholds: dict = None):
    thread_name = current_thread().name
    logger.debug(f"Starting processing for shape {shape_id} with {len(df)} points on thread {thread_name}")
    
    results = []
    try:
        # Original geometry is always preserved for comparison in the viewer
        original_coords = list(zip(df['shape_pt_lon'], df['shape_pt_lat']))
        original_geom = LineString(original_coords) if len(original_coords) >= 2 else None

        # 0. Pre-matching stub filter (opt-in; diagnostic-only by default)
        if enable_stub_filter:
            pre_filtered_df = cleaner.filter_perpendicular_stubs(df, max_stub_meters=max_stub_meters)
            removed_stubs_count = len(df) - len(pre_filtered_df)
            if removed_stubs_count > 0:
                logger.info(f"Pre-matching filter removed {removed_stubs_count} side-stub point(s) from shape_id={shape_id}")
        else:
            pre_filtered_df = df

        # 1. Map Match (continuity-aware, gaps split, diagnostics preserved)
        match = matcher.match_shape(pre_filtered_df)

        if original_geom is not None:
            results.append(_feature(shape_id, "original", original_geom))

        if not match.success or match.geometry is None:
            logger.warning(f"Map matching failed for shape_id={shape_id}: {match.error}")
            # No centerline candidate exists; only then fall back to the source shape.
            if original_geom is not None:
                results.append(_feature(
                    shape_id, "cleaned_fallback", original_geom,
                    match_status="failed",
                    rejection_reason=match.error or "Map matching returned None",
                ))
            return shape_id, results, match.error or "Map matching returned None"

        # 2. Validate the matched centerline
        metrics = compute_match_metrics(original_coords, match.geometry, match)
        quality = classify_match(metrics, quality_thresholds)

        logger.info(
            f"shape_id={shape_id} | distance={match.distance_meters}m | confidence={match.confidences} "
            f"| segments={len(match.segments)} | repairs={match.repair_count} "
            f"| status={quality['status']} | reasons={quality['reasons']}"
        )
        if match.osm_nodes:
            logger.debug(f"shape_id={shape_id} OSM Node Trajectory: {match.osm_nodes[:15]}...")

        # Always emit the centerline result so we never regress to raw GPS jitter;
        # diagnostics flag matches that need review/repair.
        results.append(_feature(
            shape_id, "cleaned", match.geometry,
            match_status=quality["status"],
            rejection_reason="; ".join(quality["reasons"]),
            confidence=match.confidences,
            min_confidence=metrics["min_confidence"],
            mean_confidence=metrics["mean_confidence"],
            endpoint_error=metrics["endpoint_error"],
            start_error=metrics["start_error"],
            end_error=metrics["end_error"],
            length_ratio=metrics["length_ratio"],
            max_lateral_deviation=metrics["max_lateral_deviation"],
            p95_lateral_deviation=metrics["p95_lateral_deviation"],
            source_length=metrics["source_length"],
            matched_length=metrics["matched_length"],
            segment_count=metrics["segment_count"],
            repair_count=metrics["repair_count"],
        ))

        return shape_id, results, None

    except Exception as e:
        logger.error(f"Failure on shape_id={shape_id} on thread {thread_name}: {type(e).__name__}: {e}", exc_info=True)
        return shape_id, [], str(e)

def process_gtfs_shapes(gtfs_path: str, osrm_url: str, output_path: str, profile: str, max_points: int = 500, routes: list[str] = None, limit_shapes: int = None, workers: int = 4, log_file: str = "execution_debug.log", max_stub_meters: float = 75.0, snap_radius: float = 15.0, use_bearings: bool = True, bearing_range: int = 45, enable_stub_filter: bool = False, quality_thresholds: dict = None):
    logger = setup_logging(log_file)
    logger.info(f"Parsing shapes from {gtfs_path}...")
    
    try:
        shapes, route_mapping = parse_shapes(gtfs_path, limit_routes=routes)
    except Exception as e:
        logger.critical(f"Error parsing GTFS: {e}", exc_info=True)
        sys.exit(1)
        
    logger.info(f"Found {len(shapes)} unique shapes for bus routes.")
    
    if limit_shapes and limit_shapes > 0:
        shape_keys = list(shapes.keys())[:limit_shapes]
        shapes = {k: shapes[k] for k in shape_keys}
        logger.info(f"Debug Mode: Limited processing to first {len(shapes)} shapes.")
    
    matcher = OSRMMapMatcher(
        base_url=osrm_url,
        profile=profile,
        max_points=max_points,
        snap_radius_meters=snap_radius,
        use_bearings=use_bearings,
        bearing_range=bearing_range
    )
    cleaner = ShapeCleaner()
    
    all_results = []
    failed_shapes = []
    
    logger.info(f"Starting map matching using {workers} parallel worker thread(s) (Snap Radius: {snap_radius}m | Bearings: {use_bearings} ±{bearing_range}° | Pre-filter: {enable_stub_filter})...")
    
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_shape = {
                executor.submit(_process_single_shape, shape_id, df, matcher, cleaner, logger, max_stub_meters, enable_stub_filter, quality_thresholds): shape_id
                for shape_id, df in shapes.items()
            }
            
            for future in as_completed(future_to_shape):
                shape_id = future_to_shape[future]
                try:
                    s_id, res, err = future.result()
                    if err:
                        failed_shapes.append((s_id, err))
                    else:
                        all_results.extend(res)
                except Exception as exc:
                    logger.error(f"Unhandled thread execution error for shape_id={shape_id}: {exc}", exc_info=True)
                    failed_shapes.append((shape_id, str(exc)))
    else:
        # Sequential execution
        for shape_id, df in shapes.items():
            s_id, res, err = _process_single_shape(shape_id, df, matcher, cleaner, logger, max_stub_meters, enable_stub_filter, quality_thresholds)
            if err:
                failed_shapes.append((s_id, err))
            else:
                all_results.extend(res)
                
    logger.info(f"Processing complete. {len(all_results) // 2} shapes succeeded, {len(failed_shapes)} failed.")
    
    if failed_shapes:
        logger.warning(f"Failed shapes log ({len(failed_shapes)} total): {dict(failed_shapes)}")
        
    if not all_results:
        logger.critical("No shapes were successfully processed.")
        sys.exit(1)
        
    # Export to GeoJSON using GeoPandas
    logger.info(f"Exporting GeoJSON to {output_path}...")
    gdf = gpd.GeoDataFrame(all_results, geometry="geometry", crs="EPSG:4326")
    gdf.to_file(output_path, driver="GeoJSON")
    
    # Inject route_mapping into the GeoJSON root
    with open(output_path, 'r') as f:
        data = json.load(f)
    data['route_mapping'] = route_mapping
    with open(output_path, 'w') as f:
        json.dump(data, f)
        
    logger.info(f"Successfully wrote cleaned shapes and route mapping to {output_path}. Debug log written to {log_file}.")

def main():
    parser = argparse.ArgumentParser(description="GTFS Enhancer: Clean route shapes using OSRM map matching.")
    parser.add_argument("gtfs_path", help="Path to GTFS zip file, directory, or HTTP(S) URL")
    parser.add_argument("output_path", help="Path to save the output GeoJSON")
    parser.add_argument("--osrm-url", default="http://localhost:5000", help="Base URL of local OSRM instance (default: http://localhost:5000)")
    parser.add_argument("--profile", default="bus", help="OSRM routing profile to use (default: bus)")
    parser.add_argument("--max-points", type=int, default=500, help="Maximum trace points per OSRM request before downsampling (default: 500)")
    parser.add_argument("--snap-radius", type=float, default=15.0, help="OSRM search radius in meters for snapping points (default: 15.0; tighten to ~8-10m near parallel corridors)")
    parser.add_argument("--bearing-range", type=int, default=45, help="Allowed directional heading variance in degrees +/- (default: 45)")
    parser.add_argument("--no-bearings", action="store_true", help="Disable directional heading/bearing matching in OSRM")
    parser.add_argument("--enable-stub-filter", action="store_true", help="Enable pre-matching out-and-back stub filtering (diagnostic-only by default)")
    parser.add_argument("--max-stub-meters", type=float, default=75.0, help="Maximum distance in meters to classify a pre-matching side-stub (default: 75.0)")
    parser.add_argument("--min-confidence", type=float, default=0.75, help="Mean confidence below which a match is flagged suspect (default: 0.75)")
    parser.add_argument("--max-endpoint-error", type=float, default=40.0, help="Max mean start/end displacement in meters before a match is flagged (default: 40.0)")
    parser.add_argument("--max-lateral-deviation", type=float, default=50.0, help="Max perpendicular deviation in meters before a match is flagged (default: 50.0)")
    parser.add_argument("--routes", type=str, default=None, help="Comma-separated list of route IDs or names to process (debug mode)")
    parser.add_argument("--limit-shapes", type=int, default=None, help="Limit processing to the first N shapes (debug mode)")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel worker threads (default: 4)")
    parser.add_argument("--log-file", type=str, default="execution_debug.log", help="Log file path for race condition diagnostics (default: execution_debug.log)")
    
    args = parser.parse_args()
    
    is_url = args.gtfs_path.startswith("http://") or args.gtfs_path.startswith("https://")
    if not is_url and not os.path.exists(args.gtfs_path):
        print(f"Error: Path {args.gtfs_path} does not exist.")
        sys.exit(1)
        
    routes_list = [r.strip() for r in args.routes.split(",")] if args.routes else None
    quality_thresholds = {
        "min_confidence": args.min_confidence,
        "max_endpoint_error": args.max_endpoint_error,
        "max_lateral_deviation": args.max_lateral_deviation,
    }
    process_gtfs_shapes(
        args.gtfs_path,
        args.osrm_url,
        args.output_path,
        args.profile,
        args.max_points,
        routes_list,
        args.limit_shapes,
        args.workers,
        args.log_file,
        args.max_stub_meters,
        args.snap_radius,
        use_bearings=not args.no_bearings,
        bearing_range=args.bearing_range,
        enable_stub_filter=args.enable_stub_filter,
        quality_thresholds=quality_thresholds
    )

if __name__ == "__main__":
    main()
