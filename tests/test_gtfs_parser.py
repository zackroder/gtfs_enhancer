import pytest
import pandas as pd
import tempfile
import os
import zipfile
from src.gtfs_parser import parse_shapes

@pytest.fixture
def sample_gtfs_zip():
    # Create a temporary zip file with dummy GTFS files
    temp_dir = tempfile.mkdtemp()
    zip_path = os.path.join(temp_dir, 'gtfs.zip')
    
    shapes_csv = """shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence,shape_dist_traveled
shape_1,37.7749,-122.4194,1,0.0
shape_1,37.7750,-122.4190,2,0.1
shape_2,37.7849,-122.4094,1,0.0
shape_2,37.7850,-122.4090,2,0.1
shape_3,37.7949,-122.3994,1,0.0
shape_3,37.7950,-122.3990,2,0.1
"""

    routes_csv = """route_id,route_short_name,route_type
route_1,Bus 10,3
route_2,Tram 20,0
"""

    trips_csv = """route_id,service_id,trip_id,shape_id
route_1,svc_1,trip_1,shape_1
route_1,svc_1,trip_2,shape_2
route_2,svc_1,trip_3,shape_3
"""
    
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr('shapes.txt', shapes_csv)
        zf.writestr('routes.txt', routes_csv)
        zf.writestr('trips.txt', trips_csv)
        
    yield zip_path
    
    # Cleanup
    os.remove(zip_path)
    os.rmdir(temp_dir)

def test_parse_shapes_returns_filtered_dict_and_mapping(sample_gtfs_zip):
    shapes, route_mapping = parse_shapes(sample_gtfs_zip)
    
    assert isinstance(shapes, dict)
    assert 'shape_1' in shapes
    assert 'shape_2' in shapes
    assert 'shape_3' not in shapes # Tram route shape should be filtered out
    
    # Check route mapping
    assert "Bus 10" in route_mapping
    assert set(route_mapping["Bus 10"]) == {'shape_1', 'shape_2'}
    
    df1 = shapes['shape_1']
    assert len(df1) == 2

def test_parse_shapes_handles_directory(sample_gtfs_zip):
    # Extract the zip to test directory reading
    temp_dir = tempfile.mkdtemp()
    with zipfile.ZipFile(sample_gtfs_zip, 'r') as zf:
        zf.extractall(temp_dir)
        
    shapes, route_mapping = parse_shapes(temp_dir)
    assert 'shape_1' in shapes
    
    # Cleanup
    import shutil
    shutil.rmtree(temp_dir)
