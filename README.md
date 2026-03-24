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

## 🆕 What's New in v1.1 — Full Feature List

### Plugin Architecture

| File | Role |
|------|------|
| `GeoLevelQGIS/__init__.py` | QGIS entry point — `classFactory(iface)` |
| `GeoLevelQGIS/metadata.txt` | Plugin registry metadata (name, version 1.1, qgisMinimumVersion 3.0) |
| `GeoLevelQGIS/geo_level_plugin.py` | Main plugin class — lifecycle, menus, pipeline, map↔dock sync |
| `GeoLevelQGIS/geo_level_dockwidget.py` | PyQt5 dock panel — file list, 3-tab detail view, class selector |
| `GeoLevelQGIS/geo_level_dialog.py` | Legacy popup dialog (file selector, class selector, output dir) |
| `GeoLevelQGIS/geo_level_settings_dialog.py` | Editable H1-H6 class parameters table with live registry patching |
| `GeoLevelQGIS/geo_level_lsa_dialog.py` | LSA results viewer — heights, corrections, std devs, residuals |
| `GeoLevelQGIS/geo_level_reporter.py` | PDF report generator via QPrinter — zero external dependencies |
| `GeoLevelQGIS/core_logic/process.py` | Bridge entry-point — parse → validate → export pipeline |
| `GeoLevelQGIS/core_logic/` | Full copy of the geodetic engine (parsers, validators, engine, gis) |

---

## 🖥️ Dock Widget

The plugin replaces the popup dialog with a persistent **QDockWidget** docked to the right side of QGIS.

### Left Panel
- `QListWidget` — loaded leveling lines, colour-coded **green** (valid) / **red** (invalid)
- `+ Add Files` button — opens file dialog, emits `files_added` signal to trigger the full pipeline
- Precision class combo (H1–H6) with live description label

### Right Panel — 3 Tabs

| Tab | Content |
|-----|---------|
| **Line Details** | 6-column `QTableWidget`: #, From, To, Backsight (m), Foresight (m), Distance (m) |
| **Validation** | Status label (✅ VALID / ❌ INVALID + reason), error/warning text area |
| **Adjustment** | "Adjust This Line" + "LSA — Full Network Adjustment" buttons + result text area |

### Map ↔ Dock Sync
- Selecting a feature on the QGIS map canvas highlights the matching line in the dock list
- Selecting a line in the dock zooms the map canvas to that line's geometry (1.2× bbox scale)
- Sync uses the `filename` attribute on GeoJSON line features

---

## 📋 Top-Level Menu — `&Geo Leveling`

Inserted before the QGIS Help menu:

```
&Geo Leveling
├── Project / פרויקט
│   ├── Open / Load Files…        — file + class + output dir dialog
│   ├── Save Project…             — serialise to .glp JSON
│   ├── Load Project…             — restore from .glp JSON
│   └── Project Properties…       — lines, points, class, layer, output dir
├── Analysis / ניתוח
│   ├── Validate All Lines        — re-run BatchValidator, refresh dock colours
│   ├── Detect Loops / Double-Runs — LoopAnalyzer with misclosure check
│   ├── Network Adjustment (LSA)… — full LSA with fixed-point input dialog
│   └── Adjust Selected Line      — proportional misclosure on current line
├── Settings / הגדרות
│   ├── Class Parameters…         — editable H1-H6 table
│   └── Encoding…                 — input file encoding (cp1255/utf-8/latin-1)
└── Help / עזרה
    └── About Geo Level Gravi
```

---

## 🧮 LSA Results Viewer

`Analysis → Network Adjustment (LSA)…` opens a fixed-point input dialog, then shows:

### Statistics Group Box
- σ₀² (Reference Variance), σ₀ (Unit Weight Std Dev in mm)
- Degrees of Freedom, Iterations, K Coefficient, Total Distance

### Adjusted Heights Table
| Point ID | Adjusted Height (m) | Correction (mm) | Std Dev (mm) |
|----------|--------------------:|----------------:|-------------:|
| Fixed points highlighted **light-blue** | | | |
| Unknown points show `—` for Correction | | | |

### Residuals Table
- Observation key (`FROM-TO`), Residual (mm)
- Residuals > 5 mm highlighted **orange**

### Map Layer Update
After adjustment, an `adj_height` field is added to point features in the "Geodetic Results" layer and populated with the LSA results.

---

## 📄 PDF Report Generator

Click **Export PDF Report / הפק דוח PDF** in the LSA dialog to produce a formal survey certificate.

**Zero external dependencies** — uses `QPrinter` + `QTextDocument` bundled with QGIS/OSGeo4W.

### Report Sections
1. **Header** — bilingual title (EN + Hebrew), company name placeholder, surveyor/project/class meta, date, compliance text (`Directive ג2 (2021)`), navy rule
2. **Network Statistics** — 2-column table: σ₀², σ₀, DoF, iterations, K, total distance, fixed/adjusted point counts
3. **Adjusted Heights** — 5-column table with navy header row; fixed points highlighted light-blue; correction `—` for unknowns
4. **Observation Residuals** — 2-column table; residuals > 5 mm highlighted orange
5. **Digital Stamp** — `[GeoLevel-LSA-Verified-YYYY]` + authorized signature line

### BiDi Support
Hebrew characters in point IDs are auto-detected (U+05D0–U+05EA). If found, `QTextOption(Qt.RightToLeft)` is applied to the document for correct right-to-left rendering.

### Customisation via `project_info` dict
```python
GeoLevelReporter(result, fixed_points, project_info={
    "company":      "My Survey Company",
    "surveyor":     "Eng. Name",
    "project_name": "Highway 1 Survey",
    "class":        "H3"
})
```

---

## ⚙️ Class Parameters Settings

`Settings → Class Parameters…` opens an editable table of all H1-H6 regulation parameters:

| Column | Description |
|--------|-------------|
| Class | Read-only label |
| k (mm/√km) | Tolerance coefficient — `T = k × √L` |
| Max Sight Geom. (m) | Maximum geometric sight distance |
| Method Required | BF, BFFB, or FB |
| Max Line (km) | `∞` for unlimited |
| Max Dist Imbal. (m) | Maximum cumulative distance imbalance |

- **Save** — validates inputs, patches live `CLASS_REGISTRY_BY_NAME` in-memory (no QGIS restart needed), persists to `~/.geodetic_tool/settings.json`
- **Reset to Defaults** — deletes `settings.json`, reloads Survey of Israel defaults

---

## 💾 Project Files

`Project → Save Project…` / `Load Project…` serialises the current session to a `.glp` JSON file:

```json
{
  "class": "H3",
  "output_dir": "C:/survey/output",
  "files": ["C:/survey/KMA58.DAT", "C:/survey/KMA59.DAT"]
}
```

Files are saved in `GeoLevelQGIS/projects/`. Missing files are reported on load.

---

## 🚀 QGIS Plugin Installation

### Prerequisites
- QGIS 3.0 or higher (tested on QGIS 3.34.4)
- Python 3.8+ (bundled with QGIS on Windows)

### Step 1 — Copy the plugin folder

**Windows:**
```bat
xcopy "GeoLevelQGIS" "%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\GeoLevelQGIS" /E /I /Y
```

**Linux / macOS:**
```bash
cp -r GeoLevelQGIS ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/GeoLevelQGIS
```

### Step 2 — Enable the plugin in QGIS
1. Open QGIS 3.34.4
2. Menu → **Plugins → Manage and Install Plugins…**
3. Switch to the **Installed** tab
4. Find **Geo Level Gravi** → tick the checkbox
5. Click **Close** — the toolbar icon and `&Geo Leveling` menu appear

### Step 3 — Run the plugin
1. Click the **Geo Level Gravi** toolbar icon (or `Geo Leveling → Project → Open / Load Files…`)
2. Add your `.DAT` / `.RAW` / `.GSI` measurement files
3. Select precision class (H1–H6, default H3)
4. Choose an output directory
5. Results load automatically as a new QGIS vector layer; dock panel opens on the right

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
├── __init__.py                    ← classFactory(iface) — QGIS entry point
├── metadata.txt                   ← Plugin registry (name, version 1.1, qgisMinimumVersion 3.0)
├── geo_level_plugin.py            ← GeoLevelPlugin — menus, pipeline, map sync
├── geo_level_dockwidget.py        ← GeoLevelDockWidget — dock panel (list + 3 tabs)
├── geo_level_dialog.py            ← Legacy popup dialog
├── geo_level_settings_dialog.py   ← H1-H6 class parameters editor
├── geo_level_lsa_dialog.py        ← LSA results viewer + PDF export button
├── geo_level_reporter.py          ← PDF report generator (QPrinter, zero deps)
├── projects/                      ← .glp project files saved here
└── core_logic/                    ← Geodetic engine
    ├── process.py                 ← Bridge: parse → validate → export
    ├── parsers/                   ← Trimble DAT, Leica RAW/GSI
    ├── validators/                ← H1-H6 regulation checks
    ├── engine/                    ← LSA, line adjustment, loop detection
    ├── gis/                       ← GeoJSON export + QML style generator
    ├── exporters/                 ← FA0, FA1, FTEG, REZ
    └── config/                    ← Class registry, settings, models
```

---

## 🔌 Plugin Processing Pipeline

```
User clicks "Open / Load Files…" or "+ Add Files"
        │
        ▼
geo_level_plugin.py → _process_files(file_paths, class, output_dir)
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
GeoLevelDockWidget.load_lines(lines, val_results)
        │  colour-codes list items green/red
        ▼
Layer appears on QGIS map canvas ✅

─────────────────────────────────────────────────────
User clicks "Network Adjustment (LSA)…"
        │
        ▼
QInputDialog → fixed_points {BM1: 100.000, BM2: 105.500}
        │
        ▼
LeastSquaresAdjuster().adjust_from_lines(lines, fixed_points)
        │  solves (AᵀPA)x̂ = AᵀPL
        ▼
GeoLevelLSADialog(result, fixed_points)
        │  shows σ₀², DoF, adjusted heights, residuals
        │
        ├─ "Export PDF Report" → GeoLevelReporter.generate_pdf(path)
        │                        QPrinter → A4 PDF with 5 sections
        │
        └─ _apply_lsa_to_layer(result)
           adds adj_height field to point features ✅
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

All parameters are editable at runtime via `Settings → Class Parameters…` and persist to `settings.json`.

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
- **Least Squares Adjustment (LSA)**: Network adjustment — parametric method `V = Ax̂ − L`, normal equations `(AᵀPA)x̂ = AᵀPL`, weight `P[i,i] = 1/dist_km`
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
- **PDF**: Formal survey certificate via `GeoLevelReporter` (QPrinter, zero deps)

---

## 💻 Standalone Usage (without QGIS)

### Graphical Interface (Tkinter)
```bash
python geodetic_tool/gui/app.py
```

### Command-Line Interface
```bash
python geodetic_tool/cli/main.py info measurement.DAT
python geodetic_tool/cli/main.py validate *.DAT *.raw
python geodetic_tool/cli/main.py geojson *.DAT *.raw -o ./output -p network_name
```

### Python API
```python
from geodetic_tool.parsers import create_parser
from geodetic_tool.validators import LevelingValidator

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
σ₀² = VᵀPV / (n - u)   where n = observations, u = unknowns
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

### Recent Commits (v1.1)
- **PDF Report** — `GeoLevelReporter` using QPrinter + QTextDocument; BiDi Hebrew support; digital stamp; Export PDF button in LSA dialog
- **LSA Results Dialog** — `GeoLevelLSADialog` with σ₀², DoF, adjusted heights table, residuals table; `adj_height` field written to map layer
- **Class Parameters Dialog** — `GeoLevelSettingsDialog` with live registry patching and `settings.json` persistence
- **Top-Level Menu** — `&Geo Leveling` with 4 sub-menus (Project, Analysis, Settings, Help)
- **Dock Widget** — `GeoLevelDockWidget` replacing popup dialog; 3-tab detail view; map↔dock sync
- **GeoJSON NULL Fix** — point features correctly populate `point_id`, `height`, `is_benchmark`, `status`
- **Project Save/Load** — `.glp` JSON serialisation
- **Loop Detection** — `LoopAnalyzer` with misclosure tolerance check

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
- [x] LSA network adjustment (parametric method)
- [x] Tkinter GUI with class selector
- [x] Persistent settings
- [x] GeoJSON export for QGIS
- [x] **QGIS native plugin (PyQt5 UI)**
- [x] **Dock widget with 3-tab detail view**
- [x] **Top-level &Geo Leveling menu (4 sub-menus)**
- [x] **Map ↔ dock bidirectional sync**
- [x] **Auto layer loading into QGIS map canvas**
- [x] **QML auto-styling by precision class**
- [x] **LSA Results Viewer dialog (σ₀², DoF, heights, residuals)**
- [x] **adj_height field written to QGIS point layer after LSA**
- [x] **PDF report generator (QPrinter, zero external deps, BiDi Hebrew)**
- [x] **Class Parameters settings dialog with live registry patching**
- [x] **Project Save/Load (.glp JSON)**
- [x] **Loop detection with misclosure check**

### Planned 🔜
- [ ] Benchmark coordinate input dialog inside QGIS
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
