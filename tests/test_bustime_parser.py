import json
import tempfile
import os

from src.bustime_parser import parse_bustime_patterns

SAMPLE = {
    "routes": {
        "1": [
            {
                "pid": "8085",
                "rtdir": "Northbound",
                "ln": "24973",
                "pt": [
                    {"seq": "1", "lat": "41.831312", "lon": "-87.621697", "typ": "S", "stpid": "15314", "stpnm": "35th Street & Indiana", "pdist": "0"},
                    {"seq": "2", "lat": "41.831310", "lon": "-87.621770", "typ": "W"},
                    {"seq": "3", "lat": "41.832980", "lon": "-87.621800", "typ": "S", "stpid": "1560", "stpnm": "Indiana & 34th Street", "pdist": "609"},
                ],
            },
            {
                "pid": "6351",
                "rtdir": "Southbound",
                "ln": "23521",
                "pt": [
                    {"seq": "1", "lat": "41.874500", "lon": "-87.644386", "typ": "S", "stpid": "13155", "stpnm": "Desplaines & Harrison Terminal", "pdist": "0"},
                    {"seq": "2", "lat": "41.874328", "lon": "-87.644360", "typ": "W"},
                ],
            },
        ],
        "J14": [
            {
                "pid": "1234",
                "rtdir": "Southbound",
                "ln": "10000",
                "pt": [
                    {"seq": "1", "lat": "41.800000", "lon": "-87.600000", "typ": "W"},
                    {"seq": "2", "lat": "41.799900", "lon": "-87.599900", "typ": "W"},
                ],
            }
        ],
    }
}


def _write(tmpdir: str) -> str:
    path = os.path.join(tmpdir, "bustime_patterns.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(SAMPLE, f)
    return path


def test_parse_bustime_patterns_shapes_and_mapping():
    with tempfile.TemporaryDirectory() as tmp:
        shapes, route_mapping, _ = parse_bustime_patterns(_write(tmp))

    assert set(shapes.keys()) == {"1:8085", "1:6351", "J14:1234"}

    shape = shapes["1:8085"]
    assert list(shape.columns) == ["shape_id", "shape_pt_sequence", "shape_pt_lat", "shape_pt_lon"]
    assert len(shape) == 3
    assert shape.iloc[0]["shape_pt_lat"] == 41.831312
    assert shape.iloc[0]["shape_pt_lon"] == -87.621697
    assert shape.iloc[2]["shape_pt_sequence"] == 3

    assert route_mapping["1"] == ["1:8085", "1:6351"]
    assert route_mapping["J14"] == ["J14:1234"]


def test_parse_bustime_patterns_stop_usage():
    with tempfile.TemporaryDirectory() as tmp:
        _, _, stop_usage = parse_bustime_patterns(_write(tmp))

    stops = stop_usage["1:8085"]
    assert [s["stop_id"] for s in stops] == ["15314", "1560"]
    assert stops[0]["stop_name"] == "35th Street & Indiana"
    assert stops[0]["stop_sequence"] == 1
    assert stops[1]["stop_sequence"] == 3

    # patterns with no stops produce no stop_usage entry
    assert "J14:1234" not in stop_usage
