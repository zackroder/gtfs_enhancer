import logging

import pandas as pd
from shapely.geometry import LineString

from src.main import _process_single_shape
from src.map_matcher import MatchResult, SegmentResult


def _logger():
    logger = logging.getLogger("test_gtfs_enhancer")
    logger.handlers = [logging.NullHandler()]
    logger.setLevel(logging.CRITICAL)
    return logger


def _df():
    return pd.DataFrame({
        'shape_pt_lon': [0.0, 0.0, 0.0],
        'shape_pt_lat': [0.0, 0.0001, 0.0002],
        'shape_pt_sequence': [1, 2, 3],
    })


class FakeMatcher:
    def __init__(self, match):
        self.match = match

    def match_shape(self, df):
        return self.match


class FakeCleaner:
    def __init__(self, tail_spans=None, tail_points_removed=0):
        self.tail_spans = tail_spans or []
        self.tail_points_removed = tail_points_removed

    def find_out_and_back_stubs(self, df, **kwargs):
        return df, []

    def filter_out_and_back_stubs(self, df, **kwargs):
        return df, []

    def project_stops_to_shape(self, df, stops, **kwargs):
        projected = []
        for i, s in enumerate(stops):
            projected.append({
                "stop_id": s["stop_id"], "stop_name": s.get("stop_name", ""),
                "stop_sequence": s.get("stop_sequence", 1),
                "lat": s["lat"], "lon": s["lon"],
                "shape_index": i, "shape_distance_meters": 0.0,
                "distance_to_shape_meters": 0.0,
                "is_first_stop": s.get("is_first_stop", False),
                "is_last_stop": s.get("is_last_stop", False),
                "is_terminal": False, "is_associated": True,
            })
        return projected

    def find_stop_tails(self, df, projected, **kwargs):
        return [dict(t) for t in self.tail_spans]

    def remove_stop_tails(self, df, tails):
        return df.iloc[:-self.tail_points_removed].reset_index(drop=True), tails


def _clean_match():
    seg = SegmentResult(
        geometry=LineString([(0.0, 0.0), (0.0, 0.0001), (0.0, 0.0002)]),
        confidence=0.98,
        distance_meters=22.0,
        osm_nodes=[10, 11, 12],
        source_start=0,
        source_end=2,
        tracepoint_indices=[0, 1, 2],
    )
    return MatchResult(
        success=True,
        geometry=seg.geometry,
        segments=[seg],
        tracepoints=[],
        confidences=[0.98],
        distance_meters=22.0,
        osm_nodes=[10, 11, 12],
        request_coords=[],
        repair_count=0,
    )


def test_process_single_shape_success_emits_original_and_cleaned():
    matcher = FakeMatcher(_clean_match())
    cleaner = FakeCleaner()
    shape_id, results, err = _process_single_shape("s1", _df(), matcher, cleaner, _logger())

    assert err is None
    assert len(results) == 2

    original = next(r for r in results if r["status"] == "original")
    cleaned = next(r for r in results if r["status"] == "cleaned")

    assert original["geometry"].coords[0] == (0.0, 0.0)
    assert cleaned["match_status"] == "clean"
    assert cleaned["rejection_reason"] == ""
    assert cleaned["min_confidence"] == 0.98
    assert cleaned["endpoint_error"] == 0.0
    assert cleaned["length_ratio"] == 1.0
    assert cleaned["segment_count"] == 1
    assert cleaned["repair_count"] == 0


def test_process_single_shape_flags_low_confidence():
    match = _clean_match()
    match.confidences = [0.2]
    match.segments[0].confidence = 0.2
    matcher = FakeMatcher(match)

    _, results, err = _process_single_shape("s1", _df(), matcher, FakeCleaner(), _logger())
    cleaned = next(r for r in results if r["status"] == "cleaned")

    assert err is None
    assert cleaned["match_status"] in ("suspect", "untrusted")
    assert any("confidence" in r for r in cleaned["rejection_reason"].split(";"))


def test_process_single_shape_falls_back_only_when_no_geometry():
    matcher = FakeMatcher(MatchResult(success=False, error="OSRM HTTP error 500"))
    _, results, err = _process_single_shape("s1", _df(), matcher, FakeCleaner(), _logger())

    assert err == "OSRM HTTP error 500"
    statuses = {r["status"] for r in results}
    assert "original" in statuses
    assert "cleaned_fallback" in statuses
    fallback = next(r for r in results if r["status"] == "cleaned_fallback")
    assert fallback["match_status"] == "failed"
    assert fallback["rejection_reason"] == "OSRM HTTP error 500"


def test_process_single_shape_removes_stop_tails_and_reports_diagnostics():
    stop_config = {
        "enable": True,
        "max_tail_meters": 125.0,
        "stop_radius_meters": 25.0,
        "return_corridor_meters": 20.0,
        "association_radius_meters": 50.0,
    }
    stops = [{"stop_id": "s1", "stop_name": "Stop", "stop_sequence": 1, "lat": 0.0, "lon": 0.0003}]
    cleaner = FakeCleaner(
        tail_spans=[{
            "start": 1, "end": 1, "tip": 1, "stop_id": "s1",
            "stop_distance_meters": 5.0, "tail_length_meters": 60.0,
            "return_distance_meters": 10.0, "excursion_meters": 30.0,
            "span_points": 1, "is_terminal": False,
        }],
        tail_points_removed=1,
    )
    matcher = FakeMatcher(_clean_match())

    shape_id, results, err = _process_single_shape(
        "s1", _df(), matcher, cleaner, _logger(), stop_config=stop_config, stops_for_shape=stops
    )

    assert err is None
    cleaned = next(r for r in results if r["status"] == "cleaned")
    assert cleaned["stop_tail_count"] == 1
    assert cleaned["stop_tail_points_removed"] == 1
    assert cleaned["stop_tail_stop_ids"] == ["s1"]
    assert cleaned["stop_associated_count"] == 1


def test_process_single_shape_stop_tails_disabled():
    stop_config = {"enable": False, "max_tail_meters": 125.0, "stop_radius_meters": 25.0,
                   "return_corridor_meters": 20.0, "association_radius_meters": 50.0}
    cleaner = FakeCleaner(tail_spans=[], tail_points_removed=0)
    matcher = FakeMatcher(_clean_match())

    shape_id, results, err = _process_single_shape(
        "s1", _df(), matcher, cleaner, _logger(), stop_config=stop_config, stops_for_shape=[{"stop_id": "s1", "lat": 0.0, "lon": 0.0}]
    )

    assert err is None
    cleaned = next(r for r in results if r["status"] == "cleaned")
    assert cleaned["stop_tail_count"] == 0
    assert cleaned["stop_tail_points_removed"] == 0
