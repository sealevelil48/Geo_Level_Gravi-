"""
Validators Package

Validation logic for geodetic leveling data.
Updated to comply with Survey of Israel Directive ג2 (2021).
"""
import re
from pathlib import Path
from typing import List, Optional, Tuple
import logging

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from ..config.models import (
    LevelingLine, ValidationResult, LineStatus, StationSetup
)
from ..config.settings import (
    get_settings, is_benchmark, is_turning_point, calculate_tolerance
)
from ..config.israel_survey_regulations import (
    get_class_parameters, calculate_new_tolerance, MeasurementType
)


logger = logging.getLogger(__name__)


class LevelingValidator:
    """
    Validator for leveling lines.

    Validates according to Survey of Israel Directive ג2 (06/06/2021).
    Supports accuracy classes H1-H6 with specific requirements for each.
    """

    def __init__(self, leveling_class: int = 3, use_new_regulations: bool = True):
        """
        Initialize validator.

        Args:
            leveling_class: Accuracy class (1-6), defaults to H3
            use_new_regulations: Use new 2021 regulations (recommended)
        """
        self.settings = get_settings()
        self.leveling_class = leveling_class
        self.use_new_regulations = use_new_regulations

        if use_new_regulations:
            try:
                self.class_params = get_class_parameters(leveling_class)
            except ValueError as e:
                logger.warning(f"Failed to load class parameters: {e}. Using defaults.")
                self.class_params = None
        else:
            self.class_params = None
    
    def validate(self, line: LevelingLine) -> ValidationResult:
        """
        Perform all validation checks on a leveling line.

        Args:
            line: LevelingLine to validate

        Returns:
            ValidationResult with all checks
        """
        result = ValidationResult(is_valid=True)

        # Standard checks
        result.endpoint_valid = self._check_endpoint(line, result)
        result.naming_valid = self._check_naming(line, result)
        result.data_complete = self._check_completeness(line, result)

        # New regulation-based checks (if enabled)
        if self.use_new_regulations and self.class_params:
            self._check_line_length(line, result)
            self._check_sight_distances(line, result)
            self._check_measurement_method(line, result)
            self._check_distance_balance(line, result)

        # Tolerance check (uses new formula if available)
        result.tolerance_valid = self._check_tolerance(line, result)

        return result
    
    def _check_endpoint(self, line: LevelingLine, result: ValidationResult) -> bool:
        """Check if end point is a valid benchmark."""
        if not line.end_point:
            result.add_error("No end point defined")
            return False
        
        if is_turning_point(line.end_point):
            result.add_error(
                f"End point '{line.end_point}' is a turning point (numeric). "
                f"Leveling line must end on a named benchmark."
            )
            line.status = LineStatus.INVALID_ENDPOINT
            return False
        
        if not is_benchmark(line.end_point):
            result.add_warning(
                f"End point '{line.end_point}' may not be a valid benchmark name"
            )
        
        return True
    
    def _check_naming(self, line: LevelingLine, result: ValidationResult) -> bool:
        """Check if filename matches internal point IDs."""
        filename = line.filename.lower()
        start = line.start_point.lower() if line.start_point else ""
        end = line.end_point.lower() if line.end_point else ""
        
        # Extract potential point names from filename
        # Common patterns: "3747MPI-5469MPI", "KMA58", etc.
        
        # Check if both start and end points appear in filename
        has_start = start and start in filename
        has_end = end and end in filename
        
        # If filename contains a separator, check order
        for sep in ['-', '_', ' to ']:
            if sep in filename:
                parts = filename.split(sep)
                if len(parts) >= 2:
                    file_start = parts[0].strip()
                    file_end = parts[-1].strip().split('.')[0]  # Remove extension
                    
                    # Check for front-to-back error
                    if start in file_end and end in file_start:
                        result.add_error(
                            f"Front-to-Back naming error detected. "
                            f"File '{line.filename}' suggests {file_start}→{file_end}, "
                            f"but data shows {line.start_point}→{line.end_point}"
                        )
                        line.status = LineStatus.NAMING_ERROR
                        return False
        
        return True
    
    def _check_completeness(self, line: LevelingLine, result: ValidationResult) -> bool:
        """Check if data is complete."""
        if not line.setups:
            result.add_error("No measurement setups found")
            line.status = LineStatus.INCOMPLETE
            return False
        
        if len(line.setups) < self.settings.validation.min_setups:
            result.add_warning(
                f"Only {len(line.setups)} setups found. "
                f"Minimum expected: {self.settings.validation.min_setups}"
            )
        
        # Check for missing height differences
        missing_dh = sum(1 for s in line.setups if s.height_diff is None)
        if missing_dh > 0:
            result.add_warning(
                f"{missing_dh} setups missing height difference calculations"
            )
        
        return True
    
    def _check_line_length(self, line: LevelingLine, result: ValidationResult) -> bool:
        """
        Check if line length is within class limits (נספח ב', סעיף 1.2).

        Args:
            line: LevelingLine to check
            result: ValidationResult to update

        Returns:
            True if valid or no limit, False if exceeds
        """
        if not self.class_params:
            return True

        distance_km = line.total_distance / 1000.0
        is_valid, message = self.class_params.validate_line_length(distance_km)

        if not is_valid:
            result.add_error(message)
            return False

        return True

    def _check_sight_distances(self, line: LevelingLine, result: ValidationResult) -> bool:
        """
        Check if sight distances are within class limits (נספח ב', סעיף 2.1, 3.1).

        Validates individual setup distances against maximum allowed
        for geometric or trigonometric leveling.

        Args:
            line: LevelingLine to check
            result: ValidationResult to update

        Returns:
            True if all distances valid, False otherwise
        """
        if not self.class_params:
            return True

        # Determine measurement type from method
        # For now, assume geometric if method is BF/BFFB
        # In future, could be enhanced with actual instrument type
        measurement_type = MeasurementType.GEOMETRIC

        all_valid = True
        max_violations = 5  # Limit error messages

        for i, setup in enumerate(line.setups):
            if i >= max_violations:
                result.add_warning(f"... and {len(line.setups) - i} more setups not checked")
                break

            # Check backsight distance
            if setup.distance_back is not None:
                is_valid, message = self.class_params.validate_sight_distance(
                    setup.distance_back, measurement_type
                )
                if not is_valid:
                    result.add_error(f"Setup {setup.setup_number} backsight: {message}")
                    all_valid = False

            # Check foresight distance
            if setup.distance_fore is not None:
                is_valid, message = self.class_params.validate_sight_distance(
                    setup.distance_fore, measurement_type
                )
                if not is_valid:
                    result.add_error(f"Setup {setup.setup_number} foresight: {message}")
                    all_valid = False

        return all_valid

    def _check_measurement_method(self, line: LevelingLine, result: ValidationResult) -> bool:
        """
        Check if measurement method meets class requirements (נספח ב', סעיף 1.5).

        Args:
            line: LevelingLine to check
            result: ValidationResult to update

        Returns:
            True if method is valid, False otherwise
        """
        if not self.class_params:
            return True

        is_valid, message = self.class_params.validate_method(line.method)

        if not is_valid:
            result.add_error(message)
            return False

        return True

    def _check_distance_balance(self, line: LevelingLine, result: ValidationResult) -> bool:
        """
        Check if backsight/foresight distances are balanced (נספח ב', סעיף 1.3, 1.4).

        Validates:
        1. Individual setup balance (max difference per setup)
        2. Cumulative balance over entire line

        Args:
            line: LevelingLine to check
            result: ValidationResult to update

        Returns:
            True if balanced, False otherwise
        """
        if not self.class_params:
            return True

        cumulative_diff = 0.0
        all_valid = True

        for setup in line.setups:
            if setup.distance_back is None or setup.distance_fore is None:
                continue

            # Check individual setup balance
            diff = abs(setup.distance_back - setup.distance_fore)
            if diff > self.class_params.max_single_distance_imbalance_m:
                result.add_error(
                    f"Setup {setup.setup_number}: Distance imbalance {diff:.2f} m "
                    f"exceeds limit {self.class_params.max_single_distance_imbalance_m} m"
                )
                all_valid = False

            # Accumulate for cumulative check
            cumulative_diff += (setup.distance_back - setup.distance_fore)

        # Check cumulative balance
        if abs(cumulative_diff) > self.class_params.max_cumulative_distance_imbalance_m:
            result.add_error(
                f"Cumulative distance imbalance {abs(cumulative_diff):.2f} m "
                f"exceeds limit {self.class_params.max_cumulative_distance_imbalance_m} m"
            )
            all_valid = False

        return all_valid

    def _check_tolerance(self, line: LevelingLine, result: ValidationResult,
                         known_dh: float = None) -> bool:
        """
        Check if misclosure is within tolerance.

        Uses new Survey of Israel formula if enabled:
        Tolerance = k × √(Distance_km)

        Args:
            line: LevelingLine to check
            result: ValidationResult to update
            known_dh: Known height difference between endpoints (optional)
        """
        if known_dh is None:
            # Cannot check without known heights
            return True

        # Calculate misclosure
        computed_dh = line.total_height_diff
        misclosure_m = abs(computed_dh - known_dh)
        misclosure_mm = misclosure_m * 1000

        # Calculate allowable tolerance (using new or old formula)
        if self.use_new_regulations and self.class_params:
            distance_km = line.total_distance / 1000.0
            tolerance_mm = self.class_params.get_tolerance_mm(distance_km)
            formula_used = f"±{self.class_params.tolerance_coefficient}√L"
        else:
            tolerance_mm = calculate_tolerance(line.total_distance, self.leveling_class)
            formula_used = "legacy"

        line.misclosure = misclosure_mm

        if misclosure_mm > tolerance_mm:
            result.add_error(
                f"Misclosure {misclosure_mm:.2f} mm exceeds tolerance {tolerance_mm:.2f} mm "
                f"(Class {self.class_params.class_name if self.class_params else self.leveling_class}, "
                f"formula: {formula_used})"
            )
            line.status = LineStatus.EXCEEDED_TOLERANCE
            return False

        return True


class BatchValidator:
    """Validator for multiple leveling lines."""

    def __init__(self, leveling_class: int = 3, use_new_regulations: bool = True):
        """
        Initialize batch validator.

        Args:
            leveling_class: Accuracy class (1-6), defaults to H3
            use_new_regulations: Use new 2021 regulations (recommended)
        """
        self.validator = LevelingValidator(leveling_class, use_new_regulations)
    
    def validate_batch(self, lines: List[LevelingLine]) -> List[Tuple[LevelingLine, ValidationResult]]:
        """
        Validate multiple leveling lines.
        
        Args:
            lines: List of LevelingLine objects
            
        Returns:
            List of tuples (LevelingLine, ValidationResult)
        """
        results = []
        for line in lines:
            result = self.validator.validate(line)
            results.append((line, result))
        return results
    
    def get_summary(self, results: List[ValidationResult]) -> dict:
        """
        Get summary statistics of validation results.
        
        Args:
            results: List of ValidationResult objects
            
        Returns:
            Dictionary with summary statistics
        """
        total = len(results)
        valid = sum(1 for r in results if r.is_valid)
        invalid = total - valid
        
        # Count specific issues
        endpoint_issues = sum(1 for r in results if not r.endpoint_valid)
        naming_issues = sum(1 for r in results if not r.naming_valid)
        tolerance_issues = sum(1 for r in results if not r.tolerance_valid)
        
        return {
            'total': total,
            'valid': valid,
            'invalid': invalid,
            'endpoint_issues': endpoint_issues,
            'naming_issues': naming_issues,
            'tolerance_issues': tolerance_issues,
            'pass_rate': valid / total if total > 0 else 0
        }


def validate_line(line: LevelingLine) -> ValidationResult:
    """
    Convenience function to validate a single leveling line.
    
    Args:
        line: LevelingLine to validate
        
    Returns:
        ValidationResult
    """
    validator = LevelingValidator()
    return validator.validate(line)


def check_endpoint(point_id: str) -> Tuple[bool, str]:
    """
    Check if a point ID is a valid endpoint (benchmark).
    
    Args:
        point_id: Point ID to check
        
    Returns:
        Tuple of (is_valid, message)
    """
    if not point_id:
        return False, "Point ID is empty"
    
    if is_turning_point(point_id):
        return False, f"'{point_id}' is a turning point (numeric only)"
    
    if is_benchmark(point_id):
        return True, f"'{point_id}' is a valid benchmark"
    
    return False, f"'{point_id}' format is unclear"
