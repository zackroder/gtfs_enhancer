import pandas as pd
from unittest.mock import patch, MagicMock
from shapely.geometry import LineString
from src.map_matcher import OSRMMapMatcher, SegmentResult


def _response(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    return r


def _initial_payload_segments():
    return {
        "code": "Ok",
        "tracepoints": [
            {"matchings_index": 0, "waypoint_index": 0},
            {"matchings_index": 0, "waypoint_index": 1},
            {"matchings_index": 1, "waypoint_index": 0},
            {"matchings_index": 1, "waypoint_index": 1},
        ],
        "matchings": [
            {
                "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0002], [0.0, 0.0003]]},
                "confidence": 0.98,
                "distance": 12.0,
                "indices": [2, 3],
                "legs": [{"annotation": {"nodes": [10, 11, 12]}}],
            },
            {
                "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0007], [0.0, 0.0008]]},
                "confidence": 0.98,
                "distance": 12.0,
                "indices": [7, 8],
                "legs": [{"annotation": {"nodes": [30, 31, 32]}}],
            },
        ],
    }


def _bridge_payload():
    return {
        "code": "Ok",
        "tracepoints": [],
        "matchings": [
            {
                "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0003], [0.0, 0.0005], [0.0, 0.0007]]},
                "confidence": 0.99,
                "distance": 24.0,
                "indices": [0, 1, 2],
                # node trajectory overlaps seg0 tail (12) and seg1 head (30)
                "legs": [{"annotation": {"nodes": [12, 13, 14, 15, 30]}}],
            }
        ],
    }


def _df(n):
    lats = [0.0 + i * 0.0001 for i in range(n)]
    return pd.DataFrame({
        'shape_pt_lon': [0.0] * n,
        'shape_pt_lat': lats,
        'shape_pt_sequence': list(range(1, n + 1)),
    })


@patch('src.map_matcher.requests.get')
def test_score_candidate_rewards_same_road_node_continuity(mock_get):
    matcher = OSRMMapMatcher()
    prev = SegmentResult(
        geometry=LineString([(0.0, 0.0), (0.0, 0.0002)]),
        confidence=0.98, distance_meters=22.0, osm_nodes=[100, 101, 102, 103],
        source_start=0, source_end=1, tracepoint_indices=[0, 1],
    )
    nxt = SegmentResult(
        geometry=LineString([(0.0, 0.0006), (0.0, 0.0008)]),
        confidence=0.98, distance_meters=22.0, osm_nodes=[200, 201, 202, 203],
        source_start=3, source_end=4, tracepoint_indices=[3, 4],
    )
    gap = [(0.0, 0.0003), (0.0, 0.0004), (0.0, 0.0005)]
    cand_geom = LineString([(0.0, 0.0002), (0.0, 0.0006)])

    # Candidate A continues the same road (shares nodes with both neighbors)
    nodes_a = [103, 104, 105, 200]
    # Candidate B is a parallel-road snap (shares no nodes)
    nodes_b = [900, 901, 902, 903]

    score_a = matcher._score_candidate(cand_geom, nodes_a, gap, prev, nxt, 0.9)
    score_b = matcher._score_candidate(cand_geom, nodes_b, gap, prev, nxt, 0.9)

    assert score_a < score_b
    assert matcher._node_continuity(nodes_a, prev, nxt) >= 2.0
    assert matcher._node_continuity(nodes_b, prev, nxt) == 0.0


@patch('src.map_matcher.requests.get')
def test_bridge_gaps_connects_disjoint_segments_without_artificial_line(mock_get):
    n = 12
    initial = _initial_payload_segments()
    bridge = _bridge_payload()

    def side_effect(url, **kwargs):
        num = len(url.split('/')[-1].split(';'))
        if num == n:
            return _response(initial)
        return _response(bridge)

    mock_get.side_effect = side_effect

    # window_context_points=1 keeps the repair window (indices 1..9) distinct
    # from the full 12-point request so we can tell the two calls apart.
    matcher = OSRMMapMatcher(window_context_points=1)
    result = matcher.match_shape(_df(n))

    assert result.success
    assert result.repair_count == 1
    assert len(result.segments) == 3
    assert result.segments[1].repaired is True
    # Fully connected: no disjoint runs remain
    assert result.error == ""
    assert list(result.geometry.coords) == [
        (0.0, 0.0002), (0.0, 0.0003), (0.0, 0.0005), (0.0, 0.0007), (0.0, 0.0008),
    ]


@patch('src.map_matcher.requests.get')
def test_refine_low_confidence_replaces_wrong_road_snap(mock_get):
    n = 15
    # Initial match snaps the middle span onto a parallel road 100m east.
    initial = {
        "code": "Ok",
        "tracepoints": [{"matchings_index": 0} if 3 <= i <= 11 else None for i in range(n)],
        "matchings": [
            {
                "geometry": {"type": "LineString", "coordinates": [
                    [0.001, 0.0003], [0.001, 0.0004], [0.001, 0.0005],
                    [0.001, 0.0006], [0.001, 0.0007], [0.001, 0.0008],
                    [0.001, 0.0009], [0.001, 0.0010], [0.001, 0.0011],
                ]},
                "confidence": 0.30,
                "distance": 88.0,
                "indices": [3, 11],
                "legs": [{"annotation": {"nodes": [900, 901, 902, 903]}}],
            }
        ],
    }
    window = {
        "code": "Ok",
        "tracepoints": [],
        "matchings": [
            {
                "geometry": {"type": "LineString", "coordinates": [
                    [0.0, 0.0003], [0.0, 0.0005], [0.0, 0.0007], [0.0, 0.0009], [0.0, 0.0011],
                ]},
                "confidence": 0.97,
                "distance": 88.0,
                "indices": [0, 1, 2, 3, 4],
                "legs": [{"annotation": {"nodes": [50, 51, 52, 53, 54]}}],
            }
        ],
    }

    def side_effect(url, **kwargs):
        num = len(url.split('/')[-1].split(';'))
        if num == n:
            return _response(initial)
        return _response(window)

    mock_get.side_effect = side_effect

    matcher = OSRMMapMatcher(window_context_points=2, min_confidence=0.75)
    result = matcher.match_shape(_df(n))

    assert result.success
    assert result.repair_count == 1
    assert len(result.segments) == 1
    assert result.segments[0].repaired is True
    assert list(result.segments[0].geometry.coords) == [
        (0.0, 0.0003), (0.0, 0.0005), (0.0, 0.0007), (0.0, 0.0009), (0.0, 0.0011),
    ]


@patch('src.map_matcher.requests.get')
def test_refine_keeps_low_confidence_segment_when_candidate_is_no_better(mock_get):
    n = 15
    initial = {
        "code": "Ok",
        "tracepoints": [{"matchings_index": 0} if 3 <= i <= 11 else None for i in range(n)],
        "matchings": [
            {
                "geometry": {"type": "LineString", "coordinates": [
                    [0.0, 0.0003], [0.0, 0.0004], [0.0, 0.0005], [0.0, 0.0006],
                ]},
                "confidence": 0.70,
                "distance": 44.0,
                "indices": [3, 6],
                "legs": [{"annotation": {"nodes": [60, 61, 62, 63]}}],
            }
        ],
    }
    # The window candidate is on a parallel road, so it must NOT replace the segment.
    window = {
        "code": "Ok",
        "tracepoints": [],
        "matchings": [
            {
                "geometry": {"type": "LineString", "coordinates": [
                    [0.001, 0.0003], [0.001, 0.0004], [0.001, 0.0005], [0.001, 0.0006],
                ]},
                "confidence": 0.95,
                "distance": 44.0,
                "indices": [0, 1, 2, 3],
                "legs": [{"annotation": {"nodes": [800, 801, 802, 803]}}],
            }
        ],
    }

    def side_effect(url, **kwargs):
        num = len(url.split('/')[-1].split(';'))
        if num == n:
            return _response(initial)
        return _response(window)

    mock_get.side_effect = side_effect

    matcher = OSRMMapMatcher(window_context_points=2, min_confidence=0.75)
    result = matcher.match_shape(_df(n))

    assert result.success
    assert result.repair_count == 0
    assert len(result.segments) == 1
    assert result.segments[0].repaired is False
