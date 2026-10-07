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

from core_logic.parsers.base_parser import BaseParser
from core_logic.config.models import LevelingLine, StationSetup, LineStatus
from core_logic.config.settings import get_settings, is_benchmark


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

        Station grouping (BFFB / BF)
        ─────────────────────────────
        The Trimble M5 records a full station as a block of KD1 lines ending with
        a Z-summary line (the line that has a Z field but no Rb/Rf reading).

        For BFFB the block contains: Rb1, Rf1, Rf2, Rb2.
        For BF  the block contains: Rb,  Rf.

        The Z value on the summary line is the instrument's cumulative height
        (already temperature-corrected) — it is the authoritative source for
        per-station dH.  Deriving dH from Z-differences avoids double-counting
        the individual readings, which was the bug that caused a factor-of-~2.7
        error on BFFB files.

        "Station repeated" handling
        ───────────────────────────
        When the instrument operator repeats a station (TO  Station repeated),
        the repeated block REPLACES the previous one.  We discard the previous
        station's setup and use the repeated data instead.  The Z value after
        the repeated block is already cumulative from the start of the line, so
        using Z-differences automatically handles this correctly as long as we
        only keep the LAST station block at each slot.
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

        # ── Pass 1: collect station blocks ──────────────────────────────────
        # A "station block" is all KD1 lines between consecutive Z-summary lines.
        # Each block yields exactly ONE StationSetup whose dH comes from the Z diff.

        # State for the current in-progress station block
        current_from_point = None   # backsight point id (from_point)
        current_to_point   = None   # foresight point id (to_point)
        current_rb1        = None   # first Rb in the block
        current_rb1_dist   = None
        current_rf1        = None   # first Rf in the block
        current_rf1_dist   = None
        current_rb2        = None   # second Rb (BFFB only)
        current_rb2_dist   = None
        current_rf2        = None   # second Rf (BFFB only)
        current_rf2_dist   = None
        current_temp       = None
        rf_count           = 0      # how many Rf readings seen in current block
        rb_count           = 0      # how many Rb readings seen in current block

        prev_z             = 0.0    # cumulative Z before this station
        setup_number       = 0
        in_measurement     = False

        for raw_line in lines:
            line = raw_line.strip()
            if not line or '|' not in line:
                continue

            parts = [p.strip() for p in line.split('|')]
            if len(parts) < 3:
                continue

            content = parts[2]

            # ── TO (text annotation) records ────────────────────────────────
            if content.startswith('TO'):
                text = content[2:].strip()

                if 'Start-Line' in text:
                    in_measurement = True
                    if 'BFFB' in text:
                        leveling_line.method = 'BFFB'
                    elif 'BF' in text:
                        leveling_line.method = 'BF'
                    elif 'FB' in text:
                        leveling_line.method = 'FB'

                elif 'End-Line' in text:
                    in_measurement = False

                elif 'Station repeated' in text:
                    # The NEXT station block replaces the LAST committed setup.
                    # 1. Roll back the last committed setup and its Z contribution.
                    if leveling_line.setups:
                        discarded = leveling_line.setups.pop()
                        prev_z = discarded.cumulative_height - discarded.height_diff \
                                 if discarded.height_diff is not None \
                                 else prev_z
                        setup_number -= 1
                    # 2. Also clear any partial in-progress block state so readings
                    #    from the discarded attempt do not bleed into the replacement.
                    current_from_point = None
                    current_to_point   = None
                    current_rb1 = current_rb2 = None
                    current_rf1 = current_rf2 = None
                    current_rb1_dist = current_rb2_dist = None
                    current_rf1_dist = current_rf2_dist = None
                    rb_count = rf_count = 0

                continue

            # ── KD1 (measurement) records ────────────────────────────────────
            if content.startswith('KD1'):
                kd1_content = content[3:].strip()
                point_id = self._extract_point_id(kd1_content)

                temp_match = self.temp_pattern.search(line)
                if temp_match:
                    current_temp = float(temp_match.group(1))

                # Apply value patterns only to the data columns (parts[3..5]),
                # not the full raw line.  This prevents a point ID that begins
                # with a label letter (e.g. "Z123" in col 2) from being mistaken
                # for a height or distance field.
                data_cols = '|'.join(parts[3:]) if len(parts) > 3 else ''
                rb_match = self.rb_pattern.search(data_cols)
                rf_match = self.rf_pattern.search(data_cols)
                z_match  = self.z_pattern.search(data_cols)
                sh_match = self.sh_pattern.search(data_cols)
                hd_match = self.hd_pattern.search(data_cols)

                hd_val = float(hd_match.group(1)) if hd_match else 0.0

                # Rb reading (backsight) ──────────────────────────────────────
                if rb_match:
                    rb_val = float(rb_match.group(1))
                    rb_count += 1
                    if rb_count == 1:
                        current_rb1      = rb_val
                        current_rb1_dist = hd_val
                        current_from_point = point_id
                        if not leveling_line.start_point:
                            leveling_line.start_point = point_id
                    else:
                        # Second Rb in BFFB block
                        current_rb2      = rb_val
                        current_rb2_dist = hd_val

                # Rf reading (foresight) ──────────────────────────────────────
                elif rf_match:
                    rf_val = float(rf_match.group(1))
                    rf_count += 1
                    if rf_count == 1:
                        current_rf1      = rf_val
                        current_rf1_dist = hd_val
                        current_to_point = point_id
                    else:
                        current_rf2      = rf_val
                        current_rf2_dist = hd_val

                # Sh line — end-point marker (no Rb/Rf, has Sh field) ─────────
                elif sh_match:
                    leveling_line.end_point = point_id

                # Z-summary line — closes the current station block ───────────
                # The Z-summary line has a Z field but no Rb or Rf reading.
                # It carries the instrument's cumulative height after this station.
                if z_match and not rb_match and not rf_match:
                    z_val = float(z_match.group(1))
                    station_dh = z_val - prev_z

                    # Representative Rb and Rf for the setup record.
                    # For BFFB use the mean of both halves; for BF use the single pair.
                    if current_rb1 is not None and current_rf1 is not None:
                        if current_rb2 is not None and current_rf2 is not None:
                            # BFFB: mean readings (representative only — dH comes from Z diff)
                            rep_rb   = (current_rb1 + current_rb2) / 2
                            rep_rf   = (current_rf1 + current_rf2) / 2
                            dist_b   = (current_rb1_dist + current_rb2_dist) / 2
                            dist_f   = (current_rf1_dist + current_rf2_dist) / 2
                        else:
                            # BF: single pair
                            rep_rb   = current_rb1
                            rep_rf   = current_rf1
                            dist_b   = current_rb1_dist or 0.0
                            dist_f   = current_rf1_dist or 0.0

                        setup_number += 1
                        setup = StationSetup(
                            setup_number=setup_number,
                            from_point=current_from_point or "",
                            to_point=current_to_point or "",
                            backsight_reading=rep_rb,
                            foresight_reading=rep_rf,
                            distance_back=dist_b,
                            distance_fore=dist_f,
                            temperature=current_temp,
                            # dH from the instrument's Z difference — authoritative
                            height_diff=station_dh,
                            cumulative_height=z_val,
                        )
                        leveling_line.setups.append(setup)
                        prev_z = z_val

                    # Reset block state
                    current_from_point = None
                    current_to_point   = None
                    current_rb1 = current_rb2 = None
                    current_rf1 = current_rf2 = None
                    current_rb1_dist = current_rb2_dist = None
                    current_rf1_dist = current_rf2_dist = None
                    rb_count = rf_count = 0

                continue

            # ── KD2 (line-summary) records ───────────────────────────────────
            if content.startswith('KD2'):
                kd2_content = content[3:].strip()
                point_id = self._extract_point_id(kd2_content)
                if point_id:
                    leveling_line.end_point = point_id

                # Apply value patterns only to the data columns, same as KD1.
                kd2_data = '|'.join(parts[3:]) if len(parts) > 3 else ''

                # Use the instrument's authoritative summary distances (Db + Df)
                db_match = self.db_pattern.search(kd2_data)
                df_match = self.df_pattern.search(kd2_data)
                if db_match and df_match:
                    leveling_line.total_distance = (
                        float(db_match.group(1)) + float(df_match.group(1))
                    )

                # The KD2 Z field is the final cumulative dH for the whole line —
                # use it directly instead of summing per-setup values to avoid any
                # floating-point accumulation error.
                z_match = self.z_pattern.search(kd2_data)
                if z_match:
                    leveling_line.total_height_diff = float(z_match.group(1))

        # ── Fallback totals ──────────────────────────────────────────────────
        # Only reached when the file has no KD2 record (non-standard files).
        if leveling_line.total_distance == 0:
            leveling_line.total_distance = sum(
                s.distance_back + s.distance_fore for s in leveling_line.setups
            )
        if leveling_line.total_height_diff == 0.0 and leveling_line.setups:
            leveling_line.total_height_diff = sum(
                s.height_diff for s in leveling_line.setups if s.height_diff is not None
            )

        # ── Validate end point ───────────────────────────────────────────────
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
