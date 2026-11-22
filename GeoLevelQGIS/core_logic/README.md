# Geodetic Leveling Automation Tool
## כלי אוטומציה לפילוס גיאודטי

A comprehensive Python application for automating geodetic leveling calculations and survey data processing.

---

## 🎯 Features

### Data Parsing
- **Trimble DAT format** - Full support for Trimble digital level data files
- **Leica RAW/GSI format** - Support for Leica GSI-8 and GSI-16 formats
- Automatic format detection based on content and file extension
- Multi-encoding support (cp1255/Hebrew ANSI, UTF-8, Latin-1)

### Validation
- Endpoint validation (ensures lines end on named benchmarks)
- Naming convention checks (front-to-back detection)
- Data completeness verification
- Tolerance checking by leveling class

### Calculations
- Height difference calculations
- Misclosure distribution (proportional and equal)
- Line adjustment between known benchmarks
- Least Squares Adjustment (LSA) for network adjustment
- Loop detection and double-run analysis

### Export Formats
- **FA0** - Adjustment input format
- **FA1** - Detailed adjustment report
- **FTEG** - Simplified measurement data
- **REZ** - Summary results
- **GeoJSON** - For GIS visualization (QGIS compatible)

---

## 📁 Project Structure

```
geodetic_tool/
├── parsers/              # File format parsers
│   ├── base_parser.py    # Abstract base class
│   ├── trimble_parser.py # Trimble DAT parser
│   └── leica_parser.py   # Leica RAW/GSI parser
│
├── validators/           # Data validation
│   └── __init__.py       # LevelingValidator, BatchValidator
│
├── engine/               # Core calculations
│   ├── height_calculator.py  # Height difference calculations
│   ├── line_adjustment.py    # Single line adjustment
│   ├── least_squares.py      # Network adjustment (LSA)
│   └── loop_detector.py      # Loop and double-run detection
│
├── exporters/            # Output formats
│   └── __init__.py       # FA0, FA1, FTEG, REZ exporters
│
├── gis/                  # GIS integration
│   └── geojson_export.py # GeoJSON export + QGIS styles
│
├── config/               # Configuration
│   ├── settings.py       # Application settings
│   └── models.py         # Data models
│
├── cli/                  # Command-line interface
│   └── main.py           # CLI entry point
│
│   └── app.py            # Tkinter GUI application
│
├── tests/                # Test suite
│   └── test_parsers.py   # Parser tests
│
└── docs/                 # Documentation
    └── ARCHITECTURE.md   # Technical architecture
```

---

## 🚀 Installation

```bash
# Clone or copy the project
cd geodetic_tool

# Install dependencies (if needed)
pip install pandas --break-system-packages
```

---

## 💻 Usage

### Command-Line Interface

```bash
# Parse and validate files
python3 cli/main.py parse file1.DAT file2.raw

# Validate files with summary
python3 cli/main.py validate *.DAT *.raw

# Export to specific format
python3 cli/main.py export --format fa0 --project myproject file1.DAT

# Export to all formats
python3 cli/main.py export --format all --project myproject *.DAT

# Export to GeoJSON for GIS
python3 cli/main.py geojson *.DAT *.raw -o ./output -p network_name

# Show file information
python3 cli/main.py info KMA58_DAT.txt
```

### Graphical Interface

Inside QGIS: the Geo Level Gravi plugin. Standalone Tkinter application: `main` branch (`python run_gui.py`).

### Python API

```python
from parsers import create_parser
from validators import LevelingValidator
from engine import LoopAnalyzer, detect_double_runs

# Parse a file
parser = create_parser('measurement.DAT')
line = parser.parse('measurement.DAT')

# Validate
validator = LevelingValidator()
result = validator.validate(line)
print(f"Valid: {result.is_valid}")

# Analyze double-runs
analyzer = LoopAnalyzer([line1, line2])
pairs = detect_double_runs([line1, line2])
```

---

## 📊 Supported File Formats

### Input Formats

| Format | Extension | Description |
|--------|-----------|-------------|
| Trimble DAT | .dat, .DAT | Trimble digital level data |
| Leica RAW | .raw, .RAW | Leica raw measurement data |
| Leica GSI | .gsi, .GSI | Leica GSI-8/16 format |

### Output Formats

| Format | Description |
|--------|-------------|
| FA0 | Adjustment input (benchmarks + observations) |
| FA1 | Detailed adjustment report with iterations |
| FTEG | Simplified measurement data |
| REZ | Summary results |
| GeoJSON | GIS-compatible vector data |

---

## 🔧 Configuration

Edit `config/settings.py` to customize:

- Tolerance coefficients (mm/√km by class)
- Default encoding (cp1255 for Hebrew)
- Benchmark detection patterns
- Leica GSI word indices

---

## 📋 Data Validation Rules

1. **Endpoint Validation**: Lines must end on named benchmarks (containing letters), not numeric turning points
2. **Naming Convention**: Detects front-to-back naming errors
3. **Data Completeness**: Minimum setups, valid readings
4. **Tolerance Check**: Misclosure within class tolerance

---

## 🧮 Calculation Methods

### Height Difference
```
dH = Backsight_Reading - Foresight_Reading
```

### Tolerance by Class
```
T = k × √(Distance_km)

Class 1: k = 1.0 mm/√km
Class 2: k = 2.0 mm/√km
Class 3: k = 3.0 mm/√km
Class 4: k = 6.0 mm/√km
```

### Misclosure Distribution
- **Proportional**: Correction proportional to cumulative distance
- **Equal**: Equal correction per setup

### Least Squares Adjustment
- Parametric method: V = A×X - L
- Normal equations: N×X = U where N = Aᵀ×P×A
- Distance-based weighting: P[i,i] = 1/distance_km

---

## 🗺️ GIS Integration

Export to GeoJSON for visualization in QGIS:

```bash
python3 cli/main.py geojson *.DAT -o ./output -p network
```

This creates:
- `network_lines.geojson` - Line features
- `network_lines.qml` - QGIS line style
- `network_points.qml` - QGIS point style

Open in QGIS and drag the QML files onto the layers for automatic styling.

---

## 🧪 Testing

```bash
# Run test suite
python3 tests/test_parsers.py
```

---

## 📝 License

Internal use only.

---

## 👥 Contributors

Developed using AI-assisted coding.
