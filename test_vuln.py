"""Test for the untrusted input vulnerability in rows_to_project."""
import sys
sys.path.insert(0, r"c:/Users/user01/Downloads/geodetic_tool_v1.1/GeoLevelQGIS")

from geo_level_gpkg import rows_to_project

# Simulate malicious line_rows with string total_distance and int start_point
line_rows = [
    {
        "line_idx": 0,
        "filename": "test.DAT",
        "start_point": 5,  # Should be string, but is int
        "end_point": "BM002",
        "method": "BF",
        "date": None,
        "instrument_id": None,
        "total_distance": "abc",  # Should be float, but is string
        "total_height_diff": 1.234,
        "misclosure": None,
        "status": "VALID",
        "validation_errors": "[]",
        "is_used": 1,
        "original_direction": "BF",
        "manager_override": 0,
        "validation": None,
        "source_path": None
    }
]

setup_rows = [
    {
        "line_idx": 0,
        "setup_number": 1,
        "from_point": "BM001",
        "to_point": "BM002",
        "backsight_reading": "NaN",  # Should be float, but is string
        "foresight_reading": 1.234,
        "distance_back": 50.0,
        "distance_fore": 50.0,
        "temperature": None,
        "height_diff": None,
        "cumulative_height": None,
        "is_used": 1
    }
]

# This should fail but doesn't
try:
    lines, vrs = rows_to_project(line_rows, setup_rows)
    print(f"Successfully created line with:")
    print(f"  start_point type: {type(lines[0].start_point)}, value: {lines[0].start_point}")
    print(f"  total_distance type: {type(lines[0].total_distance)}, value: {lines[0].total_distance}")
    print(f"  setup backsight_reading type: {type(lines[0].setups[0].backsight_reading)}, value: {lines[0].setups[0].backsight_reading}")
    
    # Now try to format it like populate_line_details does
    try:
        dist_str = "{:.2f}".format(lines[0].total_distance)
        print(f"Format total_distance succeeded: {dist_str}")
    except TypeError as e:
        print(f"ERROR: Format total_distance failed: {e}")
    
    try:
        point_str = "Line: " + (lines[0].start_point or "?") + " -> " + (lines[0].end_point or "?")
        print(f"String concat succeeded: {point_str}")
    except TypeError as e:
        print(f"ERROR: String concat failed: {e}")
        
    try:
        bs = "{:.5f}".format(lines[0].setups[0].backsight_reading)
        print(f"Format backsight succeeded: {bs}")
    except TypeError as e:
        print(f"ERROR: Format backsight failed: {e}")
        
except Exception as e:
    print(f"rows_to_project failed: {e}")
    import traceback
    traceback.print_exc()
