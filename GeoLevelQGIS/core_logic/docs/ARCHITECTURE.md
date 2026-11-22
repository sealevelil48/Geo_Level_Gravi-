# Geodetic Automation Tool - Architecture

## 🎯 Project Overview

A Python-based software application for automating geodetic leveling calculations and pre/post-processing of survey data.

---

## 📊 Agent Structure

### 1. **DataParserAgent** (`parsers/`)
**Responsibility:** Parse and extract data from various geodetic file formats.

| Module | Description |
|--------|-------------|
| `trimble_parser.py` | Parse Trimble DAT files (pipe-delimited format) |
| `leica_parser.py` | Parse Leica GSI-8/16 RAW files |
| `base_parser.py` | Abstract base class for parsers |

**Output:** Standardized `pandas.DataFrame` with measurement data.

---

### 2. **ValidationAgent** (`validators/`)
**Responsibility:** Validate parsed data for correctness and quality control.

| Module | Description |
|--------|-------------|
| `point_validator.py` | Check start/end points are valid benchmarks |
| `naming_validator.py` | Validate file naming vs internal point IDs |
| `measurement_validator.py` | Check for repeated/missing measurements |
| `closure_validator.py` | Validate loop closures within tolerance |

**Validation Rules:**
- End point must be a named benchmark (not numeric turning point)
- File name should match internal start/end point naming
- Detect "Front to Back" errors (BF vs FB direction)

---

### 3. **GeodesyEngineAgent** (`engine/`)
**Responsibility:** Core geodetic calculations and adjustments.

| Module | Description |
|--------|-------------|
| `height_calculator.py` | Compute height differences from rod readings |
| `line_adjustment.py` | Line adjustment (תיאום קו) calculations |
| `loop_adjustment.py` | Loop closure adjustment (תיאום לולאה) |
| `least_squares.py` | Least Squares Adjustment implementation |
| `tolerance_calculator.py` | Calculate allowable tolerances |

**Key Formulas:**
- Height Difference: `dH = Backsight - Foresight`
- Tolerance: `T = k * sqrt(D)` where D is distance in km
- Misclosure: `M = ΣdH - (H_end - H_start)`

---

### 4. **ExportAgent** (`exporters/`)
**Responsibility:** Generate output files in required formats.

| Module | Description |
|--------|-------------|
| `rez_exporter.py` | Generate REZ summary files |
| `fa0_exporter.py` | Generate FA0 adjustment input files |
| `fa1_exporter.py` | Generate FA1 detailed adjustment reports |
| `fteg_exporter.py` | Generate FTEG measurement line files |

**Requirements:**
- All exports in ANSI encoding
- Fixed-width column formatting

---

### 5. **GISIntegrationAgent** (`gis/`)
**Responsibility:** GIS visualization and export.

| Module | Description |
|--------|-------------|
| `geojson_exporter.py` | Export measurement lines as GeoJSON |
| `qgis_plugin/` | QGIS plugin for visualization |
| `loop_finder.py` | Automatic loop detection algorithm |

**Features:**
- Point markers for benchmarks
- Lines connecting measurement points
- Labels for point names and file names

---

### 6. **InterfaceAgents** (`cli/`; the QGIS plugin UI lives in the plugin root, the Tkinter app only on the `main` branch)
**Responsibility:** User interfaces.

| Module | Description |
|--------|-------------|
| `cli/main.py` | Command-line interface |

---

## 📁 Data Flow

```
┌─────────────────┐
│   Input Files   │
│  (DAT, RAW)     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  DataParser     │
│  Agent          │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Validation     │
│  Agent          │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Geodesy        │
│  Engine         │
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌───────┐ ┌───────┐
│Export │ │  GIS  │
│Agent  │ │Agent  │
└───┬───┘ └───┬───┘
    │         │
    ▼         ▼
┌───────┐ ┌───────┐
│REZ,FA0│ │GeoJSON│
│FA1    │ │QGIS   │
└───────┘ └───────┘
```

---

## 📋 File Formats

### Trimble DAT Format
```
For M5|Adr   N|KD1  PointID  Temp C  1   1|Rb  X.XXXXX m   |HD   YYY.YYY m   |
```

| Field | Description |
|-------|-------------|
| `KD1` | Measurement record |
| `TO` | Text/metadata record |
| `Rb` | Backsight rod reading |
| `Rf` | Foresight rod reading |
| `HD` | Horizontal distance |
| `Z` | Computed height |

### Leica GSI Format
| Word Index | Description |
|------------|-------------|
| 11 | Point ID |
| 32 | Horizontal distance |
| 331/332 | Staff readings (Face 1) |
| 335/336 | Staff readings (Face 2) |
| 571-574 | Quality indicators |
| 83 | Height |

---

## ⚠️ Validation Rules

### 1. Endpoint Validation
- **VALID:** Ends on named benchmark (e.g., `5793MPI`, `609U`)
- **INVALID:** Ends on numeric turning point (e.g., `5`, `11`)

### 2. Naming Convention
- File name should contain start-end point pattern
- Detect front-to-back (BF) vs back-to-front (FB) errors

### 3. Known Examples
| File | Status | Issue |
|------|--------|-------|
| KMA58 | ✅ Valid | Correct: 5793MPI → 5792MPI |
| KMA59 | ✅ Valid | Correct: 5792MPI → 5793MPI |
| KMA57 | ❌ Bad | Front-to-back naming error |
| KMA60 | ❌ Bad | Front-to-back naming error |
| KMA186 | ❌ Bad | Ends on turning point `5` |

---

## 🔧 Configuration

### config/settings.py
```python
TOLERANCE_FACTOR = 0.003  # mm/sqrt(km)
ENCODING = 'cp1255'       # Hebrew ANSI
BENCHMARK_PATTERN = r'[A-Z]+'  # Pattern for valid benchmarks
```

---

## 📦 Dependencies

```
pandas>=1.5.0
numpy>=1.23.0
scipy>=1.9.0  # For Least Squares
geojson>=2.5.0
(no GUI toolkit - the plugin UI uses QGIS/PyQt)
```
