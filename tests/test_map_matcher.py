import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from src.map_matcher import OSRMMapMatcher

@pytest.fixture
def sample_shape_df():
    data = {
        'shape_pt_lon': [-122.4194, -122.4190, -122.4180],
        'shape_pt_lat': [37.7749, 37.7750, 37.7755],
        'shape_pt_sequence': [1, 2, 3]
    }
    return pd.DataFrame(data)

@patch('src.map_matcher.requests.get')
def test_match_shape_success(mock_get, sample_shape_df):
    # Setup mock response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "code": "Ok",
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
                "confidence": 0.95
            }
        ]
    }
    mock_get.return_value = mock_response

    matcher = OSRMMapMatcher(base_url="http://localhost:5000")
    
    # We expect this to return a Shapely LineString and details dict
    matched_geom, details = matcher.match_shape(sample_shape_df)
    
    # Assert requests.get was called correctly
    assert mock_get.called
    call_args = mock_get.call_args
    url = call_args[0][0]
    
    assert url.startswith("http://localhost:5000/match/v1/bus/-122.41940,37.77490;-122.41900,37.77500;-122.41800,37.77550")
    
    kwargs = call_args[1]
    assert "params" in kwargs
    params = kwargs["params"]
    assert params.get("geometries") == "geojson"
    assert params.get("radiuses") == "15;15;15"

@patch('src.map_matcher.requests.get')
def test_match_shape_downsamples(mock_get):
    # Setup mock response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "code": "Ok",
        "matchings": [{"geometry": {"type": "LineString", "coordinates": [[0,0], [1,1]]}, "confidence": 0.95}]
    }
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
def test_match_shape_stitches_multiple_matchings(mock_get, sample_shape_df):
    mock_response = MagicMock()
    mock_response.status_code = 200
    # Simulate OSRM returning 2 separate matching segments due to a gap
    mock_response.json.return_value = {
        "code": "Ok",
        "matchings": [
            {
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[0.0, 0.0], [0.0, 0.5]]
                }
            },
            {
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[0.0, 0.5], [0.0, 1.0]]
                }
            }
        ]
    }
    mock_get.return_value = mock_response
    
    matcher = OSRMMapMatcher()
    geom, details = matcher.match_shape(sample_shape_df)
    
    # Verify that the two segments were stitched together into a 3-point LineString [(0,0), (0,0.5), (0,1)]
    assert list(geom.coords) == [(0.0, 0.0), (0.0, 0.5), (0.0, 1.0)]
