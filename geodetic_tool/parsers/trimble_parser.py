"""
Trimble DAT File Parser

Parses Trimble digital level DAT files in pipe-delimited format.

File Format:
    For M5|Adr   N|TO  text                     |                      |...
    For M5|Adr   N|KD1  PointID  Temp C  1   1|Rb        X.XXXXX m   |HD   YYY.YYY m   |
    For M5|Adr   N|KD1  PointID  Temp C  1   1|Rf        X.XXXXX m   |HD   YYY.YYY m   |
    For M5|Adr   N|KD1  PointID  Temp C      1|                      |                 |Z    X.XXXXX m   |
"""
import re
from pathlib import Path
from typing import List, Optional, Tuple
from datetime import datetime
import logging

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers.base_parser import BaseParser
from config.models import LevelingLine, StationSetup, LineStatus
from config.settings import get_settings, is_benchmark


logger = logging.getLogger(__name__)


class TrimbleParser(BaseParser):
    """Parser for Trimble DAT format files."""
    
    def __init__(self, encoding: str = None):
        super().__init__(encoding)
        self.settings = get_settings()
        
        # Regex patterns for parsing
        self.rb_pattern = re.compile(r'Rb\s+([\d.-]+)\s*m')  # Backsight
        self.rf_pattern = re.compile(r'Rf\s+([\d.-]+)\s*m')  # Foresight
        self.hd_pattern = re.compile(r'HD\s+([\d.-]+)\s*m')  # Horizontal distance
        self.z_pattern = re.compile(r'Z\s+([\d.-]+)\s*m')    # Height
        self.sh_pattern = re.compile(r'Sh\s+([\d.-]+)\s*m')  # Final height shift
        self.dz_pattern = re.compile(r'dz\s+([\d.-]+)\s*m')  # Height difference
        self.db_pattern = re.compile(r'Db\s+([\d.-]+)\s*m')  # Distance back
        self.df_pattern = re.compile(r'Df\s+([\d.-]+)\s*m')  # Distance forward
        self.temp_pattern = re.compile(r'([\d.]+)\s*C')      # Temperature
        
    def detect_format(self, filepath: str) -> bool:
        """Check if file is Trimble DAT format."""
        try:
            lines = self.read_file(filepath)[:10]
            for line in lines:
                if '|' in line and ('For M5' in line or 'KD1' in line or 'TO' in line):
                    return True
        except:
            pass
        return False
    
    def parse(self, filepath: str) -> LevelingLine:
        """
        Parse a Trimble DAT file.
        
        Args:
            filepath: Path to the DAT file
            
        Returns:
            LevelingLine object with parsed data
        """
        self.clear_messages()
        filename = self.extract_filename(filepath)
        lines = self.read_file(filepath)
        
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
        current_rb = None
        current_rb_dist = None
        current_temp = None
        setup_number = 0
        in_measurement = False
        
        for i, line in enumerate(lines):
            line = line.strip()
            if not line or '|' not in line:
                continue
            
            parts = [p.strip() for p in line.split('|')]
            if len(parts) < 3:
                continue
            
            content = parts[2]
            
            # Parse TO (text) records
            if content.startswith('TO'):
                text = content[2:].strip()
                
                if 'Start-Line' in text:
                    in_measurement = True
                    # Extract method (BF or BFFB)
                    if 'BFFB' in text:
                        leveling_line.method = 'BFFB'
                    elif 'BF' in text:
                        leveling_line.method = 'BF'
                    elif 'FB' in text:
                        leveling_line.method = 'FB'
                    
                elif 'End-Line' in text:
                    in_measurement = False
                    
                elif text.endswith('.dat'):
                    # Internal filename
                    pass
                    
                continue
            
            # Parse KD1 (measurement) records
            if content.startswith('KD1'):
                kd1_content = content[3:].strip()
                
                # Extract point ID (first token before temperature)
                point_id = self._extract_point_id(kd1_content)
                
                # Extract temperature if present
                temp_match = self.temp_pattern.search(line)
                if temp_match:
                    current_temp = float(temp_match.group(1))
                
                # Check for Rb (backsight)
                rb_match = self.rb_pattern.search(line)
                if rb_match:
                    current_rb = float(rb_match.group(1))
                    current_from_point = point_id
                    
                    # Get distance
                    hd_match = self.hd_pattern.search(line)
                    if hd_match:
                        current_rb_dist = float(hd_match.group(1))
                    
                    # Set start point if first measurement
                    if not leveling_line.start_point:
                        leveling_line.start_point = point_id
                
                # Check for Rf (foresight)
                rf_match = self.rf_pattern.search(line)
                if rf_match and current_rb is not None:
                    rf = float(rf_match.group(1))
                    
                    # Get distance
                    rf_dist = 0.0
                    hd_match = self.hd_pattern.search(line)
                    if hd_match:
                        rf_dist = float(hd_match.group(1))
                    
                    # Create setup
                    setup_number += 1
                    setup = StationSetup(
                        setup_number=setup_number,
                        from_point=current_from_point or "",
                        to_point=point_id,
                        backsight_reading=current_rb,
                        foresight_reading=rf,
                        distance_back=current_rb_dist or 0.0,
                        distance_fore=rf_dist,
                        temperature=current_temp
                    )
                    leveling_line.setups.append(setup)
                
                # Check for Z (accumulated height)
                z_match = self.z_pattern.search(line)
                if z_match and leveling_line.setups:
                    z_value = float(z_match.group(1))
                    leveling_line.setups[-1].cumulative_height = z_value
                
                # Check for Sh (final height shift) - indicates end point
                sh_match = self.sh_pattern.search(line)
                if sh_match:
                    leveling_line.end_point = point_id
            
            # Parse KD2 (summary) records
            if content.startswith('KD2'):
                kd2_content = content[3:].strip()
                point_id = self._extract_point_id(kd2_content)
                
                if point_id:
                    leveling_line.end_point = point_id
                
                # Extract summary distances
                db_match = self.db_pattern.search(line)
                df_match = self.df_pattern.search(line)
                if db_match and df_match:
                    db = float(db_match.group(1))
                    df = float(df_match.group(1))
                    leveling_line.total_distance = (db + df) / 2
        
        # Calculate totals if not already set
        if leveling_line.total_distance == 0:
            leveling_line.calculate_totals()
        else:
            # Calculate total height diff from setups
            leveling_line.total_height_diff = sum(
                s.height_diff for s in leveling_line.setups if s.height_diff is not None
            )
        
        # Validate end point
        if not is_benchmark(leveling_line.end_point):
            leveling_line.status = LineStatus.INVALID_ENDPOINT
            leveling_line.validation_errors.append(
                f"End point '{leveling_line.end_point}' is a turning point, not a benchmark"
            )
        
        return leveling_line
    
    def _extract_point_id(self, kd_content: str) -> str:
        """
        Extract point ID from KD1/KD2 content.
        
        The point ID is the first token, possibly followed by temperature or other data.
        Special handling for:
        - Named benchmarks: 5793MPI, 640B, etc.
        - Turning points: 1, 2, 3, etc.
        - Points with markers: ##### indicates special status
        """
        # Remove ##### markers
        content = kd_content.replace('#####', '').strip()
        
        # Split by whitespace
        tokens = content.split()
        if not tokens:
            return ""
        
        point_id = tokens[0]
        
        # Clean up any trailing characters
        point_id = point_id.strip()
        
        return point_id
    
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
def parse_trimble_dat(filepath: str) -> LevelingLine:
    """
    Parse a Trimble DAT file.
    
    Args:
        filepath: Path to the DAT file
        
    Returns:
        LevelingLine object
    """
    parser = TrimbleParser()
    return parser.parse(filepath)
