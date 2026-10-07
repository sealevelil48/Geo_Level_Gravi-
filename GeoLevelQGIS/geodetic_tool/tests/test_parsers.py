"""
Test Script for Geodetic Tool

Tests parsers and validators with sample data from the project.
"""
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers import create_parser, detect_file_format
from parsers.base_parser import FileFormat
from parsers.trimble_parser import TrimbleParser
from parsers.leica_parser import LeicaParser
from validators import validate_line, LevelingValidator
from config.settings import is_benchmark, is_turning_point


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


if __name__ == '__main__':
    main()
