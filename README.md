# Geodetic Leveling Tool v1.1 + QGIS Plugin
## כלי אוטומציה לפילוס גיאודטי — תוסף QGIS

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![QGIS](https://img.shields.io/badge/QGIS-3.0%2B-green.svg)](https://qgis.org/)
[![Branch](https://img.shields.io/badge/Branch-GeoLevelGraviQGIS-orange.svg)](https://github.com/sealevelil48/Geo_Level_Gravi-/tree/GeoLevelGraviQGIS)
[![License](https://img.shields.io/badge/License-Internal-red.svg)]()
[![Israeli Survey Regulations](https://img.shields.io/badge/Regulations-Directive%20%D7%932%202021-green.svg)]()

A comprehensive Python application for automating geodetic leveling calculations and survey data processing, compliant with **Israeli Survey Regulations (Directive ג2, 2021)**.

This branch (`GeoLevelGraviQGIS`) extends the core tool with a **native QGIS plugin** — replacing the Tkinter desktop GUI with a fully integrated PyQt5/PyQGIS interface that loads results directly into the QGIS map canvas with **real georeferenced ITM 2005 coordinates** resolved from a PostgreSQL/PostGIS benchmark database.

---

## What's New in v1.1 — Full Feature List

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
| `GeoLevelQGIS/geolevel_db_manager.py` | PostgreSQL/PostGIS benchmark resolver — spatial disambiguation, ITM coords |
| `GeoLevelQGIS/qgis_line_layer_builder.py` | Direct QGIS memory layer builder — three-pass DB coordinate resolver |
| `GeoLevelQGIS/core_logic/process.py` | Bridge entry-point — parse → validate → export pipeline |
| `GeoLevelQGIS/core_logic/` | Full copy of the geodetic engine (parsers, validators, engine, gis) |

---

## Benchmark Database Integration

The plugin connects to a **PostgreSQL/PostGIS** database containing the Survey of Israel benchmark network. All leveling lines are georeferenced using real ITM 2005 (EPSG:2039) coordinates fetched from the DB, then transformed to WGS84 for GeoJSON export.

### `BenchmarkDBManager` (`geolevel_db_manager.py`)

The central coordinate resolver. Never instantiate directly — always use `get_db_manager()`.

**Spatial disambiguation pipeline** (handles the case where multiple DB rows share the same benchmark name from different cities/regions):

| Stage | What happens |
|---|---|
| **1. Batch SQL fetch** | One round-trip fetches ITM coordinates for all DAT point names via `ST_X(geom_full)` / `ST_Y(geom_full)` — always correct EPSG:2039 metres regardless of raw column order |
| **2. Spatial Median filter** | Computes the spatial median (immune to extreme outliers), then keeps only points within the 75th-percentile Euclidean distance — strips rogue same-name duplicates from other regions |
| **3. K-Means k=1 centroid** | Runs `scipy.cluster.vq.kmeans` on the filtered core after whitening; de-whitens to restore ITM 2005 scale. Falls back to plain mean when scipy is unavailable |
| **4. Proximity resolution** | `resolve_benchmark(name)` picks the DB candidate closest to the project centroid — correct city every time |

**Name normalisation** (`_normalise_name`): strips all punctuation and uppercases before matching, so `3349/MPI` and `3349MPI` resolve to the same benchmark.

**Seeding from QGIS layer** (`seed_from_qgis_layer`): reads the `"נקודות בקרה"` control-point layer already loaded in the QGIS project and seeds the centroid pool from its geometry — coordinates always come from the PostGIS geometry, not from raw attribute columns.

**Manager-Override mode**: Lines whose validation status is forced to `VALID_BY_MANAGER` (amber UI) are included in LSA even when the automated validator rejects them. Status persists across dock refreshes and is serialised in `.glp` project files.

### `BenchmarkRecord` fields

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Point name as stored in DB |
| `x` | `Optional[float]` | Easting, ITM 2005 (EPSG:2039) |
| `y` | `Optional[float]` | Northing, ITM 2005 (EPSG:2039) |
| `gova_ort` | `Optional[float]` | Orthometric height (m) |
| `ot_nekuda` | `str` | Point letter code |
| `mispar_nekuda` | `int` | Point number |
| `shem_darga_gova` | `Optional[str]` | Height accuracy class label |

### Configuring the DB connection

Via `Settings → DB Connection…` in the plugin, or programmatically:

```python
from geolevel_db_manager import get_db_manager
mgr = get_db_manager()
mgr.configure(
    host="your-server",
    port=5432,
    dbname="survey_db",
    user="survey_user",
    table="benchmarks",
    authcfg="",   # QGIS auth manager config ID (optional)
)
```

---

## QGIS Line Layer Builder (`qgis_line_layer_builder.py`)

Builds a QGIS **memory LineString layer** (EPSG:2039) directly from the DB — bypassing the GeoJSON intermediate file — with a **three-pass coordinate resolver**:

### Pass 1 — Base Resolution
Iterates all unique point IDs, calls `resolve_benchmark()` for each, stores `(Easting, Northing)` in a local `resolved_coords` dict.

### Pass 2 — Topological Estimation (unknown / PKT points)
For temporary field points (`PKT1`, `PKT2`, etc.) that are not in the DB:

| Scenario | Estimation method |
|---|---|
| Connected to **2+ known** neighbours | Arithmetic mean of all known-neighbour coordinates (centroid) |
| Connected to **1 known** neighbour only | Known coord + `total_distance` offset along +X axis |

Estimated points appear at a topologically sensible position on the map — **not** collapsed onto their neighbour as a zero-length artifact.

### Pass 3 — Feature Construction
Builds `QgsFeature` objects exclusively from `resolved_coords` (both DB-resolved and topologically estimated). Attributes written: `start_point`, `end_point`, `filename`, `total_distance`, `total_height_diff`, `status`.

---

## Real Coordinate GeoJSON Export

`core_logic/process.py` now produces GeoJSON with **real WGS84 coordinates** (EPSG:4326) instead of fake schematic circles.

### How it works

```
BenchmarkRecord.x / .y  (ITM 2005, EPSG:2039 metres)
        │
        ▼
QgsCoordinateTransform(EPSG:2039 → EPSG:4326)
        │   Inverse Transverse Mercator
        │   False E  219529.584 m
        │   False N  626907.39  m
        │   Scale     1.0000067
        │   Origin   31.7343936°N / 35.2045169°E
        ▼
CoordinateManager.add_point(pid, lon, lat, height)
        │
        ▼
GeoJSONExporter(coord_manager=cm)
        │
        ▼
geo_level_result_lines.geojson  — real lon/lat ~34–36°E, 30–33°N
```

Falls back to schematic coords silently when the DB is not configured or PyQGIS is unavailable (no crash, log message emitted).

---

## Processing Pipeline

```
User clicks "Open / Load Files…" or "+ Add Files"
        │
        ▼
geo_level_plugin.py → _process_files(file_paths, class, output_dir)
        │
        ├─ 1. PARSE      create_parser(fp).parse(fp)
        │                auto-detects Trimble / Leica format
        │
        ├─ 2. VALIDATE   BatchValidator(class_num).validate_batch(lines)
        │                H1-H6 tolerance, sight distance, method rules
        │                VALID_BY_MANAGER lines bypass automated rejection
        │
        ├─ 3. DB COORDS  _build_coord_manager(lines, get_db_manager())
        │                ITM → WGS84 via QgsCoordinateTransform
        │
        ├─ 4. GEOJSON    export_network_to_geojson(lines, output_dir,
        │                    coord_manager=cm)
        │                writes geo_level_result_lines.geojson + .qml
        │
        └─ 5. LINE LAYER QGISLineLayerBuilder.process_line_features(rows)
                         3-pass resolver → memory LineString layer (EPSG:2039)
                         QgsProject.instance().addMapLayer(layer)
        │
        ▼
GeoLevelDockWidget.load_lines(lines, val_results)
colour-codes list: green (valid), red (invalid), amber (VALID_BY_MANAGER)

─────────────────────────────────────────────────────
User clicks "Network Adjustment (LSA)…"
        │
        ▼
QInputDialog → fixed_points {BM1: 100.000, BM2: 105.500}
        │
        ▼
LeastSquaresAdjuster().adjust_from_lines(lines, fixed_points)
        │   solves (AᵀPA)x̂ = AᵀPL
        ▼
GeoLevelLSADialog(result, fixed_points)
        │   σ₀², DoF, adjusted heights, residuals
        │
        ├─ "Export PDF Report" → GeoLevelReporter.generate_pdf(path)
        │                        QPrinter → A4 PDF with 5 sections
        └─ _apply_lsa_to_layer(result)
           adds adj_height field to point features
```

---

## Dock Widget

The plugin replaces the popup dialog with a persistent **QDockWidget** docked to the right side of QGIS.

### Left Panel
- `QListWidget` — loaded leveling lines, colour-coded **green** (valid) / **red** (invalid) / **amber** (VALID_BY_MANAGER)
- `+ Add Files` button — opens file dialog, emits `files_added` signal to trigger the full pipeline
- Precision class combo (H1–H6) with live description label

### Right Panel — 3 Tabs

| Tab | Content |
|-----|---------|
| **Line Details** | 6-column `QTableWidget`: #, From, To, Backsight (m), Foresight (m), Distance (m) |
| **Validation** | Status label (VALID / INVALID / VALID_BY_MANAGER), error/warning text area |
| **Adjustment** | "Adjust This Line" + "LSA — Full Network Adjustment" buttons + result text area |

### Map ↔ Dock Sync
- Selecting a feature on the QGIS map canvas highlights the matching line in the dock list
- Selecting a line in the dock zooms the map canvas to that line's geometry (1.2× bbox scale)
- Sync uses the `filename` attribute on line features

---

## Top-Level Menu — `&Geo Leveling`

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

---

## LSA Results Viewer

### Statistics Group Box
- σ₀² (Reference Variance), σ₀ (Unit Weight Std Dev in mm)
- Degrees of Freedom, Iterations, K Coefficient, Total Distance

### Adjusted Heights Table
| Point ID | Adjusted Height (m) | Correction (mm) | Std Dev (mm) |
|----------|--------------------:|----------------:|-------------:|
| Fixed points highlighted light-blue | | | |
| Unknown points show `—` for Correction | | | |

### Residuals Table
- Observation key (`FROM-TO`), Residual (mm)
- Residuals > 5 mm highlighted orange

---

## PDF Report Generator

**Zero external dependencies** — uses `QPrinter` + `QTextDocument` bundled with QGIS/OSGeo4W.

### Report Sections
1. **Header** — bilingual title (EN + Hebrew), company/surveyor/project/class meta, compliance text (`Directive ג2 (2021)`), navy rule
2. **Network Statistics** — 2-column table: σ₀², σ₀, DoF, iterations, K, total distance
3. **Adjusted Heights** — 5-column table; fixed points light-blue; correction `—` for unknowns
4. **Observation Residuals** — 2-column table; >5 mm residuals highlighted orange
5. **Digital Stamp** — `[GeoLevel-LSA-Verified-YYYY]` + signature line

BiDi: Hebrew point IDs (U+05D0–U+05EA) auto-detected → `QTextOption(Qt.RightToLeft)` applied.

---

## Class Parameters Settings

| Column | Description |
|--------|-------------|
| k (mm/√km) | Tolerance coefficient — `T = k × √L` |
| Max Sight Geom. (m) | Maximum geometric sight distance |
| Method Required | BF, BFFB, or FB |
| Max Line (km) | `∞` for unlimited |
| Max Dist Imbal. (m) | Maximum cumulative distance imbalance |

- **Save** — patches live `CLASS_REGISTRY_BY_NAME` in-memory, persists to `~/.geodetic_tool/settings.json`
- **Reset to Defaults** — reloads Survey of Israel defaults

---

## QGIS Plugin Installation

### Prerequisites
- QGIS 3.0 or higher (tested on QGIS 3.34.4)
- Python 3.8+ (bundled with QGIS on Windows)
- PostgreSQL/PostGIS server with Survey of Israel benchmark table (optional — plugin works without DB but uses schematic coordinates)

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
1. Menu → **Plugins → Manage and Install Plugins…**
2. Switch to the **Installed** tab → find **Geo Level Gravi** → tick the checkbox

### Step 3 — Run the plugin
1. Click the toolbar icon or `Geo Leveling → Project → Open / Load Files…`
2. Add `.DAT` / `.RAW` / `.GSI` measurement files
3. Select precision class (H1–H6, default H3)
4. Choose an output directory
5. Results load as a georeferenced QGIS vector layer; dock panel opens on the right

### Development Reload (no QGIS restart)
```bat
xcopy "GeoLevelQGIS" "%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\GeoLevelQGIS" /E /I /Y
```
Then press **Ctrl+F5** in QGIS (Plugin Reloader).

**Sanity check in QGIS Python Console (Ctrl+Alt+P):**
```python
import GeoLevelQGIS
print("Plugin found:", GeoLevelQGIS.__file__)

# Verify the ITM→WGS84 fix is present
from GeoLevelQGIS import geolevel_db_manager as m
print('ST_X fix present:', 'ST_X(geom_full)' in m._SQL_TEMPLATE)
# Expected: ST_X fix present: True
```

---

## Plugin Folder Structure

```
GeoLevelQGIS/
├── __init__.py                    ← classFactory(iface)
├── metadata.txt                   ← Plugin registry (v1.1, QGIS 3.0+)
├── geo_level_plugin.py            ← Menus, pipeline, map sync
├── geo_level_dockwidget.py        ← Dock panel (list + 3 tabs)
├── geo_level_dialog.py            ← Legacy popup dialog
├── geo_level_settings_dialog.py   ← H1-H6 class parameters editor
├── geo_level_lsa_dialog.py        ← LSA results viewer + PDF export
├── geo_level_reporter.py          ← PDF generator (QPrinter, zero deps)
├── geolevel_db_manager.py         ← PostgreSQL/PostGIS benchmark resolver
│                                     Spatial Median + K-Means k=1 centroid
│                                     ITM 2005 coords via ST_X/ST_Y(geom_full)
├── qgis_line_layer_builder.py     ← Memory LineString layer builder
│                                     3-pass resolver: DB → topo estimate → build
├── projects/                      ← .glp project files
└── core_logic/
    ├── process.py                 ← parse → validate → DB coords → export
    ├── parsers/                   ← Trimble DAT, Leica RAW/GSI
    ├── validators/                ← H1-H6 regulation checks
    ├── engine/                    ← LSA, line adjustment, loop detection
    ├── gis/
    │   ├── geojson_export.py      ← GeoJSON export — accepts pre-built CoordinateManager
    │   └── qgis_integration.py    ← Virtual layer helpers
    ├── exporters/                 ← FA0, FA1, FTEG, REZ
    └── config/                    ← Class registry, settings, models
```

---

## Israeli Survey Regulations — Class System

Compliant with **Survey of Israel Directive ג2 (2021)**

| Class | Tolerance | Max Line (km) | Max Sight Geometric (m) | Method Required |
|-------|-----------|---------------|------------------------|-----------------|
| **H1** | ±3mm√L | Unlimited | 30 | BFFB |
| **H2** | ±5mm√L | 60 | 40 | BFFB |
| **H3** | ±10mm√L | 24 | 50 | BFFB |
| **H4** | ±20mm√L | 10 | 80 | BF |
| **H5** | ±30mm√L | 5 | 100 | BF |
| **H6** | ±60mm√L | 4 | 100 | BF |

All parameters are editable at runtime via `Settings → Class Parameters…` and persist to `settings.json`.

---

## Standalone Usage (without QGIS)

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

## Calculation Methods

### Tolerance
```
T = k × √(Distance_km)    [mm]

H1: k = 3    H2: k = 5    H3: k = 10
H4: k = 20   H5: k = 30   H6: k = 60
```

### Least Squares Adjustment
```
Parametric:  V = A×X - L
Normal eq:   N×X = U   where N = Aᵀ×P×A, U = Aᵀ×P×L
Weighting:   P[i,i] = 1/distance_km
σ₀² = VᵀPV / (n - u)   where n = observations, u = unknowns
```

### Spatial Median Centroid (disambiguation)
```
1. spatial_median = (median(E), median(N))   — immune to extreme outliers
2. dists = ||coord_i - spatial_median||
3. core  = coords where dist <= percentile(dists, 75)
4. centroid = kmeans(whiten(core), k=1)   de-whitened to ITM 2005 metres
```

---

## Supported File Formats

| Format | Extensions | Description |
|--------|-----------|-------------|
| Trimble DAT | `.dat`, `.DAT` | Trimble digital level data |
| Leica RAW | `.raw`, `.RAW` | Leica raw measurement data |
| Leica GSI | `.gsi`, `.GSI` | Leica GSI-8 and GSI-16 |

---

## Recent Commit History

| Commit | Change |
|--------|--------|
| `da57da2` | Real ITM→WGS84 coords in GeoJSON + multi-pass PKT resolver |
| `2946a4a` | PermissionError resilience on shared servers |
| `74f913b` | WGS84/ITM mix-up fix + QGISLineLayerBuilder wired into pipeline |
| `18fb16d` | Split-column seeding, Egypt (0,0) fix, QGIS log routing |
| `c61fa6c` | QGISLineLayerBuilder created (no spatial_cache, no pandas) |
| `2e14429` | Spatial Median + K-Means k=1 centroid ('center of area') |

---

## Features Checklist

### Completed
- [x] Multi-format parsing (Trimble, Leica)
- [x] Israeli Survey Regulations H1-H6
- [x] LSA network adjustment (parametric method)
- [x] Tkinter GUI with class selector
- [x] Persistent settings
- [x] QGIS native plugin (PyQt5)
- [x] Dock widget with 3-tab detail view
- [x] Top-level menu (4 sub-menus)
- [x] Map ↔ dock bidirectional sync
- [x] LSA Results Viewer (σ₀², DoF, heights, residuals)
- [x] PDF report generator (QPrinter, zero deps, BiDi Hebrew)
- [x] Class Parameters settings dialog (live registry patching)
- [x] Project Save/Load (.glp JSON)
- [x] Loop detection with misclosure check
- [x] PostgreSQL/PostGIS benchmark DB integration
- [x] Spatial Median + K-Means k=1 geographic disambiguation
- [x] QGISLineLayerBuilder — direct memory layer, 3-pass resolver
- [x] PKT/unknown point topological estimation
- [x] Real ITM→WGS84 GeoJSON export (EPSG:2039 → EPSG:4326)
- [x] Manager-Override mode (VALID_BY_MANAGER, amber UI)

### Planned
- [ ] Benchmark coordinate input dialog inside QGIS
- [ ] QGIS Processing Framework provider (batch toolbox)
- [ ] Cloud storage integration

---

## Contact

- GitHub: [@sealevelil48](https://github.com/sealevelil48)
- Repository: [Geo_Level_Gravi-](https://github.com/sealevelil48/Geo_Level_Gravi-.git)
- Branch: [GeoLevelGraviQGIS](https://github.com/sealevelil48/Geo_Level_Gravi-/tree/GeoLevelGraviQGIS)

---

**Built with precision for Israeli geodetic surveying**
