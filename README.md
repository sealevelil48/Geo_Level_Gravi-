# Geodetic Leveling Tool v1.1
## כלי אוטומציה לפילוס גיאודטי

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Internal-red.svg)]()
[![Israeli Survey Regulations](https://img.shields.io/badge/Regulations-Directive%20%D7%932%202021-green.svg)]()

A comprehensive Python application for automating geodetic leveling calculations and survey data processing, compliant with **Israeli Survey Regulations (Directive ג2, 2021)**.

---

## 🎯 Key Features

### 📊 Data Processing
- **Multi-Format Support**: Trimble DAT, Leica RAW/GSI-8/GSI-16
- **Automatic Format Detection**: Smart content-based parser selection
- **Multi-Encoding**: Hebrew ANSI (cp1255), UTF-8, Latin-1
- **Batch Processing**: Process multiple files simultaneously

### ✅ Validation & Compliance
- **Israeli Survey Regulations (Directive ג2, 2021)**: Full 16-feature implementation
- **Class System (H1-H6)**: Precision classes with tolerance calculations
- **Endpoint Validation**: Named benchmark verification
- **Naming Convention Checks**: Front-to-back detection
- **Tolerance Checking**: Distance-based precision validation

### 🧮 Advanced Calculations
- **Least Squares Adjustment (LSA)**: Network adjustment with Ax+L and Bv+W methods
- **Height Difference Calculations**: Accurate backsight-foresight processing
- **Misclosure Distribution**: Proportional and equal distribution methods
- **Loop Detection**: Automatic loop and double-run analysis
- **Line Adjustment**: Between known benchmarks

### 🎨 User Interface
- **Graphical Interface (GUI)**: Modern Tkinter-based application
- **Class Selector**: Interactive H1-H6 class selection with real-time updates
- **Info Dialogs**: Detailed class parameter display
- **Status Bar**: Live feedback on current class and operations
- **Network Adjustment Dialog**: Full LSA integration with visual results

### 📤 Export Formats
- **FA0**: Adjustment input format (benchmarks + observations)
- **FA1**: Detailed adjustment report with iterations
- **FTEG**: Simplified measurement data
- **REZ**: Summary results
- **GeoJSON**: GIS-compatible format for QGIS visualization

---

## 🆕 What's New in v1.1

### November 2026 — engine shared with the QGIS plugin

- **Correct LSA engine** (`geodetic_tool/engine/least_squares.py`, `network_prep.py`):
  parametric V = Ax − L with L = Lb − L0 solved directly (the old loop added the
  solution 10 times); conditional Bv + W = 0 with an independent set of loop and
  benchmark conditions; clear error when a group of points has no fixed point.
  Verified against the condition-adjustment exercise (RFH-95/96/97).
- **Step 1**: forward/return runs averaged once, discrepancy checked against
  k·√L with the one-direction length. **Step 2**: approximate heights table.
- **Line length fix**: a line's length is the sum of back + fore sights (it was halved).
- **Import .FA0 / .FA1 / .rez**: every row becomes its own editable line; known
  heights in the file are offered as fixed points.
- **One adjustment window** (Analysis ▸ Network Adjustment (LSA), Ctrl+Shift+N, and
  "Adjust This Line"): lines to include, fixed points, Steps 1–2, parametric /
  conditional, single-line adjustment (Directive G2 App. C), export FA0 / FA1 / PDF.
- **Position estimation** (`geodetic_tool/gis/topo_estimator.py`): Dijkstra on
  measured lengths for points without coordinates, also through chains of points.


### Latest Updates (February 2026)

#### **Default Class Selector (H1-H6)** 🎯
- GUI combo box for selecting precision class (H1-H6)
- Persistent settings stored in `~/.geodetic_tool/settings.json`
- Info button (ℹ️) displaying detailed class parameters
- Real-time status updates when class changes
- Automatic application of selected class to all validations

#### **Israeli Survey Regulations - All 16 Features** 📋
- Complete implementation of Directive ג2 (2021)
- Class parameters registry (H1-H6)
- Tolerance formulas: ±3mm√L to ±60mm√L
- Distance limits and sight distance constraints
- Method requirements (BFFB for H1-H3, BF for H4-H6)
- Special requirements per class

#### **Enhanced Network Adjustment** 🌐
- Full LSA (Least Squares Adjustment) integration
- Ax+L and Bv+W methods
- Interactive adjustment dialog with visual results
- Iteration tracking and convergence monitoring
- Distance-based weighting

#### **Settings Management** ⚙️
- New `settings_manager.py` module
- User-customizable class parameters
- Backup and restore functionality
- JSON-based configuration

---

## 📋 Israeli Survey Regulations - Class System

Compliant with **Survey of Israel Directive ג2 (2021)**

| Class | Tolerance | Max Line (km) | Max Sight Geometric (m) | Method Required |
|-------|-----------|---------------|------------------------|-----------------|
| **H1** | ±3mm√L | Unlimited | 30 | BFFB |
| **H2** | ±5mm√L | 60 | 40 | BFFB |
| **H3** | ±10mm√L | 24 | 50 | BFFB |
| **H4** | ±20mm√L | 10 | 80 | BF |
| **H5** | ±30mm√L | 5 | 100 | BF |
| **H6** | ±60mm√L | 4 | 100 | BF |

**Default Class**: H3 (Third Order Leveling)

### Setting Your Default Class

**GUI Method (Recommended)**:
1. Open the Geodetic Tool
2. Find "Active Class / דרגת דיוק:" at bottom-right
3. Select your desired class (H1-H6)
4. Click ℹ️ button to view detailed parameters

**Programmatic Method**:
```python
from geodetic_tool.config.israel_survey_regulations import set_default_class

# Set to H2
set_default_class("H2")
```

---

## 🚀 Installation

### Prerequisites
- Python 3.8 or higher
- pip package manager

### Quick Install

```bash
# Clone the repository
git clone https://github.com/sealevelil48/Geo_Level_Gravi-.git
cd Geo_Level_Gravi-

# Install dependencies
pip install -r requirements.txt

# Or install the package
pip install -e .
```

### Dependencies
- `pandas >= 1.3.0`
- `numpy >= 1.20.0`

---

## 💻 Usage

### Graphical Interface (GUI)

```bash
# Launch the GUI application
python geodetic_tool/gui/app.py
```

**GUI Features**:
- File browser for selecting measurement files
- Batch processing with progress tracking
- Class selector (H1-H6) with info dialogs
- Network adjustment dialog with LSA
- Export to multiple formats
- Real-time validation feedback

### Command-Line Interface (CLI)

```bash
# Parse and display file information
python geodetic_tool/cli/main.py info measurement.DAT

# Validate files
python geodetic_tool/cli/main.py validate *.DAT *.raw

# Export to specific format
python geodetic_tool/cli/main.py export --format fa0 --project myproject file1.DAT

# Export to all formats
python geodetic_tool/cli/main.py export --format all --project survey_2026 *.DAT

# Export to GeoJSON for QGIS
python geodetic_tool/cli/main.py geojson *.DAT *.raw -o ./output -p network_name
```

### Python API

```python
from geodetic_tool.parsers import create_parser
from geodetic_tool.validators import LevelingValidator
from geodetic_tool.engine.loop_detector import LoopAnalyzer, detect_double_runs
from geodetic_tool.config.israel_survey_regulations import (
    get_default_class,
    get_class_parameters
)

# Parse a measurement file
parser = create_parser('measurement.DAT')
line = parser.parse('measurement.DAT')

# Validate using default class
validator = LevelingValidator()
result = validator.validate(line)
print(f"Valid: {result.is_valid}")

# Get current class parameters
current_class = get_default_class()  # Returns "H3" by default
params = get_class_parameters(3)
print(f"Tolerance: ±{params.tolerance_coefficient}mm√L")

# Analyze loops and double-runs
analyzer = LoopAnalyzer([line1, line2])
pairs = detect_double_runs([line1, line2])
```

---

## 📁 Project Structure

```
geodetic_tool/
├── config/
│   ├── israel_survey_regulations.py  # Directive ג2 implementation
│   ├── settings_manager.py           # Persistent settings
│   ├── models.py                     # Data models
│   └── settings.py                   # App configuration
│
├── parsers/
│   ├── base_parser.py                # Abstract parser
│   ├── trimble_parser.py             # Trimble DAT format
│   └── leica_parser.py               # Leica RAW/GSI format
│
├── validators/
│   └── __init__.py                   # LevelingValidator, BatchValidator
│
├── engine/
│   ├── height_calculator.py          # Height calculations
│   ├── line_adjustment.py            # Line adjustment
│   ├── least_squares.py              # LSA implementation
│   └── loop_detector.py              # Loop detection
│
├── exporters/
│   └── __init__.py                   # FA0, FA1, FTEG, REZ
│
├── gis/
│   └── geojson_export.py             # GeoJSON + QGIS styles
│
├── gui/
│   └── app.py                        # Tkinter GUI
│
└── cli/
    └── main.py                       # CLI interface
```

---

## 🗺️ GIS Integration

Export measurement networks to GeoJSON for visualization in QGIS:

```bash
python geodetic_tool/cli/main.py geojson *.DAT -o ./output -p network
```

**Output Files**:
- `network_lines.geojson` - Line features with measurements
- `network_lines.qml` - QGIS line style (auto-styled by class)
- `network_points.qml` - QGIS point style (benchmarks)

**QGIS Usage**:
1. Open QGIS
2. Load the GeoJSON files
3. Drag the QML files onto the layers for automatic styling

---

## 🧮 Calculation Methods

### Tolerance Calculation
```
T = k × √(Distance_km)

Where k is the tolerance coefficient:
H1: k = 3.0 mm/√km
H2: k = 5.0 mm/√km
H3: k = 10.0 mm/√km
H4: k = 20.0 mm/√km
H5: k = 30.0 mm/√km
H6: k = 60.0 mm/√km
```

### Height Difference
```
ΔH = Σ(Backsight) - Σ(Foresight)
```

### Misclosure Distribution
- **Proportional**: Correction ∝ cumulative distance
- **Equal**: Uniform correction per setup

### Least Squares Adjustment
```
Parametric method: V = A×X - L
Normal equations: N×X = U
Where: N = Aᵀ×P×A, U = Aᵀ×P×L
Weighting: P[i,i] = 1/distance_km
```

---

## 🔧 Configuration

### Settings File Location
- **Windows**: `C:\Users\<username>\.geodetic_tool\settings.json`
- **Linux/Mac**: `~/.geodetic_tool/settings.json`

### Example Settings
```json
{
  "version": "1.0",
  "format": "geodetic_tool_class_parameters",
  "default_class": "H3",
  "class_parameters": {
    "H1": {
      "tolerance_coefficient": 3.0,
      "max_line_length_km": null,
      "max_sight_distance_geometric_m": 30,
      "method_required": "BFFB"
    },
    ...
  }
}
```

### Customization
Edit `geodetic_tool/config/settings.py` for:
- Default encoding preferences
- Benchmark detection patterns
- Parser configurations
- Leica GSI word indices

---

## 📊 Supported File Formats

### Input Formats

| Format | Extensions | Description | Encoding Support |
|--------|-----------|-------------|------------------|
| Trimble DAT | `.dat`, `.DAT` | Trimble digital level data | cp1255, UTF-8, Latin-1 |
| Leica RAW | `.raw`, `.RAW` | Leica raw measurement data | cp1255, UTF-8, Latin-1 |
| Leica GSI | `.gsi`, `.GSI` | Leica GSI-8 and GSI-16 | cp1255, UTF-8, Latin-1 |

### Output Formats

| Format | Description | Use Case |
|--------|-------------|----------|
| **FA0** | Adjustment input | Network adjustment software |
| **FA1** | Detailed adjustment report | Quality control, documentation |
| **FTEG** | Simplified measurement data | Quick data exchange |
| **REZ** | Summary results | Final reports |
| **GeoJSON** | GIS vector format | QGIS, web mapping |

---

## 📚 Documentation

- **[FEATURE_CLASS_SELECTOR.md](FEATURE_CLASS_SELECTOR.md)** - Complete class selector documentation
- **[CLASS_SELECTOR_GUIDE.txt](CLASS_SELECTOR_GUIDE.txt)** - Quick user guide
- **[geodetic_tool/README.md](geodetic_tool/README.md)** - Technical architecture
- **[geodetic_tool/docs/ARCHITECTURE.md](geodetic_tool/docs/ARCHITECTURE.md)** - System architecture

---

## 🧪 Testing

```bash
# Run test suite
python geodetic_tool/tests/test_parsers.py

# Test class selector
python test_class_selector.py
```

---

## 🛠️ Development

### Recent Commits
- **v1.1** - Default class selector with persistent settings
- **v1.1** - Israeli Survey Regulations (Directive ג2) - All 16 features
- **v1.1** - Enhanced Network Adjustment Dialog with LSA
- **v1.1** - Comprehensive LSA (Ax+L and Bv+W methods)
- **v1.0** - Initial release with parsers, validators, exporters

### Contributing
This is an internal tool. For issues or feature requests, please contact the development team.

---

## 📝 License

Internal use only. Not for public distribution.

---

## 🙏 Acknowledgments

- Developed with AI-assisted coding (Claude Sonnet 4.5)
- Compliant with **Israeli Survey of Israel Directive ג2 (2021)**
- Based on geodetic surveying best practices

---

## 📧 Contact

For support or inquiries:
- GitHub: [@sealevelil48](https://github.com/sealevelil48)
- Repository: [Geo_Level_Gravi-](https://github.com/sealevelil48/Geo_Level_Gravi-.git)

---

## 🌟 Features Roadmap

### Completed ✅
- [x] Multi-format parsing (Trimble, Leica)
- [x] Israeli Survey Regulations (H1-H6)
- [x] LSA network adjustment
- [x] GUI with class selector
- [x] Persistent settings
- [x] GeoJSON export for QGIS

### Planned 🔜
- [ ] Automatic report generation (PDF)
- [ ] Web-based interface
- [ ] Cloud storage integration
- [ ] Real-time GPS integration
- [ ] Advanced visualization dashboard

---

**Built with precision for Israeli geodetic surveying** 🇮🇱
