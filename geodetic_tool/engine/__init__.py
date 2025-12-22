"""
Engine Package

Core geodetic calculation modules.
"""
from .height_calculator import (
    calculate_height_diff,
    calculate_line_totals,
    calculate_misclosure,
    calculate_allowable_misclosure,
    distribute_misclosure,
    apply_corrections,
    create_measurement_summary,
    check_bf_consistency,
    merge_bf_measurements
)

from .line_adjustment import (
    LineAdjuster,
    adjust_single_line
)

from .least_squares import (
    LeastSquaresAdjuster,
    simple_adjustment
)

from .loop_detector import (
    Loop,
    NetworkGraph,
    LoopAnalyzer,
    detect_double_runs
)

__all__ = [
    # Height calculator
    'calculate_height_diff',
    'calculate_line_totals',
    'calculate_misclosure',
    'calculate_allowable_misclosure',
    'distribute_misclosure',
    'apply_corrections',
    'create_measurement_summary',
    'check_bf_consistency',
    'merge_bf_measurements',
    
    # Line adjustment
    'LineAdjuster',
    'adjust_single_line',
    
    # Least squares
    'LeastSquaresAdjuster',
    'simple_adjustment',
    
    # Loop detection
    'Loop',
    'NetworkGraph',
    'LoopAnalyzer',
    'detect_double_runs',
]
