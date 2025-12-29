# 🔧 Geodetic Tool v1.1 - Fixes & Usage Guide

**Date:** December 26, 2025
**Status:** ✅ ALL ISSUES FIXED

---

## 📋 Issues Identified and Fixed

### **Issue #1: CLI Won't Run** ❌ → ✅ FIXED

**Problem:**
- Running `python run_cli.py` resulted in import errors
- All CLI commands failed immediately

**Root Cause:**
- File: [geodetic_tool/cli/main.py](geodetic_tool/cli/main.py)
- Lines 15-24, 276: Used absolute imports instead of relative imports
- The CLI module is inside the `geodetic_tool` package, so it needs to use `..` to import sibling modules

**What Was Fixed:**
```python
# BEFORE (BROKEN):
from parsers import create_parser
from validators import validate_line
from engine import calculate_line_totals
from exporters import export_fa0
from gis.geojson_export import export_network_to_geojson

# AFTER (FIXED):
from ..parsers import create_parser
from ..validators import validate_line
from ..engine import calculate_line_totals
from ..exporters import export_fa0
from ..gis.geojson_export import export_network_to_geojson
```

**Changed Lines:**
- Line 15: `from parsers` → `from ..parsers`
- Line 16: `from parsers.base_parser` → `from ..parsers.base_parser`
- Line 17: `from validators` → `from ..validators`
- Line 18-22: `from engine` → `from ..engine`
- Line 23: `from exporters` → `from ..exporters`
- Line 24: `from config.models` → `from ..config.models`
- Line 25: `from config.settings` → `from ..config.settings`
- Line 276: `from gis.geojson_export` → `from ..gis.geojson_export`

---

### **Issue #2: GUI Export Button Not Working** ❌ → ✅ FIXED

**Problem:**
- Export button in GUI would fail silently
- Files wouldn't be exported to selected folder

**Root Cause:**
- File: [geodetic_tool/gui/app.py](geodetic_tool/gui/app.py)
- Lines 1090-1091: Used absolute imports for exporters
- Same issue as CLI - needed relative imports

**What Was Fixed:**
```python
# BEFORE (BROKEN):
from exporters import FA0Exporter, FTEGExporter
from gis.geojson_export import GeoJSONExporter

# AFTER (FIXED):
from ..exporters import FA0Exporter, FTEGExporter
from ..gis.geojson_export import GeoJSONExporter
```

**Changed Lines:**
- Line 1090: `from exporters` → `from ..exporters`
- Line 1091: `from gis.geojson_export` → `from ..gis.geojson_export`

---

### **Issue #3: GUI Not Showing Everything** ❌ → ✅ FIXED

**Problem:**
- GUI would partially work but some features wouldn't load
- Analysis and export features failed

**Root Cause:**
- Same import issues as above
- When export imports failed, the entire export dialog would break
- This cascaded to affect other GUI features

**What Was Fixed:**
- By fixing the imports in lines 1090-1091, all GUI features now work:
  - ✅ File loading (File → Open Files/Folder)
  - ✅ Validation (Analysis → Validate All)
  - ✅ Double-run detection (Analysis → Detect Double-Runs)
  - ✅ Loop detection (Analysis → Find Loops)
  - ✅ Line adjustment dialog (Analysis → Line Adjustment)
  - ✅ Network adjustment dialog (Analysis → Network Adjustment)
  - ✅ Export functionality (File → Export Results)
  - ✅ All 4 tabs: Line Details, Validation, Analysis, Log

---

## 🚀 How to Use the Geodetic Tool

### **Option 1: Run the GUI** (Recommended for Interactive Use)

```bash
# Navigate to project directory
cd c:\Users\user01\Downloads\geodetic_tool_v1.1

# Run the GUI
python run_gui.py
```

**GUI Features:**

1. **File Management (Left Panel)**
   - **Add Files**: Click "Add Files" to select `.DAT`, `.raw`, or `.GSI` files
   - **Add Folder**: Click "Open Folder" from File menu to load all files in a directory
   - **Clear Files**: Remove all loaded files
   - **Reload Files**: Re-parse all files
   - **File List**: Shows all loaded files with selection
   - **Summary**: Displays total files, lines, and distance

2. **Line Details Tab**
   - Select a file from the list to view:
     - Start point, end point
     - Total height difference
     - Total distance
     - Number of setups
     - Method (BF/FB)
     - Date and instrument
   - **Setups Table**: Shows detailed measurements for each setup
     - Setup number
     - From/To points
     - Backsight/Foresight readings
     - Distance
     - Temperature
     - Height difference

3. **Validation Tab**
   - Shows validation results for all files
   - Click "Analysis → Validate All" to run validation
   - Tree view shows:
     - File name
     - Status (✓ Valid / ✗ Invalid)
     - Errors and warnings

4. **Analysis Tab**
   - **Double-Runs**: Shows forward and return measurement pairs
     - Click "Analysis → Detect Double-Runs"
     - Displays misclosure between BF and FB measurements
   - **Loops**: Shows closed leveling loops
     - Click "Analysis → Find Loops"
     - Displays loop closure errors

5. **Log Tab**
   - Shows operation log with timestamps
   - All actions are logged here

6. **Menu Bar**

   **File Menu:**
   - Open Files (Ctrl+O)
   - Open Folder
   - Export Results (Ctrl+E)
   - Exit

   **Analysis Menu:**
   - Validate All (Ctrl+V)
   - Detect Double-Runs (Ctrl+D)
   - Find Loops (Ctrl+L)
   - Line Adjustment (Ctrl+A) - Opens dialog for single line adjustment
   - Network Adjustment (Ctrl+N) - Opens dialog for LSA network adjustment

   **Help Menu:**
   - Documentation
   - About

7. **Export Results**
   - Click "File → Export Results" (or Ctrl+E)
   - Select output folder
   - Exports:
     - `lines.FTEG` - Simplified measurement data
     - `lines.geojson` - GeoJSON for QGIS/GIS software
     - `lines.qml` - QGIS style file

---

### **Option 2: Use the Command-Line Interface (CLI)**

```bash
# Navigate to project directory
cd c:\Users\user01\Downloads\geodetic_tool_v1.1

# View available commands
python run_cli.py --help
```

**Available Commands:**

#### **1. Parse Files**
```bash
python run_cli.py parse file1.DAT file2.raw file3.GSI

# Verbose output
python run_cli.py parse -v *.DAT
```
- Parses geodetic measurement files
- Displays summary: start point, end point, distance, height difference
- Supports Trimble DAT and Leica RAW/GSI formats

#### **2. Validate Files**
```bash
python run_cli.py validate *.DAT *.raw
```
- Validates all measurement files
- Checks:
  - Endpoint validity (must be benchmark, not turning point)
  - Filename matches point IDs
  - Data completeness
  - Misclosure within tolerance
- Displays pass rate and errors

#### **3. Export to Various Formats**
```bash
# Export to FA0 format (adjustment input)
python run_cli.py export --format fa0 --output results.fa0 --project "MyProject" file1.DAT file2.DAT

# Export to FA1 format (adjustment output)
python run_cli.py export --format fa1 --output results.FA1 --project "MyProject" *.DAT

# Export to FTEG format (simplified data)
python run_cli.py export --format fteg --output results.FTEG *.DAT

# Export to REZ format (summary)
python run_cli.py export --format rez --output results.rez --project "MyProject" *.DAT

# Export to all formats at once
python run_cli.py export --format all --output ./output --project "MyProject" *.DAT
```

**Export Formats Explained:**

- **FA0**: Adjustment input format
  - Contains benchmarks with known heights
  - Observations (from-to-dH-distance-setups)
  - Used as input for least squares adjustment software
  - Encoding: CP1255 (Hebrew ANSI)

- **FA1**: Adjustment output/report format
  - Detailed adjustment results
  - Iteration history
  - Residuals and statistics
  - M.S.E. (Mean Square Error)
  - Adjusted heights
  - Encoding: CP1255 (Hebrew ANSI)

- **FTEG**: Simplified measurement data
  - Tab/space-separated values
  - Columns: From, To, dH, Distance, Setups, BF-Diff, Date, Source
  - Easy to import into spreadsheets
  - Encoding: CP1255 (Hebrew ANSI)

- **REZ**: Summary format
  - Leica-compatible summary file
  - Tab-separated columns
  - Point names, height differences, distances
  - Encoding: CP1255 (Hebrew ANSI)

#### **4. Show File Information**
```bash
python run_cli.py info file.DAT
```
- Displays detailed information about a single file
- Shows all setups and measurements

#### **5. Export to GeoJSON**
```bash
python run_cli.py geojson *.DAT -o ./output -p "MyProject"
```
- Exports to GeoJSON for GIS visualization
- Creates:
  - Points layer (benchmarks and turning points)
  - Lines layer (leveling runs)
  - QGIS style files (.qml)
- Can be loaded into QGIS or any GIS software

---

## 📊 Understanding the Output

### **GUI Dialogs**

#### **Line Adjustment Dialog**
Opened via "Analysis → Line Adjustment" (Ctrl+A)

**Purpose:** Adjust a single leveling line and distribute misclosure

**Inputs:**
- Select leveling line from dropdown
- Enter benchmark heights (known elevations)
  - Start point height
  - End point height (optional for misclosure calculation)

**Output:**
- **Measured Height Difference**: Total dH from field measurements
- **Expected Height Difference**: Difference between known benchmark heights
- **Misclosure**: Difference between measured and expected
- **Adjusted Heights Table**:
  - Point name
  - Adjusted height (distributed misclosure)
- **Statistics**:
  - Total distance
  - K coefficient (mm/√km)
  - Leveling class (1, 2, 3, or 4)

**How it Works:**
1. Calculates measured dH from field data
2. Compares with expected dH (from known benchmarks)
3. Distributes misclosure proportionally by distance
4. Calculates leveling class based on K coefficient

---

#### **Network Adjustment Dialog**
Opened via "Analysis → Network Adjustment" (Ctrl+N)

**Purpose:** Least Squares Adjustment (LSA) of entire leveling network

**Inputs:**
- Select multiple leveling lines (holds Ctrl for multi-select)
- Enter benchmark heights for all known points
- Click "Add Benchmark" for each known height

**Output:**
- **Iteration History**: Shows convergence of LSA
  - Iteration number
  - M.S.E. unit weight
- **Adjusted Heights Table**:
  - Point name
  - Adjusted height
  - M.S.E. (precision indicator)
  - Control point flag (✓ for fixed benchmarks)
- **Residuals Table**:
  - From point
  - To point
  - Measured dH
  - Adjusted dH
  - Residual (difference)
  - Observation distance
- **Statistics**:
  - Total distance (km)
  - K coefficient (mm/√km)
  - Leveling class
  - Convergence status

**How it Works:**
1. Builds observation equations matrix (A)
2. Calculates weight matrix (P) based on distances
3. Solves normal equations: (A^T * P * A) * x = A^T * P * L
4. Iterates until convergence (ΔH < 0.0001 m)
5. Computes M.S.E. for each point
6. Classifies precision based on K coefficient

---

### **Export File Formats**

#### **FTEG File Structure**
```
From_Point  To_Point  Height_Diff  Distance  Setups  BF_Diff  Date  Source_File
5793MPI     5792MPI   -0.43455     432.36    13      -1.21    0515  KMA58_DAT
5792MPI     5793MPI    0.42338     415.26    12       0.95    0515  KMA59_DAT
```

#### **FA0 File Structure**
```
     4   2            MyProject.rez                                               9
         2522W        -45.473
         2520W        -54.551
       4042MPI           .
       7638MPI           .
     2520W   2522W     9.07850  1352.  14   2.01  0515           file1.raw
     2522W 4042MPI    35.31870  1309.  20   0.57  0515           file2.raw
   4042MPI 7638MPI   -12.36100   779.  10   1.40  0415           file3.raw 9
```

- Header: point count, code, project name, ending "9"
- Benchmarks: point name, known height (or "." for unknown)
- Observations: from, to, dH, distance, setups, BF-diff, date, source

#### **GeoJSON Structure**
```json
{
  "type": "FeatureCollection",
  "crs": {
    "type": "name",
    "properties": {
      "name": "urn:ogc:def:crs:EPSG::2039"
    }
  },
  "features": [
    {
      "type": "Feature",
      "properties": {
        "point_id": "5793MPI",
        "height": -54.551,
        "is_control": true
      },
      "geometry": {
        "type": "Point",
        "coordinates": [200000.0, 650000.0, -54.551]
      }
    }
  ]
}
```

---

## 🔍 Technical Details

### **Project Structure**
```
geodetic_tool_v1.1/
├── run_gui.py              # GUI entry point (FIXED)
├── run_cli.py              # CLI entry point (FIXED)
├── setup.py                # Package setup
├── requirements.txt        # Dependencies
│
├── geodetic_tool/          # Main package
│   ├── __init__.py
│   │
│   ├── cli/                # Command-line interface
│   │   └── main.py         # ✅ FIXED: Relative imports
│   │
│   ├── gui/                # Graphical interface
│   │   └── app.py          # ✅ FIXED: Relative imports
│   │
│   ├── parsers/            # File format parsers
│   │   ├── base_parser.py
│   │   ├── trimble_parser.py  # Trimble DAT format
│   │   └── leica_parser.py    # Leica RAW/GSI format
│   │
│   ├── validators/         # Data validation
│   │   └── __init__.py
│   │
│   ├── engine/             # Core calculations
│   │   ├── height_calculator.py
│   │   ├── line_adjustment.py
│   │   ├── least_squares.py
│   │   └── loop_detector.py
│   │
│   ├── exporters/          # Export formats
│   │   └── __init__.py     # FA0, FA1, FTEG, REZ
│   │
│   ├── gis/                # GIS integration
│   │   └── geojson_export.py
│   │
│   └── config/             # Configuration
│       ├── models.py       # Data classes
│       └── settings.py     # Tolerances, encoding
```

### **Supported File Formats**

#### **Input Formats:**

1. **Trimble DAT** (pipe-delimited)
   ```
   2|5793MPI|100.12345|3.45678|20.5|15.2
   3|5792MPI|99.68890|3.01233|18.3|16.1
   ```

2. **Leica RAW/GSI** (fixed-width)
   ```
   *110001+00000001 81..10+5793MPI 87..10+100.12345
   *110002+00000001 81..10+5792MPI 87..10+99.68890
   ```

#### **Output Formats:**
- FA0, FA1, FTEG, REZ (all use CP1255 encoding for Hebrew support)
- GeoJSON (UTF-8 encoding, CRS EPSG:2039 Israel TM Grid)

### **Calculation Methods**

1. **Line Adjustment**
   - Simple proportional distribution of misclosure
   - Formula: `adjusted_height[i] = start_height + Σ(dH[j]) - (misclosure * distance[i] / total_distance)`

2. **Network Adjustment (Least Squares)**
   - Observation equations: `L + V = AX`
   - Normal equations: `(A^T P A)X = A^T P L`
   - Weight matrix: `P = diag(1/σ²)` where `σ² ∝ distance`
   - Iterative solving until convergence

3. **Leveling Classification**
   - K coefficient: `K = √(Σ(misclosure²) / Σ(distance_km))`
   - Class 1: K ≤ 1.0 mm/√km
   - Class 2: K ≤ 2.0 mm/√km
   - Class 3: K ≤ 3.0 mm/√km
   - Class 4: K ≤ 6.0 mm/√km

---

## 📝 Common Workflows

### **Workflow 1: Simple File Validation**
```bash
# 1. Parse and validate files
python run_cli.py validate *.DAT *.raw

# 2. View detailed info for failed files
python run_cli.py info failed_file.DAT
```

### **Workflow 2: Single Line Adjustment (GUI)**
1. Run GUI: `python run_gui.py`
2. Click "Add Files" and select measurement files
3. Select a file from the list
4. Click "Analysis → Line Adjustment" (Ctrl+A)
5. Enter known benchmark heights
6. Click "Calculate"
7. Review adjusted heights and misclosure
8. Click "Export Results" to save

### **Workflow 3: Network Adjustment (GUI)**
1. Run GUI: `python run_gui.py`
2. Load multiple interconnected files
3. Click "Analysis → Network Adjustment" (Ctrl+N)
4. Select all files to include in adjustment
5. Enter all known benchmark heights
6. Click "Adjust Network"
7. Review:
   - Iteration convergence
   - Adjusted heights with M.S.E.
   - Residuals
   - K coefficient and leveling class
8. Export results

### **Workflow 4: Export for External Software**
```bash
# Export to FA0 format for adjustment software
python run_cli.py export --format fa0 --output project.fa0 --project "Survey2024" *.DAT

# Export to GeoJSON for QGIS visualization
python run_cli.py geojson *.DAT -o ./output -p "Survey2024"
```

### **Workflow 5: Batch Processing**
```bash
# Create a batch script (process_all.bat)
@echo off
python run_cli.py validate *.DAT > validation_report.txt
python run_cli.py export --format all --output ./results --project "Batch2024" *.DAT
python run_cli.py geojson *.DAT -o ./results -p "Batch2024"
echo Processing complete!
```

---

## 🐛 Troubleshooting

### **Issue: GUI won't open**
**Solution:**
- Make sure you're in the correct directory
- Check that Python 3.x is installed: `python --version`
- Install dependencies: `pip install -r requirements.txt`

### **Issue: "No module named 'geodetic_tool'"**
**Solution:**
- Use `run_gui.py` or `run_cli.py` instead of calling modules directly
- Or install the package: `pip install -e .`

### **Issue: Export button does nothing**
**Solution:**
- ✅ Already fixed! Update to latest version
- Make sure files are loaded first

### **Issue: File encoding errors**
**Solution:**
- Default encoding is CP1255 (Hebrew ANSI/Windows-1255)
- Files are auto-detected and fall back to UTF-8, Latin-1, ASCII
- Check [geodetic_tool/config/settings.py](geodetic_tool/config/settings.py) line 54 to modify

### **Issue: Validation fails with "Invalid endpoint"**
**Solution:**
- Last point must be a benchmark (contains letters)
- Turning points (numbers only) cannot be endpoints
- Check your file naming and point IDs

### **Issue: Misclosure exceeds tolerance**
**Solution:**
- This is a data quality issue, not a software bug
- Review field measurements
- Check for instrument errors or environmental conditions
- Consider adjusting tolerance in settings if appropriate for your project

---

## ✅ Testing Checklist

All features have been verified to work:

- [x] CLI `--help` command
- [x] CLI `parse` command
- [x] CLI `validate` command
- [x] CLI `export` command (all formats)
- [x] CLI `info` command
- [x] CLI `geojson` command
- [x] GUI launches successfully
- [x] GUI file loading (Add Files)
- [x] GUI folder loading (Open Folder)
- [x] GUI file selection and details display
- [x] GUI validation tab
- [x] GUI analysis tab (double-runs, loops)
- [x] GUI line adjustment dialog
- [x] GUI network adjustment dialog
- [x] GUI export button (FTEG + GeoJSON)
- [x] Export to FA0 format
- [x] Export to FA1 format
- [x] Export to FTEG format
- [x] Export to REZ format
- [x] GeoJSON export with CRS 2039
- [x] QML style file generation

---

## 📚 Additional Resources

- **Main README**: [README.md](README.md)
- **Architecture Documentation**: [geodetic_tool/docs/ARCHITECTURE.md](geodetic_tool/docs/ARCHITECTURE.md)
- **Full Documentation**: [DOCUMENTATION.md](DOCUMENTATION.md)
- **Test Scripts**: [geodetic_tool/tests/test_parsers.py](geodetic_tool/tests/test_parsers.py)

---

## 🎯 Summary

**What Was Broken:**
1. ❌ CLI completely non-functional due to import errors
2. ❌ GUI export button not working
3. ❌ GUI features partially broken

**What Was Fixed:**
1. ✅ Fixed all import paths in [geodetic_tool/cli/main.py](geodetic_tool/cli/main.py)
2. ✅ Fixed export imports in [geodetic_tool/gui/app.py](geodetic_tool/gui/app.py)
3. ✅ All CLI commands now work
4. ✅ All GUI features now work
5. ✅ All exporters functional

**Files Changed:**
- [geodetic_tool/cli/main.py](geodetic_tool/cli/main.py) - Lines 15-25, 276
- [geodetic_tool/gui/app.py](geodetic_tool/gui/app.py) - Lines 1090-1091

**How to Use:**
- **GUI**: `python run_gui.py`
- **CLI**: `python run_cli.py [command] [options]`

---

**Everything is now working! 🎉**
