import pandas as pd
import zipfile
import os

def parse_shapes(gtfs_path: str) -> dict[str, pd.DataFrame]:
    """
    Parses shapes.txt from a GTFS zip file or directory.
    
    Args:
        gtfs_path: Path to a GTFS .zip file or an extracted GTFS directory.
        
    Returns:
        A dictionary mapping shape_id to a pandas DataFrame containing the shape points,
        sorted by shape_pt_sequence.
    """
    if os.path.isdir(gtfs_path):
        shapes_file = os.path.join(gtfs_path, 'shapes.txt')
        if not os.path.exists(shapes_file):
            raise FileNotFoundError(f"shapes.txt not found in {gtfs_path}")
        df = pd.read_csv(shapes_file)
    elif zipfile.is_zipfile(gtfs_path):
        with zipfile.ZipFile(gtfs_path) as zf:
            if 'shapes.txt' not in zf.namelist():
                raise FileNotFoundError(f"shapes.txt not found in {gtfs_path}")
            with zf.open('shapes.txt') as f:
                df = pd.read_csv(f)
    else:
        raise ValueError(f"{gtfs_path} is neither a directory nor a valid zip file")
        
    # Ensure it's sorted by shape_pt_sequence
    df = df.sort_values(by=['shape_id', 'shape_pt_sequence'])
    
    # Group by shape_id
    shapes = {}
    for shape_id, group in df.groupby('shape_id'):
        shapes[str(shape_id)] = group.reset_index(drop=True)
        
    return shapes
