import logging

import pandas as pd
from shapely.geometry import LineString

from src.main import _process_single_shape
from src.map_matcher import MatchResult
from src.shape_cleaner import ShapeCleaner


def _logger():
    logger = logging.getLogger("test_gtfs_enhancer")
    logger.handlers = [logging.NullHandler()]
    logger.setLevel(logging.CRITICAL)
    return logger


def _df():
    # A clean corridor with an exact-return stop-tail poke at index 1
    return pd.DataFrame({
        'shape_pt_lon': [0.0, 0.0003, 0.0, 0.0, 0.0, 0.0],
        'shape_pt_lat': [0.0, 0.0, 0.0, 0.0001, 0.0002, 0.0003],
        'shape_pt_sequence': [1, 2, 3, 4, 5, 6],
    })


class FakeMatcher:
    def __init__(self, match):
        self.match = match

    def match_coords(self, coords):
        return self.match


def _clean_match():
    return MatchResult(
        success=True,
        geometry=LineString([(0.0, 0.0), (0.0, 0.0001), (0.0, 0.0002), (0.0, 0.0003)]),
        confidences=[0.98],
        distance_meters=33.0,
        osm_nodes=[10, 11, 12],
    )


def test_process_single_shape_emits_all_intermediate_stages():
    matcher = FakeMatcher(_clean_match())
    cleaner = ShapeCleaner()
    shape_id, results, err = _process_single_shape("s1", _df(), matcher, cleaner, _logger())

    assert err is None
    statuses = {r["status"] for r in results}
    assert {"original", "simplified", "stop_removed", "cleaned"} <= statuses

    original = next(r for r in results if r["status"] == "original")
    simplified = next(r for r in results if r["status"] == "simplified")
    stop_removed = next(r for r in results if r["status"] == "stop_removed")
    cleaned = next(r for r in results if r["status"] == "cleaned")

    assert original["points"] == len(_df())
    assert len(simplified["geometry"].coords) <= original["points"]
    assert stop_removed["stop_excursions_removed"] >= 0

    assert cleaned["match_status"] in ("clean", "suspect", "untrusted")
    assert cleaned["original_points"] == original["points"]
    assert cleaned["stop_removed_points"] == len(stop_removed["geometry"].coords)
    assert cleaned["matched_points"] == 4


def test_process_single_shape_falls_back_when_match_fails():
    matcher = FakeMatcher(MatchResult(success=False, error="OSRM HTTP error 500"))
    _, results, err = _process_single_shape("s1", _df(), matcher, ShapeCleaner(), _logger())

    assert err == "OSRM HTTP error 500"
    statuses = {r["status"] for r in results}
    assert "cleaned_fallback" in statuses
    fallback = next(r for r in results if r["status"] == "cleaned_fallback")
    assert fallback["match_status"] == "failed"
    assert fallback["rejection_reason"] == "OSRM HTTP error 500"


def test_process_single_shape_reports_stop_excursion_removal():
    # With a stop at the poke tip, the A-B-C poke-out at index 1 is removed.
    matcher = FakeMatcher(_clean_match())
    stops = [{"stop_id": "s1", "stop_name": "Stop", "stop_sequence": 1, "lat": 0.0, "lon": 0.0003}]
    _, results, err = _process_single_shape("s1", _df(), matcher, ShapeCleaner(), _logger(), stops_for_shape=stops)

    assert err is None
    cleaned = next(r for r in results if r["status"] == "cleaned")
    assert cleaned["stop_excursions_removed"] == 1
    assert cleaned["stop_excursion_details"][0]["deviation_meters"] > 20.0
