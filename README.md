# Geodetic Leveling Tool v1.1 + QGIS Plugin
## כלי אוטומציה לפילוס גיאודטי — תוסף QGIS

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![QGIS](https://img.shields.io/badge/QGIS-3.0%2B-green.svg)](https://qgis.org/)
[![Branch](https://img.shields.io/badge/Branch-GeoLevelGraviQGIS-orange.svg)](https://github.com/sealevelil48/Geo_Level_Gravi-/tree/GeoLevelGraviQGIS)
[![License](https://img.shields.io/badge/License-Internal-red.svg)]()
[![Israeli Survey Regulations](https://img.shields.io/badge/Regulations-Directive%20%D7%932%202021-green.svg)]()

A comprehensive Python application for automating geodetic leveling calculations and survey data processing, compliant with **Israeli Survey Regulations (Directive ג2, 2021)**.

This branch (`GeoLevelGraviQGIS`) extends the core tool with a **native QGIS plugin** — replacing the Tkinter desktop GUI with a fully integrated PyQt5/PyQGIS interface that loads results directly into the QGIS map canvas.

---

## 🆕 What's New in This Branch — QGIS Plugin

### Plugin Architecture

| File | Role |
|------|------|
| `GeoLevelQGIS/__init__.py` | QGIS entry point — `classFactory(iface)` |
| `GeoLevelQGIS/metadata.txt` | Plugin registry metadata (name, version, qgisMinimumVersion) |
| `GeoLevelQGIS/geo_level_plugin.py` | Main plugin class — lifecycle, bridge, layer loading |
| `GeoLevelQGIS/geo_level_dialog.py` | PyQt5 UI dialog (file selector, class selector, output dir, Run button) |
| `GeoLevelQGIS/core_logic/process.py` | Bridge entry-point — parse → validate → export pipeline |
| `GeoLevelQGIS/core_logic/` | Full copy of the geodetic engine (parsers, validators, engine, gis) |

### QGIS Plugin Features
- **Native PyQt5 UI** — replaces Tkinter, runs inside QGIS without a separate window
- **Multi-file selector** — browse and queue `.DAT`, `.RAW`, `.GSI` files
- **H1-H6 class selector** — live description label updates per class
- **Output directory picker** — choose where GeoJSON and QML files are written
- **Run Adjustment & Map button** — single click triggers the full pipeline
- **Auto layer loading** — results appear directly on the QGIS map canvas
- **QML auto-styling** — lines and points styled automatically by class
- **QGIS message bar** — success/error feedback in the native QGIS status bar
- **QGIS log panel** — full processing log in Plugins → Log Messages

---

## 🚀 QGIS Plugin Installation

### Prerequisites
- QGIS 3.0 or higher (tested on QGIS 3.34.4)
- Python 3.8+ (bundled with QGIS on Windows)

### Step 1 — Copy the plugin folder

**Windows (your machine):**
```bat
xcopy "GeoLevelQGIS" "%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\GeoLevelQGIS" /E /I /Y
```

**Linux / macOS:**
```bash
cp -r GeoLevelQGIS ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/GeoLevelQGIS
```

> The plugin was already deployed automatically to:
> `C:\Users\user01\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\GeoLevelQGIS`

### Step 2 — Enable the plugin in QGIS
1. Open QGIS 3.34.4
2. Menu → **Plugins → Manage and Install Plugins…**
3. Switch to the **Installed** tab
4. Find **Geo Level Gravi** → tick the checkbox
5. Click **Close** — the toolbar icon appears

### Step 3 — Run the plugin
1. Click the **Geo Level Gravi** toolbar icon (or menu → Plugins → Geo Level Gravi)
2. Add your `.DAT` / `.RAW` / `.GSI` measurement files
3. Select precision class (H1–H6, default H3)
4. Choose an output directory
5. Click **▶ Run Adjustment & Map**
6. Results load automatically as a new QGIS vector layer

---

## 🔄 Development Workflow (Plugin Reloader)

Install the **Plugin Reloader** plugin from the QGIS Plugin Manager, then:

1. Menu → **Plugins → Plugin Reloader → Configure** → select `GeoLevelQGIS`
2. After any code change, press **Ctrl+F5** to reload — no QGIS restart needed

**Sanity check in QGIS Python Console (Ctrl+Alt+P):**
```python
import GeoLevelQGIS
print("Plugin found:", GeoLevelQGIS.__file__)
```

---

## 🗂️ Plugin Folder Structure

```
GeoLevelQGIS/
├── __init__.py                  ← classFactory(iface) — QGIS entry point
├── metadata.txt                 ← Plugin registry (name, version 1.1, qgisMinimumVersion 3.0)
├── geo_level_plugin.py          ← GeoLevelPlugin class (initGui, run, unload)
├── geo_level_dialog.py          ← PyQt5 dialog (4 UI elements)
└── core_logic/                  ← Geodetic engine
    ├── process.py               ← Bridge: parse → validate → export
    ├── parsers/                 ← Trimble DAT, Leica RAW/GSI
    ├── validators/              ← H1-H6 regulation checks
    ├── engine/                  ← LSA, line adjustment, loop detection
    ├── gis/                     ← GeoJSON export + QML style generator
    ├── exporters/               ← FA0, FA1, FTEG, REZ
    └── config/                  ← Class registry, settings, models
```

---

## 🔌 Plugin Processing Pipeline

```
User clicks "Run Adjustment & Map"
        │
        ▼
geo_level_plugin.py → run()
        │  reads: file_paths, leveling_class, output_dir
        ▼
core_logic/process.py → process_geodetic_data()
        │
        ├─ 1. PARSE    create_parser(fp).parse(fp)
        │              auto-detects Trimble / Leica format
        │
        ├─ 2. VALIDATE BatchValidator(class_num).validate_batch(lines)
        │              applies H1-H6 tolerance, sight distance, method rules
        │
        └─ 3. EXPORT   export_network_to_geojson(lines, output_dir)
                       writes geo_level_result_lines.geojson + .qml
        │
        ▼
QgsVectorLayer(lines_geojson) + layer.loadNamedStyle(qml)
QgsProject.instance().addMapLayer(layer)
        │
        ▼
Layer appears on QGIS map canvas ✅
```

---

## 📋 Israeli Survey Regulations — Class System

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

---

## 🎯 Key Features (Core Tool)

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

### 📤 Export Formats
- **FA0**: Adjustment input format (benchmarks + observations)
- **FA1**: Detailed adjustment report with iterations
- **FTEG**: Simplified measurement data
- **REZ**: Summary results
- **GeoJSON**: GIS-compatible format for QGIS visualization

---

## 💻 Standalone Usage (without QGIS)

### Graphical Interface (Tkinter)
```bash
python geodetic_tool/gui/app.py
```

### Command-Line Interface
```bash
# Parse and display file information
python geodetic_tool/cli/main.py info measurement.DAT

# Validate files
python geodetic_tool/cli/main.py validate *.DAT *.raw

# Export to GeoJSON for QGIS
python geodetic_tool/cli/main.py geojson *.DAT *.raw -o ./output -p network_name
```

### Python API
```python
from geodetic_tool.parsers import create_parser
from geodetic_tool.validators import LevelingValidator
from geodetic_tool.config.israel_survey_regulations import get_default_class

parser = create_parser('measurement.DAT')
line = parser.parse('measurement.DAT')

validator = LevelingValidator()
result = validator.validate(line)
print(f"Valid: {result.is_valid}")
```

---

## 🧮 Calculation Methods

### Tolerance
```
T = k × √(Distance_km)    [mm]

H1: k = 3    H2: k = 5    H3: k = 10
H4: k = 20   H5: k = 30   H6: k = 60
```

### Height Difference
```
ΔH = Σ(Backsight) - Σ(Foresight)
```

### Least Squares Adjustment
```
Parametric:  V = A×X - L
Normal eq:   N×X = U   where N = Aᵀ×P×A, U = Aᵀ×P×L
Weighting:   P[i,i] = 1/distance_km
```

---

## 📊 Supported File Formats

| Format | Extensions | Description |
|--------|-----------|-------------|
| Trimble DAT | `.dat`, `.DAT` | Trimble digital level data |
| Leica RAW | `.raw`, `.RAW` | Leica raw measurement data |
| Leica GSI | `.gsi`, `.GSI` | Leica GSI-8 and GSI-16 |

---

## 🛠️ Development

### Branch Overview

| Branch | Purpose |
|--------|---------|
| `main` | Stable core tool (Tkinter GUI + CLI) |
| `GeoLevelGraviQGIS` | QGIS plugin wrapper (this branch) |

### Recent Commits
- **v1.1 QGIS** — Native QGIS plugin with PyQt5 UI and core logic bridge
- **v1.1 QGIS** — Auto-deployment to `%APPDATA%\QGIS\...\plugins\GeoLevelQGIS`
- **v1.1** — Default class selector with persistent settings
- **v1.1** — Israeli Survey Regulations (Directive ג2) — All 16 features
- **v1.1** — Enhanced Network Adjustment Dialog with LSA
- **v1.0** — Initial release with parsers, validators, exporters

### Installation (standalone)
```bash
git clone https://github.com/sealevelil48/Geo_Level_Gravi-.git
cd Geo_Level_Gravi-
git checkout GeoLevelGraviQGIS
pip install -r requirements.txt
```

---

## 🌟 Features Roadmap

### Completed ✅
- [x] Multi-format parsing (Trimble, Leica)
- [x] Israeli Survey Regulations (H1-H6)
- [x] LSA network adjustment
- [x] Tkinter GUI with class selector
- [x] Persistent settings
- [x] GeoJSON export for QGIS
- [x] **QGIS native plugin (PyQt5 UI)**
- [x] **Auto layer loading into QGIS map canvas**
- [x] **QML auto-styling by precision class**

### Planned 🔜
- [ ] Benchmark coordinate input dialog inside QGIS
- [ ] Automatic PDF report generation
- [ ] QGIS Processing Framework provider (batch toolbox)
- [ ] Cloud storage integration
- [ ] Real-time GPS integration

---

## 📧 Contact

- GitHub: [@sealevelil48](https://github.com/sealevelil48)
- Repository: [Geo_Level_Gravi-](https://github.com/sealevelil48/Geo_Level_Gravi-.git)
- Branch: [GeoLevelGraviQGIS](https://github.com/sealevelil48/Geo_Level_Gravi-/tree/GeoLevelGraviQGIS)

---

**Built with precision for Israeli geodetic surveying** 🇮🇱
