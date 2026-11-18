"""
Test Script for Geodetic Tool

Tests parsers and validators with sample data from the project.
"""
import sys
import unittest
import tempfile
import os
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers import create_parser, detect_file_format
from parsers.base_parser import FileFormat
from parsers.trimble_parser import TrimbleParser
from parsers.leica_parser import LeicaParser
from parsers.rez_parser import RezParser
from parsers.fa0_parser import FA0Parser
from validators import validate_line, LevelingValidator
from config.settings import is_benchmark, is_turning_point
from config.models import LevelingLine, StationSetup, LineStatus


def test_trimble_parser():
    """Test Trimble DAT parser with sample files."""
    print("\n" + "=" * 60)
    print("TESTING TRIMBLE DAT PARSER")
    print("=" * 60)
    
    # Test files from project
    test_files = [
        ('/mnt/project/KMA58_DAT.txt', 'valid', '5793MPI', '5792MPI'),
        ('/mnt/project/KMA59_DAT.txt', 'valid', '5792MPI', '5793MPI'),
        ('/mnt/project/KMA57_DAT.txt', 'bad_naming', '640B', '5793MPI'),
        ('/mnt/project/KMA60_DAT.txt', 'bad_naming', '5793MPI', '740B'),
        ('/mnt/project/KMA186_DAT.txt', 'no_endpoint', '5812MPI', '5'),  # Ends on turning point
    ]
    
    parser = TrimbleParser()
    
    for filepath, expected_status, expected_start, expected_end in test_files:
        path = Path(filepath)
        if not path.exists():
            print(f"\n⚠ File not found: {filepath}")
            continue
        
        print(f"\n--- {path.name} (expected: {expected_status}) ---")
        
        try:
            line = parser.parse(filepath)
            
            print(f"  Start Point: {line.start_point}")
            print(f"  End Point: {line.end_point}")
            print(f"  Method: {line.method}")
            print(f"  Setups: {line.num_setups}")
            print(f"  Total Distance: {line.total_distance:.2f} m")
            print(f"  Height Diff: {line.total_height_diff:.5f} m")
            print(f"  Status: {line.status.value}")
            
            # Validate
            result = validate_line(line)
            
            # Check expected start/end
            start_ok = line.start_point == expected_start
            end_ok = line.end_point == expected_end
            
            print(f"\n  Start point match: {'✓' if start_ok else '✗'} (expected {expected_start}, got {line.start_point})")
            print(f"  End point match: {'✓' if end_ok else '✗'} (expected {expected_end}, got {line.end_point})")
            
            # Check endpoint validation
            if expected_status == 'no_endpoint':
                endpoint_detected = not result.endpoint_valid
                print(f"  Endpoint issue detected: {'✓' if endpoint_detected else '✗'}")
            
            if result.errors:
                print(f"  Errors: {result.errors}")
            if result.warnings:
                print(f"  Warnings: {result.warnings}")
            
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()


def test_leica_parser():
    """Test Leica GSI parser with sample files."""
    print("\n" + "=" * 60)
    print("TESTING LEICA GSI PARSER")
    print("=" * 60)
    
    test_files = [
        ('/mnt/project/3747MPI-5469MPI_raw.txt', '3747MPI', '5469MPI'),
        ('/mnt/project/5475MPI-3755MPI_raw.txt', '5475MPI', '3755MPI'),
    ]
    
    parser = LeicaParser()
    
    for filepath, expected_start, expected_end in test_files:
        path = Path(filepath)
        if not path.exists():
            print(f"\n⚠ File not found: {filepath}")
            continue
        
        print(f"\n--- {path.name} ---")
        
        try:
            line = parser.parse(filepath)
            
            print(f"  Start Point: {line.start_point}")
            print(f"  End Point: {line.end_point}")
            print(f"  Setups: {line.num_setups}")
            print(f"  Total Distance: {line.total_distance:.2f} m")
            print(f"  Height Diff: {line.total_height_diff:.5f} m")
            print(f"  Status: {line.status.value}")
            
            # Check filename extraction worked
            print(f"\n  Expected start: {expected_start}, got: {line.start_point}")
            print(f"  Expected end: {expected_end}, got: {line.end_point}")
            
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()


def test_format_detection():
    """Test automatic format detection."""
    print("\n" + "=" * 60)
    print("TESTING FORMAT DETECTION")
    print("=" * 60)
    
    test_files = [
        ('/mnt/project/KMA58_DAT.txt', FileFormat.TRIMBLE_DAT),
        ('/mnt/project/3747MPI-5469MPI_raw.txt', FileFormat.LEICA_GSI),
    ]
    
    for filepath, expected_format in test_files:
        path = Path(filepath)
        if not path.exists():
            print(f"\n⚠ File not found: {filepath}")
            continue
        
        detected = detect_file_format(filepath)
        match = '✓' if detected == expected_format else '✗'
        print(f"  {path.name}: {match} (expected {expected_format.value}, got {detected.value})")


def test_benchmark_detection():
    """Test benchmark vs turning point detection."""
    print("\n" + "=" * 60)
    print("TESTING BENCHMARK DETECTION")
    print("=" * 60)
    
    test_cases = [
        ('5793MPI', True, False),   # Benchmark
        ('640B', True, False),      # Benchmark
        ('609U', True, False),      # Benchmark
        ('5', False, True),         # Turning point
        ('11', False, True),        # Turning point
        ('25', False, True),        # Turning point
        ('M91', True, False),       # Special benchmark
    ]
    
    for point_id, exp_benchmark, exp_turning in test_cases:
        is_bm = is_benchmark(point_id)
        is_tp = is_turning_point(point_id)
        
        bm_match = '✓' if is_bm == exp_benchmark else '✗'
        tp_match = '✓' if is_tp == exp_turning else '✗'
        
        print(f"  {point_id:12} -> Benchmark: {bm_match} ({is_bm}), Turning Point: {tp_match} ({is_tp})")


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("GEODETIC TOOL TEST SUITE")
    print("=" * 60)

    test_benchmark_detection()
    test_format_detection()
    test_trimble_parser()
    test_leica_parser()

    print("\n" + "=" * 60)
    print("TEST SUITE COMPLETE")
    print("=" * 60 + "\n")


# ============================================================================
# UNITTEST TEST CLASSES FOR REZ AND FA0 PARSERS
# ============================================================================

class TestRezParser(unittest.TestCase):
    """Test suite for RezParser."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_data_dir = Path(__file__).parent / "data"
        self.parser = RezParser()

    def test_parse_valid_rez_file(self):
        """Parse sample .rez file and verify structure."""
        test_file = self.test_data_dir / "sample.rez"
        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        line = self.parser.parse(str(test_file))

        # Verify LevelingLine is returned
        self.assertIsNotNone(line)
        self.assertIsInstance(line, LevelingLine)

        # Verify basic structure
        self.assertEqual(line.start_point, "5793MPI")
        self.assertEqual(line.end_point, "5790MPI")
        self.assertEqual(line.num_setups, 1)

        # Verify synthetic setup exists
        self.assertEqual(len(line.setups), 1)
        setup = line.setups[0]
        self.assertEqual(setup.setup_number, 1)
        self.assertEqual(setup.from_point, "5793MPI")
        self.assertEqual(setup.to_point, "5790MPI")

    def test_parse_rez_with_multiple_observations(self):
        """Verify aggregation of multiple observations."""
        test_file = self.test_data_dir / "sample.rez"
        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        line = self.parser.parse(str(test_file))

        # Sum should aggregate all observations:
        # dH: 0.12345 + (-0.08765) + 0.05432 = 0.09012 m
        # distance: 100.00 + 85.50 + 90.25 = 275.75 m
        expected_total_dh = 0.12345 - 0.08765 + 0.05432
        expected_total_dist = 100.00 + 85.50 + 90.25

        self.assertAlmostEqual(
            line.total_height_diff,
            expected_total_dh,
            places=4,
            msg="Total height difference should be sum of observations",
        )
        self.assertAlmostEqual(
            line.total_distance,
            expected_total_dist,
            places=2,
            msg="Total distance should be sum of observations",
        )

    def test_parse_rez_missing_file(self):
        """Verify FileNotFoundError raised for missing file."""
        with self.assertRaises(FileNotFoundError):
            self.parser.parse("/nonexistent/path/missing.rez")

    def test_parse_rez_empty_file(self):
        """Verify ValueError for empty file."""
        test_file = self.test_data_dir / "empty.rez"
        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        with self.assertRaises(ValueError) as ctx:
            self.parser.parse(str(test_file))

        self.assertIn("Empty or unreadable", str(ctx.exception))

    def test_rez_synthetic_setup_fields(self):
        """Verify all StationSetup fields are non-None."""
        test_file = self.test_data_dir / "sample.rez"
        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        line = self.parser.parse(str(test_file))
        setup = line.setups[0]

        # All critical fields must be non-None
        self.assertIsNotNone(setup.setup_number)
        self.assertIsNotNone(setup.from_point)
        self.assertIsNotNone(setup.to_point)
        self.assertIsNotNone(setup.backsight_reading)
        self.assertIsNotNone(setup.foresight_reading)
        self.assertIsNotNone(setup.distance_back)
        self.assertIsNotNone(setup.distance_fore)
        self.assertIsNotNone(setup.height_diff)

    def test_rez_total_calculations(self):
        """Verify total_distance and total_height_diff calculations."""
        test_file = self.test_data_dir / "sample.rez"
        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        line = self.parser.parse(str(test_file))

        # Synthetic setup has distance_back and distance_fore
        setup = line.setups[0]
        setup_total_distance = setup.distance_back + setup.distance_fore

        # Line's total_distance should equal synthetic setup's total
        self.assertAlmostEqual(
            line.total_distance,
            setup_total_distance,
            places=2,
            msg="Line total distance should match synthetic setup distance",
        )

        # Height diff should come from setup
        self.assertAlmostEqual(
            line.total_height_diff,
            setup.height_diff,
            places=4,
            msg="Line total height diff should match setup height diff",
        )


class TestFA0Parser(unittest.TestCase):
    """Test suite for FA0Parser."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_data_dir = Path(__file__).parent / "data"
        self.parser = FA0Parser()

    def test_parse_valid_fa0_file(self):
        """Parse sample .FA0 file and verify structure."""
        test_file = self.test_data_dir / "sample.FA0"
        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        line = self.parser.parse(str(test_file))

        # Verify LevelingLine is returned
        self.assertIsNotNone(line)
        self.assertIsInstance(line, LevelingLine)

        # Verify basic structure
        self.assertEqual(line.start_point, "5793MPI")
        self.assertEqual(line.end_point, "5790MPI")
        self.assertEqual(line.num_setups, 1)

        # Verify synthetic setup exists
        self.assertEqual(len(line.setups), 1)
        setup = line.setups[0]
        self.assertEqual(setup.setup_number, 1)
        self.assertEqual(setup.from_point, "5793MPI")
        self.assertEqual(setup.to_point, "5790MPI")

    def test_parse_fa0_fixed_width(self):
        """Verify fixed-width column parsing."""
        test_file = self.test_data_dir / "sample.FA0"
        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        line = self.parser.parse(str(test_file))

        # Points should be extracted correctly from fixed-width columns
        self.assertEqual(line.start_point, "5793MPI")
        self.assertEqual(line.end_point, "5790MPI")

        # Height differences and distances should be parsed
        self.assertGreater(line.total_distance, 0)

    def test_parse_fa0_missing_file(self):
        """Verify FileNotFoundError raised for missing file."""
        with self.assertRaises(FileNotFoundError):
            self.parser.parse("/nonexistent/path/missing.FA0")

    def test_parse_fa0_empty_file(self):
        """Verify ValueError for empty file."""
        test_file = self.test_data_dir / "empty.FA0"
        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        with self.assertRaises(ValueError):
            self.parser.parse(str(test_file))

    def test_fa0_synthetic_setup_fields(self):
        """Verify all StationSetup fields are non-None."""
        test_file = self.test_data_dir / "sample.FA0"
        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        line = self.parser.parse(str(test_file))
        setup = line.setups[0]

        # All critical fields must be non-None
        self.assertIsNotNone(setup.setup_number)
        self.assertIsNotNone(setup.from_point)
        self.assertIsNotNone(setup.to_point)
        self.assertIsNotNone(setup.backsight_reading)
        self.assertIsNotNone(setup.foresight_reading)
        self.assertIsNotNone(setup.distance_back)
        self.assertIsNotNone(setup.distance_fore)
        self.assertIsNotNone(setup.height_diff)

    def test_fa0_total_calculations(self):
        """Verify aggregation and total calculations."""
        test_file = self.test_data_dir / "sample.FA0"
        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        line = self.parser.parse(str(test_file))

        # Sum of observations
        expected_dh = 0.12345 - 0.08765 + 0.05432
        expected_dist = 100.0 + 85.5 + 90.25

        self.assertAlmostEqual(
            line.total_height_diff, expected_dh, places=4,
            msg="FA0 total height diff should match observation sum"
        )
        self.assertAlmostEqual(
            line.total_distance, expected_dist, places=2,
            msg="FA0 total distance should match observation sum"
        )


class TestRezToggleDirection(unittest.TestCase):
    """Test direction toggle for REZ parsed lines."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_data_dir = Path(__file__).parent / "data"
        self.parser = RezParser()

    def test_rez_toggle_direction(self):
        """Test that toggle_direction inverts height difference."""
        test_file = self.test_data_dir / "sample.rez"
        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        line = self.parser.parse(str(test_file))

        original_dh = line.total_height_diff
        original_start = line.start_point
        original_end = line.end_point

        # Toggle direction
        line.toggle_direction()

        # Height diff should be inverted
        self.assertAlmostEqual(
            line.total_height_diff,
            -original_dh,
            places=4,
            msg="Height difference should invert on toggle",
        )

        # Start and end should swap
        self.assertEqual(line.start_point, original_end)
        self.assertEqual(line.end_point, original_start)

        # Toggle back
        line.toggle_direction()

        # Should return to original
        self.assertAlmostEqual(line.total_height_diff, original_dh, places=4)
        self.assertEqual(line.start_point, original_start)
        self.assertEqual(line.end_point, original_end)


class TestFA0ToggleDirection(unittest.TestCase):
    """Test direction toggle for FA0 parsed lines."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_data_dir = Path(__file__).parent / "data"
        self.parser = FA0Parser()

    def test_fa0_toggle_direction(self):
        """Test that toggle_direction inverts height difference."""
        test_file = self.test_data_dir / "sample.FA0"
        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        line = self.parser.parse(str(test_file))

        original_dh = line.total_height_diff
        original_start = line.start_point
        original_end = line.end_point

        # Toggle direction
        line.toggle_direction()

        # Height diff should be inverted
        self.assertAlmostEqual(
            line.total_height_diff,
            -original_dh,
            places=4,
            msg="Height difference should invert on toggle",
        )

        # Start and end should swap
        self.assertEqual(line.start_point, original_end)
        self.assertEqual(line.end_point, original_start)

        # Toggle back
        line.toggle_direction()

        # Should return to original
        self.assertAlmostEqual(line.total_height_diff, original_dh, places=4)
        self.assertEqual(line.start_point, original_start)
        self.assertEqual(line.end_point, original_end)


if __name__ == '__main__':
    # Run unittest tests
    unittest.main(exit=False, verbosity=2)

    # Also run legacy test functions
    main()
