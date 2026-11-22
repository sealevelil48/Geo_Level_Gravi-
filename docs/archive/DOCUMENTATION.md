# Geodetic Leveling Automation Tool
## Complete Technical Documentation

**Version:** 1.0.0  
**Last Updated:** December 2024  
**Author:** Development Team

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Project Overview](#2-project-overview)
3. [Architecture](#3-architecture)
4. [Module Reference](#4-module-reference)
5. [Data Formats](#5-data-formats)
6. [Usage Guide](#6-usage-guide)
7. [API Reference](#7-api-reference)
8. [Development Guide](#8-development-guide)
9. [Roadmap & Future Work](#9-roadmap--future-work)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Executive Summary

### Purpose
The **Geodetic Leveling Automation Tool** is a Python-based software solution designed to automate the processing, validation, adjustment, and export of geodetic leveling survey data. It replaces manual data processing workflows with an automated pipeline that ensures accuracy, consistency, and traceability.

### Key Features
- **Multi-format Support:** Parses Trimble DAT and Leica RAW/GSI file formats
- **Automated Validation:** Detects endpoint errors, naming inconsistencies, and tolerance violations
- **Professional Calculations:** Implements line adjustment (תיאום קו) and least squares network adjustment (תיאום רשת)
- **Loop Detection:** Automatically identifies closed loops and double-run pairs
- **Multiple Export Formats:** FA0, FA1, FTEG, REZ (ANSI encoding), GeoJSON for QGIS
- **Dual Interface:** Command-line (CLI) and graphical (GUI) interfaces

### Business Value
- Reduces manual data processing time by 80%+
- Eliminates human calculation errors
- Provides consistent, auditable output formats
- Integrates with existing GIS workflows (QGIS)

---

## 2. Project Overview

### 2.1 Problem Statement
Geodetic leveling surveys produce raw measurement files in various proprietary formats. Processing these files currently requires:
- Manual data entry and transcription
- Complex calculations prone to human error
- Tedious validation against tolerance standards
- Multiple export formats for different downstream systems

### 2.2 Solution
This tool automates the entire workflow:

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐     ┌─────────────┐
│   RAW DATA  │ ──> │   PARSING    │ ──> │  VALIDATION │ ──> │ CALCULATION │
│  DAT / RAW  │     │  & EXTRACT   │     │  & CHECKS   │     │    ENGINE   │
└─────────────┘     └──────────────┘     └─────────────┘     └─────────────┘
                                                                     │
                                                                     v
┌─────────────┐     ┌──────────────┐     ┌─────────────┐     ┌─────────────┐
│    QGIS     │ <── │   GEOJSON    │ <── │   EXPORT    │ <── │ ADJUSTMENT  │
│  DISPLAY    │     │   OUTPUT     │     │  FA0/FA1    │     │    LSA      │
└─────────────┘     └──────────────┘     └─────────────┘     └─────────────┘
```

### 2.3 Technology Stack
| Component | Technology |
|-----------|------------|
| Language | Python 3.8+ |
| Data Processing | pandas, numpy |
| GUI | Tkinter |
| GIS Integration | GeoJSON, QGIS QML styles |
| File Encoding | UTF-8, ANSI (cp1255 for Hebrew) |

---

## 3. Architecture

### 3.1 Project Structure
```
geodetic_tool/
├── __init__.py           # Package initialization
├── README.md             # Quick start guide
│
├── parsers/              # File format parsers
│   ├── __init__.py       # Parser exports
│   ├── base_parser.py    # Abstract base class & factory
│   ├── trimble_parser.py # Trimble DAT format parser
│   └── leica_parser.py   # Leica RAW/GSI format parser
│
├── validators/           # Data validation
│   └── __init__.py       # LevelingValidator, BatchValidator
│
├── engine/               # Core calculations
│   ├── __init__.py       # Engine exports
│   ├── height_calculator.py  # Height difference calculations
│   ├── line_adjustment.py    # Single line adjustment (תיאום קו)
│   ├── least_squares.py      # Network adjustment (LSA)
│   └── loop_detector.py      # Loop and double-run detection
│
├── exporters/            # Output formats
│   └── __init__.py       # FA0, FA1, FTEG, REZ exporters
│
├── gis/                  # GIS integration
│   ├── __init__.py       # GIS exports
│   └── geojson_export.py # GeoJSON + QGIS styles
│
├── config/               # Configuration
│   ├── __init__.py       # Config exports
│   ├── models.py         # Data models (dataclasses)
│   └── settings.py       # Application settings
│
├── cli/                  # Command-line interface
│   ├── __init__.py
│   └── main.py           # CLI entry point
│
├── gui/                  # Graphical interface
│   ├── __init__.py
│   └── app.py            # Tkinter GUI application
│
├── tests/                # Test suite
│   ├── __init__.py
│   └── test_parsers.py   # Parser tests
│
└── docs/                 # Documentation
    └── ARCHITECTURE.md   # Technical architecture
```

### 3.2 Data Flow

```
                              ┌──────────────────────────────────────┐
                              │           INPUT FILES                │
                              │  *.DAT (Trimble)  *.RAW (Leica)     │
                              └──────────────────┬───────────────────┘
                                                 │
                                                 v
                              ┌──────────────────────────────────────┐
                              │         PARSER LAYER                 │
                              │  TrimbleParser / LeicaParser         │
                              │  Auto-detection via base_parser.py   │
                              └──────────────────┬───────────────────┘
                                                 │
                                                 v
                              ┌──────────────────────────────────────┐
                              │         DATA MODELS                  │
                              │  LevelingLine, StationSetup,         │
                              │  Benchmark, MeasurementSummary       │
                              └──────────────────┬───────────────────┘
                                                 │
                      ┌──────────────────────────┼──────────────────────────┐
                      │                          │                          │
                      v                          v                          v
         ┌─────────────────────┐   ┌─────────────────────┐   ┌─────────────────────┐
         │     VALIDATION      │   │      ENGINE         │   │      EXPORT         │
         │  - Endpoint check   │   │  - Line Adjustment  │   │  - FA0 / FA1        │
         │  - Naming check     │   │  - LSA (Network)    │   │  - FTEG / REZ       │
         │  - Tolerance check  │   │  - Loop Detection   │   │  - GeoJSON          │
         └─────────────────────┘   └─────────────────────┘   └─────────────────────┘
```

### 3.3 Class Diagram

```
┌─────────────────────┐         ┌─────────────────────┐
│   BaseParser        │<────────│   TrimbleParser     │
│   (Abstract)        │         └─────────────────────┘
│   + parse()         │<────────┌─────────────────────┐
│   + detect_format() │         │   LeicaParser       │
└─────────────────────┘         └─────────────────────┘
          │
          │ creates
          v
┌─────────────────────┐         ┌─────────────────────┐
│   LevelingLine      │────────>│   StationSetup      │
│   - filename        │  1..*   │   - backsight       │
│   - start_point     │         │   - foresight       │
│   - end_point       │         │   - height_diff     │
│   - total_distance  │         └─────────────────────┘
│   - total_height_diff│
└─────────────────────┘
          │
          │ validated by
          v
┌─────────────────────┐         ┌─────────────────────┐
│  LevelingValidator  │────────>│  ValidationResult   │
│   + validate()      │         │   - is_valid        │
│   + check_endpoint()│         │   - errors[]        │
│   + check_naming()  │         │   - warnings[]      │
└─────────────────────┘         └─────────────────────┘
          │
          │ adjusted by
          v
┌─────────────────────┐         ┌─────────────────────┐
│   LineAdjuster      │         │ LeastSquaresAdjuster│
│   + adjust()        │         │   + adjust()        │
│   + adjust_multiple │         │   + adjust_from_lines│
└─────────────────────┘         └─────────────────────┘
          │
          │ results in
          v
┌─────────────────────┐
│  AdjustmentResult   │
│   - adjusted_heights│
│   - residuals       │
│   - mse_unit_weight │
│   - k_coefficient   │
└─────────────────────┘
```

---

## 4. Module Reference

### 4.1 Parsers Module (`parsers/`)

#### `base_parser.py`
| Function/Class | Description |
|----------------|-------------|
| `BaseParser` | Abstract base class for all parsers |
| `create_parser(file_path)` | Factory function - auto-detects format and returns appropriate parser |
| `detect_file_format(file_path)` | Determines if file is Trimble DAT or Leica RAW/GSI |
| `is_benchmark(point_id)` | Checks if point ID represents a benchmark (contains letters) |

#### `trimble_parser.py`
| Function/Class | Description |
|----------------|-------------|
| `TrimbleParser` | Parses Trimble DAT format files |
| `parse()` | Main parsing method, returns `LevelingLine` |
| `_parse_kd1_line()` | Parses KD1 measurement records |
| `_parse_kd2_line()` | Parses KD2 summary records |

#### `leica_parser.py`
| Function/Class | Description |
|----------------|-------------|
| `LeicaParser` | Parses Leica RAW/GSI format files |
| `parse()` | Main parsing method, returns `LevelingLine` |
| `_decode_word()` | Decodes GSI word format (WI + data) |
| `_parse_block()` | Parses a complete measurement block |

### 4.2 Engine Module (`engine/`)

#### `height_calculator.py`
| Function | Description |
|----------|-------------|
| `calculate_misclosure(measured_dh, start_h, end_h)` | Computes misclosure |
| `distribute_misclosure(line, misclosure, method)` | Distributes error correction |
| `apply_corrections(line, corrections)` | Applies corrections to setups |

#### `line_adjustment.py`
| Class/Function | Description |
|----------------|-------------|
| `LineAdjuster` | Adjusts single leveling line between two known benchmarks |
| `adjust(line, start_bm, end_bm)` | Main adjustment method |
| `adjust_multiple_runs(lines, start_bm, end_bm)` | Averages multiple runs |
| `adjust_single_line(line, start_h, end_h)` | Convenience function |

#### `least_squares.py`
| Class/Function | Description |
|----------------|-------------|
| `LeastSquaresAdjuster` | Full network least squares adjustment |
| `adjust(observations, fixed_points)` | Main LSA method |
| `adjust_from_lines(lines, fixed_points)` | Convenience method for LevelingLine input |
| `simple_adjustment(obs_list, fixed)` | Quick adjustment function |

**Mathematical Model:**
```
Observation Equation: V = A·X - L

Where:
  V = residuals vector
  A = design matrix (coefficients)
  X = parameter vector (unknown heights)
  L = observation vector (measured height differences)

Normal Equations: N·X = U
  N = Aᵀ·P·A
  U = Aᵀ·P·L
  P = weight matrix (diagonal, weights = 1/distance_km)

M.S.E. of unit weight: σ₀ = √(VᵀPV / (n-u))
  n = number of observations
  u = number of unknowns
```

#### `loop_detector.py`
| Class/Function | Description |
|----------------|-------------|
| `Loop` | Represents a closed loop with misclosure calculation |
| `NetworkGraph` | Graph structure with DFS-based loop finding |
| `LoopAnalyzer` | High-level API for loop analysis |
| `detect_double_runs(lines)` | Identifies forward/return measurement pairs |

### 4.3 Validators Module (`validators/`)

| Class | Description |
|-------|-------------|
| `LevelingValidator` | Validates single LevelingLine |
| `BatchValidator` | Validates multiple lines |

**Validation Rules:**
1. **Endpoint Check:** Line must start and end on named benchmarks (not turning points)
2. **Naming Check:** File name should match start-end point IDs
3. **Tolerance Check:** Misclosure must be within class-specific tolerance
4. **Data Completeness:** All required fields must be present

### 4.4 Exporters Module (`exporters/`)

| Class/Function | Description |
|----------------|-------------|
| `FA0Exporter` | Exports FA0 format (input for TT2) |
| `FA1Exporter` | Exports FA1 format (adjustment results) |
| `FTEGExporter` | Exports FTEG format (raw measurements) |
| `REZExporter` | Exports REZ format (summary) |

### 4.5 GIS Module (`gis/`)

| Class/Function | Description |
|----------------|-------------|
| `GeoJSONExporter` | Creates GeoJSON for network visualization |
| `CoordinateManager` | Manages point coordinates |
| `QGISStyleGenerator` | Creates QML style files for QGIS |
| `export_network_to_geojson()` | Main export function |

---

## 5. Data Formats

### 5.1 Input Formats

#### Trimble DAT Format
```
For M5|Adr     1|TO  kma58.dat                  |                      |
For M5|Adr     4|TO  Start-Line         BF     1|                      |
For M5|Adr     5|KD1  5793MPI                  1|                      |Z         0.00000 m   |
For M5|Adr     6|KD1  5793MPI      22.0 C  1   1|Rb        0.23395 m   |HD         30.439 m   |
For M5|Adr     7|KD1        1      22.0 C  1   1|Rf        1.64070 m   |HD         30.773 m   |
For M5|Adr     8|KD1        1      22.0 C      1|                      |Z        -1.40675 m   |
```

#### Leica RAW/GSI Format
```
110124+0000000003747MPI 83..08+0000000105710200 
110125+0000000003747MPI 32...8+0000000001289347 331.08+0000000000122303 
110126+0000000000000001 32...8+0000000001274136 332.08+0000000000218228 
```

### 5.2 Output Formats

#### FA0 Format (TT2 Input)
```
  11   2            sapir_tt.rez                                               9
    2520W        -54.551
    2533W        -18.578
2520W    2522W        9.07850  1352.  14   2.01  0515           UnionFiles.raw
```

#### FA1 Format (Adjustment Results)
```
ITERATION  NO. 1
M.S.E. OF UNIT WEIGHT =  0.001969

NO.      ADJUSTED     APPROX          DIF         M.S.E.
1 2520W      -54.55100
2 2533W      -18.57800
3 2522W      -45.47146   -45.47300      0.00154     0.002113
```

---

## 6. Usage Guide

### 6.1 Installation

```bash
# Clone or extract the project
cd geodetic_tool

# Install dependencies
pip install -r requirements.txt

# Optional: Install as package
pip install -e .
```

### 6.2 Command Line Interface (CLI)

```bash
# Parse files
python run_cli.py parse C:\data\*.DAT

# Validate files
python run_cli.py validate C:\data\*.DAT

# Show file info
python run_cli.py info C:\data\KMA58.DAT

# Export to GeoJSON
python run_cli.py geojson C:\data\*.DAT -o ./output -p MyProject

# Full help
python run_cli.py --help
```

### 6.3 Graphical Interface (GUI)

```bash
python run_gui.py
```

**GUI Features:**
1. **File Panel (Left):** Load and manage measurement files
2. **Line Details Tab:** View selected line details and setups
3. **Validation Tab:** Run batch validation with results grid
4. **Analysis Tab:** Double-run detection, loop analysis, adjustments
5. **Log Tab:** Operation history and messages

### 6.4 Python API

```python
from geodetic_tool.parsers import create_parser, detect_file_format
from geodetic_tool.validators import LevelingValidator, BatchValidator
from geodetic_tool.engine import LineAdjuster, LeastSquaresAdjuster
from geodetic_tool.config.models import Benchmark

# Parse a file
parser = create_parser("data/KMA58.DAT")
line = parser.parse()

print(f"Start: {line.start_point}")
print(f"End: {line.end_point}")
print(f"Distance: {line.total_distance:.2f} m")
print(f"Height Diff: {line.total_height_diff:.5f} m")

# Validate
validator = LevelingValidator()
result = validator.validate(line)
print(f"Valid: {result.is_valid}")

# Line adjustment with known heights
start_bm = Benchmark(point_id="5793MPI", height=100.000)
end_bm = Benchmark(point_id="5792MPI", height=99.575)

adjuster = LineAdjuster()
adjusted_line, info = adjuster.adjust(line, start_bm, end_bm)

print(f"Misclosure: {info['misclosure_mm']:.3f} mm")
print(f"Within tolerance: {info['within_tolerance']}")

# Network adjustment (LSA)
fixed_points = {
    "5793MPI": 100.000,
    "5792MPI": 99.575
}

lsa = LeastSquaresAdjuster()
result = lsa.adjust_from_lines([line1, line2, line3], fixed_points)

print(f"M.S.E.: {result.mse_unit_weight:.6f}")
for point, height in result.adjusted_heights.items():
    print(f"  {point}: {height:.5f} m")
```

---

## 7. API Reference

### 7.1 Core Data Models

#### `LevelingLine`
```python
@dataclass
class LevelingLine:
    filename: str               # Source filename
    start_point: str            # Starting benchmark ID
    end_point: str              # Ending benchmark ID
    setups: List[StationSetup]  # List of measurement setups
    method: str = "BF"          # Measurement method
    total_distance: float       # Total line distance (m)
    total_height_diff: float    # Total height difference (m)
    status: LineStatus          # Validation status
```

#### `StationSetup`
```python
@dataclass
class StationSetup:
    setup_number: int           # Setup sequence number
    from_point: str             # Backsight point ID
    to_point: str               # Foresight point ID
    backsight_reading: float    # Rb in meters
    foresight_reading: float    # Rf in meters
    distance_back: float        # Distance to backsight (m)
    distance_fore: float        # Distance to foresight (m)
    height_diff: float          # Computed dH = Rb - Rf
```

#### `Benchmark`
```python
@dataclass
class Benchmark:
    point_id: str               # Point identifier
    height: float               # Known height (m)
    order: int = 3              # Control order (1=highest)
```

#### `AdjustmentResult`
```python
@dataclass
class AdjustmentResult:
    iteration: int              # Number of iterations
    mse_unit_weight: float      # M.S.E. of unit weight
    adjusted_heights: Dict[str, float]  # Point -> height
    residuals: Dict[str, float]         # Obs -> residual (mm)
    mse_heights: Dict[str, float]       # Point -> M.S.E.
    k_coefficient: float        # Classification coefficient K
```

### 7.2 Configuration Settings

```python
# config/settings.py

# Tolerance classes (mm per sqrt(km))
TOLERANCE_CLASSES = {
    1: 3.0,   # First order
    2: 5.0,   # Second order  
    3: 10.0,  # Third order
    4: 20.0   # Fourth order
}

# Calculate tolerance
def calculate_tolerance(distance_m: float, leveling_class: int) -> float:
    distance_km = distance_m / 1000.0
    k = TOLERANCE_CLASSES.get(leveling_class, 10.0)
    return k * math.sqrt(distance_km)
```

---

## 8. Development Guide

### 8.1 Setting Up Development Environment

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt
pip install pytest pytest-cov  # For testing

# Install package in development mode
pip install -e .
```

### 8.2 Running Tests

```bash
# Run all tests
python -m pytest tests/

# Run with coverage
python -m pytest tests/ --cov=geodetic_tool --cov-report=html

# Run specific test file
python -m pytest tests/test_parsers.py -v
```

### 8.3 Adding a New Parser

1. Create new parser file in `parsers/`:
```python
# parsers/newformat_parser.py
from .base_parser import BaseParser

class NewFormatParser(BaseParser):
    def parse(self) -> LevelingLine:
        # Implement parsing logic
        pass
```

2. Register in `parsers/__init__.py`
3. Update `detect_file_format()` in `base_parser.py`

### 8.4 Adding a New Exporter

1. Create new exporter in `exporters/__init__.py`:
```python
class NewExporter:
    def __init__(self, output_dir: str, encoding: str = 'cp1255'):
        self.output_dir = Path(output_dir)
        self.encoding = encoding
    
    def export(self, data: List[LevelingLine], filename: str):
        # Implement export logic
        pass
```

### 8.5 Code Style Guidelines

- Follow PEP 8 style guide
- Use type hints for all function parameters and returns
- Document all public functions with docstrings
- Use dataclasses for data structures
- Keep functions focused and under 50 lines
- Use logging instead of print statements

---

## 9. Roadmap & Future Work

### 9.1 Completed Features ✅
- [x] Trimble DAT parser
- [x] Leica RAW/GSI parser
- [x] Validation engine
- [x] Height calculations
- [x] Line adjustment (תיאום קו)
- [x] Least squares adjustment (LSA)
- [x] Loop detection
- [x] Double-run analysis
- [x] FA0/FA1/FTEG/REZ exporters
- [x] GeoJSON export with QGIS styles
- [x] CLI interface
- [x] GUI application

### 9.2 Planned Features 🔄
- [ ] **PKT file integration** - Load known benchmark coordinates
- [ ] **Database integration** - PostgreSQL for project storage
- [ ] **QGIS plugin** - Direct integration with QGIS
- [ ] **Gravimetry support** - Gravity corrections
- [ ] **TT2 comparison** - Compare results with TT2 software
- [ ] **Report generation** - PDF/Word reports
- [ ] **Batch processing** - Process entire folders automatically
- [ ] **Network diagrams** - Visual network topology

### 9.3 Known Issues
1. Large files (>1000 setups) may be slow in GUI
2. Some edge cases in Leica format may not parse correctly
3. GeoJSON uses schematic coordinates when real coordinates unavailable

---

## 10. Troubleshooting

### 10.1 Common Issues

**Issue:** "No module named 'pandas'"
```bash
pip install pandas numpy
```

**Issue:** "Can't open file 'gui/app.py'"
```bash
# Use the run scripts from project root
python run_gui.py
python run_cli.py
```

**Issue:** Hebrew characters display incorrectly
- Ensure terminal supports UTF-8
- For export files, use `encoding='cp1255'` (ANSI Hebrew)

**Issue:** "Singular matrix" in LSA
- Check that you have at least one fixed point
- Verify network is connected (no isolated segments)

### 10.2 Debug Logging

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# In your code
logger = logging.getLogger(__name__)
logger.debug("Processing file: %s", filename)
```

### 10.3 Contact & Support

For issues and feature requests, contact the development team.

---

## Appendix A: Glossary

| Term | Hebrew | Description |
|------|--------|-------------|
| Benchmark | נ"צ (נקודת צפי) | Known control point with fixed height |
| BF | הלוך | Back-Forward (standard direction) |
| FB | חזור | Forward-Back (return direction) |
| Double-run | הלוך-שוב | Forward and return measurements |
| Line adjustment | תיאום קו | Adjusting single line between benchmarks |
| Network adjustment | תיאום רשת | LSA of entire network |
| Loop | לולאה | Closed circuit of measurements |
| Misclosure | סגירה | Difference between measured and expected |
| Turning point | נ"ע (נקודת עזר) | Intermediate point (not a benchmark) |
| Setup | מעמד | Single instrument position |
| LSA | מפ"ר | Least Squares Adjustment |

---

## Appendix B: File Format Specifications

### B.1 Trimble DAT Record Types

| Code | Description |
|------|-------------|
| TO | Text/Operator information |
| KD1 | Measurement record |
| KD2 | Summary record |

### B.2 Leica GSI Word Indices

| WI | Description |
|----|-------------|
| 11 | Point ID |
| 32 | Horizontal distance |
| 331 | Staff reading (back) |
| 332 | Staff reading (fore) |
| 335 | Staff reading (back, inverted) |
| 336 | Staff reading (fore, inverted) |
| 571 | Height difference (mm) |
| 573 | Cumulative height |
| 83 | Height of instrument |

---

*End of Documentation*
