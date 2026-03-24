"""
Line Coordinator Module

Intelligently merges multiple leveling line segments into consolidated lines.
Implements Phase 3 features: Items 5, 12/13, 14.

Features:
- Smart vector reversal (auto-detects direction mismatches)
- Common node detection (PKT alignment)
- State management (Merged/Excluded status)
"""

from typing import List, Dict, Set, Tuple, Optional
from dataclasses import dataclass, field
from collections import defaultdict
import logging

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ..config.models import LevelingLine, StationSetup, LineStatus

logger = logging.getLogger(__name__)


@dataclass
class MergeCandidate:
    """Represents a potential merge between lines."""
    lines: List[LevelingLine]
    merge_order: List[int]  # Index order for merging
    reverse_flags: List[bool]  # Which lines need reversal
    common_nodes: List[str]  # Shared turning points
    total_distance: float = 0.0
    is_valid: bool = True
    reason: Optional[str] = None


class LineCoordinator:
    """
    Coordinates and merges leveling line segments.

    Item 5: Line Coordination engine
    Item 12/13: Merge with smart vector reversal
    Item 14: State management (Merged/Excluded)
    """

    def __init__(self, lines: List[LevelingLine] = None):
        """
        Initialize coordinator with optional lines.

        Args:
            lines: List of LevelingLine objects to coordinate
        """
        self.lines = lines or []
        self.merge_history: List[Dict] = []

    def add_line(self, line: LevelingLine):
        """Add a line to the coordinator."""
        self.lines.append(line)

    def add_lines(self, lines: List[LevelingLine]):
        """Add multiple lines to the coordinator."""
        self.lines.extend(lines)

    def find_merge_candidates(self, selected_indices: List[int] = None) -> List[MergeCandidate]:
        """
        Find lines that can be merged together.

        Args:
            selected_indices: Specific line indices to consider (None = all lines)

        Returns:
            List of MergeCandidate objects
        """
        candidates = []

        # If specific lines selected, only check those
        if selected_indices:
            lines_to_check = [self.lines[i] for i in selected_indices if i < len(self.lines)]
        else:
            lines_to_check = self.lines

        # Build connectivity graph
        graph = self._build_connectivity_graph(lines_to_check)

        # Find mergeable chains
        for start_line in lines_to_check:
            candidate = self._find_mergeable_chain(start_line, lines_to_check, graph)
            if candidate and len(candidate.lines) > 1:
                # Check if this candidate is a duplicate
                if not self._is_duplicate_candidate(candidate, candidates):
                    candidates.append(candidate)

        return candidates

    def _build_connectivity_graph(self, lines: List[LevelingLine]) -> Dict[str, List[Tuple[LevelingLine, str]]]:
        """
        Build graph of line connections.

        Returns:
            Dict mapping point_id -> [(line, other_endpoint), ...]
        """
        graph = defaultdict(list)

        for line in lines:
            if not line.is_used:
                continue

            # Add edges for both endpoints
            graph[line.start_point].append((line, line.end_point))
            graph[line.end_point].append((line, line.start_point))

        return graph

    def _find_mergeable_chain(self, start_line: LevelingLine,
                             all_lines: List[LevelingLine],
                             graph: Dict) -> Optional[MergeCandidate]:
        """
        Find a chain of lines that can be merged starting from start_line.

        Uses DFS to explore possible merge paths.
        """
        if not start_line.is_used:
            return None

        # Try to build chain in forward direction
        chain = [start_line]
        reverse_flags = [False]
        current_endpoint = start_line.end_point
        used_lines = {id(start_line)}

        while True:
            # Find lines connected to current endpoint
            next_line = None
            needs_reverse = False

            for connected_line, other_end in graph.get(current_endpoint, []):
                if id(connected_line) in used_lines:
                    continue

                # Check connection type
                if connected_line.start_point == current_endpoint:
                    # Line continues forward: A→B, B→C
                    next_line = connected_line
                    needs_reverse = False
                    current_endpoint = connected_line.end_point
                elif connected_line.end_point == current_endpoint:
                    # Line is reversed: A→B, C→B (need to reverse to B→C)
                    next_line = connected_line
                    needs_reverse = True
                    current_endpoint = connected_line.start_point

                if next_line:
                    break

            if not next_line:
                break

            chain.append(next_line)
            reverse_flags.append(needs_reverse)
            used_lines.add(id(next_line))

        if len(chain) < 2:
            return None

        # Extract common nodes (turning points between segments)
        common_nodes = []
        for i in range(len(chain) - 1):
            if reverse_flags[i]:
                node = chain[i].start_point
            else:
                node = chain[i].end_point
            common_nodes.append(node)

        # Calculate total distance
        total_dist = sum(line.total_distance for line in chain)

        return MergeCandidate(
            lines=chain,
            merge_order=list(range(len(chain))),
            reverse_flags=reverse_flags,
            common_nodes=common_nodes,
            total_distance=total_dist,
            is_valid=True
        )

    def _is_duplicate_candidate(self, candidate: MergeCandidate,
                               existing: List[MergeCandidate]) -> bool:
        """Check if candidate is duplicate of existing ones."""
        candidate_ids = frozenset(id(line) for line in candidate.lines)

        for existing_candidate in existing:
            existing_ids = frozenset(id(line) for line in existing_candidate.lines)
            if candidate_ids == existing_ids:
                return True

        return False

    def merge_lines(self, candidate: MergeCandidate,
                   merged_filename: str = None) -> LevelingLine:
        """
        Merge lines according to candidate specification.

        Args:
            candidate: MergeCandidate describing how to merge
            merged_filename: Optional custom filename for merged line

        Returns:
            New merged LevelingLine
        """
        if not candidate.is_valid or len(candidate.lines) < 2:
            raise ValueError("Cannot merge invalid or single-line candidate")

        # Determine merged filename
        if not merged_filename:
            first_line = candidate.lines[0]
            last_line = candidate.lines[-1]

            # Determine actual start/end after reversal
            if candidate.reverse_flags[0]:
                start = first_line.end_point
            else:
                start = first_line.start_point

            if candidate.reverse_flags[-1]:
                end = last_line.start_point
            else:
                end = last_line.end_point

            merged_filename = f"MERGED_{start}-{end}"

        # Collect all setups in correct order
        merged_setups = []
        cumulative_height = 0.0
        setup_counter = 1

        for i, line in enumerate(candidate.lines):
            needs_reverse = candidate.reverse_flags[i]

            # Get setups (create copy to avoid modifying originals)
            if needs_reverse:
                # Create reversed copy of line
                line_copy = line.copy()
                line_copy.toggle_direction()
                line_setups = line_copy.setups
            else:
                line_setups = [self._copy_setup(s) for s in line.setups]

            # Renumber setups and update cumulative heights
            for setup in line_setups:
                setup.setup_number = setup_counter
                if setup.height_diff is not None:
                    cumulative_height += setup.height_diff
                    setup.cumulative_height = cumulative_height
                merged_setups.append(setup)
                setup_counter += 1

        # Determine start/end points
        first_line = candidate.lines[0]
        last_line = candidate.lines[-1]

        if candidate.reverse_flags[0]:
            merged_start = first_line.end_point
        else:
            merged_start = first_line.start_point

        if candidate.reverse_flags[-1]:
            merged_end = last_line.start_point
        else:
            merged_end = last_line.end_point

        # Create merged line
        merged_line = LevelingLine(
            filename=merged_filename,
            start_point=merged_start,
            end_point=merged_end,
            setups=merged_setups,
            method="BF",  # Default to BF
            date=candidate.lines[0].date,  # Use first line's date
            instrument_id=candidate.lines[0].instrument_id,
            is_used=True,
            original_direction="BF"
        )

        # Calculate totals
        merged_line.calculate_totals()
        merged_line.status = LineStatus.VALID

        # Record merge history (Item 14: State management)
        self.merge_history.append({
            'merged_line': merged_line,
            'source_lines': candidate.lines,
            'reverse_flags': candidate.reverse_flags,
            'common_nodes': candidate.common_nodes
        })

        logger.info(f"Merged {len(candidate.lines)} lines into {merged_filename}")

        return merged_line

    def _copy_setup(self, setup: StationSetup) -> StationSetup:
        """Create a copy of a setup."""
        return StationSetup(
            setup_number=setup.setup_number,
            from_point=setup.from_point,
            to_point=setup.to_point,
            backsight_reading=setup.backsight_reading,
            foresight_reading=setup.foresight_reading,
            distance_back=setup.distance_back,
            distance_fore=setup.distance_fore,
            height_diff=setup.height_diff,
            cumulative_height=setup.cumulative_height,
            temperature=setup.temperature,
            is_used=setup.is_used
        )

    def apply_merge(self, candidate: MergeCandidate,
                   target_list: List[LevelingLine],
                   merged_filename: str = None) -> LevelingLine:
        """
        Apply merge and update state management (Item 14).

        Creates merged line and marks originals as excluded.

        Args:
            candidate: MergeCandidate to apply
            target_list: List to add merged line to (usually self.lines)
            merged_filename: Optional custom filename

        Returns:
            The newly created merged line
        """
        # Create merged line
        merged_line = self.merge_lines(candidate, merged_filename)

        # Item 14: Mark original lines as excluded
        for line in candidate.lines:
            line.is_used = False
            line.status = LineStatus.EXCLUDED_FROM_MERGE
            logger.info(f"Excluded from merge: {line.filename}")

        # Add merged line to target list
        target_list.append(merged_line)

        return merged_line

    def get_merge_summary(self, candidate: MergeCandidate) -> Dict:
        """
        Get summary information about a merge candidate.

        Returns:
            Dictionary with merge details
        """
        first_line = candidate.lines[0]
        last_line = candidate.lines[-1]

        # Determine actual start/end
        if candidate.reverse_flags[0]:
            start = first_line.end_point
        else:
            start = first_line.start_point

        if candidate.reverse_flags[-1]:
            end = last_line.start_point
        else:
            end = last_line.end_point

        return {
            'num_segments': len(candidate.lines),
            'start_point': start,
            'end_point': end,
            'total_distance': candidate.total_distance,
            'total_setups': sum(len(line.setups) for line in candidate.lines),
            'segments': [
                {
                    'filename': line.filename,
                    'direction': f"{line.start_point}→{line.end_point}",
                    'needs_reversal': candidate.reverse_flags[i],
                    'distance': line.total_distance,
                    'setups': len(line.setups)
                }
                for i, line in enumerate(candidate.lines)
            ],
            'common_nodes': candidate.common_nodes
        }


# Convenience functions

def find_mergeable_lines(lines: List[LevelingLine]) -> List[MergeCandidate]:
    """
    Find all possible line merges in a list.

    Args:
        lines: List of LevelingLine objects

    Returns:
        List of MergeCandidate objects
    """
    coordinator = LineCoordinator(lines)
    return coordinator.find_merge_candidates()


def merge_selected_lines(lines: List[LevelingLine],
                        indices: List[int],
                        merged_filename: str = None) -> LevelingLine:
    """
    Merge specific lines by index.

    Args:
        lines: Full list of lines
        indices: Indices of lines to merge
        merged_filename: Optional custom filename

    Returns:
        New merged LevelingLine
    """
    coordinator = LineCoordinator(lines)
    selected = [lines[i] for i in indices if i < len(lines)]

    # Build candidate manually
    candidate = coordinator._find_mergeable_chain(selected[0], selected,
                                                 coordinator._build_connectivity_graph(selected))

    if not candidate:
        raise ValueError("Selected lines cannot be merged (no continuous connection)")

    return coordinator.merge_lines(candidate, merged_filename)


if __name__ == '__main__':
    # Test with sample data
    print("Line Coordinator Module - Test")
    print("=" * 50)

    # Would need actual LevelingLine objects to test
    # This is a placeholder for future testing
    print("Module loaded successfully")
