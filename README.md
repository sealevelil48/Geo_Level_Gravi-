# Geo Level Gravi — QGIS plugin
## תוסף QGIS לעיבוד ואיזון מדידות גובה (פילוס גיאומטרי)

[![QGIS](https://img.shields.io/badge/QGIS-3.x-green.svg)](https://qgis.org/)
[![Branch](https://img.shields.io/badge/branch-GeoLevelGraviQGIS-orange.svg)](https://github.com/sealevelil48/Geo_Level_Gravi-/tree/GeoLevelGraviQGIS)
[![Directive](https://img.shields.io/badge/Directive-%D7%922%20(2021)-blue.svg)]()

QGIS plugin for processing geometric-leveling measurements according to the
Survey of Israel directive **ג2 (2021)**. It reads field files, validates every
line, places all points on the map with real ITM 2005 coordinates from the
benchmark database, and adjusts the network by least squares, with FA0 / FA1
/ PDF output.

> **Branches**
> * `GeoLevelGraviQGIS` (this branch): the QGIS plugin, in `GeoLevelQGIS/`.
> * [`main`](https://github.com/sealevelil48/Geo_Level_Gravi-/tree/main): the
>   standalone desktop application (Tkinter GUI + CLI) built on the same engine.

---

## Features

| Area | What it does |
|---|---|
| **Input** | Trimble DAT, Leica RAW/GSI, and the summary formats **FA0 / FA1 / REZ** (one line per row; known heights in the file are kept as fixed-point candidates). Hebrew (cp1255) point names supported. |
| **Validation** | Per-line checks for classes H1–H6: misclosure k·√L, sight lengths, BF/BFFB method, naming. Lines can be toggled used/unused, reversed, or approved by a manager (`VALID_BY_MANAGER`). |
| **Point Verification** (before loading) | Every field ID is matched to the benchmark DB (ASCII ↔ Hebrew forms, with or without `/`). Duplicated names are resolved by comparing DB distances with the measured line lengths. Open cases go to a dialog with a 12-row candidate list (name, E/N, height, class, locality, distance) and a **Search DB** box for partial names or localities. |
| **Map** | Lines and points as layers in EPSG:2039, styled by validation status. Points missing from the DB (PKT, temporary points) are placed by the topology estimator (yellow diamonds). |
| **Point Settings** | Edit after loading: re-pick a DB point, search, exclude, or mark a point "estimate". |
| **Adjustment** (one window) | Step 1: average forward/return runs (checked against k·√L, one-direction L). Step 2: approximate heights. Then parametric (V = AX − L) or conditional (Bv + w = 0) least squares, single-line adjustment, **Apply to Map**, **Export FA0 / FA1**, **PDF report**. See [docs/LSA_ENGINE.md](docs/LSA_ENGINE.md). |
| **Analysis** | Double-run detection, loop finder, line merging, Excel export of the validation table. |
| **Project state** | GeoPackage-first: lines, setups, validation, point choices and the last adjustment are stored in one `.gpkg` beside the data and autosaved after every edit. A small `.glp` file points to it. |

### Tolerances (Directive ג2, 2021)

| Class | H1 | H2 | H3 | H4 | H5 | H6 |
|---|---|---|---|---|---|---|
| k (mm·√km) | 3 | 5 | 10 | 20 | 30 | 60 |

Allowed misclosure = k·√L, with **L the one-direction length** in km.

---

## Installation

1. Clone or download this branch.
2. Run **`deploy.bat`** (Windows). It copies `GeoLevelQGIS/` into
   `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\` (mirror copy, tests and caches excluded).
   On other systems copy `GeoLevelQGIS/` into your QGIS profile's `python/plugins/` folder.
3. Restart QGIS and enable **Geo Level Gravi** in *Plugins → Manage and Install Plugins*.

Dependencies: QGIS 3.x already includes PyQt5 and numpy. The benchmark database
needs `psycopg2` (included with OSGeo4W QGIS). See `requirements.txt`.

### Benchmark database

*Geo Level Gravi → Settings → Database Connection…*: host, port, database,
user, table. The password is kept in the **QGIS Authentication Manager**,
never in a settings file. The table needs the columns `mispar_nekuda`,
`ot_nekuda`, `name`, `gova_ort`, `shem_darga_gova`, `geom_full`
(Point, EPSG:2039), optionally `mispar_nekuda_kfula` / `ot_nekuda_kfula`,
`kfar_aher_name`, `hearot`. Without a database the plugin still works; points
are then placed schematically.

If the project contains a layer named **`נקודות בקרה`** (control points), it
is used first to place points.

---

## Typical workflow

1. **Open / Load Files…**: choose the measurement files and the class (H1–H6).
2. **Point Verification**: confirm or pick the DB point for each open ID.
   Use *Search DB* when the right point is not listed; choose *Decide later*
   to let matching continue from the points you are sure of.
3. Check the dock: red lines are invalid; open a line for its setups and messages.
4. **Network Adjustment (LSA)** → *Prepare (Steps 1–2)* → choose fixed points
   → *Run Adjustment* → *Apply to Map* / *Export FA0* / *Export FA1* / *PDF Report*.
5. The project is saved automatically in the GeoPackage; *Save Project…*
   writes the `.glp` you reopen later.

---

## Performance notes

Loading is built to stay fast on national-size data and slow networks:

* **Database**: one reused connection per session. Names are looked up in
  batches (one query per 500 names, all name forms), duplicate-location checks
  run as one spatial query, and results are cached for the session.
* **Files**: each file is read once; the second pass reuses the parsed lines.
* **Topology estimator**: one multi-source Dijkstra for the whole network.
* **Map**: the canvas is frozen while layers are built and saved, then drawn once.
* **GeoPackage**: built in the local temp folder, then copied into place and
  renamed atomically, so network or synced folders stay fast and a failed save
  never corrupts the project.

Reference project (348 files, 175 points, 40 ms network latency): about
**0.6 s** of processing, down from 99 s in earlier versions.

---

## Repository layout

```
GeoLevelQGIS/                     the plugin (this folder is what deploy.bat installs)
├── geo_level_plugin.py           plugin class: menus, load pipeline, map ↔ dock sync, GPKG state
├── geo_level_dockwidget.py       dock: line list, setups, validation, log
├── geo_level_point_resolution_dialog.py   Point Verification / Point Settings
├── geo_level_enhanced_lsa_dialog.py       adjustment window (Steps 1–2, LSA, exports)
├── geolevel_db_manager.py        benchmark DB: batch lookup, search, network matching
├── qgis_line_layer_builder.py    line layer from resolved coordinates
├── geo_level_gpkg.py             GeoPackage read/write (layers + lv_lines / lv_setups / lv_meta)
├── geo_level_reporter.py         PDF report (QPrinter)
├── geo_level_*_dialog.py         settings, merge, exclusion dialogs
└── core_logic/                   engine shared with the standalone app
    ├── parsers/                  DAT, RAW/GSI, FA0, FA1, REZ
    ├── validators/               class rules H1–H6
    ├── engine/                   least_squares, network_prep, loops, line tools
    ├── gis/                      GeoJSON export, topology estimator
    ├── exporters/                FA0 / FA1 / REZ writers
    ├── process.py                parse → validate → coordinates → GeoJSON/REZ
    └── tests/                    engine unit tests (pytest)
tests/                            plugin-level tests (DB matching, GeoPackage, security)
docs/                             LSA_ENGINE.md (adjustment maths), reference/ (source PDFs)
deploy.bat                        Windows installer script
```

---

## Tests

```bash
cd GeoLevelQGIS && python -m pytest core_logic/tests    # engine: LSA, Steps 1-2, FA0/FA1/REZ, topology
cd ..
python tests/test_point_resolution.py                   # DB matching (in-memory fake DB)
python tests/test_gpkg_roundtrip.py                     # GeoPackage state round-trip
python tests/test_vuln.py                               # untrusted-input checks
```

The LSA tests reproduce textbook networks (parametric AX−L and conditional
Bv+w=0) to 0.1 mm. `core_logic/tests/test_parsers.py` needs sample field
files that are not in the repository.

---

## License

Internal use: Survey of Israel / Israel Mapping Center.
