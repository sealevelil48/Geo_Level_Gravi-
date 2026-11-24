"""
Loop Detector Module

Detects closed loops in leveling networks for adjustment and quality control.
Updated to use Survey of Israel Directive ג2 (2021) regulations.
"""
from typing import List, Dict, Set, Tuple, Optional
from dataclasses import dataclass, field
from collections import defaultdict
import math

from geodetic_tool.config.models import LevelingLine, MeasurementSummary
from geodetic_tool.config.israel_survey_regulations import get_class_parameters, CLASS_REGISTRY


@dataclass
class Loop:
    """Represents a closed loop in the leveling network."""
    lines: List[LevelingLine]
    points: List[str]  # Ordered list of points in the loop
    total_distance: float = 0.0
    misclosure: float = 0.0  # Theoretical misclosure (should be 0 for closed loop)
    allowable_tolerance: float = 0.0  # mm
    
    @property
    def is_closed(self) -> bool:
        """Check if loop is properly closed."""
        return len(self.points) > 2 and self.points[0] == self.points[-1]
    
    @property
    def num_lines(self) -> int:
        return len(self.lines)
    
    @property
    def tolerance_class(self) -> int:
        """
        Determine class based on misclosure using new regulations (H1-H6).

        Returns:
            Class number (1-6). If exceeds all tolerances, returns 6 (least precise class).
            Note: Removed "exceed all" (0) logic per Item 10 - always returns valid class.
        """
        dist_km = self.total_distance / 1000.0
        misclosure_mm = abs(self.misclosure * 1000)

        # Check against all classes from strictest (H1) to most lenient (H6)
        for class_num in range(1, 7):
            try:
                params = get_class_parameters(class_num)
                allowed_mm = params.get_tolerance_mm(dist_km)
                if misclosure_mm <= allowed_mm:
                    return class_num
            except ValueError:
                continue

        # If exceeds all, return H6 (not 0!)
        # The caller should use check_tolerance() to determine if it exceeds target class
        return 6

    def check_tolerance(self, target_class: int = 3) -> tuple[bool, float, float]:
        """
        Check if loop satisfies target class tolerance (Item 10).

        Args:
            target_class: Target class (1-6), default H3

        Returns:
            (within_tolerance, misclosure_mm, tolerance_mm)
        """
        dist_km = self.total_distance / 1000.0
        misclosure_mm = abs(self.misclosure * 1000)

        params = get_class_parameters(target_class)
        tolerance_mm = params.get_tolerance_mm(dist_km)

        within_tolerance = misclosure_mm <= tolerance_mm

        return within_tolerance, misclosure_mm, tolerance_mm
    
    def calculate_misclosure(self, target_class: int = 3):
        """
        Calculate the misclosure of the loop.

        Args:
            target_class: Target accuracy class (1-6), defaults to H3 (Item 10 - changed from H1)
        """
        total_dh = 0.0
        total_dist = 0.0

        for line in self.lines:
            # Determine if line needs to be reversed based on loop direction
            total_dh += line.total_height_diff
            total_dist += line.total_distance

        self.misclosure = total_dh
        self.total_distance = total_dist

        # Calculate allowable tolerance using target class (Item 10: changed default to H3)
        try:
            params = get_class_parameters(target_class)
            dist_km = total_dist / 1000.0
            self.allowable_tolerance = params.get_tolerance_mm(dist_km)
        except ValueError:
            # Fallback to H3 if invalid class (not H1!)
            dist_km = total_dist / 1000.0
            self.allowable_tolerance = 10.0 * math.sqrt(dist_km)
    
    def __str__(self) -> str:
        points_str = " → ".join(self.points)
        return f"Loop: {points_str} (Misclosure: {self.misclosure*1000:.2f}mm, Tolerance: ±{self.allowable_tolerance:.2f}mm)"


class NetworkGraph:
    """Graph representation of leveling network for loop detection."""
    
    def __init__(self):
        self.adjacency: Dict[str, List[Tuple[str, LevelingLine]]] = defaultdict(list)
        self.lines: List[LevelingLine] = []
        self.points: Set[str] = set()
    
    def add_line(self, line: LevelingLine):
        """Add a leveling line to the network."""
        if not line.start_point or not line.end_point:
            return
        
        self.lines.append(line)
        self.points.add(line.start_point)
        self.points.add(line.end_point)
        
        # Add edges in both directions (undirected graph)
        self.adjacency[line.start_point].append((line.end_point, line))
        self.adjacency[line.end_point].append((line.start_point, line))
    
    def add_lines(self, lines: List[LevelingLine]):
        """Add multiple leveling lines to the network."""
        for line in lines:
            self.add_line(line)
    
    def get_neighbors(self, point: str) -> List[Tuple[str, LevelingLine]]:
        """Get all neighbors of a point."""
        return self.adjacency.get(point, [])
    
    def find_all_loops(self, max_loop_size: int = 10) -> List[Loop]:
        """
        Find all closed loops in the network using DFS.
        
        Args:
            max_loop_size: Maximum number of lines in a loop to search for
            
        Returns:
            List of Loop objects
        """
        loops = []
        visited_loops: Set[frozenset] = set()  # To avoid duplicate loops
        
        for start_point in self.points:
            found_loops = self._find_loops_from_point(start_point, max_loop_size)
            
            for loop in found_loops:
                # Create a canonical representation of the loop
                loop_signature = frozenset(id(line) for line in loop.lines)
                
                if loop_signature not in visited_loops:
                    visited_loops.add(loop_signature)
                    loop.calculate_misclosure()
                    loops.append(loop)
        
        return loops
    
    def _find_loops_from_point(self, start: str, max_size: int) -> List[Loop]:
        """Find all loops starting from a given point."""
        loops = []
        
        def dfs(current: str, path: List[str], used_lines: List[LevelingLine], depth: int):
            if depth > max_size:
                return
            
            for neighbor, line in self.get_neighbors(current):
                # Check if this line was already used
                if line in used_lines:
                    continue
                
                # Found a loop back to start
                if neighbor == start and len(used_lines) >= 2:
                    new_path = path + [neighbor]
                    new_lines = used_lines + [line]
                    loop = Loop(
                        lines=new_lines.copy(),
                        points=new_path.copy()
                    )
                    loops.append(loop)
                    continue
                
                # Continue DFS if neighbor not in path (except start)
                if neighbor not in path[1:]:  # Allow return to start only
                    dfs(neighbor, path + [neighbor], used_lines + [line], depth + 1)
        
        dfs(start, [start], [], 0)
        return loops
    
    def find_minimum_loops(self) -> List[Loop]:
        """
        Find the set of independent minimum loops (basis loops).
        
        For a network with V vertices, E edges, and C connected components,
        the number of independent loops is: L = E - V + C
        """
        all_loops = self.find_all_loops()
        # Prefer the shortest loops, then keep a loop only if it is
        # independent of the loops already chosen (GF(2) cycle-space basis).
        all_loops.sort(key=lambda x: (x.num_lines, x.total_distance))

        num_vertices = len(self.points)
        num_edges = len(self.lines)
        num_components = self._count_components()
        expected_loops = max(0, num_edges - num_vertices + num_components)

        index = {id(ln): i for i, ln in enumerate(self.lines)}
        basis = {}          # pivot bit -> reduced mask
        chosen = []
        for loop in all_loops:
            mask = 0
            for ln in loop.lines:
                i = index.get(id(ln))
                if i is not None:
                    mask ^= (1 << i)
            while mask:
                pivot = mask.bit_length() - 1
                if pivot in basis:
                    mask ^= basis[pivot]
                else:
                    basis[pivot] = mask
                    chosen.append(loop)
                    break
            if len(chosen) >= expected_loops:
                break
        return chosen

    def _count_components(self) -> int:
        """Count connected components in the network."""
        visited = set()
        components = 0
        
        for point in self.points:
            if point not in visited:
                components += 1
                self._dfs_visit(point, visited)
        
        return components
    
    def _dfs_visit(self, point: str, visited: Set[str]):
        """DFS helper for component counting."""
        visited.add(point)
        for neighbor, _ in self.get_neighbors(point):
            if neighbor not in visited:
                self._dfs_visit(neighbor, visited)


class LoopAnalyzer:
    """Analyzes loops in leveling networks."""
    
    def __init__(self, lines: List[LevelingLine] = None):
        self.graph = NetworkGraph()
        if lines:
            self.graph.add_lines(lines)
    
    def add_line(self, line: LevelingLine):
        """Add a leveling line to the analyzer."""
        self.graph.add_line(line)
    
    def add_lines(self, lines: List[LevelingLine]):
        """Add multiple leveling lines."""
        self.graph.add_lines(lines)
    
    def find_loops(self, max_size: int = 10) -> List[Loop]:
        """Find all loops in the network."""
        return self.graph.find_all_loops(max_size)
    
    def find_basis_loops(self) -> List[Loop]:
        """Find the minimum set of independent loops."""
        return self.graph.find_minimum_loops()
    
    def analyze_double_run(self, line1: LevelingLine, line2: LevelingLine,
                          target_class=3) -> Dict:
        """
        Double-run check of one section (Directive G2 2021, Appendix C).

        line1 is the forward run; line2 the return run (or a repeat in the
        same direction).  delta = dH_fwd + dH_ret (return in its own
        direction), allowed = k*sqrt(L) with L the ONE-direction length in km
        (mean of the two runs), mean dH = (dH_fwd - dH_ret)/2 in the forward
        direction.  Same numbers as network_prep.merge_observations (Step 1).

        Args:
            target_class: 1-6 or 'H1'-'H6' (default H3)
        """
        from geodetic_tool.engine.network_prep import (
            achieved_class, class_number, tolerance_mm)

        ends1 = {line1.start_point, line1.end_point}
        if ends1 != {line2.start_point, line2.end_point} or len(ends1) != 2:
            return {
                'valid': False,
                'error': 'Lines do not form a proper double-run'
            }
        opposite = line1.start_point == line2.end_point
        dh_forward = line1.total_height_diff
        dh_return = line2.total_height_diff
        # line2 reduced to the forward direction
        dh_return_fwd = -dh_return if opposite else dh_return

        misclosure = dh_forward - dh_return_fwd      # = dH_fwd + dH_ret
        misclosure_mm = misclosure * 1000.0
        mean_dh = (dh_forward + dh_return_fwd) / 2.0  # = (dH_fwd - dH_ret)/2
        total_distance = line1.total_distance + line2.total_distance
        one_dir_m = total_distance / 2.0              # App. B 4.1: L one direction

        cls = class_number(target_class)
        tol_mm = tolerance_mm(one_dir_m, cls)
        best = achieved_class(misclosure_mm, one_dir_m)
        return {
            'valid': True,
            'forward_dh': dh_forward,
            'return_dh': dh_return,
            'same_direction': not opposite,
            'misclosure': misclosure,
            'misclosure_mm': misclosure_mm,
            'mean_dh': mean_dh,
            'total_distance': total_distance,
            'distance_one_direction': one_dir_m,
            'target_class': cls,
            'achieved_class': best,
            'tolerance_class': best,  # backward compatibility
            'within_tolerance': abs(misclosure_mm) <= tol_mm + 1e-9,
            'tolerance_mm': tol_mm,
            'class_name': f"H{best}" if best else "Exceeds H6",
        }

    def get_network_summary(self) -> Dict:
        """Get a summary of the network."""
        loops = self.find_loops()
        
        return {
            'num_points': len(self.graph.points),
            'num_lines': len(self.graph.lines),
            'num_loops': len(loops),
            'points': list(self.graph.points),
            'loops': loops
        }


def detect_double_runs(lines: List[LevelingLine]) -> List[Tuple[LevelingLine, LevelingLine]]:
    """
    Double-run pairs: lines measuring the same section (same two end points,
    either direction), unused lines skipped.  The first run of a section is
    the forward run; it is paired with every further run of that section.
    (network_prep.analyze_double_runs gives the full Appendix C check.)
    """
    groups: Dict[frozenset, List[LevelingLine]] = {}
    order = []
    for ln in lines:
        if not getattr(ln, "is_used", True):
            continue
        if not ln.start_point or not ln.end_point or ln.start_point == ln.end_point:
            continue
        key = frozenset((ln.start_point, ln.end_point))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(ln)
    pairs = []
    for key in order:
        runs = groups[key]
        for other in runs[1:]:
            pairs.append((runs[0], other))
    return pairs


if __name__ == '__main__':
    # Test with sample data
    from geodetic_tool.parsers.base_parser import create_parser
    
    # Parse sample files
    sample_files = [
        '/mnt/project/KMA58_DAT.txt',
        '/mnt/project/KMA59_DAT.txt',
    ]
    
    lines = []
    for filepath in sample_files:
        parser = create_parser(filepath)
        if parser:
            line = parser.parse(filepath)
            lines.append(line)
            print(f"Parsed: {line.start_point} → {line.end_point}, dH={line.total_height_diff:.5f}m")
    
    # Analyze network
    analyzer = LoopAnalyzer(lines)
    
    # Check for double-runs
    pairs = detect_double_runs(lines)
    print(f"\nFound {len(pairs)} double-run pair(s)")
    
    for fwd, ret in pairs:
        result = analyzer.analyze_double_run(fwd, ret)
        if result['valid']:
            print(f"\nDouble-run: {fwd.start_point} ↔ {fwd.end_point}")
            print(f"  Forward dH:  {result['forward_dh']*1000:.3f} mm")
            print(f"  Return dH:   {result['return_dh']*1000:.3f} mm")
            print(f"  Misclosure:  {result['misclosure_mm']:.3f} mm")
            print(f"  Mean dH:     {result['mean_dh']*1000:.3f} mm")
            print(f"  Class:       {result['tolerance_class']}")
            print(f"  Within tolerance: {result['within_tolerance']}")
