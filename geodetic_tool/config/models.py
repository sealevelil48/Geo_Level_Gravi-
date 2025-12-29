"""
Data Models for Geodetic Measurements

Core data structures used throughout the geodetic tool.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum
import pandas as pd


class MeasurementDirection(Enum):
    """Direction of measurement in a leveling line."""
    FORWARD = "forward"   # BF: Backsight to Foresight
    BACKWARD = "backward"  # FB: Foresight to Backsight


class LineStatus(Enum):
    """Status of a leveling line."""
    VALID = "valid"
    INVALID_ENDPOINT = "invalid_endpoint"  # Ends on turning point
    NAMING_ERROR = "naming_error"          # File name doesn't match points
    INCOMPLETE = "incomplete"              # Missing data
    EXCEEDED_TOLERANCE = "exceeded_tolerance"


@dataclass
class StationSetup:
    """Single station setup in a leveling measurement."""
    setup_number: int
    from_point: str
    to_point: str
    backsight_reading: float      # Rb in meters
    foresight_reading: float      # Rf in meters
    distance_back: float          # HD to backsight in meters
    distance_fore: float          # HD to foresight in meters
    temperature: Optional[float] = None  # Celsius
    height_diff: Optional[float] = None  # Computed dH = Rb - Rf
    cumulative_height: Optional[float] = None  # Z from start
    is_used: bool = True  # NEW: Flag to include/exclude in exports

    def __post_init__(self):
        """Calculate height difference if not provided."""
        if self.height_diff is None and self.backsight_reading and self.foresight_reading:
            self.height_diff = self.backsight_reading - self.foresight_reading


@dataclass
class LevelingLine:
    """Complete leveling line from one benchmark to another."""
    filename: str
    start_point: str
    end_point: str
    setups: List[StationSetup] = field(default_factory=list)
    method: str = "BF"  # BF, BFFB, FB
    date: Optional[datetime] = None
    instrument_id: Optional[str] = None

    # Computed values
    total_distance: float = 0.0      # Total line distance in meters
    total_height_diff: float = 0.0   # Total height difference in meters
    misclosure: Optional[float] = None  # Misclosure if endpoints are known
    status: LineStatus = LineStatus.VALID
    validation_errors: List[str] = field(default_factory=list)

    # NEW: Export control and direction management
    is_used: bool = True  # Flag to include/exclude entire line in exports
    original_direction: str = "BF"  # Track original direction for reversal
    
    @property
    def num_setups(self) -> int:
        """Number of station setups."""
        return len(self.setups)
    
    @property
    def distance_km(self) -> float:
        """Total distance in kilometers."""
        return self.total_distance / 1000.0
    
    def calculate_totals(self):
        """Calculate total distance and height difference from setups."""
        self.total_distance = sum(
            (s.distance_back + s.distance_fore) / 2 for s in self.setups
        )
        self.total_height_diff = sum(
            s.height_diff for s in self.setups if s.height_diff is not None
        )

    def toggle_direction(self):
        """
        Toggle line direction between BF and FB.
        Automatically inverts height differences and swaps start/end points.
        """
        # Swap start and end points
        self.start_point, self.end_point = self.end_point, self.start_point

        # Toggle method
        if self.method == "BF":
            self.method = "FB"
        elif self.method == "FB":
            self.method = "BF"

        # Invert height differences
        for setup in self.setups:
            if setup.height_diff is not None:
                setup.height_diff *= -1
            # Swap from/to points in setups
            setup.from_point, setup.to_point = setup.to_point, setup.from_point

        # Recalculate totals
        self.total_height_diff *= -1

    def get_used_setups(self) -> List['StationSetup']:
        """Return only setups marked as used."""
        return [s for s in self.setups if s.is_used]

    def copy(self) -> 'LevelingLine':
        """Create a deep copy of this line for joint projects."""
        import copy
        return copy.deepcopy(self)
    
    def to_dataframe(self) -> pd.DataFrame:
        """Convert setups to a pandas DataFrame."""
        data = []
        for setup in self.setups:
            data.append({
                'SetupNum': setup.setup_number,
                'FromPoint': setup.from_point,
                'ToPoint': setup.to_point,
                'BacksightReading': setup.backsight_reading,
                'ForesightReading': setup.foresight_reading,
                'DistanceBack': setup.distance_back,
                'DistanceFore': setup.distance_fore,
                'HeightDiff': setup.height_diff,
                'CumulativeHeight': setup.cumulative_height,
                'Temperature': setup.temperature
            })
        return pd.DataFrame(data)


@dataclass
class Benchmark:
    """Known control point (benchmark) with fixed height."""
    point_id: str
    height: float              # Height in meters
    order: int = 3             # Control order (1=highest precision)
    description: Optional[str] = None
    easting: Optional[float] = None   # X coordinate
    northing: Optional[float] = None  # Y coordinate


@dataclass
class MeasurementSummary:
    """Summary of a measurement line for export."""
    from_point: str
    to_point: str
    height_diff: float        # DH in meters
    distance: float           # Distance in meters
    num_setups: int
    bf_diff: float            # BF difference in mm
    year_month: str           # MMYY format
    source_file: str

    # Adjustment values
    residual: Optional[float] = None     # v in mm
    adjusted_dh: Optional[float] = None  # Adjusted height diff

    # NEW: Export control
    is_used: bool = True  # Flag to include/exclude in exports


@dataclass
class AdjustmentResult:
    """Results from a leveling adjustment."""
    iteration: int
    mse_unit_weight: float  # M.S.E. of unit weight
    adjusted_heights: Dict[str, float]  # Point ID -> adjusted height
    residuals: Dict[str, float]         # Observation ID -> residual
    mse_heights: Dict[str, float]       # Point ID -> M.S.E. of height
    total_distance_km: float
    total_diff_mm: float
    k_coefficient: float                # Classification coefficient
    
    def to_dataframe(self) -> pd.DataFrame:
        """Convert adjusted heights to DataFrame."""
        data = []
        for point_id, height in self.adjusted_heights.items():
            data.append({
                'PointID': point_id,
                'AdjustedHeight': height,
                'MSE': self.mse_heights.get(point_id, None)
            })
        return pd.DataFrame(data)


@dataclass
class ValidationResult:
    """Result of validating a leveling line."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    # Specific checks
    endpoint_valid: bool = True
    naming_valid: bool = True
    tolerance_valid: bool = True
    data_complete: bool = True
    
    def add_error(self, message: str):
        """Add an error and mark as invalid."""
        self.errors.append(message)
        self.is_valid = False
    
    def add_warning(self, message: str):
        """Add a warning without marking invalid."""
        self.warnings.append(message)


@dataclass
class ProjectData:
    """Container for a complete geodetic project."""
    name: str
    lines: List[LevelingLine] = field(default_factory=list)
    benchmarks: Dict[str, Benchmark] = field(default_factory=dict)
    adjustment_results: Optional[AdjustmentResult] = None

    # NEW: Joint project support
    is_joint_project: bool = False
    source_projects: List[str] = field(default_factory=list)  # Track source project names
    project_path: Optional[str] = None  # File path for persistence

    def add_line(self, line: LevelingLine):
        """Add a leveling line to the project."""
        self.lines.append(line)

    def add_benchmark(self, benchmark: Benchmark):
        """Add a known benchmark to the project."""
        self.benchmarks[benchmark.point_id] = benchmark

    def get_all_points(self) -> set:
        """Get all unique point IDs from all lines."""
        points = set()
        for line in self.lines:
            points.add(line.start_point)
            points.add(line.end_point)
            for setup in line.setups:
                points.add(setup.from_point)
                points.add(setup.to_point)
        return points

    def get_used_lines(self) -> List[LevelingLine]:
        """Return only lines marked as used for export."""
        return [line for line in self.lines if line.is_used]

    def copy(self) -> 'ProjectData':
        """Create a deep copy of this project (for joint projects)."""
        import copy
        return copy.deepcopy(self)

    def merge_from(self, other_project: 'ProjectData'):
        """
        Merge another project into this one (for joint projects).
        Creates deep copies to avoid modifying source projects.
        """
        # Copy lines
        for line in other_project.lines:
            self.lines.append(line.copy())

        # Copy benchmarks
        for point_id, bm in other_project.benchmarks.items():
            if point_id not in self.benchmarks:
                import copy
                self.benchmarks[point_id] = copy.deepcopy(bm)

        # Track source
        if other_project.name not in self.source_projects:
            self.source_projects.append(other_project.name)

    def lines_to_dataframe(self) -> pd.DataFrame:
        """Convert all lines to summary DataFrame."""
        data = []
        for line in self.lines:
            data.append({
                'Filename': line.filename,
                'StartPoint': line.start_point,
                'EndPoint': line.end_point,
                'NumSetups': line.num_setups,
                'TotalDistance': line.total_distance,
                'HeightDiff': line.total_height_diff,
                'Method': line.method,
                'Status': line.status.value,
                'IsUsed': line.is_used  # NEW
            })
        return pd.DataFrame(data)
