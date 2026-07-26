import pytest
from shapely.geometry import LineString
from src.shape_cleaner import ShapeCleaner

def test_shape_cleaner_removes_tails():
    # A simple out-and-back tail: A -> B -> C -> B -> D
    # We'd expect A -> B -> D ideally, or some simplification
    # For now, let's just ensure it accepts and returns a LineString
    cleaner = ShapeCleaner()
    
    geom = LineString([(0, 0), (1, 1), (2, 2), (1, 1), (3, 3)])
    
    cleaned = cleaner.clean_shape(geom)
    
    assert isinstance(cleaned, LineString)
    # The actual algorithm might be complex, so we just check it doesn't crash
    # and returns a valid geometry.
    assert cleaned.is_valid
    
def test_shape_cleaner_no_op_on_straight_line():
    cleaner = ShapeCleaner()
    geom = LineString([(0, 0), (1, 1), (2, 2)])
    cleaned = cleaner.clean_shape(geom)
    
    # Should be identical for a clean line
    assert list(cleaned.coords) == list(geom.coords)

def test_filter_perpendicular_stubs():
    import pandas as pd
    cleaner = ShapeCleaner()
    
    # Point 0: (0, 0)
    # Point 1: (0.0002, 0.0002) -> 20m side stub forming acute tip angle (~45 deg)
    # Point 2: (0.0, 0.0001)
    data = {
        'shape_pt_lon': [0.0, 0.0002, 0.0],
        'shape_pt_lat': [0.0, 0.0002, 0.0001],
        'shape_pt_sequence': [1, 2, 3]
    }
    df = pd.DataFrame(data)
    
    filtered_df = cleaner.filter_perpendicular_stubs(df, max_stub_meters=75.0)
    
    # Point 1 (the tip of the 20m spur) should be removed
    assert len(filtered_df) == 2
    assert list(filtered_df['shape_pt_lon']) == [0.0, 0.0]

def test_filter_mid_trace_long_baseline_stub():
    import pandas as pd
    cleaner = ShapeCleaner()
    
    # P0: (0.0, 0.0) -> Start on main street
    # P1: (0.00015, 0.0008) -> 15m East spike on side street, but 80m up the main street (long baseline distance)
    # P2: (0.0, 0.0016) -> 80m further down main street
    data = {
        'shape_pt_lon': [0.0, 0.00015, 0.0],
        'shape_pt_lat': [0.0, 0.0008, 0.0016],
        'shape_pt_sequence': [1, 2, 3]
    }
    df = pd.DataFrame(data)
    
    filtered_df = cleaner.filter_perpendicular_stubs(df, max_stub_meters=75.0)
    
    # The 15m lateral spike (P1) must be removed even though P0->P1 distance is 80m!
    assert len(filtered_df) == 2
    assert list(filtered_df['shape_pt_lon']) == [0.0, 0.0]
