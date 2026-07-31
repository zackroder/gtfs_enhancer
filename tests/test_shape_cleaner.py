import pandas as pd
from shapely.geometry import LineString
from src.shape_cleaner import ShapeCleaner

def test_clean_shape_no_op_on_straight_line():
    cleaner = ShapeCleaner()
    geom = LineString([(0, 0), (1, 1), (2, 2)])
    cleaned = cleaner.clean_shape(geom)
    assert list(cleaned.coords) == list(geom.coords)

def test_clean_shape_removes_immediate_out_and_back():
    cleaner = ShapeCleaner()
    geom = LineString([(0, 0), (1, 1), (0, 0), (2, 2)])
    cleaned = cleaner.clean_shape(geom)
    assert list(cleaned.coords) == [(0, 0), (2, 2)]

def _df(lons, lats):
    return pd.DataFrame({
        'shape_pt_lon': lons,
        'shape_pt_lat': lats,
        'shape_pt_sequence': list(range(1, len(lons) + 1)),
    })

def test_detect_out_and_back_stub():
    cleaner = ShapeCleaner()
    # A -> B -> C(tip) -> B' -> A' -> D : a 20m spur out and back along a straight corridor.
    # ~1e-5 degrees longitude ~= 1m. Corridor along y axis (lat), spur along x axis (lon).
    lons = [0.0, 0.0, 0.00020, 0.0, 0.0, 0.0]
    lats = [0.0, 0.0001, 0.0001, 0.0001, 0.0002, 0.0003]
    df = _df(lons, lats)

    _, info = cleaner.find_out_and_back_stubs(df, max_stub_meters=75.0)
    assert len(info) >= 1
    span = info[0]
    assert span["excursion_meters"] > 15.0

def test_filter_out_and_back_stub_removes_spur():
    cleaner = ShapeCleaner()
    lons = [0.0, 0.0, 0.00020, 0.0, 0.0, 0.0]
    lats = [0.0, 0.0001, 0.0001, 0.0001, 0.0002, 0.0003]
    df = _df(lons, lats)

    filtered, info = cleaner.filter_out_and_back_stubs(df, max_stub_meters=75.0)
    assert len(filtered) < len(df)
    assert len(info) >= 1

def test_detector_does_not_remove_legitimate_sharp_turn():
    # A single sharp 90-degree corner (A -> B -> C). Even though the local angle
    # is acute, there is no out-and-back reversal, so nothing should be removed.
    cleaner = ShapeCleaner()
    lons = [0.0, 0.0, 0.001]
    lats = [0.0, 0.001, 0.001]
    df = _df(lons, lats)

    _, info = cleaner.find_out_and_back_stubs(df, max_stub_meters=75.0)
    assert info == []

def test_detector_does_not_remove_terminal_loop():
    # A u-shaped terminal loop that returns to the same corridor but continues past
    # the junction. The excursion point projects onto the end of the chord, which
    # the detector treats as a wrap-around (not a spur).
    cleaner = ShapeCleaner()
    lons = [0.0, 0.0, 0.00030, 0.00030, 0.00060, 0.00060, 0.00090]
    lats = [0.0, 0.0001, 0.0001, 0.0002, 0.0002, 0.0003, 0.0003]
    df = _df(lons, lats)

    _, info = cleaner.find_out_and_back_stubs(df, max_stub_meters=75.0)
    assert info == []
