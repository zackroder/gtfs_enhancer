import pytest
import pandas as pd
import tempfile
import os
import zipfile
from src.gtfs_parser import parse_shapes

@pytest.fixture
def sample_gtfs_zip():
    # Create a temporary zip file with a dummy shapes.txt
    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, 'gtfs.zip')
    
    shapes_csv = """shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence,shape_dist_traveled
shape_1,37.7749,-122.4194,1,0.0
shape_1,37.7750,-122.4190,2,0.1
shape_2,37.7849,-122.4094,1,0.0
shape_2,37.7850,-122.4090,2,0.1
"""
    
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr('shapes.txt', shapes_csv)
        
    yield zip_path
    
    # Cleanup
    os.remove(zip_path)
    os.rmdir(temp_dir)

def test_parse_shapes_returns_dict_of_dataframes(sample_gtfs_zip):
    shapes = parse_shapes(sample_gtfs_zip)
    
    assert isinstance(shapes, dict)
    assert 'shape_1' in shapes
    assert 'shape_2' in shapes
    
    df1 = shapes['shape_1']
    assert len(df1) == 2
    assert list(df1['shape_pt_lat']) == [37.7749, 37.7750]
    # Check that it's sorted by shape_pt_sequence
    assert list(df1['shape_pt_sequence']) == [1, 2]

def test_parse_shapes_handles_directory(sample_gtfs_zip):
    # Extract the zip to test directory reading
    temp_dir = tempfile.mkdtemp()
    with zipfile.ZipFile(sample_gtfs_zip, 'r') as zf:
        zf.extractall(temp_dir)
        
    shapes = parse_shapes(temp_dir)
    assert 'shape_1' in shapes
    
    # Cleanup
    os.remove(os.path.join(temp_dir, 'shapes.txt'))
    os.rmdir(temp_dir)
