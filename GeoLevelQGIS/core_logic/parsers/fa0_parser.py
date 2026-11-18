"""
FA0 File Parser

Parses .fa0 format files (fixed-width column format) containing aggregated
leveling line measurements and benchmark data.

File Format:
    Line 1: Header - "  <num_points>   2            <project_name>9"
    Lines 2-N: Benchmarks - "    <point_id:8><height:12.3f>" or unknowns "    <point_id:8>           ."
    Following lines: Observations - "<from:8><to:8><dh:12.5f><dist:7.0f>.<setups:4><bf_diff:7.2f>  <date:4>  <source>"
    Last line: Terminator - " 9"

For each .fa0 file, the parser:
1. Parses header line to extract num_points and project_name
2. Parses benchmark section until observations start
3. Extracts all observation lines (fixed-width columns)
4. Calculates synthetic readings from aggregated observations
5. Creates ONE synthetic StationSetup
6. Returns LevelingLine with that single setup
"""
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from core_logic.parsers.base_parser import BaseParser
from core_logic.config.models import LevelingLine, StationSetup, LineStatus


logger = logging.getLogger(__name__)


class FA0Parser(BaseParser):
    """Parser for FA0 format files (fixed-width summary format)."""

    def __init__(self, encoding: str = None):
        super().__init__(encoding)

    def detect_format(self, filepath: str) -> bool:
        """Check if file is FA0 format."""
        return filepath.lower().endswith('.fa0')

    def parse(self, filepath: str) -> LevelingLine:
        """
        Parse a FA0 file and return a LevelingLine with a single synthetic setup.

        Args:
            filepath: Path to the FA0 file

        Returns:
            LevelingLine object with a single synthetic StationSetup

        Raises:
            ValueError: If file is empty, has no observations, or is malformed
        """
        logger.info(f"Parsing .fa0 file: {filepath}")

        # Read file lines
        lines = self.read_file(filepath)
        if not lines:
            raise ValueError("Empty or unreadable file")

        # Parse header to get num_points and project_name
        num_points, project_name = self._parse_header(lines)
        logger.debug(f"Header: {num_points} points, project: {project_name}")

        # Parse benchmark section and observations
        benchmarks = {}  # point_id -> height (meters)
        observations = self._parse_observations(lines, num_points, benchmarks)

        if not observations:
            raise ValueError("No observation lines found")

        logger.debug(
            f"Extracted {len(benchmarks)} benchmarks, {len(observations)} observations"
        )

        # Create synthetic setup from aggregated observations
        synthetic_setup = self._create_synthetic_setup(observations)

        # Extract from/to points and totals
        from_point = observations[0]["from_point"]
        to_point = observations[-1]["to_point"]
        total_distance = sum(obs["distance"] for obs in observations)
        total_height_diff = sum(obs["height_diff"] for obs in observations)

        # Create LevelingLine with synthetic setup
        leveling_line = LevelingLine(
            filename=self.extract_filename(filepath),
            start_point=from_point,
            end_point=to_point,
            setups=[synthetic_setup],
            total_distance=total_distance,
            total_height_diff=total_height_diff,
            status=LineStatus.VALID,
            source_path=str(filepath),
        )

        logger.debug(
            f"Created synthetic setup: {from_point}→{to_point}, "
            f"dH={total_height_diff:.5f}m, distance={total_distance:.1f}m"
        )
        logger.info(
            f"Parsed .fa0: {from_point}→{to_point}, {len(observations)} obs, "
            f"{total_distance:.1f}m total"
        )

        return leveling_line

    def _parse_header(self, lines: List[str]) -> Tuple[int, str]:
        """
        Extract header information from first line.

        Format: "  <num_points>   2            <project_name>9"
        - Columns 0-2: spaces
        - Columns 3-6: num_points (right-aligned integer)
        - Columns 7-13: spaces + '2'
        - Remaining: project_name + '9'

        Args:
            lines: List of file lines

        Returns:
            Tuple of (num_points: int, project_name: str)
        """
        if not lines:
            raise ValueError("Empty file")

        header_line = lines[0]

        try:
            # Extract num_points from columns ~3-6
            # Try to extract the numeric portion
            num_points_str = header_line[2:7].strip()
            num_points = int(num_points_str) if num_points_str else 0

            # Extract project_name (everything after position 13, excluding trailing '9')
            project_name = header_line[13:].rstrip("9").strip()

            logger.debug(f"Parsed header: num_points={num_points}, project={project_name}")
            return num_points, project_name

        except (ValueError, IndexError) as e:
            logger.warning(f"Failed to parse header line: {header_line} ({e})")
            return 0, "Unknown"

    def _parse_observations(
        self, lines: List[str], num_points: int, benchmarks: Dict[str, float]
    ) -> List[Dict[str, any]]:
        """
        Extract observation rows and benchmark data from file lines.

        Benchmarks section format:
        - Each benchmark: "    <point_id:8><height:12.3f>" (height left-aligned as float)
        - Unknown points: "    <point_id:8>           ." (height = dot)

        Observations section format:
        - "<from:8><to:8><dh:12.5f><dist:7.0f>.<setups:4><bf_diff:7.2f>  <date:4>  <source>"
        - from: columns 0-7
        - to: columns 8-15
        - dh: columns 16-28
        - distance: columns 29-35
        - separator: column 36 (dot)
        - setups: columns 37-40
        - bf_diff: columns 41-47
        - date: columns 51-54
        - source: columns 57+

        Args:
            lines: List of file lines
            num_points: Number of benchmark points expected
            benchmarks: Dict to populate with benchmark data

        Returns:
            List of observation dictionaries with keys:
            - from_point: Starting point name
            - to_point: Ending point name
            - height_diff: Height difference in meters
            - distance: Distance in meters
            - setups_count: Number of setups
            - bf_diff: Backsight-foresight difference
            - date: Measurement date
            - source: Source identifier
        """
        observations = []
        benchmarks_parsed = 0
        in_observations = False

        for line_num, line in enumerate(lines[1:], 2):  # Skip header (line 1)
            if not line.strip():
                continue

            # Check for terminator line (single '9')
            if line.strip() == "9":
                logger.debug(f"Found terminator at line {line_num}")
                break

            # Check if we're still in benchmark section
            if benchmarks_parsed < num_points and not in_observations:
                # Try to parse as benchmark
                if len(line) >= 20:
                    try:
                        point_id = line[4:12].strip()  # Columns 4-11
                        height_str = line[12:24].strip()  # Columns 12-23

                        if height_str and height_str != ".":
                            try:
                                height = float(height_str)
                                benchmarks[point_id] = height
                                benchmarks_parsed += 1
                                logger.debug(f"Benchmark {point_id}: {height:.3f}m")
                                continue
                            except ValueError:
                                pass

                    except IndexError:
                        pass

            # Try to parse as observation (fixed-width columns)
            in_observations = True

            if len(line) < 37:  # Minimum length for observation line
                logger.warning(
                    f"Line {line_num} too short ({len(line)} chars), skipping"
                )
                continue

            obs = self._parse_fixed_width_observation(line)
            if obs:
                observations.append(obs)
                logger.debug(
                    f"Line {line_num}: {obs['from_point']}→{obs['to_point']}, "
                    f"dH={obs['height_diff']:.5f}m, dist={obs['distance']:.1f}m"
                )
            else:
                logger.warning(f"Failed to parse observation at line {line_num}")

        return observations

    def _parse_fixed_width_observation(self, line: str) -> Optional[Dict[str, any]]:
        """
        Parse a single fixed-width observation line.

        Format:
        - from_point: columns 0-7
        - to_point: columns 8-15
        - height_diff: columns 16-28 (12.5f)
        - distance: columns 29-35 (7.0f)
        - separator: column 36 (dot)
        - setups: columns 37-40 (right-aligned integer)
        - bf_diff: columns 40-47 (7.2f, includes sign)
        - date: columns 50-53 (4-digit year)
        - source: columns 56+

        Args:
            line: Single observation line (fixed-width)

        Returns:
            Dict with parsed observation data, or None if parsing fails
        """
        try:
            from_point = line[0:8].strip()
            to_point = line[8:16].strip()

            # Parse height difference (columns 16-28)
            dh_str = line[16:29].strip()
            height_diff = float(dh_str) if dh_str else 0.0

            # Parse distance (columns 29-35)
            dist_str = line[29:36].strip()
            distance = float(dist_str) if dist_str else 0.0

            # Parse setups count: extract from remainder, split by whitespace
            # Format: "    5-0.02  2024  File1"
            remainder = line[37:].strip() if len(line) > 37 else ""

            setups_count = 1
            bf_diff = 0.0
            date_str = ""
            source_str = ""

            if remainder:
                # Split by whitespace to get individual components
                parts = remainder.split()
                if parts:
                    # First part should be setups+bf_diff combined like "5-0.02"
                    combined = parts[0]

                    # Try to extract setups and bf_diff from combined string
                    # e.g., "5-0.02" -> setups=5, bf_diff=-0.02
                    # e.g., "6-0.01" -> setups=6, bf_diff=-0.01
                    if '-' in combined:
                        # Find the last hyphen (in case point names have hyphens)
                        idx = combined.rfind('-')
                        setups_str = combined[:idx]
                        bf_diff_str = combined[idx:]
                    else:
                        setups_str = combined
                        bf_diff_str = "0"

                    try:
                        setups_count = int(setups_str.strip()) if setups_str else 1
                    except ValueError:
                        setups_count = 1

                    try:
                        bf_diff = float(bf_diff_str) if bf_diff_str else 0.0
                    except ValueError:
                        bf_diff = 0.0

                # Extract date and source from remaining parts
                if len(parts) > 1:
                    date_str = parts[1] if parts[1].isdigit() or len(parts[1]) == 4 else ""
                if len(parts) > 2:
                    source_str = " ".join(parts[2:])

            if not from_point or not to_point:
                logger.warning(f"Invalid point names in line: {line[:40]}")
                return None

            return {
                "from_point": from_point,
                "to_point": to_point,
                "height_diff": height_diff,
                "distance": distance,
                "setups_count": setups_count,
                "bf_diff": bf_diff,
                "date": date_str,
                "source": source_str,
            }

        except (ValueError, IndexError) as e:
            logger.error(f"Failed to parse fixed-width observation: {e}")
            return None

    def _create_synthetic_setup(
        self, observations: List[Dict[str, any]]
    ) -> StationSetup:
        """
        Create a single synthetic StationSetup from aggregated observations.

        The synthetic setup represents the entire line as a single measurement:
        - setup_number = 1 (always, since it's one synthetic setup)
        - from_point = first observation from_point
        - to_point = last observation to_point
        - total_distance = sum of all observation distances
        - total_height_diff = sum of all observation heights
        - backsight_reading = (total_distance / 2) + (total_height_diff / 2) * 0.001
        - foresight_reading = (total_distance / 2) - (total_height_diff / 2) * 0.001
        - distance_back = total_distance / 2
        - distance_fore = total_distance / 2

        Args:
            observations: List of observation dictionaries

        Returns:
            StationSetup object representing aggregated line data
        """
        from_point = observations[0]["from_point"]
        to_point = observations[-1]["to_point"]

        # Aggregate totals
        total_distance = sum(obs["distance"] for obs in observations)
        total_height_diff = sum(obs["height_diff"] for obs in observations)

        # Synthetic readings (representing average conditions across line)
        # These are synthetic and don't correspond to actual physical readings,
        # but allow the line to be represented in the standard StationSetup format.
        # The strategy distributes the total distance equally to backsight and foresight,
        # and uses the height difference to create representative readings.
        half_distance = total_distance / 2.0
        half_height = total_height_diff / 2.0

        backsight_reading = half_distance + half_height
        foresight_reading = half_distance - half_height

        # Create synthetic setup
        synthetic_setup = StationSetup(
            setup_number=1,
            from_point=from_point,
            to_point=to_point,
            backsight_reading=backsight_reading,
            foresight_reading=foresight_reading,
            distance_back=half_distance,
            distance_fore=half_distance,
            temperature=None,
            height_diff=total_height_diff,
        )

        return synthetic_setup


def parse_fa0_file(filepath: str) -> LevelingLine:
    """
    Convenience function to parse a FA0 file.

    Args:
        filepath: Path to the FA0 file

    Returns:
        LevelingLine object with parsed data
    """
    parser = FA0Parser()
    return parser.parse(filepath)
