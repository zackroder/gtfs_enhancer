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
    def find_out_and_back_stubs(self, df, **kwargs):
        return df, []

    def filter_out_and_back_stubs(self, df, **kwargs):
        return df, []


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
