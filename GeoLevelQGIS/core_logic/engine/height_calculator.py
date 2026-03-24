"""
Height Calculator Module

Core height difference calculations for leveling.
"""
from typing import List, Dict, Optional, Tuple
from pathlib import Path
import numpy as np
import logging

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from ..config.models import LevelingLine, StationSetup, MeasurementSummary
from ..config.settings import calculate_tolerance


logger = logging.getLogger(__name__)


def calculate_height_diff(backsight: float, foresight: float) -> float:
    """
    Calculate height difference from rod readings.
    
    dH = Backsight - Foresight
    
    Positive dH means the foresight point is higher than the backsight point.
    
    Args:
        backsight: Backsight rod reading in meters
        foresight: Foresight rod reading in meters
        
    Returns:
        Height difference in meters
    """
    return backsight - foresight


def calculate_line_totals(line: LevelingLine) -> Tuple[float, float]:
    """
    Calculate total distance and height difference for a line.
    
    Args:
        line: LevelingLine object
        
    Returns:
        Tuple of (total_distance, total_height_diff)
    """
    total_distance = 0.0
    total_height_diff = 0.0
    
    for setup in line.setups:
        # Average distance for each setup
        setup_dist = (setup.distance_back + setup.distance_fore) / 2
        total_distance += setup_dist
        
        # Height difference
        if setup.height_diff is not None:
            total_height_diff += setup.height_diff
        elif setup.backsight_reading and setup.foresight_reading:
            dh = calculate_height_diff(setup.backsight_reading, setup.foresight_reading)
            total_height_diff += dh
    
    return total_distance, total_height_diff


def calculate_misclosure(
    computed_dh: float,
    start_height: float,
    end_height: float
) -> float:
    """
    Calculate misclosure for a leveling line.
    
    Misclosure = Computed_dH - (End_Height - Start_Height)
    
    Args:
        computed_dh: Sum of all height differences in meters
        start_height: Known height of start point in meters
        end_height: Known height of end point in meters
        
    Returns:
        Misclosure in meters (positive = too high, negative = too low)
    """
    expected_dh = end_height - start_height
    return computed_dh - expected_dh


def calculate_allowable_misclosure(distance_m: float, leveling_class: int = 3) -> float:
    """
    Calculate allowable misclosure based on distance.
    
    Args:
        distance_m: Total line distance in meters
        leveling_class: Leveling order (1-4)
        
    Returns:
        Allowable misclosure in millimeters
    """
    return calculate_tolerance(distance_m, leveling_class)


def distribute_misclosure(
    line: LevelingLine,
    misclosure: float,
    method: str = 'proportional'
) -> List[float]:
    """
    Distribute misclosure correction across setups.
    
    Methods:
        - 'proportional': Distribute proportional to distance
        - 'equal': Distribute equally across all setups
    
    Args:
        line: LevelingLine object
        misclosure: Misclosure in meters (to be removed)
        method: Distribution method
        
    Returns:
        List of corrections for each setup (in meters)
    """
    n_setups = len(line.setups)
    if n_setups == 0:
        return []
    
    corrections = []
    
    if method == 'equal':
        # Equal distribution
        correction_per_setup = -misclosure / n_setups
        corrections = [correction_per_setup] * n_setups
        
    elif method == 'proportional':
        # Proportional to distance
        total_distance = line.total_distance
        if total_distance == 0:
            return [0.0] * n_setups
        
        for setup in line.setups:
            setup_dist = (setup.distance_back + setup.distance_fore) / 2
            proportion = setup_dist / total_distance
            correction = -misclosure * proportion
            corrections.append(correction)
    
    return corrections


def apply_corrections(line: LevelingLine, corrections: List[float]) -> LevelingLine:
    """
    Apply corrections to height differences and recalculate cumulative heights.
    
    Args:
        line: LevelingLine object (modified in place)
        corrections: List of corrections for each setup
        
    Returns:
        Modified LevelingLine
    """
    if len(corrections) != len(line.setups):
        raise ValueError("Number of corrections must match number of setups")
    
    cumulative_height = 0.0
    
    for setup, correction in zip(line.setups, corrections):
        # Apply correction to height difference
        if setup.height_diff is not None:
            setup.height_diff += correction
        
        # Update cumulative height
        if setup.height_diff is not None:
            cumulative_height += setup.height_diff
        setup.cumulative_height = cumulative_height
    
    # Recalculate total
    line.total_height_diff = cumulative_height
    
    return line


def create_measurement_summary(
    line: LevelingLine,
    bf_diff_mm: float = 0.0,
    year_month: str = ""
) -> MeasurementSummary:
    """
    Create a summary record for export.
    
    Args:
        line: LevelingLine object
        bf_diff_mm: BF difference in millimeters
        year_month: Year/month code (MMYY format)
        
    Returns:
        MeasurementSummary object
    """
    return MeasurementSummary(
        from_point=line.start_point,
        to_point=line.end_point,
        height_diff=line.total_height_diff,
        distance=line.total_distance,
        num_setups=line.num_setups,
        bf_diff=bf_diff_mm,
        year_month=year_month,
        source_file=line.filename
    )


def check_bf_consistency(
    forward_dh: float,
    backward_dh: float,
    tolerance_mm: float = None
) -> Tuple[bool, float]:
    """
    Check consistency between forward and backward measurements.
    
    Args:
        forward_dh: Height difference from forward run (A to B)
        backward_dh: Height difference from backward run (B to A)
        tolerance_mm: Maximum allowed difference in mm (optional)
        
    Returns:
        Tuple of (is_consistent, difference_mm)
    """
    # The backward run should have opposite sign
    diff_mm = (forward_dh + backward_dh) * 1000  # Convert to mm
    
    if tolerance_mm is None:
        return True, abs(diff_mm)
    
    return abs(diff_mm) <= tolerance_mm, abs(diff_mm)


def merge_bf_measurements(
    forward_line: LevelingLine,
    backward_line: LevelingLine
) -> Tuple[float, float]:
    """
    Merge forward and backward measurements into a single value.
    
    Args:
        forward_line: Forward (BF) measurement
        backward_line: Backward (FB) measurement
        
    Returns:
        Tuple of (mean_dh, bf_diff_mm)
    """
    forward_dh = forward_line.total_height_diff
    backward_dh = backward_line.total_height_diff
    
    # Mean of forward and negative of backward
    mean_dh = (forward_dh - backward_dh) / 2
    
    # BF difference in mm
    bf_diff = (forward_dh + backward_dh) * 1000
    
    return mean_dh, bf_diff
