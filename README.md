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
>   the QGIS plugin. Same engine, plus map layers, the benchmark database
>   (PostGIS) with Point Verification, and GeoPackage project state.

---

## Features

| Area | What it does |
|---|---|
| **Input** | Trimble DAT, Leica RAW / GSI-8 / GSI-16, and the summary formats **FA0 / FA1 / REZ** (one line per row; known heights in the file become fixed-point candidates). Hebrew (cp1255), UTF-8 and Latin-1 encodings. |
| **Validation** | Classes H1–H6: misclosure k·√L, sight lengths, BF/BFFB method, endpoint and naming checks. Lines can be reversed or excluded; the removed-files report keeps track. |
| **Analysis** | Double-run detection, loop finder, line merging (with direction alignment), point exclusion with search. |
| **Adjustment** (one window) | Step 1: forward/return runs averaged and checked against k·√L (one-direction L). Step 2: approximate heights. Parametric (V = AX − L) or conditional (Bv + w = 0) least squares; weights by distance, setups or equal; single-line adjustment. Export **FA0**, **FA1**, **PDF report**. See [docs/LSA_ENGINE.md](docs/LSA_ENGINE.md). |
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
```

Tkinter comes with the standard Python installers on Windows and macOS. On
Linux install `python3-tk`.

## Usage

### GUI
```bash
python run_gui.py
```
1. **File → Open Files / Open Folder**, then choose the class (H1–H6) in the status bar.
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
├── gis/            GeoJSON export, topology estimator
├── config/         models, class parameters, settings, project manager
├── gui/            app.py (main window and dialogs), adjustment_window.py, window_utils.py
├── cli/            command-line interface
└── tests/          pytest unit tests
tests/              script tests (LSA demo, class selector)
docs/               LSA_ENGINE.md, archive/ (older notes)
run_gui.py, run_cli.py, setup.py
```

## Tests

```bash
python -m pytest geodetic_tool/tests     # LSA (textbook networks), Steps 1-2, FA0/FA1/REZ, topology
python tests/test_lsa.py                 # parametric + conditional demo
python tests/test_class_selector.py
```
`geodetic_tool/tests/test_parsers.py` needs sample field files that are not
in the repository.

---

## License

Internal use: Survey of Israel / Israel Mapping Center.
