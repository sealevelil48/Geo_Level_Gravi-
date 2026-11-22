# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Geo Level Gravi — QGIS plugin for geometric-leveling processing and least-squares adjustment, compliant with Israeli Survey Regulations (Directive ג2, 2021). Reads Trimble DAT, Leica RAW/GSI and FA0/FA1/REZ summary files with Hebrew encoding (cp1255).

This repository has two branches:
- `main` — standalone tool (Tkinter GUI + CLI) in `geodetic_tool/`, same engine
- `GeoLevelGraviQGIS` — this branch: only the QGIS plugin in `GeoLevelQGIS/` (no standalone copy here)

---

## Commands

```bash
# Engine tests (pytest)
cd GeoLevelQGIS && python -m pytest core_logic/tests
# Plugin-level tests (plain scripts, run from repo root)
python tests/test_point_resolution.py
python tests/test_gpkg_roundtrip.py
python tests/test_vuln.py

# Deploy to QGIS (Windows): mirror-copies GeoLevelQGIS/ into the default profile
deploy.bat
```

---

## Architecture

### Engine (`GeoLevelQGIS/core_logic/`, shared with `main`'s `geodetic_tool/`)

**Input Files → Parsers → Validators → Engine → Exporters/GIS**

- **`config/models.py`** — `LevelingLine`, `StationSetup`, `Benchmark`, `ValidationResult`, `AdjustmentResult`, `LineStatus`.
- **`config/israel_survey_regulations.py`** — H1-H6 parameters, `CLASS_REGISTRY_BY_NAME`.
- **`config/settings_manager.py`** — user settings in `~/.geodetic_tool/settings.json`.
- **`parsers/`** — `create_parser(path)` auto-detects DAT / RAW / GSI / FA0 / FA1 / REZ; `parse_many(path)` returns a list of lines.
- **`validators/`** — `LevelingValidator`, `BatchValidator`.
- **`engine/`** — `least_squares.py`, `network_prep.py` (Steps 1–2, datum check, conditions), loops, line tools.
- **`exporters/`** — FA0, FA1, FTEG, REZ (cp1255, fixed width).
- **`gis/`** — `geojson_export.py`, `topo_estimator.py`.
- **`process.py`** — `process_geodetic_data(paths, class, out_dir, name_overrides, parse_only, preparsed)`.

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
| `geo_level_enhanced_lsa_dialog.py` | `GeoLevelEnhancedLSADialog` — the single adjustment window (network + single line, Steps 1–2, exports) |
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
| `_run_lsa()` | Launch Enhanced LSA dialog (`GeoLevelEnhancedLSADialog`) with support for Parametric and Conditional adjustment methods → `_apply_lsa_to_layer` |
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
│   ├── Line Adjustment…
│   ├── Network Adjustment (LSA)…  [Enhanced LSA engine]
│   └── Merge Line Segments…
├── Settings / הגדרות
│   ├── Class Parameters…
│   ├── Encoding…
│   ├── Point Exclusion…
│   └── Database Connection…
└── Help / עזרה
    └── About Geo Level Gravi
```

**Note**: "Network Adjustment (LSA)" is now unified with the Enhanced LSA engine (supports both Parametric and Conditional adjustment methods). The legacy standard LSA implementation has been deprecated.

### `GeoLevelDockWidget` (`geo_level_dockwidget.py`)

Left panel: `QListWidget` (colour-coded green/red by validation) + class combo + `+ Add Files` button.

Right panel: `QTabWidget` with 4 tabs:
- **Line Details** — 6-col `QTableWidget`: #, From, To, Backsight (m), Foresight (m), Distance (m)
- **Validation** — status label + error/warning `QTextEdit`
- **Analysis** — Double-Runs, Loops, and Network Adjustment (LSA) buttons
- **Log** — read-only log messages

Signals: `line_selected(int)`, `adjust_line_requested(int)`, `lsa_requested()`, `files_added(list,str,str)`, `double_runs_requested()`, `loops_requested()`

Key methods: `load_lines(lines, val_results)`, `populate_line_details(line)`, `update_validation_tab(line)`, `show_adjustment_result(text)`, `select_line_by_filename(filename)`, `get_current_index()`, `get_selected_class()`

### `GeoLevelEnhancedLSADialog` (`geo_level_enhanced_lsa_dialog.py`) — the only adjustment window

Opened by "Network Adjustment (LSA)" (dock + menu) and by "Adjust This Line" (same window, only that line ticked).
Constructor: `GeoLevelEnhancedLSADialog(lines, parent, val_results, leveling_class, known_heights, selected_lines, project_name, output_dir)`

- Left: method (Parametric / Conditional), lines table (tick/untick; invalid/excluded greyed), fixed points (Auto-Select from .FA0/.FA1 heights or DB, Fetch from DB), options (Step 1 averaging, weights distance/setups/equal, class)
- Buttons: Prepare (Steps 1–2), Run, **Apply to Map** (accept → plugin `_apply_lsa_to_layer` writes `adj_height` on "Geodetic Points" and stores `last_adjustment` in the GPKG), **Export FA0 / FA1 / PDF**
- Tabs: Summary (σ₀, dof, BM-to-BM misclosures vs k√L), Step 1 Observations, Step 2 Approximate Heights, Adjusted Heights (approx, correction, σ, 95%), Residuals, Line Adjustment (single line between two known heights, Directive G2 App. C), Matrix Diagnostics

### Adjustment engine (`core_logic/engine/least_squares.py`, `network_prep.py`)

- **Step 1** `merge_observations()`: repeated runs of a section averaged once (dH = (dH_fwd − dH_ret)/2), δ checked against k√L with L = one-direction length; merged weight n_runs/L.
- **Step 2** `approximate_heights()`: multi-source Dijkstra from fixed points (else any known height, else relative 0.000).
- `check_datum()`: raises `NetworkDatumError` when a connected group has no fixed point (no silent singular solutions).
- **Parametric**: L = Lb − L0 (L0 by direction: FROM fixed → −H, TO fixed → +H, both → H_to − H_from), X = N⁻¹U solved directly (linear model, no iterations), V = AX − L, σ₀² = VᵀPV/(n−u).
- **Conditional**: independent conditions from fundamental loops of a spanning forest + (n_fixed−1) BM paths per group; k = −M⁻¹w, v = P⁻¹Bᵀk.
- Verified against "Condition Adjustment_BvW.pdf" example 1 (RFH-95/96/97 → A=121.344, B=117.573, C=110.190, D=102.764, σ₀=4.512) in `core_logic/tests/test_adjustment.py`.

### Summary-file import (`core_logic/parsers/fa0_parser.py`, `fa1_parser.py`, `rez_parser.py`)

`parser.parse_many(path)` returns one `LevelingLine` per row (`is_summary=True`, setup count kept with evenly split placeholder setups); known heights in `parser.benchmarks` → `process_geodetic_data()["file_benchmarks"]` → plugin `_file_benchmarks` (persisted in `lv_meta`). Summary files are detected by extension before content sniffing.

### Position estimation (`core_logic/gis/topo_estimator.py`)

One multi-source Dijkstra on measured line lengths (`_nearest_anchors`): every unknown point keeps its nearest anchor and the nearest anchor at a different position; paths never pass through another anchor. Unknown points (also chains PKT1→PKT2) are placed between the two anchors at r = d1/(d1+d2), or at the path length from a single anchor. No radius limit. O(E log V) regardless of the anchor count.

### Load pipeline & performance (`geo_level_plugin._process_files`)

1. `process_geodetic_data(..., parse_only=True)` — parse only; `result["parsed"]` is a pickle snapshot of the pristine lines.
2. `_check_point_resolution` — `get_candidates_batch` (`prefetch` = one SQL per 500 names over all 6 name-key expressions + one `unnest`/`ST_DWithin` twin query), `resolve_network`, dialog rounds. `search_fn` = `db_mgr.search()` (number-only query matches `mispar_nekuda`, never the glued `mispar||ot` form).
3. `process_geodetic_data(..., preparsed=parsed)` — full run on the snapshot (taken over, not copied) with verified names.
4. Inside `_frozen_canvas(iface)`: line layer, point layer, `_sync_map_statuses` (edits only changed features), `_persist_to_gpkg`; one canvas refresh at the end.

`BenchmarkDBManager` keeps ONE psycopg2 connection (`_shared_connection`, autocommit) and per-session caches (`_query_cache`, `_twin_cache`, control-layer index `_layer_index`); `clear_session_cache()` resets all of them. `seed_from_qgis_layer` indexes the `נקודות בקרה` layer once (attributes only) and fetches geometry by feature id. Never add per-point SQL in loops — use `prefetch` / `prefetch_twins`.

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
- **GeoPackage-first state model** (`geo_level_gpkg.py`, schema 2): the project `.gpkg` (default `<output_dir>/geo_level_results.gpkg`) is the authoritative project state — `geo_lines`/`geo_points` map layers plus `lv_lines` (full `LevelingLine` + JSON `validation`), `lv_setups`, `lv_meta` (`leveling_class`, `point_overrides`, `point_origin`, `pid_remap`, `resolved_points` = exact DB record per point / None = estimated, `dat_files`, `output_dir`, `schema_version`). Load Project accepts a `.gpkg` directly or a `.glp`; either way no DAT file is parsed and no DB query runs. Statuses/validation are restored exactly as saved ("saved status wins"; schema-1 files are re-validated then forced back to the saved status). In-memory edits (override/use/direction toggles, point exclusion, class settings) autosave to the lv_* tables via `_autosave_gpkg`; Edit Point rewrites the whole GPKG via `_rebuild_map_layers` → `_persist_to_gpkg`. lv_meta is untrusted input: always pass it through `gp.sanitize_meta()`.
- `.glp` (v2, JSON in `GeoLevelQGIS/projects/`) is a pointer: `{version, class, output_dir, gpkg (relative), files[], point_overrides}`. `files[]` is the legacy fallback when the GPKG is missing; `_repoint_glp` updates `gpkg` if a locked file forced a `*_N.gpkg` name.
- Live registry patching: `GeoLevelSettingsDialog` patches `CLASS_REGISTRY_BY_NAME` objects in-memory so validators immediately use new k values without QGIS restart.
- Validation re-run after settings change: plugin re-runs `BatchValidator` and calls `dock.load_lines` to refresh dock list colours.
- `select_line_by_filename` blocks signals during programmatic selection to avoid re-triggering zoom.
- **Network Adjustment (LSA) Consolidation** (v1.1 November 2026): The legacy standard LSA implementation has been completely removed in favor of the unified Enhanced LSA engine (`GeoLevelEnhancedLSADialog`). All UI triggers (menu, dock buttons, signals) now route to a single `_run_lsa()` method that launches the Enhanced LSA dialog. The Enhanced LSA engine supports both Parametric (Ax+L) and Conditional (Bv+W) adjustment methods with automatic method selection, database integration, and comprehensive results display (adjusted heights, residuals, matrix diagnostics). Users no longer see separate "Network Adjustment (LSA)" and "Network Adjustment (Enhanced)" options — only the unified "Network Adjustment (LSA)" entry point, which opens the full-featured Enhanced LSA dialog.

---

## Dependencies

### QGIS plugin
- `psycopg2` — benchmark DB (bundled with OSGeo4W QGIS); no scipy needed
- All Qt/PyQGIS dependencies bundled with QGIS/OSGeo4W
- `QPrinter`, `QTextDocument` — used for PDF generation (no external libs needed)
- `numpy` — used by `LeastSquaresAdjuster`

---

## Deploy Workflow

Run `deploy.bat` from the repo root (Windows). It uses `robocopy /MIR` from `GeoLevelQGIS/` into `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\GeoLevelQGIS`, excluding `__pycache__` and `tests`. Restart QGIS (or Plugin Reloader) afterwards.

Sanity check in QGIS Python Console (`Ctrl+Alt+P`):
```python
import GeoLevelQGIS
print("Plugin found:", GeoLevelQGIS.__file__)
```
