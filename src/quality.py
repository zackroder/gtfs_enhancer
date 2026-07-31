"""Validation metrics and classification for map-matched shapes.

These helpers quantify how much a matched geometry deviates from the source
GTFS shape (endpoint error, lateral deviation, length ratio) and combine that
with OSRM confidence to classify a match as clean, suspect, or untrusted.
The goal is to *flag* low-quality matches so they can be repaired or reviewed,
not to discard the centerline geometry in favor of raw GTFS points.
"""

import math
from typing import Optional

from shapely.geometry import LineString, Point


def _dist_meters(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    mean_lat = math.radians((p1[1] + p2[1]) / 2.0)
    dx = (p2[0] - p1[0]) * 111000.0 * math.cos(mean_lat)
    dy = (p2[1] - p1[1]) * 111000.0
    return math.hypot(dx, dy)


def polyline_length(coords: list[tuple[float, float]]) -> float:
    return sum(_dist_meters(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def compute_match_metrics(original_coords: list[tuple[float, float]], geometry: LineString, match) -> dict:
    """
    Compute validation metrics comparing the source shape to the matched geometry.

    Args:
        original_coords: Ordered (lon, lat) coordinates of the source GTFS shape.
        geometry: The matched Shapely LineString (or best-effort centerline).
        match: The MatchResult produced by the map matcher.

    Returns:
        dict with source_length, matched_length, length_ratio, endpoint_error,
        start_error, end_error, max_lateral_deviation, p95_lateral_deviation,
        min_confidence, mean_confidence.
    """
    src_len = polyline_length(original_coords)
    matched_coords = list(geometry.coords)
    matched_len = polyline_length(matched_coords)

    start_err = _dist_meters(original_coords[0], matched_coords[0])
    end_err = _dist_meters(original_coords[-1], matched_coords[-1])

    dists = []
    for c in original_coords:
        try:
            dists.append(Point(c).distance(geometry))
        except Exception:
            dists.append(float('inf'))

    confs = list(match.confidences or [])

    metrics = {
        "source_length": round(src_len, 1),
        "matched_length": round(matched_len, 1),
        "length_ratio": round((matched_len / src_len), 3) if src_len > 0 else 1.0,
        "start_error": round(start_err, 1),
        "end_error": round(end_err, 1),
        "endpoint_error": round((start_err + end_err) / 2.0, 1),
        "max_lateral_deviation": round(max(dists) * 111000.0, 1) if dists else 0.0,
        "p95_lateral_deviation": round(sorted(dists)[int(len(dists) * 0.95) - 1] * 111000.0, 1) if dists else 0.0,
        "min_confidence": round(min(confs), 4) if confs else 0.0,
        "mean_confidence": round(sum(confs) / len(confs), 4) if confs else 0.0,
    }
    return metrics


def classify_match(metrics: dict, thresholds: Optional[dict] = None) -> dict:
    """
    Classify a matched shape as clean, suspect, or untrusted based on metrics.

    Thresholds (configurable):
        min_confidence: mean confidence below which a match is suspect.
        max_endpoint_error: mean start/end displacement in meters.
        max_lateral_deviation: max perpendicular deviation in meters.
        length_ratio_min / length_ratio_max: matched/source length sanity bounds.

    Returns:
        dict with 'status' in {'clean', 'suspect', 'untrusted'} and 'reasons'.
    """
    defaults = {
        "min_confidence": 0.75,
        "max_endpoint_error": 40.0,
        "max_lateral_deviation": 50.0,
        "length_ratio_min": 0.75,
        "length_ratio_max": 1.35,
    }
    t = {**defaults, **(thresholds or {})}

    reasons = []
    if metrics.get("mean_confidence", 1.0) < t["min_confidence"]:
        reasons.append(f"low confidence ({metrics['mean_confidence']:.2f})")
    if metrics.get("endpoint_error", 0.0) > t["max_endpoint_error"]:
        reasons.append(f"endpoint error {metrics['endpoint_error']:.0f}m")
    if metrics.get("max_lateral_deviation", 0.0) > t["max_lateral_deviation"]:
        reasons.append(f"lateral deviation {metrics['max_lateral_deviation']:.0f}m")
    ratio = metrics.get("length_ratio", 1.0)
    if not (t["length_ratio_min"] <= ratio <= t["length_ratio_max"]):
        reasons.append(f"length ratio {ratio:.2f}")

    if not reasons:
        status = "clean"
    elif len(reasons) <= 2:
        status = "suspect"
    else:
        status = "untrusted"

    return {"status": status, "reasons": reasons}
