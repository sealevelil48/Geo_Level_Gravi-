"""
Line Adjustment Module (תיאום קו)

Adjusts a single leveling line between two known benchmarks.
"""
from typing import List, Optional, Tuple, Dict
from pathlib import Path
import logging

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from ..config.models import LevelingLine, Benchmark, AdjustmentResult
from ..config.settings import calculate_tolerance
from .height_calculator import (
    calculate_misclosure,
    distribute_misclosure,
    apply_corrections
)


logger = logging.getLogger(__name__)


class LineAdjuster:
    """Performs line adjustment for leveling measurements."""
    
    def __init__(self):
        self.leveling_class = 3  # Default leveling class
    
    def adjust(
        self,
        line: LevelingLine,
        start_benchmark: Benchmark,
        end_benchmark: Benchmark
    ) -> Tuple[LevelingLine, dict]:
        """
        Adjust a leveling line between two known benchmarks.
        
        Args:
            line: LevelingLine to adjust
            start_benchmark: Known start point
            end_benchmark: Known end point
            
        Returns:
            Tuple of (adjusted_line, adjustment_info)
        """
        # Verify points match
        if line.start_point != start_benchmark.point_id:
            raise ValueError(
                f"Start point mismatch: line has {line.start_point}, "
                f"benchmark is {start_benchmark.point_id}"
            )
        
        if line.end_point != end_benchmark.point_id:
            raise ValueError(
                f"End point mismatch: line has {line.end_point}, "
                f"benchmark is {end_benchmark.point_id}"
            )
        
        # Calculate misclosure
        misclosure = calculate_misclosure(
            line.total_height_diff,
            start_benchmark.height,
            end_benchmark.height
        )
        
        misclosure_mm = misclosure * 1000
        
        # Calculate allowable tolerance
        tolerance_mm = calculate_tolerance(line.total_distance, self.leveling_class)
        
        # Check if within tolerance
        within_tolerance = abs(misclosure_mm) <= tolerance_mm
        
        # Distribute corrections
        corrections = distribute_misclosure(line, misclosure, method='proportional')
        
        # Apply corrections
        adjusted_line = apply_corrections(line, corrections)
        
        # Calculate adjusted heights for intermediate points
        intermediate_heights = self._calculate_intermediate_heights(
            adjusted_line,
            start_benchmark.height
        )
        
        adjustment_info = {
            'misclosure_m': misclosure,
            'misclosure_mm': misclosure_mm,
            'tolerance_mm': tolerance_mm,
            'within_tolerance': within_tolerance,
            'start_height': start_benchmark.height,
            'end_height': end_benchmark.height,
            'computed_dh': line.total_height_diff,
            'expected_dh': end_benchmark.height - start_benchmark.height,
            'intermediate_heights': intermediate_heights,
            'corrections': corrections
        }
        
        return adjusted_line, adjustment_info
    
    def _calculate_intermediate_heights(
        self,
        line: LevelingLine,
        start_height: float
    ) -> Dict[str, float]:
        """
        Calculate adjusted heights for intermediate turning points.
        
        Args:
            line: Adjusted LevelingLine
            start_height: Known height of start point
            
        Returns:
            Dictionary mapping point ID to height
        """
        heights = {line.start_point: start_height}
        current_height = start_height
        
        for setup in line.setups:
            if setup.height_diff is not None:
                current_height += setup.height_diff
            heights[setup.to_point] = current_height
        
        return heights
    
    def adjust_multiple_runs(
        self,
        lines: List[LevelingLine],
        start_benchmark: Benchmark,
        end_benchmark: Benchmark
    ) -> Tuple[float, float, dict]:
        """
        Adjust and average multiple runs between same benchmarks.
        
        Args:
            lines: List of LevelingLine objects (forward and/or backward runs)
            start_benchmark: Known start point
            end_benchmark: Known end point
            
        Returns:
            Tuple of (mean_dh, bf_diff_mm, adjustment_info)
        """
        if not lines:
            raise ValueError("No lines provided")
        
        adjusted_dhs = []
        total_bf_diff = 0
        
        for line in lines:
            # Determine if forward or backward run
            is_forward = (line.start_point == start_benchmark.point_id and
                         line.end_point == end_benchmark.point_id)
            is_backward = (line.start_point == end_benchmark.point_id and
                          line.end_point == start_benchmark.point_id)
            
            if is_forward:
                adjusted_dhs.append(line.total_height_diff)
            elif is_backward:
                # Reverse the sign for backward run
                adjusted_dhs.append(-line.total_height_diff)
            else:
                logger.warning(f"Line {line.filename} does not match endpoints")
        
        if not adjusted_dhs:
            raise ValueError("No matching lines found")
        
        # Calculate mean
        mean_dh = sum(adjusted_dhs) / len(adjusted_dhs)
        
        # Calculate BF difference if we have both directions
        if len(adjusted_dhs) >= 2:
            # BF diff is the spread between measurements
            bf_diff_mm = (max(adjusted_dhs) - min(adjusted_dhs)) * 1000
        else:
            bf_diff_mm = 0
        
        adjustment_info = {
            'num_runs': len(lines),
            'individual_dhs': adjusted_dhs,
            'mean_dh': mean_dh,
            'bf_diff_mm': bf_diff_mm,
            'expected_dh': end_benchmark.height - start_benchmark.height,
            'misclosure_mm': (mean_dh - (end_benchmark.height - start_benchmark.height)) * 1000
        }
        
        return mean_dh, bf_diff_mm, adjustment_info


def adjust_single_line(
    line: LevelingLine,
    start_height: float,
    end_height: float
) -> Tuple[LevelingLine, dict]:
    """
    Convenience function to adjust a single line.
    
    Args:
        line: LevelingLine to adjust
        start_height: Known height of start point
        end_height: Known height of end point
        
    Returns:
        Tuple of (adjusted_line, adjustment_info)
    """
    start_bm = Benchmark(point_id=line.start_point, height=start_height)
    end_bm = Benchmark(point_id=line.end_point, height=end_height)
    
    adjuster = LineAdjuster()
    return adjuster.adjust(line, start_bm, end_bm)
