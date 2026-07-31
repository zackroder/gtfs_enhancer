import pandas as pd
from src.shape_cleaner import simplify_coords, remove_spikes, ShapeCleaner


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
    # tolerance=0 disables simplification
    simplified = simplify_coords(coords, tolerance_meters=0.0)
    assert len(simplified) == len(coords)


def test_remove_spikes_removes_returning_poke_out():
    # A(0,0) -> B(0.0003,0) -> C(0,0.0001): B pokes ~33m out to a stop and returns.
    coords = [(0.0, 0.0), (0.0003, 0.0), (0.0, 0.0001)]
    cleaned, removed = remove_spikes(coords)
    assert len(removed) == 1
    assert list(cleaned) == [(0.0, 0.0), (0.0, 0.0001)]


def test_remove_spikes_preserves_turn():
    # A 90-degree turn must not be removed (it does not return to the corridor).
    coords = [(0.0, 0.0), (0.0002, 0.0), (0.0002, 0.0002)]
    cleaned, removed = remove_spikes(coords)
    assert removed == []
    assert list(cleaned) == coords


def test_remove_spikes_preserves_terminal_loop():
    # A u-shaped terminal loop returning but with a large chord (wrap-around).
    coords = [(0.0, 0.0), (0.0, 0.0001), (0.0003, 0.0001), (0.0003, 0.0002), (0.0, 0.0002), (0.0, 0.0003)]
    cleaned, removed = remove_spikes(coords)
    assert removed == []


def test_remove_spikes_requires_deviation_exceeding_return():
    # Small wiggle: deviation exists but is not a returning poke-out.
    coords = [(0.0, 0.0), (0.00005, 0.0), (0.0001, 0.0)]
    cleaned, removed = remove_spikes(coords)
    assert removed == []


def _df(lons, lats):
    return pd.DataFrame({
        'shape_pt_lon': lons,
        'shape_pt_lat': lats,
        'shape_pt_sequence': list(range(1, len(lons) + 1)),
    })


def test_preprocess_shape_returns_all_stages():
    df = _df([0.0, 0.0, 0.0003, 0.0, 0.0], [0.0, 0.0001, 0.0001, 0.0001, 0.0002])
    stages = ShapeCleaner().preprocess_shape(df)

    assert set(['original', 'simplified', 'spike_removed', 'final', 'spikes']) <= set(stages)
    assert stages['original'][0] == (0.0, 0.0)
    assert stages['original'][-1] == (0.0, 0.0002)
    assert len(stages['simplified']) <= len(stages['original'])
    assert len(stages['spike_removed']) <= len(stages['simplified'])
    assert len(stages['final']) >= 2
