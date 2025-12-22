"""
Geodetic Tool - Main Entry Point

Command-line interface for the geodetic automation tool.
"""
import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers import create_parser, detect_file_format
from parsers.base_parser import FileFormat
from validators import validate_line, LevelingValidator, BatchValidator
from engine import (
    calculate_line_totals,
    create_measurement_summary,
    LeastSquaresAdjuster
)
from exporters import export_fa0, export_fa1, export_fteg, export_rez
from config.models import LevelingLine, Benchmark, MeasurementSummary
from config.settings import get_settings, is_benchmark


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def parse_files(filepaths: List[str]) -> List[LevelingLine]:
    """
    Parse multiple geodetic data files.
    
    Args:
        filepaths: List of file paths to parse
        
    Returns:
        List of LevelingLine objects
    """
    lines = []
    
    for fp in filepaths:
        path = Path(fp)
        if not path.exists():
            logger.warning(f"File not found: {fp}")
            continue
        
        # Detect format and create parser
        parser = create_parser(fp)
        if parser is None:
            logger.warning(f"Unknown format: {fp}")
            continue
        
        try:
            line = parser.parse(fp)
            lines.append(line)
            logger.info(
                f"Parsed {path.name}: {line.start_point} → {line.end_point}, "
                f"{line.num_setups} setups, {line.total_distance:.2f}m"
            )
        except Exception as e:
            logger.error(f"Failed to parse {fp}: {e}")
    
    return lines


def validate_files(lines: List[LevelingLine]) -> dict:
    """
    Validate parsed leveling lines.
    
    Args:
        lines: List of LevelingLine objects
        
    Returns:
        Validation summary dictionary
    """
    validator = BatchValidator()
    results = validator.validate_batch(lines)
    summary = validator.get_summary(results)
    
    # Log issues
    for line, result in zip(lines, results):
        if not result.is_valid:
            for error in result.errors:
                logger.error(f"{line.filename}: {error}")
        for warning in result.warnings:
            logger.warning(f"{line.filename}: {warning}")
    
    return summary


def print_summary(lines: List[LevelingLine]):
    """Print summary of parsed lines."""
    print("\n" + "=" * 80)
    print("LEVELING LINES SUMMARY")
    print("=" * 80)
    
    print(f"\n{'Filename':<25}{'Start':<12}{'End':<12}{'Setups':>8}{'Distance':>12}{'dH':>12}{'Status':<15}")
    print("-" * 90)
    
    for line in lines:
        status_icon = "✓" if line.status.value == "valid" else "✗"
        print(
            f"{line.filename:<25}"
            f"{line.start_point:<12}"
            f"{line.end_point:<12}"
            f"{line.num_setups:>8}"
            f"{line.total_distance:>12.2f}"
            f"{line.total_height_diff:>12.5f}"
            f"  {status_icon} {line.status.value}"
        )
    
    print("-" * 90)
    print(f"Total files: {len(lines)}")
    valid = sum(1 for l in lines if l.status.value == "valid")
    print(f"Valid: {valid}, Invalid: {len(lines) - valid}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Geodetic Leveling Automation Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Parse and validate files
  python main.py parse file1.DAT file2.raw
  
  # Export to FA0 format
  python main.py export --format fa0 --output results.fa0 file1.DAT file2.DAT
  
  # Validate files
  python main.py validate *.DAT *.raw
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to run')
    
    # Parse command
    parse_parser = subparsers.add_parser('parse', help='Parse geodetic data files')
    parse_parser.add_argument('files', nargs='+', help='Files to parse')
    parse_parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    
    # Validate command
    validate_parser = subparsers.add_parser('validate', help='Validate parsed files')
    validate_parser.add_argument('files', nargs='+', help='Files to validate')
    
    # Export command
    export_parser = subparsers.add_parser('export', help='Export to output format')
    export_parser.add_argument('files', nargs='+', help='Files to process')
    export_parser.add_argument(
        '-f', '--format', 
        choices=['fa0', 'fa1', 'fteg', 'rez', 'all'],
        default='rez',
        help='Output format'
    )
    export_parser.add_argument('-o', '--output', help='Output file path')
    export_parser.add_argument('-p', '--project', default='project', help='Project name')
    
    # Info command
    info_parser = subparsers.add_parser('info', help='Show file information')
    info_parser.add_argument('file', help='File to inspect')
    
    # GeoJSON export command
    geojson_parser = subparsers.add_parser('geojson', help='Export to GeoJSON for GIS')
    geojson_parser.add_argument('files', nargs='+', help='Files to process')
    geojson_parser.add_argument('-o', '--output', default='./output', help='Output folder')
    geojson_parser.add_argument('-p', '--project', default='leveling_network', help='Project name')
    
    args = parser.parse_args()
    
    if args.command is None:
        parser.print_help()
        return 1
    
    if args.command == 'parse':
        lines = parse_files(args.files)
        validate_files(lines)
        print_summary(lines)
        return 0
    
    elif args.command == 'validate':
        lines = parse_files(args.files)
        summary = validate_files(lines)
        print_summary(lines)
        print(f"\nValidation Summary:")
        print(f"  Pass rate: {summary['pass_rate']*100:.1f}%")
        print(f"  Endpoint issues: {summary['endpoint_issues']}")
        print(f"  Naming issues: {summary['naming_issues']}")
        return 0 if summary['invalid'] == 0 else 1
    
    elif args.command == 'export':
        lines = parse_files(args.files)
        
        if not lines:
            logger.error("No valid files to export")
            return 1
        
        # Create observations from lines
        observations = [
            create_measurement_summary(line, bf_diff_mm=0, year_month="0000")
            for line in lines
        ]
        
        # Output path
        output = args.output or f"{args.project}.{args.format}"
        
        if args.format in ['rez', 'all']:
            out_path = output if args.format == 'rez' else f"{args.project}.rez"
            export_rez(out_path, lines, args.project)
            logger.info(f"Exported REZ to {out_path}")
        
        if args.format in ['fteg', 'all']:
            out_path = output if args.format == 'fteg' else f"{args.project}.fteg"
            export_fteg(out_path, observations)
            logger.info(f"Exported FTEG to {out_path}")
        
        if args.format in ['fa0', 'all']:
            out_path = output if args.format == 'fa0' else f"{args.project}.fa0"
            # Need benchmarks - extract from lines
            benchmarks = []
            for line in lines:
                if is_benchmark(line.start_point):
                    benchmarks.append(Benchmark(line.start_point, 0.0))
                if is_benchmark(line.end_point):
                    benchmarks.append(Benchmark(line.end_point, 0.0))
            # Remove duplicates
            seen = set()
            unique_benchmarks = []
            for bm in benchmarks:
                if bm.point_id not in seen:
                    seen.add(bm.point_id)
                    unique_benchmarks.append(bm)
            
            export_fa0(out_path, unique_benchmarks, observations, args.project)
            logger.info(f"Exported FA0 to {out_path}")
        
        return 0
    
    elif args.command == 'info':
        path = Path(args.file)
        if not path.exists():
            logger.error(f"File not found: {args.file}")
            return 1
        
        file_format = detect_file_format(args.file)
        print(f"\nFile: {path.name}")
        print(f"Format: {file_format.value}")
        print(f"Size: {path.stat().st_size} bytes")
        
        parser = create_parser(args.file)
        if parser:
            line = parser.parse(args.file)
            print(f"\nStart Point: {line.start_point}")
            print(f"End Point: {line.end_point}")
            print(f"Method: {line.method}")
            print(f"Setups: {line.num_setups}")
            print(f"Total Distance: {line.total_distance:.2f} m")
            print(f"Height Difference: {line.total_height_diff:.5f} m")
            print(f"Status: {line.status.value}")
            
            if line.validation_errors:
                print("\nValidation Errors:")
                for err in line.validation_errors:
                    print(f"  ✗ {err}")
        
        return 0
    
    elif args.command == 'geojson':
        from gis.geojson_export import export_network_to_geojson
        
        lines = parse_files(args.files)
        
        if not lines:
            logger.error("No valid files to export")
            return 1
        
        output_files = export_network_to_geojson(
            lines,
            args.output,
            args.project
        )
        
        print(f"\nExported GeoJSON files:")
        for key, path in output_files.items():
            print(f"  {key}: {path}")
        
        return 0
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
