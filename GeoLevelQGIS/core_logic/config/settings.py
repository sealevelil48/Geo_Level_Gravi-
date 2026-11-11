"""
Geodetic Tool Configuration Settings
"""
import re
from dataclasses import dataclass, field
from typing import Pattern, List, Optional
from enum import Enum


class MeasurementMethod(Enum):
    """Leveling measurement method."""
    BF = "BF"       # Back-Fore (standard)
    BFFB = "BFFB"   # Back-Fore-Fore-Back (double-run)
    FB = "FB"       # Fore-Back (reverse)


class FileFormat(Enum):
    """Supported file formats."""
    TRIMBLE_DAT = "trimble_dat"
    LEICA_GSI = "leica_gsi"
    UNKNOWN = "unknown"


@dataclass
class ToleranceConfig:
    """Tolerance calculation configuration."""
    # Class coefficients for leveling tolerances (mm/sqrt(km))
    class_1: float = 1.0   # First-order leveling
    class_2: float = 2.0   # Second-order leveling  
    class_3: float = 3.0   # Third-order leveling
    class_4: float = 6.0   # Fourth-order leveling
    default_class: int = 3


@dataclass
class EncodingConfig:
    """File encoding configuration."""
    default_encoding: str = 'cp1255'  # Hebrew ANSI (Windows-1255)
    fallback_encodings: List[str] = field(default_factory=lambda: ['utf-8', 'latin-1', 'ascii'])
    output_encoding: str = 'cp1255'


@dataclass
class ValidationConfig:
    """Validation rules configuration."""
    # Pattern for valid benchmark names (contains letters)
    benchmark_pattern: Pattern = field(default_factory=lambda: re.compile(r'.*[A-Za-z]+.*'))
    
    # Pattern for turning points (numeric only)
    turning_point_pattern: Pattern = field(default_factory=lambda: re.compile(r'^\d+$'))

    # Station-ID format: up to 4 digits + up to 4 alphanumeric/Hebrew chars, total ≤ 8
    # Hebrew Unicode block: א–ת (U+05D0–U+05EA)
    point_name_pattern: Pattern = field(default_factory=lambda: re.compile(
        r'^(?=.{1,8}$)\d{0,4}[A-Za-zא-ת0-9]{0,4}$'
    ))

    # Maximum allowed misclosure per km (in mm)
    max_misclosure_per_km: float = 3.0
    
    # Minimum number of setups for valid measurement
    min_setups: int = 2


@dataclass  
class LeicaGSIConfig:
    """Leica GSI format configuration."""
    # Word indices for different data types
    WI_POINT_ID: int = 11
    WI_DISTANCE: int = 32
    WI_STAFF_B1: int = 331  # Backsight Face 1
    WI_STAFF_F1: int = 332  # Foresight Face 1
    WI_STAFF_B2: int = 335  # Backsight Face 2
    WI_STAFF_F2: int = 336  # Foresight Face 2
    WI_HEIGHT: int = 83
    WI_DH: int = 573        # Height difference
    WI_TOTAL_DIST: int = 574  # Cumulative distance
    
    # Scale factors
    distance_scale: float = 1e-5  # Convert to meters
    height_scale: float = 1e-5    # Convert to meters


@dataclass
class TrimbleConfig:
    """Trimble DAT format configuration."""
    field_delimiter: str = '|'
    measurement_prefix: str = 'KD1'
    text_prefix: str = 'TO'
    summary_prefix: str = 'KD2'
    start_marker: str = 'Start-Line'
    end_marker: str = 'End-Line'


@dataclass
class Settings:
    """Main settings container."""
    tolerance: ToleranceConfig = field(default_factory=ToleranceConfig)
    encoding: EncodingConfig = field(default_factory=EncodingConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    leica: LeicaGSIConfig = field(default_factory=LeicaGSIConfig)
    trimble: TrimbleConfig = field(default_factory=TrimbleConfig)
    
    # Output formatting
    decimal_places: int = 5
    distance_unit: str = 'm'
    height_unit: str = 'm'
    

# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """Get the global settings instance."""
    return settings


def validate_point_name(point_id: str) -> tuple:
    """
    Validate a station ID against the Israeli survey point-naming convention.

    Rules (Directive ג2, 2021):
    - Max 8 characters total.
    - Up to 4 leading digits, followed by up to 4 alphanumeric or Hebrew characters.
    - Hebrew block: א–ת (U+05D0–U+05EA).

    Returns:
        (is_valid: bool, reason: str)
    """
    if not point_id:
        return False, "Point name is empty"
    point_id = point_id.strip()
    if len(point_id) > 8:
        return False, f"'{point_id}' exceeds 8-character limit ({len(point_id)} chars)"
    if not settings.validation.point_name_pattern.match(point_id):
        return False, (
            f"'{point_id}' does not match station-ID format "
            "(up to 4 digits + up to 4 letters/digits, max 8 chars total)"
        )
    return True, "OK"


def is_benchmark(point_id: str) -> bool:
    """Check if a point ID represents a benchmark (vs turning point)."""
    if not point_id:
        return False
    point_id = point_id.strip()
    # Benchmark contains letters, turning point is purely numeric
    return bool(settings.validation.benchmark_pattern.match(point_id))


def is_turning_point(point_id: str) -> bool:
    """Check if a point ID represents a turning point."""
    if not point_id:
        return False
    point_id = point_id.strip()
    return bool(settings.validation.turning_point_pattern.match(point_id))


def calculate_tolerance(distance_m: float, leveling_class: int = None) -> float:
    """
    Calculate allowable tolerance for a given distance.
    
    Args:
        distance_m: Total distance in meters
        leveling_class: Leveling class (1-4), uses default if None
        
    Returns:
        Allowable tolerance in millimeters
    """
    if leveling_class is None:
        leveling_class = settings.tolerance.default_class
    
    distance_km = distance_m / 1000.0
    
    class_factors = {
        1: settings.tolerance.class_1,
        2: settings.tolerance.class_2,
        3: settings.tolerance.class_3,
        4: settings.tolerance.class_4,
    }
    
    factor = class_factors.get(leveling_class, settings.tolerance.class_3)
    
    # Tolerance = k * sqrt(D) where D is in km
    return factor * (distance_km ** 0.5)
