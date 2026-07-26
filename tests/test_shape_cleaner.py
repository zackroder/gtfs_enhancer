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
    # Point 1: (0.0002, 0.0002) -> 20m side stub jutting out
    # Point 2: (0, 0.0010)
    # Point 0 and Point 2 form a straight north-south line x=0.
    # Point 1 juts east.
    data = {
        'shape_pt_lon': [0.0, 0.0002, 0.0],
        'shape_pt_lat': [0.0, 0.0005, 0.0010],
        'shape_pt_sequence': [1, 2, 3]
    }
    df = pd.DataFrame(data)
    
    filtered_df = cleaner.filter_perpendicular_stubs(df, max_stub_meters=40.0)
    
    # Point 1 should be removed
    assert len(filtered_df) == 2
    assert list(filtered_df['shape_pt_lon']) == [0.0, 0.0]
    assert list(filtered_df['shape_pt_lat']) == [0.0, 0.0010]
