"""
Loop Detector Module

Detects closed loops in leveling networks for adjustment and quality control.
Updated to use Survey of Israel Directive ג2 (2021) regulations.
"""
from typing import List, Dict, Set, Tuple, Optional
from dataclasses import dataclass, field
from collections import defaultdict
import math

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ..config.models import LevelingLine, MeasurementSummary
from ..config.israel_survey_regulations import get_class_parameters, CLASS_REGISTRY


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
            Class number (1-6), or 0 if exceeds all tolerances
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

        return 0  # Exceeds all tolerances
    
    def calculate_misclosure(self, target_class: int = 1):
        """
        Calculate the misclosure of the loop.

        Args:
            target_class: Target accuracy class (1-6), defaults to H1 (strictest)
        """
        total_dh = 0.0
        total_dist = 0.0

        for line in self.lines:
            # Determine if line needs to be reversed based on loop direction
            total_dh += line.total_height_diff
            total_dist += line.total_distance

        self.misclosure = total_dh
        self.total_distance = total_dist

        # Calculate allowable tolerance using new regulations
        # Default to H1 (strictest class: ±3mm√L)
        try:
            params = get_class_parameters(target_class)
            dist_km = total_dist / 1000.0
            self.allowable_tolerance = params.get_tolerance_mm(dist_km)
        except ValueError:
            # Fallback to H1 if invalid class
            dist_km = total_dist / 1000.0
            self.allowable_tolerance = 3.0 * math.sqrt(dist_km)
    
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
        
        # Sort by number of lines (prefer smaller loops)
        all_loops.sort(key=lambda x: x.num_lines)
        
        # Select independent loops
        # This is a simplified selection - just returns smallest loops
        num_vertices = len(self.points)
        num_edges = len(self.lines)
        num_components = self._count_components()
        
        expected_loops = num_edges - num_vertices + num_components
        
        return all_loops[:max(expected_loops, len(all_loops))]
    
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
                          target_class: int = 1) -> Dict:
        """
        Analyze a double-run (back-and-forth) measurement using new regulations.

        Args:
            line1: Forward measurement
            line2: Return measurement
            target_class: Target accuracy class (1-6), defaults to H1

        Returns:
            Analysis results dictionary
        """
        if not (line1.start_point == line2.end_point and
                line1.end_point == line2.start_point):
            return {
                'valid': False,
                'error': 'Lines do not form a proper double-run'
            }

        dh_forward = line1.total_height_diff
        dh_return = line2.total_height_diff

        # For double-run, sum should be close to 0
        misclosure = dh_forward + dh_return
        misclosure_mm = misclosure * 1000

        # Calculate mean values
        mean_dh = (dh_forward - dh_return) / 2
        total_distance = line1.total_distance + line2.total_distance
        dist_km = total_distance / 1000.0

        # Tolerance for double-run using new regulations
        try:
            params = get_class_parameters(target_class)
            tolerance_mm = params.get_tolerance_mm(dist_km)
        except ValueError:
            # Fallback to H1
            tolerance_mm = 3.0 * math.sqrt(dist_km)

        # Determine achieved class (which class does this measurement satisfy)
        achieved_class = 0
        for class_num in range(1, 7):
            try:
                params = get_class_parameters(class_num)
                allowed_mm = params.get_tolerance_mm(dist_km)
                if abs(misclosure_mm) <= allowed_mm:
                    achieved_class = class_num
                    break
            except ValueError:
                continue

        return {
            'valid': True,
            'forward_dh': dh_forward,
            'return_dh': dh_return,
            'misclosure': misclosure,
            'misclosure_mm': misclosure_mm,
            'mean_dh': mean_dh,
            'total_distance': total_distance,
            'target_class': target_class,
            'achieved_class': achieved_class,
            'tolerance_class': achieved_class,  # For backward compatibility
            'within_tolerance': abs(misclosure_mm) <= tolerance_mm,
            'tolerance_mm': tolerance_mm,
            'class_name': f"H{achieved_class}" if achieved_class > 0 else "Exceeded"
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
    Detect double-run pairs in a list of leveling lines.
    
    Args:
        lines: List of LevelingLine objects
        
    Returns:
        List of tuples (forward, return) for each double-run pair
    """
    pairs = []
    used = set()
    
    for i, line1 in enumerate(lines):
        if i in used:
            continue
        
        for j, line2 in enumerate(lines[i+1:], i+1):
            if j in used:
                continue
            
            # Check if these form a double-run
            if (line1.start_point == line2.end_point and 
                line1.end_point == line2.start_point):
                pairs.append((line1, line2))
                used.add(i)
                used.add(j)
                break
    
    return pairs


if __name__ == '__main__':
    # Test with sample data
    from parsers.base_parser import create_parser
    
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
