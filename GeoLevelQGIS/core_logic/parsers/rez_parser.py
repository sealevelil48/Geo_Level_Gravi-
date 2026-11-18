"""
REZ File Parser

Parses .rez format files (tab/space-separated summary format) containing
aggregated leveling line measurements.

File Format:
    # Comment lines starting with "#"
    Column headers row
    Data rows: From | To | Height Diff | Distance | Setups | Status | Source File

For each .rez file, the parser:
1. Skips comment and header lines
2. Extracts all data rows (from_point, to_point, total_dh, total_distance, setups_count)
3. Creates ONE synthetic StationSetup with aggregated values
4. Returns LevelingLine with that single setup
"""
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from core_logic.parsers.base_parser import BaseParser
from core_logic.config.models import LevelingLine, StationSetup, LineStatus


logger = logging.getLogger(__name__)


class RezParser(BaseParser):
    """Parser for REZ format files (summary format)."""

    def __init__(self, encoding: str = None):
        super().__init__(encoding)

    def detect_format(self, filepath: str) -> bool:
        """Check if file is REZ format."""
        return filepath.lower().endswith('.rez')

    def parse(self, filepath: str) -> LevelingLine:
        """
        Parse a REZ file and return a LevelingLine with a single synthetic setup.

        Args:
            filepath: Path to the REZ file

        Returns:
            LevelingLine object with a single synthetic StationSetup

        Raises:
            ValueError: If file is empty or has no valid observation lines
        """
        logger.info(f"Parsing .rez file: {filepath}")

        # Read file lines
        lines = self.read_file(filepath)
        if not lines:
            raise ValueError("Empty or unreadable file")

        # Extract observation rows from file
        observations = self._parse_observations(lines)
        if not observations:
            raise ValueError("No observation lines found")

        # Create synthetic setup from aggregated observations
        synthetic_setup = self._create_synthetic_setup(observations)

        # Extract from/to points
        from_point = observations[0]["from_point"]
        to_point = observations[-1]["to_point"]

        # Calculate totals
        total_distance = sum(obs["distance"] for obs in observations)
        total_height_diff = sum(obs["height_diff"] for obs in observations)
        num_observations = len(observations)

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
            f"Parsed {num_observations} observations, from {from_point} to {to_point}"
        )
        logger.debug(
            f"Created synthetic setup: {from_point}→{to_point}, "
            f"dH={total_height_diff:.4f}m, distance={total_distance:.1f}m"
        )
        logger.info(
            f"Parsed .rez: {from_point}→{to_point}, {num_observations} obs, "
            f"{total_distance:.1f}m total"
        )

        return leveling_line

    def _parse_observations(self, lines: List[str]) -> List[Dict[str, any]]:
        """
        Extract observation rows from file lines.

        Skips comment lines (#) and header lines, then parses data rows.
        Each row should contain: From | To | Height Diff | Distance | Setups | Status | Source File

        Args:
            lines: List of file lines

        Returns:
            List of observation dictionaries with keys:
            - from_point: Starting point name
            - to_point: Ending point name
            - height_diff: Height difference in meters
            - distance: Distance in meters
            - setups_count: Number of setups
            - status: Line status
            - source_file: Original source file name
        """
        observations = []

        # State for detecting header row
        header_found = False

        for line_num, line in enumerate(lines, 1):
            line = line.strip()

            # Skip empty lines
            if not line:
                continue

            # Skip comment lines
            if line.startswith("#"):
                continue

            # Try to detect and skip header row (contains column names like From, To, etc.)
            if not header_found:
                if any(
                    keyword in line.lower()
                    for keyword in ["from", "to", "height", "distance", "setups"]
                ):
                    header_found = True
                    logger.debug(f"Found header row at line {line_num}: {line}")
                    continue

            # Parse data row
            # Handle both tab and space-separated values
            # Split by | first (pipe is the primary separator in the format)
            if "|" in line:
                parts = [p.strip() for p in line.split("|")]
            else:
                # Fall back to tab/space separation
                parts = line.split()

            if len(parts) < 4:
                logger.warning(f"Malformed row at line {line_num}, skipping: {line}")
                continue

            try:
                from_point = parts[0].strip()
                to_point = parts[1].strip()
                height_diff = float(parts[2])
                distance = float(parts[3])

                # Optional fields
                setups_count = int(parts[4]) if len(parts) > 4 else 1
                status = parts[5].strip() if len(parts) > 5 else "unknown"
                source_file = parts[6].strip() if len(parts) > 6 else ""

                observations.append(
                    {
                        "from_point": from_point,
                        "to_point": to_point,
                        "height_diff": height_diff,
                        "distance": distance,
                        "setups_count": setups_count,
                        "status": status,
                        "source_file": source_file,
                    }
                )

                logger.debug(
                    f"Line {line_num}: {from_point}→{to_point}, "
                    f"dH={height_diff}m, dist={distance}m"
                )

            except (ValueError, IndexError) as e:
                logger.warning(
                    f"Failed to parse row at line {line_num}: {line} ({e})"
                )
                continue

        return observations

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


def parse_rez_file(filepath: str) -> LevelingLine:
    """
    Convenience function to parse a REZ file.

    Args:
        filepath: Path to the REZ file

    Returns:
        LevelingLine object with parsed data
    """
    parser = RezParser()
    return parser.parse(filepath)
