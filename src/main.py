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
from src.bustime_parser import parse_bustime_patterns
from src.gtfs_parser import parse_shapes, parse_stop_usage
from src.map_matcher import OSRMMapMatcher
from src.shape_cleaner import ShapeCleaner
from src.quality import compute_match_metrics, classify_match

def setup_logging(log_file: str = "execution_debug.log"):
    """Sets up thread-safe logging to both console and log file."""
    logger = logging.getLogger("gtfs_enhancer")
    logger.setLevel(logging.INFO)
    
    if logger.handlers:
        return logger
        
    formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] [Thread-%(threadName)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    file_handler = logging.FileHandler(log_file, mode='w')
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    
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

def _process_single_shape(shape_id: str, df, matcher: OSRMMapMatcher, cleaner: ShapeCleaner, logger: logging.Logger, quality_thresholds: dict = None, preprocess_kwargs: dict = None, stops_for_shape: list = None):
    thread_name = current_thread().name
    logger.debug(f"Starting processing for shape {shape_id} with {len(df)} points on thread {thread_name}")
    
    results = []
    try:
        preprocess_kwargs = dict(preprocess_kwargs or {})
        preprocess_kwargs["stops"] = stops_for_shape or []
        stages = cleaner.preprocess_shape(df, **preprocess_kwargs)

        if len(stages["original"]) < 2:
            logger.warning(f"Shape {shape_id} has fewer than 2 points; skipping.")
            return shape_id, [], "Shape has fewer than 2 points"

        # Preserve each intermediate stage for review
        results.append(_feature(shape_id, "original", LineString(stages["original"]), points=len(stages["original"])))
        results.append(_feature(shape_id, "simplified", LineString(stages["simplified"]), points=len(stages["simplified"])))
        results.append(_feature(
            shape_id, "stop_removed", LineString(stages["stop_removed"]),
            points=len(stages["stop_removed"]),
            stop_excursions_removed=len(stages["removed_stops"]),
        ))

        # Map match the cleaned skeleton
        match = matcher.match_coords(stages["final"])

        if not match.success or match.geometry is None:
            logger.warning(f"Map matching failed for shape_id={shape_id}: {match.error}")
            results.append(_feature(
                shape_id, "cleaned_fallback", LineString(stages["original"]),
                match_status="failed",
                rejection_reason=match.error or "Map matching returned None",
                points=len(stages["original"]),
            ))
            return shape_id, results, match.error or "Map matching returned None"

        # Validate the matched centerline
        metrics = compute_match_metrics(stages["original"], match.geometry, match)
        quality = classify_match(metrics, quality_thresholds)

        logger.info(
            f"shape_id={shape_id} | distance={match.distance_meters}m | confidence={match.confidences} "
            f"| pts {len(stages['original'])}->{len(stages['simplified'])}->{len(stages['stop_removed'])} "
            f"| stop_excursions={len(stages['removed_stops'])} | status={quality['status']} | reasons={quality['reasons']}"
        )

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
            original_points=len(stages["original"]),
            simplified_points=len(stages["simplified"]),
            stop_removed_points=len(stages["stop_removed"]),
            matched_points=len(match.geometry.coords),
            stop_excursions_removed=len(stages["removed_stops"]),
            stop_excursion_details=stages["removed_stops"],
        ))

        return shape_id, results, None

    except Exception as e:
        logger.error(f"Failure on shape_id={shape_id} on thread {thread_name}: {type(e).__name__}: {e}", exc_info=True)
        return shape_id, [], str(e)

def process_gtfs_shapes(gtfs_path: str, osrm_url: str, output_path: str, profile: str, max_points: int = 500, routes: list[str] = None, limit_shapes: int = None, workers: int = 4, log_file: str = "execution_debug.log", snap_radius: float = 15.0, use_bearings: bool = True, bearing_range: int = 45, quality_thresholds: dict = None, preprocess_kwargs: dict = None, input_format: str = "gtfs"):
    logger = setup_logging(log_file)
    logger.info(f"Parsing shapes from {gtfs_path} (format={input_format})...")
    
    try:
        if input_format == "bustime":
            shapes, route_mapping, stop_usage = parse_bustime_patterns(gtfs_path)
            logger.info(f"Found {len(shapes)} unique bustime patterns.")
        else:
            shapes, route_mapping = parse_shapes(gtfs_path, limit_routes=routes)
            logger.info(f"Found {len(shapes)} unique shapes for bus routes.")
            stop_usage = {}
            try:
                stop_usage = parse_stop_usage(gtfs_path, limit_routes=routes)
                if stop_usage:
                    logger.info(f"Parsed stop usage for {len(stop_usage)} shapes.")
                else:
                    logger.warning("No stop data found; stop-excursion removal disabled for this feed.")
            except Exception as e:
                logger.warning(f"Failed to parse stop usage ({e}); stop-excursion removal disabled.")
    except Exception as e:
        logger.critical(f"Error parsing input: {e}", exc_info=True)
        sys.exit(1)

    if limit_shapes and limit_shapes > 0:
        shape_keys = list(shapes.keys())[:limit_shapes]
        shapes = {k: shapes[k] for k in shape_keys}
        logger.info(f"Debug Mode: Limited processing to first {len(shapes)} shapes.")
    
    matcher = OSRMMapMatcher(
        base_url=osrm_url,
        profile=profile,
        snap_radius_meters=snap_radius,
        use_bearings=use_bearings,
        bearing_range=bearing_range
    )
    cleaner = ShapeCleaner()
    
    all_results = []
    failed_shapes = []
    
    pp = preprocess_kwargs or {}
    logger.info(
        f"Starting map matching using {workers} parallel worker thread(s) "
        f"(Snap Radius: {snap_radius}m | Bearings: {use_bearings} ±{bearing_range}° | "
        f"RDP: {pp.get('simplify_tolerance_meters', 15.0)}m | Stop excursion: return<={pp.get('spike_max_return_meters', 50.0)}m dev>={pp.get('spike_min_deviation_meters', 8.0)}m)"
    )
    
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_shape = {
                executor.submit(_process_single_shape, shape_id, df, matcher, cleaner, logger, quality_thresholds, preprocess_kwargs, stop_usage.get(shape_id, [])): shape_id
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
        for shape_id, df in shapes.items():
            s_id, res, err = _process_single_shape(shape_id, df, matcher, cleaner, logger, quality_thresholds, preprocess_kwargs, stop_usage.get(shape_id, []))
            if err:
                failed_shapes.append((s_id, err))
            else:
                all_results.extend(res)
                
    logger.info(f"Processing complete. {len(all_results) // 4} shapes succeeded, {len(failed_shapes)} failed.")
    
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
    parser.add_argument("gtfs_path", help="Path to GTFS zip file, directory, URL, or bustime patterns JSON")
    parser.add_argument("output_path", help="Path to save the output GeoJSON")
    parser.add_argument("--input-format", type=str, default="gtfs", choices=["gtfs", "bustime"], help="Input format: gtfs (zip/dir/url) or bustime (getpatterns JSON)")
    parser.add_argument("--osrm-url", default="http://localhost:5000", help="Base URL of local OSRM instance (default: http://localhost:5000)")
    parser.add_argument("--profile", default="bus", help="OSRM routing profile to use (default: bus)")
    parser.add_argument("--snap-radius", type=float, default=15.0, help="OSRM search radius in meters for snapping points (default: 15.0; tighten to ~8-10m near parallel corridors)")
    parser.add_argument("--bearing-range", type=int, default=45, help="Allowed directional heading variance in degrees +/- (default: 45)")
    parser.add_argument("--no-bearings", action="store_true", help="Disable directional heading/bearing matching in OSRM")
    parser.add_argument("--simplify-tolerance", type=float, default=15.0, help="RDP simplification tolerance in meters applied before matching to strip GPS jitter and short stop tails (default: 15.0)")
    parser.add_argument("--max-points", type=int, default=500, help="Maximum trace points sent to OSRM after resampling (default: 500)")
    parser.add_argument("--spike-return", type=float, default=50.0, help="Max chord length in meters for a stop excursion to count as returning to the corridor (default: 50.0)")
    parser.add_argument("--spike-deviation", type=float, default=8.0, help="Min deviation in meters for a stop-excursion vertex to be removed (default: 8.0)")
    parser.add_argument("--stop-radius", type=float, default=60.0, help="Max distance in meters from the excursion tip to a stop for removal (default: 60.0)")
    parser.add_argument("--min-confidence", type=float, default=0.75, help="Mean confidence below which a match is flagged suspect (default: 0.75)")
    parser.add_argument("--max-endpoint-error", type=float, default=40.0, help="Max mean start/end displacement in meters before a match is flagged (default: 40.0)")
    parser.add_argument("--max-lateral-deviation", type=float, default=50.0, help="Max perpendicular deviation in meters before a match is flagged (default: 50.0)")
    parser.add_argument("--routes", type=str, default=None, help="Comma-separated list of route IDs or names to process (debug mode)")
    parser.add_argument("--limit-shapes", type=int, default=None, help="Limit processing to the first N shapes (debug mode)")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel worker threads (default: 4)")
    parser.add_argument("--log-file", type=str, default="execution_debug.log", help="Log file path (default: execution_debug.log)")
    
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
    preprocess_kwargs = {
        "simplify_tolerance_meters": args.simplify_tolerance,
        "spike_max_return_meters": args.spike_return,
        "spike_min_deviation_meters": args.spike_deviation,
        "stop_radius_meters": args.stop_radius,
        "max_points": args.max_points,
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
        args.snap_radius,
        use_bearings=not args.no_bearings,
        bearing_range=args.bearing_range,
        quality_thresholds=quality_thresholds,
        preprocess_kwargs=preprocess_kwargs,
        input_format=args.input_format
    )

if __name__ == "__main__":
    main()
