import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from shapely.geometry import LineString
from src.map_matcher import OSRMMapMatcher, _compute_bearings, _dedupe_coords

@pytest.fixture
def sample_shape_df():
    data = {
        'shape_pt_lon': [-122.4194, -122.4190, -122.4180],
        'shape_pt_lat': [37.7749, 37.7750, 37.7755],
        'shape_pt_sequence': [1, 2, 3]
    }
    return pd.DataFrame(data)

def _mock_response(payload, status=200):
    mock_response = MagicMock()
    mock_response.status_code = status
    mock_response.json.return_value = payload
    return mock_response

@patch('src.map_matcher.requests.get')
def test_match_shape_success(mock_get, sample_shape_df):
    payload = {
        "code": "Ok",
        "tracepoints": [
            {"matchings_index": 0, "waypoint_index": 0, "location": [-122.4194, 37.7749]},
            {"matchings_index": 0, "waypoint_index": 1, "location": [-122.419, 37.775]},
            {"matchings_index": 0, "waypoint_index": 2, "location": [-122.418, 37.7755]},
        ],
        "matchings": [
            {
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [-122.4194, 37.7749],
                        [-122.419, 37.775],
                        [-122.418, 37.7755]
                    ]
                },
                "confidence": 0.95,
                "distance": 100.0,
                "indices": [0, 1, 2],
                "legs": [{"annotation": {"nodes": [10, 11, 12]}}]
            }
        ]
    }
    mock_get.return_value = _mock_response(payload)

    matcher = OSRMMapMatcher(base_url="http://localhost:5000")

    result = matcher.match_shape(sample_shape_df)

    assert result.success
    assert result.geometry is not None
    assert len(result.segments) == 1
    assert result.segments[0].source_start == 0
    assert result.segments[0].source_end == 2
    assert result.segments[0].osm_nodes == [10, 11, 12]
    assert result.confidences == [0.95]
    assert result.repair_count == 0

    # Assert request shape
    assert mock_get.called
    call_args = mock_get.call_args
    url = call_args[0][0]
    assert url.startswith("http://localhost:5000/match/v1/bus/-122.41940,37.77490;-122.41900,37.77500;-122.41800,37.77550")

    kwargs = call_args[1]
    params = kwargs["params"]
    assert params.get("geometries") == "geojson"
    assert params.get("radiuses") == "15;15;15"
    # Gaps must be split so disconnected matchings are never force-stitched
    assert params.get("gaps") == "split"

@patch('src.map_matcher.requests.get')
def test_match_shape_downsamples(mock_get):
    mock_response = _mock_response({
        "code": "Ok",
        "tracepoints": [],
        "matchings": [{"geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}, "confidence": 0.95, "distance": 1.0, "indices": [0, 1], "legs": []}]
    })
    mock_get.return_value = mock_response

    matcher = OSRMMapMatcher(base_url="http://localhost:5000", max_points=3)

    data = {
        'shape_pt_lon': [0.0, 1.0, 2.0, 3.0, 4.0],
        'shape_pt_lat': [0.0, 0.00001, 0.0, 0.00001, 0.0],
        'shape_pt_sequence': [1, 2, 3, 4, 5]
    }
    df = pd.DataFrame(data)

    matcher.match_shape(df)

    assert mock_get.called
    call_args = mock_get.call_args
    url = call_args[0][0]
    coords_str = url.split('/')[-1]
    num_points = len(coords_str.split(';'))
    assert num_points <= 3

@patch('src.map_matcher.requests.get')
def test_match_shape_keeps_disjoint_segments_separate(mock_get, sample_shape_df):
    # Two matching segments whose endpoints do NOT touch. They must remain
    # separate segments and the primary geometry must NOT contain an artificial
    # straight connector between them.
    payload = {
        "code": "Ok",
        "tracepoints": [
            {"matchings_index": 0, "waypoint_index": 0, "location": [0.0, 0.0]},
            {"matchings_index": 0, "waypoint_index": 1, "location": [0.0, 0.5]},
            {"matchings_index": 1, "waypoint_index": 0, "location": [0.0, 1.5]},
            {"matchings_index": 1, "waypoint_index": 1, "location": [0.0, 2.0]},
        ],
        "matchings": [
            {
                "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [0.0, 0.5]]},
                "confidence": 0.95,
                "distance": 100.0,
                "indices": [0, 1],
                "legs": []
            },
            {
                "geometry": {"type": "LineString", "coordinates": [[0.0, 1.5], [0.0, 2.0]]},
                "confidence": 0.9,
                "distance": 100.0,
                "indices": [2, 3],
                "legs": []
            }
        ]
    }
    mock_get.return_value = _mock_response(payload)

    matcher = OSRMMapMatcher()
    result = matcher.match_shape(sample_shape_df)

    assert result.success
    assert len(result.segments) == 2
    # The 1.0-long gap between the two segments must NOT be bridged
    assert list(result.geometry.coords) == [(0.0, 0.0), (0.0, 0.5)]
    # Disjoint runs are reported in the result error string
    assert "disjoint_runs=1" in result.error

@patch('src.map_matcher.requests.get')
def test_match_shape_stitches_contiguous_segments(mock_get, sample_shape_df):
    # Two matching segments whose endpoints DO touch must be stitched into one line.
    payload = {
        "code": "Ok",
        "tracepoints": [],
        "matchings": [
            {
                "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [0.0, 0.5]]},
                "confidence": 0.95,
                "distance": 100.0,
                "indices": [0, 1],
                "legs": []
            },
            {
                "geometry": {"type": "LineString", "coordinates": [[0.0, 0.5], [0.0, 1.0]]},
                "confidence": 0.95,
                "distance": 100.0,
                "indices": [1, 2],
                "legs": []
            }
        ]
    }
    mock_get.return_value = _mock_response(payload)

    matcher = OSRMMapMatcher()
    result = matcher.match_shape(sample_shape_df)

    assert list(result.geometry.coords) == [(0.0, 0.0), (0.0, 0.5), (0.0, 1.0)]
    assert result.error == ""

def test_compute_bearings():
    coords = [(0.0, 0.0), (0.0, 1.0)]
    result = _compute_bearings(coords, bearing_range=45)
    assert result == "0,45;0,45"

    coords_east = [(0.0, 0.0), (1.0, 0.0)]
    result_east = _compute_bearings(coords_east, bearing_range=30)
    assert result_east == "90,30;90,30"

def test_dedupe_coords_preserves_endpoints():
    coords = [(0.0, 0.0), (0.0, 0.0), (1.0, 1.0), (1.0, 1.0), (2.0, 2.0)]
    assert _dedupe_coords(coords) == [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)]
