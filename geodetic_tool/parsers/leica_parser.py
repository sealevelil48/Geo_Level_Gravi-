"""
Leica GSI File Parser

Parses Leica digital level RAW/GSI files.

GSI Format:
    Each line contains multiple data blocks.
    Each block: WW.PP+DDDDDDDDDDDDDDDD
    Where:
        WW = Word Index (e.g., 11=Point ID, 32=Distance, 83=Height)
        PP = Precision/Info bits
        + or - = Sign
        D = Data (16 digits, right-justified)

Word Indices:
    11   = Point ID
    32   = Horizontal Distance (scaled by 1e-5 = meters)
    331  = Staff reading backsight Face 1
    332  = Staff reading foresight Face 1  
    335  = Staff reading backsight Face 2
    336  = Staff reading foresight Face 2
    571-574 = Quality indicators
    83   = Height (scaled by 1e-5 = meters)
    573  = Height difference
    574  = Cumulative distance
"""
import re
from pathlib import Path
from typing import List, Optional, Dict, Tuple, Any
from datetime import datetime
import logging

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers.base_parser import BaseParser
from config.models import LevelingLine, StationSetup, LineStatus
from config.settings import get_settings, is_benchmark


logger = logging.getLogger(__name__)


class LeicaParser(BaseParser):
    """Parser for Leica GSI format files."""
    
    def __init__(self, encoding: str = None):
        super().__init__(encoding)
        self.cfg = get_settings().leica
        
        # Word index to name mapping
        self.wi_names = {
            11: 'point_id',
            32: 'distance',
            331: 'staff_b1',
            332: 'staff_f1',
            335: 'staff_b2',
            336: 'staff_f2',
            83: 'height',
            573: 'height_diff',
            574: 'cumulative_dist',
            571: 'quality_1',
            572: 'quality_2',
        }
        
        # Pattern for GSI data blocks
        # Format: WIIIPP+DDDDDDDDDDDDDDDD or WIIIPP-DDDDDDDDDDDDDDDD
        self.block_pattern = re.compile(
            r'(\d{2,3})\.{0,2}(\d{0,2})([+-])(\d{16})'
        )
        
    def detect_format(self, filepath: str) -> bool:
        """Check if file is Leica GSI format."""
        try:
            lines = self.read_file(filepath)[:10]
            for line in lines:
                # Check for typical GSI patterns
                if '+' in line and len(line.strip()) > 20:
                    # Try to parse as GSI
                    blocks = self._parse_line(line)
                    if blocks and any(wi in [11, 32, 83, 331, 332] for wi in blocks.keys()):
                        return True
        except:
            pass
        return False
    
    def _parse_line(self, line: str) -> Dict[int, Any]:
        """
        Parse a single GSI line into word index -> value dictionary.
        
        Args:
            line: Raw line from GSI file
            
        Returns:
            Dictionary mapping word index to parsed value
        """
        result = {}
        
        # Split line by spaces
        parts = line.strip().split()
        
        for part in parts:
            # Try to match the block pattern
            # Handle different formats: 110124+... or 32...8+...
            
            # Clean up the part
            part = part.strip()
            if not part:
                continue
            
            # Extract word index and value
            # Format 1: 110124+0000000003747MPI (WI=11, with address prefix)
            # Format 2: 32...8+0000000001289347 (WI=32)
            # Format 3: 331.08+0000000000122303 (WI=331)
            
            # Look for the sign
            sign_pos = -1
            for i, c in enumerate(part):
                if c in '+-':
                    sign_pos = i
                    break
            
            if sign_pos < 0:
                continue
            
            wi_part = part[:sign_pos]
            value_part = part[sign_pos:]
            
            # Parse word index
            # Remove dots and extract the base number
            wi_clean = wi_part.replace('.', '')
            
            # The word index is typically the first 2-3 digits
            try:
                if len(wi_clean) >= 2:
                    # For formats like "110124", first 2 digits are WI
                    if len(wi_clean) > 3 and wi_clean[:2] in ['11', '41']:
                        wi = int(wi_clean[:2])
                    elif len(wi_clean) == 3 or wi_clean[:3] in ['331', '332', '335', '336', '571', '572', '573', '574']:
                        wi = int(wi_clean[:3]) if len(wi_clean) >= 3 else int(wi_clean)
                    else:
                        wi = int(wi_clean[:2])
                else:
                    continue
            except ValueError:
                continue
            
            # Parse value
            sign = 1 if value_part[0] == '+' else -1
            val_str = value_part[1:]
            
            # For point IDs (WI 11), keep as string
            if wi == 11:
                result[wi] = val_str.lstrip('0') or '0'
            else:
                # Numeric value - apply scaling
                try:
                    raw_value = int(val_str) * sign
                    
                    # Apply appropriate scaling
                    if wi in [32, 83, 331, 332, 335, 336, 573, 574]:
                        result[wi] = raw_value * 1e-5  # Convert to meters
                    else:
                        result[wi] = raw_value
                except ValueError:
                    continue
        
        return result
    
    def parse(self, filepath: str) -> LevelingLine:
        """
        Parse a Leica GSI file.
        
        Args:
            filepath: Path to the GSI file
            
        Returns:
            LevelingLine object with parsed data
        """
        self.clear_messages()
        filename = self.extract_filename(filepath)
        raw_lines = self.read_file(filepath)
        
        # Initialize
        leveling_line = LevelingLine(
            filename=filename,
            start_point="",
            end_point="",
            setups=[],
            method="BF"
        )
        
        # State tracking
        current_from_point = None
        current_bs_reading = None
        current_bs_dist = None
        setup_number = 0
        benchmark_points = []  # Track all benchmark-like points
        all_points = []
        total_dist = 0.0
        
        for raw_line in raw_lines:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            
            # Parse the line
            data = self._parse_line(raw_line)
            
            if not data:
                continue
            
            # Get point ID if present
            point_id = data.get(11, None)
            if point_id:
                point_id = str(point_id).strip()
                if point_id and point_id not in ['0']:
                    all_points.append(point_id)
                    if is_benchmark(point_id):
                        benchmark_points.append(point_id)
            
            # Check for height (WI 83) - indicates instrument setup
            if 83 in data and point_id:
                # This is a station setup line
                if not leveling_line.start_point and is_benchmark(point_id):
                    leveling_line.start_point = point_id
            
            # Check for backsight reading (WI 331 or 335)
            if 331 in data or 335 in data:
                bs_reading = data.get(331) or data.get(335)
                bs_dist = data.get(32, 0)
                
                if point_id:
                    current_from_point = point_id
                    current_bs_reading = bs_reading
                    current_bs_dist = bs_dist
            
            # Check for foresight reading (WI 332 or 336)
            if 332 in data or 336 in data:
                fs_reading = data.get(332) or data.get(336)
                fs_dist = data.get(32, 0)
                
                if current_bs_reading is not None and point_id:
                    setup_number += 1
                    
                    setup = StationSetup(
                        setup_number=setup_number,
                        from_point=current_from_point or "",
                        to_point=point_id,
                        backsight_reading=current_bs_reading,
                        foresight_reading=fs_reading,
                        distance_back=current_bs_dist or 0.0,
                        distance_fore=fs_dist
                    )
                    leveling_line.setups.append(setup)
            
            # Check for height difference (WI 573) and cumulative distance (WI 574)
            if 573 in data and leveling_line.setups:
                leveling_line.setups[-1].height_diff = data[573]
            
            if 574 in data:
                # Track max cumulative distance
                if data[574] > total_dist:
                    total_dist = data[574]
        
        # Set total distance
        leveling_line.total_distance = total_dist
        
        # Determine start and end points from filename first (most reliable)
        fn_start, fn_end = self._extract_points_from_filename(filename)
        
        # If filename has both points, use them
        if fn_start and fn_end:
            # Verify that the filename points appear in the data
            if fn_start in benchmark_points:
                leveling_line.start_point = fn_start
            elif not leveling_line.start_point and benchmark_points:
                leveling_line.start_point = benchmark_points[0]
            
            if fn_end in benchmark_points:
                leveling_line.end_point = fn_end
            else:
                # End point might be last benchmark that's different from start
                for bp in reversed(benchmark_points):
                    if bp != leveling_line.start_point:
                        leveling_line.end_point = bp
                        break
        else:
            # Fallback: use parsed benchmark sequence
            if benchmark_points:
                if not leveling_line.start_point:
                    leveling_line.start_point = benchmark_points[0]
                
                # End point is the last unique benchmark (different from start)
                for bp in reversed(benchmark_points):
                    if bp != leveling_line.start_point:
                        leveling_line.end_point = bp
                        break
        
        # Final fallback: use filename values if we still don't have good endpoints
        if not leveling_line.end_point or leveling_line.end_point == leveling_line.start_point:
            if fn_end:
                leveling_line.end_point = fn_end
            elif all_points:
                leveling_line.end_point = all_points[-1]
        
        # Calculate totals from setups
        leveling_line.calculate_totals()
        
        # Use parsed distance if available
        if total_dist > 0:
            leveling_line.total_distance = total_dist
        
        # Validate
        if not is_benchmark(leveling_line.end_point):
            leveling_line.status = LineStatus.INVALID_ENDPOINT
            leveling_line.validation_errors.append(
                f"End point '{leveling_line.end_point}' is a turning point, not a benchmark"
            )
        
        return leveling_line
    
    def _extract_points_from_filename(self, filename: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Try to extract start and end points from filename.
        
        Common patterns:
            3747MPI-5469MPI -> (3747MPI, 5469MPI)
            5475MPI-3755MPI_raw -> (5475MPI, 3755MPI)
        """
        # Remove common suffixes
        clean_name = filename.replace('_raw', '').replace('.raw', '')
        
        # Try to split by hyphen or underscore
        for sep in ['-', '_', ' ']:
            if sep in clean_name:
                parts = clean_name.split(sep)
                if len(parts) >= 2:
                    start = parts[0].strip()
                    end = parts[1].strip()
                    if start and end:
                        return (start, end)
        
        return (None, None)
    
    def parse_batch(self, filepaths: List[str]) -> List[LevelingLine]:
        """
        Parse multiple files.
        
        Args:
            filepaths: List of file paths
            
        Returns:
            List of LevelingLine objects
        """
        results = []
        for fp in filepaths:
            try:
                line = self.parse(fp)
                results.append(line)
            except Exception as e:
                self.add_error(f"Failed to parse {fp}: {str(e)}")
        return results


# Convenience function
def parse_leica_gsi(filepath: str) -> LevelingLine:
    """
    Parse a Leica GSI file.
    
    Args:
        filepath: Path to the GSI file
        
    Returns:
        LevelingLine object
    """
    parser = LeicaParser()
    return parser.parse(filepath)
