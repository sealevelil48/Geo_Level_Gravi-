# 🚀 Geodetic Tool - Quick Reference Card

## ⚡ Quick Start

### Launch GUI
```bash
cd c:\Users\user01\Downloads\geodetic_tool_v1.1
python run_gui.py
```

### Launch CLI
```bash
cd c:\Users\user01\Downloads\geodetic_tool_v1.1
python run_cli.py --help
```

---

## 🖥️ GUI Quick Reference

### Keyboard Shortcuts
| Shortcut | Action |
|----------|--------|
| `Ctrl+O` | Open Files |
| `Ctrl+E` | Export Results |
| `Ctrl+V` | Validate All Files |
| `Ctrl+D` | Detect Double-Runs |
| `Ctrl+L` | Find Loops |
| `Ctrl+A` | Line Adjustment Dialog |
| `Ctrl+N` | Network Adjustment Dialog |

### Main Features
1. **Add Files** → Load .DAT, .raw, .GSI files
2. **Select File** → View details in right panel (4 tabs)
3. **Analysis Menu** → Validate, Double-runs, Loops, Adjustments
4. **Export** → FTEG + GeoJSON + QML style files

### Tabs
- **Line Details**: Measurements, setups table
- **Validation**: Pass/fail status, errors
- **Analysis**: Double-runs, loops, misclosures
- **Log**: Operation history with timestamps

---

## 💻 CLI Quick Reference

### Commands

#### Parse Files
```bash
python run_cli.py parse file1.DAT file2.raw -v
```

#### Validate Files
```bash
python run_cli.py validate *.DAT *.raw
```

#### Export to FA0
```bash
python run_cli.py export --format fa0 --output results.fa0 --project "MyProject" *.DAT
```

#### Export to All Formats
```bash
python run_cli.py export --format all --output ./results --project "MyProject" *.DAT
```

#### Export to GeoJSON
```bash
python run_cli.py geojson *.DAT -o ./output -p "MyProject"
```

#### Show File Info
```bash
python run_cli.py info file.DAT
```

---

## 📄 Export Formats

| Format | Purpose | Encoding | Use Case |
|--------|---------|----------|----------|
| **FA0** | Adjustment input | CP1255 | Import to LSA software |
| **FA1** | Adjustment output/report | CP1255 | Final adjustment results |
| **FTEG** | Simplified data | CP1255 | Spreadsheet analysis |
| **REZ** | Summary | CP1255 | Leica-compatible export |
| **GeoJSON** | GIS visualization | UTF-8 | QGIS, ArcGIS, web maps |

---

## 🔧 File Format Support

### Input Formats
- **Trimble DAT**: Pipe-delimited format
- **Leica RAW/GSI**: Fixed-width format

### Auto-Detection
Files are automatically detected based on content structure.

---

## 📊 Adjustment Types

### 1. Line Adjustment (Single Line)
- **GUI**: Analysis → Line Adjustment (Ctrl+A)
- **Input**: One line + benchmark heights
- **Output**: Adjusted heights, misclosure, K coefficient, leveling class

### 2. Network Adjustment (LSA)
- **GUI**: Analysis → Network Adjustment (Ctrl+N)
- **Input**: Multiple lines + known benchmarks
- **Output**: Least squares solution, M.S.E., residuals, K coefficient

---

## 📐 Leveling Classes

| Class | K Coefficient | Typical Use |
|-------|---------------|-------------|
| **1** | ≤ 1.0 mm/√km | High-precision control networks |
| **2** | ≤ 2.0 mm/√km | Standard geodetic leveling |
| **3** | ≤ 3.0 mm/√km | Engineering surveys |
| **4** | ≤ 6.0 mm/√km | Lower-precision surveys |

Formula: `K = √(Σ(misclosure²) / Σ(distance_km))`

---

## ⚠️ Common Validation Errors

| Error | Cause | Solution |
|-------|-------|----------|
| **Invalid Endpoint** | Last point is a turning point (numeric only) | End line on a benchmark (contains letters) |
| **Naming Error** | Filename doesn't match point IDs | Rename file to match start-end points |
| **Tolerance Exceeded** | Misclosure too large for leveling class | Re-measure or check for errors |
| **Incomplete Data** | Missing measurements | Check field data completeness |

---

## 🎯 Point Naming Rules

- **Benchmarks**: Must contain letters (e.g., `5793MPI`, `BM12A`, `2520W`)
- **Turning Points**: Numbers only (e.g., `1`, `42`, `999`)
- **Endpoints**: Must be benchmarks, not turning points

---

## 📂 Typical Workflow

### Workflow 1: Quick Validation
```bash
# 1. Validate all files
python run_cli.py validate *.DAT

# 2. Check failed files
python run_cli.py info failed_file.DAT
```

### Workflow 2: Single Line Adjustment (GUI)
1. `python run_gui.py`
2. Add Files → Select file
3. Analysis → Line Adjustment (Ctrl+A)
4. Enter benchmark heights → Calculate
5. Export Results (Ctrl+E)

### Workflow 3: Network Adjustment (GUI)
1. `python run_gui.py`
2. Open Folder → Load all files
3. Analysis → Validate All (Ctrl+V)
4. Analysis → Network Adjustment (Ctrl+N)
5. Select lines → Add benchmarks → Adjust
6. Export Results

### Workflow 4: Batch Export (CLI)
```bash
# Export everything at once
python run_cli.py export --format all --output ./results --project "Survey2024" *.DAT *.raw

# Export to GeoJSON for QGIS
python run_cli.py geojson *.DAT -o ./results -p "Survey2024"
```

---

## 🗺️ GIS Integration

### QGIS Import
1. Export to GeoJSON: `python run_cli.py geojson *.DAT -o ./output -p "Project"`
2. Open QGIS
3. Layer → Add Layer → Add Vector Layer
4. Select `output/points.geojson` and `output/lines.geojson`
5. Apply QML styles: `points.qml`, `lines.qml`

### CRS
- Default: **EPSG:2039** (Israel TM Grid)
- 3D coordinates: `[Easting, Northing, Height]`

---

## 🛠️ Troubleshooting

| Problem | Solution |
|---------|----------|
| GUI won't open | Check: `python run_gui.py`, install deps: `pip install -r requirements.txt` |
| CLI not found | Use `python run_cli.py` not just `run_cli.py` |
| Import errors | ✅ Fixed in latest version - update files |
| Export does nothing | ✅ Fixed - make sure files are loaded first |
| Encoding errors | Files use CP1255 (Hebrew ANSI) - auto-fallback to UTF-8 |

---

## 📞 File Locations

| Component | Path |
|-----------|------|
| **GUI Entry** | `run_gui.py` |
| **CLI Entry** | `run_cli.py` |
| **Parsers** | `geodetic_tool/parsers/` |
| **Exporters** | `geodetic_tool/exporters/` |
| **Calculations** | `geodetic_tool/engine/` |
| **GIS** | `geodetic_tool/gis/` |
| **Config** | `geodetic_tool/config/` |
| **Tests** | `geodetic_tool/tests/` |

---

## 📚 Documentation

- **Full Fix Guide**: [FIXES_AND_USAGE_GUIDE.md](FIXES_AND_USAGE_GUIDE.md)
- **Main README**: [README.md](README.md)
- **Architecture**: [geodetic_tool/docs/ARCHITECTURE.md](geodetic_tool/docs/ARCHITECTURE.md)
- **Full Docs**: [DOCUMENTATION.md](DOCUMENTATION.md)

---

## ✅ What Was Fixed

**Fixed Files:**
- ✅ `geodetic_tool/cli/main.py` - Import paths (lines 15-25, 276)
- ✅ `geodetic_tool/gui/app.py` - Import paths (lines 1090-1091)

**All Working Now:**
- ✅ CLI (all commands)
- ✅ GUI (all features)
- ✅ Exports (all formats)

---

**Version:** 1.1 (Fixed December 26, 2025)
**Status:** ✅ All Systems Operational
