"""
Survey of Israel Geodetic Control Regulations (2021)
Based on: "מדידה וחישוב גבהים אורתומטריים בשיטות קרקעיות"
Chapter ג' Geodetic Engineering Control, Directive ג2
Date: 06/06/2021, Edition 1

This module implements the official Israeli Survey regulations for
orthometric height measurement using ground-based methods.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from enum import Enum


class LevelingClass(Enum):
    """
    Leveling accuracy classes as defined by Survey of Israel.

    H1: First-order (highest precision)
    H2: Second-order
    H3: Third-order
    H4: Fourth-order
    H5: Fifth-order
    H6: Sixth-order (lowest precision)
    """
    H1 = 1
    H2 = 2
    H3 = 3
    H4 = 4
    H5 = 5
    H6 = 6


class MeasurementType(Enum):
    """Type of leveling measurement."""
    GEOMETRIC = "geometric"  # איזון גאומטרי - Using level and staff
    TRIGONOMETRIC = "trigonometric"  # איזון טריגונומטרי - Using total station


@dataclass
class ClassParameters:
    """
    Parameters for a specific leveling class.
    All measurements follow Survey of Israel Directive ג2.
    """
    # Basic identification
    class_level: LevelingClass
    class_name: str

    # Tolerance calculation (נספח ב', סעיף 4.1)
    # Formula: Tolerance = coefficient × √(Distance_km)
    # Result in millimeters
    tolerance_coefficient: float  # mm/√km

    # Distance limits (נספח ב', סעיף 1.2)
    max_line_length_km: Optional[float] = None  # Maximum line length in km (None = unlimited)

    # Geometric leveling parameters (נספח א', ב')
    max_sight_distance_geometric_m: float = 100.0  # Maximum distance from level to staff
    min_staff_clearance_m: Optional[float] = None  # Minimum clearance from ground
    min_staff_upper_clearance_m: Optional[float] = None  # Min clearance to top of staff

    # Trigonometric leveling parameters
    max_sight_distance_trigonometric_m: float = 200.0  # Maximum distance to prism

    # Measurement method requirements (נספח ב', סעיף 1.5)
    required_method: str = "BF"  # "BFFB" for H1-H3, "BF" for H4-H6
    min_setups: int = 2  # Minimum number of setups

    # Forward-backward run requirements (נספח ב', סעיף 1.1)
    requires_double_run: bool = True  # הלוך-חזור requirement
    max_fb_difference_mm: Optional[float] = None  # Max diff between forward and backward runs

    # Distance balance requirements (נספח ב', סעיף 1.3, 1.4)
    max_single_distance_imbalance_m: float = 10.0  # Max difference in single setup
    max_cumulative_distance_imbalance_m: float = 15.0  # Max cumulative imbalance

    # Level instrument requirements (נספח א', סעיף 1.2)
    max_instrument_error_mm_per_km: Optional[float] = None  # Manufacturer declared accuracy

    # Time constraints (נספח ב', סעיף 2)
    max_days_for_double_run: Optional[int] = None  # Maximum days to complete forward-backward

    # Special requirements
    requires_invar_staff: bool = False  # אמה עשויה אינוור
    requires_staff_supports: bool = False  # מוטות משען לייצוב האמות
    requires_calibration_monthly: bool = False  # Monthly calibration requirement
    requires_orthometric_correction: bool = False  # Gravity-based orthometric correction

    def get_tolerance_mm(self, distance_km: float) -> float:
        """
        Calculate allowable misclosure tolerance.

        Args:
            distance_km: Distance in kilometers

        Returns:
            Tolerance in millimeters

        Formula (נספח ב', 4.1):
            Tolerance = coefficient × √(Distance_km)
        """
        import math
        return self.tolerance_coefficient * math.sqrt(distance_km)

    def validate_line_length(self, distance_km: float) -> tuple[bool, Optional[str]]:
        """Check if line length is within limits."""
        if self.max_line_length_km is None:
            return True, None

        if distance_km > self.max_line_length_km:
            return False, f"Line length {distance_km:.2f} km exceeds maximum {self.max_line_length_km} km for {self.class_name}"
        return True, None

    def validate_sight_distance(self, distance_m: float, measurement_type: MeasurementType) -> tuple[bool, Optional[str]]:
        """Check if sight distance is within limits."""
        max_dist = (self.max_sight_distance_geometric_m if measurement_type == MeasurementType.GEOMETRIC
                   else self.max_sight_distance_trigonometric_m)

        if distance_m > max_dist:
            return False, f"Sight distance {distance_m:.1f} m exceeds maximum {max_dist} m for {self.class_name} ({measurement_type.value})"
        return True, None

    def validate_method(self, method: str) -> tuple[bool, Optional[str]]:
        """Check if measurement method meets requirements."""
        if self.required_method == "BFFB" and method != "BFFB":
            return False, f"{self.class_name} requires BFFB measurement method (got {method})"
        return True, None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for export/display."""
        return {
            'class': self.class_name,
            'tolerance_coeff_mm_per_sqrt_km': self.tolerance_coefficient,
            'max_line_length_km': self.max_line_length_km,
            'max_sight_geometric_m': self.max_sight_distance_geometric_m,
            'max_sight_trigonometric_m': self.max_sight_distance_trigonometric_m,
            'required_method': self.required_method,
            'requires_double_run': self.requires_double_run,
            'max_fb_diff_mm': self.max_fb_difference_mm,
        }


# ============================================================================
# OFFICIAL CLASS DEFINITIONS (נספח ב' - Appendix B, Rows 17-32)
# Based on Survey of Israel Directive ג2, 06/06/2021
# ============================================================================

# H1 - First Order (Highest Precision)
H1_PARAMETERS = ClassParameters(
    class_level=LevelingClass.H1,
    class_name="H1",
    tolerance_coefficient=3.0,  # ±3mm√L
    max_line_length_km=None,  # No limit (אין)
    max_sight_distance_geometric_m=30.0,
    max_sight_distance_trigonometric_m=80.0,
    min_staff_clearance_m=0.5,
    min_staff_upper_clearance_m=0.4,
    required_method="BFFB",
    requires_double_run=True,
    max_fb_difference_mm=None,  # Checked via tolerance formula
    max_single_distance_imbalance_m=1.0,
    max_cumulative_distance_imbalance_m=5.0,
    max_instrument_error_mm_per_km=0.3,
    requires_invar_staff=True,
    requires_staff_supports=True,
    requires_calibration_monthly=True,
    requires_orthometric_correction=True,
    max_days_for_double_run=30,
)

# H2 - Second Order
H2_PARAMETERS = ClassParameters(
    class_level=LevelingClass.H2,
    class_name="H2",
    tolerance_coefficient=5.0,  # ±5mm√L
    max_line_length_km=60.0,
    max_sight_distance_geometric_m=40.0,
    max_sight_distance_trigonometric_m=80.0,
    min_staff_clearance_m=0.5,
    min_staff_upper_clearance_m=0.4,
    required_method="BFFB",
    requires_double_run=True,
    max_fb_difference_mm=None,
    max_single_distance_imbalance_m=2.0,
    max_cumulative_distance_imbalance_m=5.0,
    max_instrument_error_mm_per_km=1.0,
    requires_invar_staff=True,
    requires_staff_supports=True,
    requires_calibration_monthly=True,
    requires_orthometric_correction=True,
    max_days_for_double_run=30,
)

# H3 - Third Order
H3_PARAMETERS = ClassParameters(
    class_level=LevelingClass.H3,
    class_name="H3",
    tolerance_coefficient=10.0,  # ±10mm√L
    max_line_length_km=24.0,
    max_sight_distance_geometric_m=50.0,
    max_sight_distance_trigonometric_m=100.0,
    min_staff_clearance_m=0.4,
    min_staff_upper_clearance_m=0.3,
    required_method="BFFB",
    requires_double_run=True,
    max_fb_difference_mm=None,
    max_single_distance_imbalance_m=2.0,
    max_cumulative_distance_imbalance_m=10.0,
    max_days_for_double_run=30,
)

# H4 - Fourth Order
H4_PARAMETERS = ClassParameters(
    class_level=LevelingClass.H4,
    class_name="H4",
    tolerance_coefficient=20.0,  # ±20mm√L
    max_line_length_km=10.0,
    max_sight_distance_geometric_m=80.0,
    max_sight_distance_trigonometric_m=150.0,
    min_staff_clearance_m=0.4,
    min_staff_upper_clearance_m=0.3,
    required_method="BF",
    requires_double_run=True,
    max_single_distance_imbalance_m=5.0,
    max_cumulative_distance_imbalance_m=10.0,
)

# H5 - Fifth Order
H5_PARAMETERS = ClassParameters(
    class_level=LevelingClass.H5,
    class_name="H5",
    tolerance_coefficient=30.0,  # ±30mm√L
    max_line_length_km=5.0,
    max_sight_distance_geometric_m=100.0,
    max_sight_distance_trigonometric_m=150.0,
    required_method="BF",
    requires_double_run=True,
    max_single_distance_imbalance_m=10.0,
    max_cumulative_distance_imbalance_m=10.0,
)

# H6 - Sixth Order (Lowest Precision)
H6_PARAMETERS = ClassParameters(
    class_level=LevelingClass.H6,
    class_name="H6",
    tolerance_coefficient=60.0,  # ±60mm√L
    max_line_length_km=4.0,
    max_sight_distance_geometric_m=100.0,
    max_sight_distance_trigonometric_m=200.0,
    required_method="BF",
    requires_double_run=False,  # Can be single run
    max_single_distance_imbalance_m=10.0,
    max_cumulative_distance_imbalance_m=15.0,
)


# Registry of all classes
CLASS_REGISTRY: Dict[int, ClassParameters] = {
    1: H1_PARAMETERS,
    2: H2_PARAMETERS,
    3: H3_PARAMETERS,
    4: H4_PARAMETERS,
    5: H5_PARAMETERS,
    6: H6_PARAMETERS,
}

CLASS_REGISTRY_BY_NAME: Dict[str, ClassParameters] = {
    "H1": H1_PARAMETERS,
    "H2": H2_PARAMETERS,
    "H3": H3_PARAMETERS,
    "H4": H4_PARAMETERS,
    "H5": H5_PARAMETERS,
    "H6": H6_PARAMETERS,
}


def get_class_parameters(leveling_class: int) -> ClassParameters:
    """
    Get parameters for a specific leveling class.

    Args:
        leveling_class: Class number (1-6)

    Returns:
        ClassParameters object

    Raises:
        ValueError: If class not in valid range
    """
    if leveling_class not in CLASS_REGISTRY:
        raise ValueError(f"Invalid leveling class: {leveling_class}. Must be 1-6.")
    return CLASS_REGISTRY[leveling_class]


def get_class_parameters_by_name(class_name: str) -> ClassParameters:
    """Get parameters by class name (H1-H6)."""
    class_name = class_name.upper()
    if class_name not in CLASS_REGISTRY_BY_NAME:
        raise ValueError(f"Invalid class name: {class_name}. Must be H1-H6.")
    return CLASS_REGISTRY_BY_NAME[class_name]


def calculate_new_tolerance(distance_m: float, leveling_class: int = 3) -> float:
    """
    Calculate tolerance using new Survey of Israel regulations.

    Args:
        distance_m: Distance in meters
        leveling_class: Class (1-6), defaults to 3

    Returns:
        Tolerance in millimeters
    """
    params = get_class_parameters(leveling_class)
    distance_km = distance_m / 1000.0
    return params.get_tolerance_mm(distance_km)


def get_all_classes_summary() -> Dict[str, Dict[str, Any]]:
    """Get summary of all class parameters for display/export."""
    return {
        name: params.to_dict()
        for name, params in CLASS_REGISTRY_BY_NAME.items()
    }
