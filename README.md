# Geodetic Leveling Tool (Geo Level Gravi), standalone app
## כלי לעיבוד ואיזון מדידות גובה (פילוס גיאומטרי)

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![Directive](https://img.shields.io/badge/Directive-%D7%922%20(2021)-blue.svg)]()

Desktop application (Tkinter) and command-line tool for processing
geometric-leveling measurements according to the Survey of Israel directive
**ג2 (2021)**: reading field files, validating lines, detecting double runs
and loops, and least-squares adjustment with FA0 / FA1 / PDF output.

> **Branches**
> * `main` (this branch): the standalone app in `geodetic_tool/`.
> * [`GeoLevelGraviQGIS`](https://github.com/sealevelil48/Geo_Level_Gravi-/tree/GeoLevelGraviQGIS):
>   the QGIS plugin. Same engine and benchmark-database logic, plus map
>   layers and GeoPackage project state.

---

## Features

| Area | What it does |
|---|---|
| **Input** | Trimble DAT, Leica RAW / GSI-8 / GSI-16, and the summary formats **FA0 / FA1 / REZ** (one line per row). In FA0/FA1 the header `npoints nfixed` marks the first *nfixed* listed points as fixed; other listed heights are approximate. Hebrew (cp1255) and UTF-8 files, including cp1255 files re-saved as UTF-8 (`2751á` is read as `2751ב`). |
| **Benchmark database** | PostgreSQL/PostGIS control-point table (e.g. `cp_bangal` / `public.cp_table`). On loading, every field ID is matched to the DB (ASCII ↔ Hebrew forms, with or without `/`); duplicated names are resolved by comparing DB distances with the measured line lengths. Open cases go to the **Point Verification** dialog (12-row candidate lists, **Search DB**, twins, *Decide later*). Same batched, cached lookups as the plugin: one connection, a few queries per load. |
| **Coordinates** | EPSG:2039 coordinates from the DB (spatial-median / k=1 centroid for unverified duplicates); points missing from the DB are estimated from the measured network. Stored in the project benchmarks, used for GeoJSON (WGS 84, same transformation as QGIS) and DB heights in the adjustment window (*Auto-Select*, *Fetch from DB*). |
| **Validation** | Classes H1–H6: misclosure k·√L, sight lengths, BF/BFFB method, endpoint and naming checks. Lines can be reversed or excluded; the removed-files report keeps track. |
| **Analysis** | Double-run detection, loop finder, line merging (with direction alignment), point exclusion with search. |
| **Per-line settings** | In Line Details each line has its own **Precision class** (or the project class) and **Measure method** (BF / BFFB / BBFF); changing them, or the project class in the status bar, re-validates at once. Saved with the project. |
| **Adjustment** (one window) | Step 1: forward/return runs averaged and checked against k·√L (one-direction L). Step 2: approximate heights. Parametric (V = AX − L) or conditional (Bv + w = 0) least squares; weights by distance, setups or equal; single-line adjustment. Export **FA0**, **FA1** (identical to the legacy program's layout), **PDF report**. See [docs/LSA_ENGINE.md](docs/LSA_ENGINE.md). |
| **Projects** | Save / load as JSON or pickle; joint projects merge several projects without changing the sources. Saves are atomic: written to temp space, then moved into place, so a failed save never damages the previous file. |
| **GIS** | GeoJSON export with QGIS styles; positions of points without coordinates estimated from the measured network (topology estimator). |

### Tolerances (Directive ג2, 2021)

| Class | H1 | H2 | H3 | H4 | H5 | H6 |
|---|---|---|---|---|---|---|
| k (mm·√km) | 3 | 5 | 10 | 20 | 30 | 60 |

Allowed misclosure = k·√L, with **L the one-direction length** in km.

---

## Installation

```bash
git clone https://github.com/sealevelil48/Geo_Level_Gravi-.git
cd Geo_Level_Gravi-
pip install -r requirements.txt        # numpy, pandas
pip install matplotlib                 # optional: charts and PDF report
pip install psycopg2-binary keyring    # optional: benchmark database (+ remembered password)
pip install pyproj                     # optional: PROJ for ITM -> WGS 84 (built-in fallback is identical)
```

### Benchmark database

*Settings → Database Connection…*: host, port, database, user and table
(`[schema.]table`), with *Test Connection*. A fresh install is pre-filled with
`localhost / cp_bangal / public.cp_table`. The settings are shared with the
QGIS plugin (`~/.geodetic_tool/settings.json`).

The password is **never** written to settings or project files. It is taken
from `GEOLEVEL_DB_PASSWORD`, else the password typed this session, else the
system keyring (when *Remember* was ticked and `keyring` is installed), else
libpq's `pgpass.conf` / trusted login. When the server rejects the login, the
app asks once. If the DB can't be reached, loading continues without DB
verification and a warning says why.

Tkinter comes with the standard Python installers on Windows and macOS. On
Linux install `python3-tk`.

## Usage

### GUI
```bash
python run_gui.py
```
1. **File → Open Files / Open Folder**, then choose the class (H1–H6) in the status bar.
   With a database configured, **Point Verification** opens for points that need a
   decision; *Cancel Loading* loads nothing. Choices are saved with the project.
2. **Analysis → Validate All**: red lines are invalid; check *Line Details* and the messages.
3. **Analysis → Network Adjustment (LSA)** → *Prepare (Steps 1–2)* → choose
   fixed points → *Run* → *Export FA0 / FA1 / PDF*.
   *Line Adjustment* opens the same window for a single line.
4. **Project → Save Project** (JSON or pickle).

Windows open sized to the screen, so they fit 1366×768 laptops.

### CLI
```bash
python run_cli.py info     file.DAT
python run_cli.py parse    *.DAT
python run_cli.py validate *.DAT
python run_cli.py export   *.DAT -f rez -o result.rez   # -f fa0 / fa1 / fteg / rez / all
python run_cli.py geojson  *.DAT -o ./output
```
`pip install -e .` also installs the `geodetic-cli` command.

---

## Performance

* Each measurement file is read once.
* Project copies and joint-project merges use a pickle round-trip, about 3×
  faster than `copy.deepcopy`.
* The topology estimator runs one multi-source Dijkstra for the whole network
  (6,500 lines / 283 known points: 2.3 s → 49 ms).
* Project saves are staged in the local temp folder and copied in one write,
  which keeps network or synced folders fast.

---

## Repository layout

```
geodetic_tool/
├── parsers/        DAT, RAW/GSI, FA0, FA1, REZ  (create_parser / parse_many)
├── validators/     class rules H1–H6
├── engine/         least_squares, network_prep (Steps 1–2), loops, line tools,
│                   point_normalizer (name forms + search ranking)
├── exporters/      FA0 / FA1 / REZ writers, PDF report
├── gis/            GeoJSON export, topology estimator, itm (EPSG:2039 -> WGS 84)
├── db/             benchmark_db (DB manager, ported from the plugin), resolution
│                   (point verification, coordinates), credentials
├── config/         models, class parameters, settings, project manager
├── gui/            app.py (main window and dialogs), adjustment_window.py,
│                   point_resolution_dialog.py, db_settings_dialog.py, window_utils.py
├── cli/            command-line interface
└── tests/          pytest unit tests
tests/              script tests (LSA demo, class selector)
docs/               LSA_ENGINE.md, archive/ (older notes)
run_gui.py, run_cli.py, setup.py
```

## Tests

```bash
python -m pytest geodetic_tool/tests     # LSA, Steps 1-2, FA0/FA1/REZ, topology, DB resolver, ITM
python tests/test_point_resolution.py    # DB point matching (same suite as the plugin)
python tests/test_lsa.py                 # parametric + conditional demo
python tests/test_class_selector.py
```
`geodetic_tool/tests/test_parsers.py` needs sample field files that are not
in the repository.

---

## License

Internal use: Survey of Israel / Israel Mapping Center.
