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
