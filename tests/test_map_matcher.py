from unittest.mock import patch, MagicMock
from src.map_matcher import OSRMMapMatcher, _compute_bearings

def _response(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    return r


@patch('src.map_matcher.requests.get')
def test_match_coords_success(mock_get):
    payload = {
        "code": "Ok",
        "matchings": [
            {
                "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [0.0, 0.5], [0.0, 1.0]]},
                "confidence": 0.95,
                "distance": 111.0,
                "legs": [{"annotation": {"nodes": [10, 11, 12]}}],
            }
        ]
    }
    mock_get.return_value = _response(payload)

    matcher = OSRMMapMatcher(base_url="http://localhost:5000")
    result = matcher.match_coords([(0.0, 0.0), (0.0, 0.001)])

    assert result.success
    assert list(result.geometry.coords) == [(0.0, 0.0), (0.0, 0.5), (0.0, 1.0)]
    assert result.confidences == [0.95]
    assert result.distance_meters == 111.0
    assert result.osm_nodes == [10, 11, 12]

    call_args = mock_get.call_args
    url = call_args[0][0]
    assert url.startswith("http://localhost:5000/match/v1/bus/0.00000,0.00000;0.00000,0.00100")
    params = call_args[1]["params"]
    assert params["geometries"] == "geojson"
    assert params["gaps"] == "ignore"
    assert params["radiuses"] == "15;15"


@patch('src.map_matcher.requests.get')
def test_match_coords_picks_highest_confidence(mock_get):
    payload = {
        "code": "Ok",
        "matchings": [
            {"geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [0.0, 0.5]]}, "confidence": 0.2, "distance": 50.0, "legs": []},
            {"geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [0.0, 1.0]]}, "confidence": 0.98, "distance": 111.0, "legs": []},
        ]
    }
    mock_get.return_value = _response(payload)

    matcher = OSRMMapMatcher()
    result = matcher.match_coords([(0.0, 0.0), (0.0, 0.001)])

    assert result.success
    assert result.confidences == [0.98]
    assert list(result.geometry.coords) == [(0.0, 0.0), (0.0, 1.0)]


@patch('src.map_matcher.requests.get')
def test_match_coords_http_error(mock_get):
    mock_get.return_value = _response({"code": "Error"}, status=500)
    result = OSRMMapMatcher().match_coords([(0.0, 0.0), (0.0, 0.001)])
    assert not result.success
    assert "500" in result.error


@patch('src.map_matcher.requests.get')
def test_match_coords_no_matchings(mock_get):
    mock_get.return_value = _response({"code": "NoMatch", "matchings": []})
    result = OSRMMapMatcher().match_coords([(0.0, 0.0), (0.0, 0.001)])
    assert not result.success


def test_match_coords_requires_two_points():
    result = OSRMMapMatcher().match_coords([(0.0, 0.0)])
    assert not result.success


def test_compute_bearings():
    coords = [(0.0, 0.0), (0.0, 1.0)]
    result = _compute_bearings(coords, bearing_range=45)
    assert result == "0,45;0,45"

    coords_east = [(0.0, 0.0), (1.0, 0.0)]
    result_east = _compute_bearings(coords_east, bearing_range=30)
    assert result_east == "90,30;90,30"
