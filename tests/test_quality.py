from shapely.geometry import LineString
from src.quality import compute_match_metrics, classify_match, polyline_length

class FakeMatch:
    def __init__(self, confidences, segments=None, repair_count=0):
        self.confidences = confidences
        self.segments = segments if segments is not None else []
        self.repair_count = repair_count

def test_polyline_length():
    # 1 degree of latitude is ~111 km
    coords = [(0.0, 0.0), (0.0, 0.001)]
    assert polyline_length(coords) == 111.0

def test_compute_match_metrics_clean():
    original = [(0.0, 0.0), (0.0, 0.001), (0.0, 0.002)]
    geometry = LineString(original)
    match = FakeMatch(confidences=[0.98], segments=[object()])
    m = compute_match_metrics(original, geometry, match)

    assert m["length_ratio"] == 1.0
    assert m["endpoint_error"] == 0.0
    assert m["max_lateral_deviation"] == 0.0
    assert m["mean_confidence"] == 0.98
    assert m["segment_count"] == 1

def test_compute_match_metrics_flags_deviation():
    # Matched geometry is parallel to the source one block (~100m) away
    original = [(0.0, 0.0), (0.0, 0.001), (0.0, 0.002)]
    geometry = LineString([(0.001, 0.0), (0.001, 0.001), (0.001, 0.002)])
    match = FakeMatch(confidences=[0.9], segments=[object()])
    m = compute_match_metrics(original, geometry, match)

    assert m["max_lateral_deviation"] > 100.0
    assert m["endpoint_error"] > 100.0

def test_classify_match_clean():
    match = FakeMatch(confidences=[0.98], segments=[object()])
    original = [(0.0, 0.0), (0.0, 0.001), (0.0, 0.002)]
    geometry = LineString(original)
    metrics = compute_match_metrics(original, geometry, match)
    result = classify_match(metrics)
    assert result["status"] == "clean"
    assert result["reasons"] == []

def test_classify_match_low_confidence():
    match = FakeMatch(confidences=[0.04], segments=[object()])
    original = [(0.0, 0.0), (0.0, 0.001), (0.0, 0.002)]
    geometry = LineString(original)
    metrics = compute_match_metrics(original, geometry, match)
    result = classify_match(metrics)
    assert result["status"] in ("suspect", "untrusted")
    assert any("confidence" in r for r in result["reasons"])

def test_classify_match_reports_repairs():
    match = FakeMatch(confidences=[0.9], segments=[object()], repair_count=2)
    original = [(0.0, 0.0), (0.0, 0.001), (0.0, 0.002)]
    geometry = LineString(original)
    metrics = compute_match_metrics(original, geometry, match)
    result = classify_match(metrics)
    assert any("repairs" in r for r in result["reasons"])

def test_classify_match_respects_custom_thresholds():
    metrics = {
        "mean_confidence": 0.9,
        "endpoint_error": 20.0,
        "max_lateral_deviation": 10.0,
        "length_ratio": 1.0,
        "repair_count": 0,
    }
    # Tight endpoint threshold should flag this match
    result = classify_match(metrics, {"max_endpoint_error": 10.0})
    assert result["status"] != "clean"
