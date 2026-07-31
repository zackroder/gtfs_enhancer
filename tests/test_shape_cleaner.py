import pandas as pd
from src.shape_cleaner import simplify_coords, remove_stop_excursions, ShapeCleaner


def test_simplify_collapses_collinear_points():
    coords = [(0.0, 0.0 + i * 0.0001) for i in range(20)]
    simplified = simplify_coords(coords, tolerance_meters=15.0)
    assert simplified[0] == coords[0]
    assert simplified[-1] == coords[-1]
    assert len(simplified) < len(coords)


def test_simplify_preserves_sharp_turn():
    coords = [(0.0, 0.0), (0.0, 0.001), (0.001, 0.001)]
    simplified = simplify_coords(coords, tolerance_meters=15.0)
    assert len(simplified) == 3


def test_simplify_zero_tolerance_keeps_points():
    coords = [(0.0, 0.0 + i * 0.0001) for i in range(20)]
    simplified = simplify_coords(coords, tolerance_meters=0.0)
    assert len(simplified) == len(coords)


def _stop(lon, lat, sid="s1"):
    return {"stop_id": sid, "stop_name": "", "stop_sequence": 1, "lat": lat, "lon": lon}


def test_remove_stop_excursion_removes_exact_return_poke():
    # A(0,0) -> B(0.0003,0) -> A'(0,0): poke out ~33m and return to the same point.
    # A stop sits at the poke tip.
    coords = [(0.0, 0.0), (0.0003, 0.0), (0.0, 0.0)]
    stops = [_stop(0.0003, 0.0)]
    cleaned, removed = remove_stop_excursions(coords, stops)
    assert len(removed) == 1
    assert list(cleaned) == [(0.0, 0.0)]


def test_remove_stop_excursion_removes_returning_triangle():
    # Corridor runs north; the shape pokes east to a stop and returns 16.7m along.
    # Corridor heading before and after the poke matches, so B is removed.
    coords = [(0.0, -0.0001), (0.0, 0.0), (0.0003, 0.0001), (0.0, 0.00015), (0.0, 0.00025)]
    stops = [_stop(0.0003, 0.0001)]
    cleaned, removed = remove_stop_excursions(coords, stops)
    assert len(removed) == 1
    assert list(cleaned) == [(0.0, -0.0001), (0.0, 0.0), (0.0, 0.00015), (0.0, 0.00025)]


def test_remove_stop_excursion_requires_stop_near_tip():
    # Same poke geometry, but no stop nearby -> nothing removed.
    coords = [(0.0, 0.0), (0.0003, 0.0), (0.0, 0.0)]
    stops = [_stop(0.0010, 0.0010)]
    cleaned, removed = remove_stop_excursions(coords, stops)
    assert removed == []
    assert list(cleaned) == coords


def test_remove_stop_excursion_preserves_90_degree_turn_near_stop():
    # A 90-degree corner with a stop at the corner must not be removed:
    # deviation < return chord (it does not return to the corridor).
    coords = [(0.0, -0.0001), (0.0, 0.0), (0.0, 0.0002), (0.0002, 0.0002), (0.0004, 0.0002)]
    stops = [_stop(0.0, 0.0)]
    cleaned, removed = remove_stop_excursions(coords, stops)
    assert removed == []
    assert list(cleaned) == coords


def test_remove_stop_excursion_preserves_corridor_points_near_stops():
    # A long straight corridor that passes near stops: no pokes, nothing removed.
    coords = [(0.0, 0.0 + i * 0.0003) for i in range(5)]
    stops = [_stop(0.0001, 0.0003), _stop(0.0001, 0.0009)]
    cleaned, removed = remove_stop_excursions(coords, stops)
    assert removed == []
    assert len(cleaned) == len(coords)


def _df(lons, lats):
    return pd.DataFrame({
        'shape_pt_lon': lons,
        'shape_pt_lat': lats,
        'shape_pt_sequence': list(range(1, len(lons) + 1)),
    })


def test_preprocess_shape_returns_all_stages():
    df = _df([0.0, 0.0, 0.0003, 0.0, 0.0], [0.0, 0.0001, 0.0001, 0.0001, 0.0002])
    stops = [_stop(0.0003, 0.0001)]
    stages = ShapeCleaner().preprocess_shape(df, stops=stops)

    assert set(['original', 'simplified', 'stop_removed', 'final', 'removed_stops']) <= set(stages)
    assert stages['original'][0] == (0.0, 0.0)
    assert len(stages['simplified']) <= len(stages['original'])
    assert len(stages['stop_removed']) <= len(stages['simplified'])
    assert len(stages['final']) >= 2


def test_preprocess_shape_without_stops_is_noop_for_excursions():
    df = _df([0.0, 0.0, 0.0003, 0.0, 0.0], [0.0, 0.0001, 0.0001, 0.0001, 0.0002])
    stages = ShapeCleaner().preprocess_shape(df, stops=[])
    assert stages["removed_stops"] == []
    assert list(stages["stop_removed"]) == list(stages["simplified"])
