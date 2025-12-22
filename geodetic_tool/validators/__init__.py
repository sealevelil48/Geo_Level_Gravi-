"""
Validators Package

Validation logic for geodetic leveling data.
"""
import re
from pathlib import Path
from typing import List, Optional, Tuple
import logging

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from ..config.models import (
    LevelingLine, ValidationResult, LineStatus
)
from ..config.settings import (
    get_settings, is_benchmark, is_turning_point, calculate_tolerance
)


logger = logging.getLogger(__name__)


class LevelingValidator:
    """Validator for leveling lines."""
    
    def __init__(self):
        self.settings = get_settings()
    
    def validate(self, line: LevelingLine) -> ValidationResult:
        """
        Perform all validation checks on a leveling line.
        
        Args:
            line: LevelingLine to validate
            
        Returns:
            ValidationResult with all checks
        """
        result = ValidationResult(is_valid=True)
        
        # Check endpoint
        result.endpoint_valid = self._check_endpoint(line, result)
        
        # Check naming convention
        result.naming_valid = self._check_naming(line, result)
        
        # Check data completeness
        result.data_complete = self._check_completeness(line, result)
        
        # Check tolerance if we have known heights
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
    
    def _check_tolerance(self, line: LevelingLine, result: ValidationResult,
                         known_dh: float = None) -> bool:
        """
        Check if misclosure is within tolerance.
        
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
        misclosure = abs(computed_dh - known_dh) * 1000  # Convert to mm
        
        # Calculate allowable tolerance
        tolerance = calculate_tolerance(line.total_distance)
        
        line.misclosure = misclosure
        
        if misclosure > tolerance:
            result.add_error(
                f"Misclosure ({misclosure:.2f} mm) exceeds tolerance ({tolerance:.2f} mm)"
            )
            line.status = LineStatus.EXCEEDED_TOLERANCE
            return False
        
        return True


class BatchValidator:
    """Validator for multiple leveling lines."""
    
    def __init__(self):
        self.validator = LevelingValidator()
    
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
