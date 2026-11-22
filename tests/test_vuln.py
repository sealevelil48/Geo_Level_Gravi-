"""Untrusted GeoPackage rows: malformed values must be dropped, not loaded."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "GeoLevelQGIS"))

from geo_level_gpkg import rows_to_project  # noqa: E402

line_rows = [{"line_idx": 0, "filename": "test.DAT", "start_point": 5,   # int, not str
              "end_point": "BM002", "total_distance": "abc", "total_height_diff": 1.234,
              "status": "VALID", "validation_errors": "[]", "is_used": 1}]
setup_rows = [{"line_idx": 0, "setup_number": 1, "from_point": "BM001", "to_point": "BM002",
               "backsight_reading": "NaN", "foresight_reading": 1.234,
               "distance_back": 50.0, "distance_fore": 50.0}]


def test_malformed_rows_are_dropped():
    lines, vrs = rows_to_project(line_rows, setup_rows)
    assert lines == [] and vrs is None


def test_bad_numbers_coerced():
    rows = [dict(line_rows[0], start_point="BM001")]
    lines, _ = rows_to_project(rows, setup_rows)
    assert len(lines) == 1
    assert lines[0].total_distance == 0.0          # "abc" -> 0.0
    assert lines[0].setups == []                     # setup with "NaN" reading dropped


if __name__ == "__main__":
    test_malformed_rows_are_dropped()
    test_bad_numbers_coerced()
    print("All checks passed")
