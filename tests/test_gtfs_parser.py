import pytest
import pandas as pd
import tempfile
import os
import zipfile
import shutil
from src.gtfs_parser import parse_shapes, parse_stop_usage

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
    shutil.rmtree(temp_dir)


@pytest.fixture
def sample_gtfs_with_stops():
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
    stops_csv = """stop_id,stop_name,stop_lat,stop_lon
s_1,First Stop,37.7749,-122.4194
s_2,Second Stop,37.7750,-122.4190
s_3,Other Stop,37.7849,-122.4094
s_4,Tram Stop,37.7949,-122.3994
"""
    stop_times_csv = """trip_id,arrival_time,departure_time,stop_id,stop_sequence
trip_1,08:00:00,08:00:00,s_1,1
trip_1,08:05:00,08:05:00,s_2,2
trip_2,09:00:00,09:00:00,s_3,1
trip_2,09:01:00,09:01:00,s_3,2
trip_3,10:00:00,10:00:00,s_4,1
trip_3,10:01:00,10:01:00,s_4,2
"""

    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr('shapes.txt', shapes_csv)
        zf.writestr('routes.txt', routes_csv)
        zf.writestr('trips.txt', trips_csv)
        zf.writestr('stops.txt', stops_csv)
        zf.writestr('stop_times.txt', stop_times_csv)

    yield zip_path

    os.remove(zip_path)
    os.rmdir(temp_dir)


def test_parse_stop_usage_groups_stops_by_shape(sample_gtfs_with_stops):
    usage = parse_stop_usage(sample_gtfs_with_stops)

    assert 'shape_1' in usage
    assert 'shape_2' in usage
    # Tram shape excluded
    assert 'shape_3' not in usage

    stops_1 = usage['shape_1']
    assert [s['stop_id'] for s in stops_1] == ['s_1', 's_2']
    assert stops_1[0]['is_first_stop'] is True
    assert stops_1[1]['is_last_stop'] is True
    assert stops_1[0]['lat'] == 37.7749

    # trip_2 has the same stop for both sequences: deduplicated to one record
    stops_2 = usage['shape_2']
    assert [s['stop_id'] for s in stops_2] == ['s_3']
    assert stops_2[0]['is_first_stop'] is True
    assert stops_2[0]['is_last_stop'] is True


def test_parse_stop_usage_respects_limit_routes(sample_gtfs_with_stops):
    usage = parse_stop_usage(sample_gtfs_with_stops, limit_routes=["Bus 10"])
    assert 'shape_1' in usage
    assert 'shape_2' in usage
    assert 'shape_3' not in usage


def test_parse_stop_usage_missing_stop_times_returns_empty(sample_gtfs_zip):
    # sample_gtfs_zip has no stops.txt / stop_times.txt
    usage = parse_stop_usage(sample_gtfs_zip)
    assert usage == {}


def test_parse_stop_usage_handles_directory(sample_gtfs_with_stops):
    temp_dir = tempfile.mkdtemp()
    with zipfile.ZipFile(sample_gtfs_with_stops, 'r') as zf:
        zf.extractall(temp_dir)

    usage = parse_stop_usage(temp_dir)
    assert 'shape_1' in usage
    assert len(usage['shape_1']) == 2

    shutil.rmtree(temp_dir)
