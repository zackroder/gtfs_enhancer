import pandas as pd
from src.shape_cleaner import simplify_coords, remove_spikes, remove_corridor_detours, ShapeCleaner


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

    assert set(['original', 'simplified', 'spike_removed', 'detour_removed', 'final', 'spikes', 'detours']) <= set(stages)
    assert stages['original'][0] == (0.0, 0.0)
    assert stages['original'][-1] == (0.0, 0.0002)
    assert len(stages['simplified']) <= len(stages['original'])
    assert len(stages['spike_removed']) <= len(stages['simplified'])
    assert len(stages['detour_removed']) <= len(stages['spike_removed'])
    assert len(stages['final']) >= 2


def test_remove_corridor_detour_removes_stop_triangle():
    # Corridor runs north along lat (lon=0). The trace pokes east to a stop
    # (~18m) at the midpoint and rejoins the corridor 60m along.
    # A(0,0) -> B(0.00016,0.00027) -> C(0,0.00054)
    coords = [(0.0, 0.0), (0.00016, 0.00027), (0.0, 0.00054)]
    # Add corridor context on both sides so entry/exit headings are known
    coords = [(0.0, -0.0001)] + coords + [(0.0, 0.00064)]

    cleaned, removed = remove_corridor_detours(coords)
    assert len(removed) == 1
    assert removed[0]["removed_points"] == 1
    # The triangle interior (B) is gone; corridor endpoints remain
    assert list(cleaned) == [(0.0, -0.0001), (0.0, 0.0), (0.0, 0.00054), (0.0, 0.00064)]


def test_remove_corridor_detour_preserves_90_degree_turn():
    # A 90-degree corner must NOT be removed: the corridor heading changes.
    # Pre-turn corridor heads north, post-turn corridor heads east.
    coords = [
        (0.0, -0.0001),   # south of A, heading north
        (0.0, 0.0),       # A
        (0.0, 0.0002),    # B north
        (0.0002, 0.0002), # C east
        (0.0004, 0.0002), # D
        (0.0006, 0.0002), # east of D, heading east
    ]
    cleaned, removed = remove_corridor_detours(coords)
    assert removed == []
    assert list(cleaned) == coords


def test_remove_corridor_detour_preserves_large_loop():
    # A block loop (large detour ratio) must not be removed.
    coords = [
        (0.0, 0.0),
        (0.0002, 0.0),
        (0.0002, 0.0002),
        (0.0, 0.0002),
        (0.0, 0.0004),
    ]
    cleaned, removed = remove_corridor_detours(coords)
    assert removed == []
    assert list(cleaned) == coords


def test_remove_corridor_detour_requires_same_corridor():
    # An S-curve that rejoins at an angle should not be flagged.
    coords = [
        (0.0, -0.0001),
        (0.0, 0.0),
        (0.0002, 0.0001),
        (0.0002, 0.0003),
        (0.0004, 0.0004),
        (0.0006, 0.0004),
    ]
    cleaned, removed = remove_corridor_detours(coords)
    assert removed == []
