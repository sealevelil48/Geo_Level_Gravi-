# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Geodetic Leveling Tool v1.1 — a Python application for automating geodetic leveling calculations and survey data processing, compliant with Israeli Survey Regulations (Directive ג2, 2021). Supports Trimble DAT and Leica RAW/GSI input formats with Hebrew encoding (cp1255).

This repository has two branches:
- `main` — standalone tool with Tkinter GUI + CLI
- `GeoLevelGraviQGIS` — native QGIS plugin (PyQt5/PyQGIS), this branch

---

## Commands

```bash
# Run standalone GUI (Tkinter)
python run_gui.py

# Run CLI
python run_cli.py parse *.DAT
python run_cli.py validate *.DAT
python run_cli.py geojson *.DAT -o ./output

# Run tests
python geodetic_tool/tests/test_parsers.py
python test_class_selector.py
python test_lsa.py

# Install dependencies
pip install -r requirements.txt
# Or editable install
pip install -e .

# Deploy QGIS plugin (Windows — run from repo root)
xcopy "GeoLevelQGIS" "%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\GeoLevelQGIS" /E /I /Y
# Then press Ctrl+F5 in QGIS to reload via Plugin Reloader
```

There is no test runner framework (pytest/unittest CLI); tests are run as standalone scripts.

---

## Architecture

### Standalone Tool (`geodetic_tool/`)

**Input Files → Parsers → Validators → Engine → Exporters/GIS**

- **`config/models.py`** — Core dataclasses: `LevelingLine`, `StationSetup`, `Benchmark`, `ValidationResult`, `AdjustmentResult`, `LineStatus`. Shared data structures across all modules.
- **`config/settings.py`** — Global `Settings` singleton (encoding, tolerance, Leica GSI word indices, Trimble format config). Access via `get_settings()`.
- **`config/israel_survey_regulations.py`** — H1-H6 class parameters, tolerance formulas, `CLASS_REGISTRY_BY_NAME` dict, `get_default_class()`, `get_class_parameters()`.
- **`config/settings_manager.py`** — Persistent user settings stored in `~/.geodetic_tool/settings.json`. Methods: `save_class_parameters`, `load_class_parameters`, `update_class_parameters(class_name, params)`, `reset_to_defaults`, `get_default_class`, `set_default_class`.
- **`parsers/`** — `BaseParser` ABC with `TrimbleParser` and `LeicaParser`. Use `create_parser(filename)` for auto-detection. Parsers return `LevelingLine` objects.
- **`validators/__init__.py`** — `LevelingValidator` (single line) and `BatchValidator` (multiple files). Validates endpoints, naming conventions, tolerances per class.
- **`engine/`** — `height_calculator.py`, `line_adjustment.py`, `least_squares.py` (LSA with Ax+L and Bv+W), `loop_detector.py`, `line_coordinator.py`, `adjustment_computations.py`.
- **`exporters/__init__.py`** — All exporters (FA0, FA1, FTEG, REZ) in a single file. Output uses cp1255 encoding with fixed-width formatting.
- **`gis/geojson_export.py`** — GeoJSON export with QGIS QML style files. Point features have `point_id`, `height`, `is_benchmark`, `status` fields. Line features have `filename` field (required for map-to-dock sync).
- **`gui/app.py`** — Tkinter GUI (single file). Includes class selector combo box (H1-H6), network adjustment dialog, batch processing.
- **`cli/main.py`** — CLI with subcommands: `info`, `validate`, `export`, `geojson`.

### QGIS Plugin (`GeoLevelQGIS/`)

**Plugin entry point → Dock Widget → core_logic bridge → QGIS map canvas**

| File | Role |
|------|------|
| `__init__.py` | QGIS entry point — `classFactory(iface)` |
| `metadata.txt` | Plugin registry (name, version 1.1, qgisMinimumVersion 3.0) |
| `geo_level_plugin.py` | `GeoLevelPlugin` — lifecycle, menus, pipeline, map sync |
| `geo_level_dockwidget.py` | `GeoLevelDockWidget` — main dock panel (list + 3 tabs) |
| `geo_level_dialog.py` | Legacy popup `QDialog` for file/class/output selection |
| `geo_level_settings_dialog.py` | `GeoLevelSettingsDialog` — editable H1-H6 parameters table |
| `geo_level_lsa_dialog.py` | `GeoLevelLSADialog` — LSA results viewer with PDF export button |
| `geo_level_reporter.py` | `GeoLevelReporter` — PDF report generator via QPrinter |
| `core_logic/` | Full copy of the geodetic engine (parsers, validators, engine, gis) |

---

## QGIS Plugin — Key Classes & Methods

### `GeoLevelPlugin` (`geo_level_plugin.py`)

State: `_lines: List[LevelingLine]`, `_val_results`, `_layer: QgsVectorLayer`, `_last_class`, `_last_output_dir`

| Method | Purpose |
|--------|---------|
| `initGui()` | Builds 4-submenu top-level `&Geo Leveling` menu + toolbar icon + dock |
| `_process_files(file_paths, class, output_dir)` | Shared pipeline: parse → validate → export → load layer → populate dock |
| `_prepare_layer_signals(layer)` | Connects `selectionChanged` on "Geodetic Results" layers |
| `_sync_dock_to_map(layer)` | Map feature selection → dock list highlight via `feat["filename"]` |
| `_zoom_to_line_feature(idx)` | Filters line geometries, scales bbox 1.2×, sets canvas extent |
| `_run_lsa()` | Input dialog for fixed points → `LeastSquaresAdjuster` → `GeoLevelLSADialog` → `_apply_lsa_to_layer` |
| `_apply_lsa_to_layer(result)` | Adds `adj_height` QgsField to point features, populates from `result.adjusted_heights` |
| `_save_project()` / `_load_project()` | Serialise/deserialise `.glp` JSON in `GeoLevelQGIS/projects/` |
| `_show_class_settings()` | Opens `GeoLevelSettingsDialog`, re-runs validation after save |

Menu structure:
```
&Geo Leveling
├── Project / פרויקט
│   ├── Open / Load Files…
│   ├── Save Project…
│   ├── Load Project…
│   └── Project Properties…
├── Analysis / ניתוח
│   ├── Validate All Lines
│   ├── Detect Loops / Double-Runs
│   ├── Network Adjustment (LSA)…
│   └── Adjust Selected Line
├── Settings / הגדרות
│   ├── Class Parameters…
│   └── Encoding…
└── Help / עזרה
    └── About Geo Level Gravi
```

### `GeoLevelDockWidget` (`geo_level_dockwidget.py`)

Left panel: `QListWidget` (colour-coded green/red by validation) + class combo + `+ Add Files` button.

Right panel: `QTabWidget` with 3 tabs:
- **Line Details** — 6-col `QTableWidget`: #, From, To, Backsight (m), Foresight (m), Distance (m)
- **Validation** — status label + error/warning `QTextEdit`
- **Adjustment** — Adjust This Line + LSA buttons + result `QTextEdit`

Signals: `line_selected(int)`, `adjust_line_requested(int)`, `lsa_requested()`, `run_requested(list,str,str)`, `files_added(list,str,str)`

Key methods: `load_lines(lines, val_results)`, `populate_line_details(line)`, `update_validation_tab(line)`, `show_adjustment_result(text)`, `select_line_by_filename(filename)`, `get_current_index()`, `get_selected_class()`

### `GeoLevelLSADialog` (`geo_level_lsa_dialog.py`)

Constructor: `GeoLevelLSADialog(result: AdjustmentResult, fixed_points: dict, parent)`

- Statistics group box: σ₀², σ₀ (mm), DoF, iterations, K, total dist
- Heights table (4 cols): Point ID, Adjusted Height (m), Correction (mm), Std Dev (mm) — fixed points highlighted light-blue
- Residuals table (2 cols): Observation, Residual (mm) — >5 mm highlighted orange
- Bottom row: **[Export PDF Report]** (dark navy) + **[Close]**
- `on_export_report()` → `QFileDialog.getSaveFileName` → `GeoLevelReporter.generate_pdf(path)`

### `GeoLevelReporter` (`geo_level_reporter.py`)

Constructor: `GeoLevelReporter(result: AdjustmentResult, fixed_points: dict, project_info: dict = None)`

`project_info` keys: `company`, `surveyor`, `project_name`, `class`

`generate_pdf(output_path)` — uses `QPrinter(HighResolution)` + `QTextDocument` + `QTextCursor`. Zero external dependencies.

PDF sections:
1. Header — bilingual title, company/surveyor meta, compliance text (`Directive ג2 (2021)`), navy rule
2. Network Statistics — 2-col table: σ₀², σ₀, DoF, iterations, K, total dist, fixed/adjusted counts
3. Adjusted Heights — 5-col table with navy header; fixed rows light-blue; correction `—` for unknowns
4. Observation Residuals — 2-col table; >5 mm residuals highlighted orange
5. Digital Stamp — `[GeoLevel-LSA-Verified-YYYY]` + signature line

BiDi: `_detect_bidi()` scans point IDs for Hebrew chars (U+05D0–U+05EA); if found, sets `QTextOption(Qt.RightToLeft)` on the document.

### `GeoLevelSettingsDialog` (`geo_level_settings_dialog.py`)

6×6 editable `QTableWidget` for H1-H6 parameters: k, Max Sight, Method, Max Line (∞ for None), Imbalance.

On Save: validates inputs → patches live `CLASS_REGISTRY_BY_NAME` in-memory → calls `mgr.update_class_parameters` per class → `accept()`.

Reset to Defaults: calls `mgr.reset_to_defaults()` + `importlib.reload(israel_survey_regulations)` → reloads table.

---

## Model Field Names (Critical — avoid wrong names)

### `StationSetup`
- `backsight_reading` (not `backsight_h`)
- `foresight_reading` (not `foresight_h`)
- `distance_back`, `distance_fore` (not `distance_m`)
- `setup_number`, `from_point`, `to_point`

### `LevelingLine`
- `start_point`, `end_point`, `filename`
- `total_distance` (metres), `total_height_diff`
- `setups: List[StationSetup]`
- `status: LineStatus` (enum — use `.value` for string)
- `num_setups` (property = `len(setups)`)

### `AdjustmentResult`
- `adjusted_heights: Dict[str, float]`
- `mse_heights: Dict[str, float]`
- `mse_unit_weight: float` (metres — multiply by 1000 for mm)
- `residuals: Dict[str, float]` (already in mm)
- `k_coefficient: float`
- `total_distance_km: float`
- `iteration: int`

### `ClassParameters` (in `CLASS_REGISTRY_BY_NAME`)
- `tolerance_coefficient` (not `k`)
- `required_method` (not `method_required`)
- `max_sight_distance_geometric_m`
- `max_line_length_km` (None = unlimited)
- `max_cumulative_distance_imbalance_m`

---

## Important Patterns

- `CLASS_REGISTRY_BY_NAME` — use this dict (not `ALL_CLASSES` which doesn't exist). Keys: `["H1","H2","H3","H4","H5","H6"]`.
- `create_parser(filename)` — auto-detects Trimble/Leica format from file content.
- Hebrew encoding (cp1255) is the default for both reading input files and writing exports. Fallback chain: cp1255 → UTF-8 → Latin-1.
- Tolerance formula: `T = k × √(distance_km)` where k varies by class (3 mm for H1 to 60 mm for H6).
- User settings persist to `~/.geodetic_tool/settings.json` and survive between sessions.
- `filename` attribute is present on GeoJSON line features — required for `_sync_dock_to_map`.
- Project files saved as `.glp` JSON in `GeoLevelQGIS/projects/` containing `{class, output_dir, files[]}`.
- Live registry patching: `GeoLevelSettingsDialog` patches `CLASS_REGISTRY_BY_NAME` objects in-memory so validators immediately use new k values without QGIS restart.
- Validation re-run after settings change: plugin re-runs `BatchValidator` and calls `dock.load_lines` to refresh dock list colours.
- `select_line_by_filename` blocks signals during programmatic selection to avoid re-triggering zoom.

---

## Dependencies

### Standalone tool
- `pandas`, `numpy` (plus `tkinter` from stdlib for GUI)

### QGIS plugin
- All Qt/PyQGIS dependencies bundled with QGIS/OSGeo4W
- `QPrinter`, `QTextDocument` — used for PDF generation (no external libs needed)
- `numpy` — used by `LeastSquaresAdjuster`

---

## Deploy Workflow

```bat
REM From repo root (Windows):
xcopy "GeoLevelQGIS" "%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\GeoLevelQGIS" /E /I /Y
```

After deploy, press `Ctrl+F5` in QGIS (Plugin Reloader) to reload without restart.

Sanity check in QGIS Python Console (`Ctrl+Alt+P`):
```python
import GeoLevelQGIS
print("Plugin found:", GeoLevelQGIS.__file__)
```
