"""
Log tab helpers + drag-and-drop file collection (plain script).

Run with QGIS's Python, or any Python with PyQt5 (qgis.PyQt is then mapped
to PyQt5):
    python tests/test_log_and_drop.py
"""
import logging
import os
import sys
import tempfile
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "GeoLevelQGIS"))

try:
    import qgis.PyQt.QtCore  # noqa: F401
except ImportError:          # outside QGIS: map qgis.PyQt -> PyQt5
    import types
    import PyQt5, PyQt5.QtCore, PyQt5.QtGui, PyQt5.QtWidgets
    q = types.ModuleType("qgis"); q.__path__ = []
    sys.modules["qgis"] = q
    sys.modules["qgis.PyQt"] = PyQt5
    for sub in ("QtCore", "QtGui", "QtWidgets"):
        sys.modules["qgis.PyQt." + sub] = getattr(PyQt5, sub)

import geo_level_log as GL                                   # noqa: E402
from geo_level_dockwidget import collect_measurement_files   # noqa: E402

failures = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


# -- formatting ---------------------------------------------------------- #
when = dt.datetime(2026, 11, 24, 9, 5, 7)
line = GL.format_entry("WARNING", "fa0_parser", "first\nsecond", when)
check(line.startswith("09:05:07 WARNING  [fa0_parser] first"), "entry header")
check(line.split("\n")[1].strip() == "second"
      and line.split("\n")[1].startswith(" " * 10), "continuation lines indented")
check(GL.qgis_level_name(0) == "INFO" and GL.qgis_level_name(1) == "WARNING"
      and GL.qgis_level_name(2) == "ERROR", "Qgis level mapping")
check(GL.minimal_logger_names(["core_logic", "core_logic.parsers", "pkg.mod", "pkg", ""])
      == ["core_logic", "pkg"], "logger names deduplicated by ancestry")

# -- capture without Qt event loop: collect posts ------------------------ #
class FakeHub:
    def __init__(self): self.items = []
    def post(self, *a): self.items.append(a)

hub = FakeHub()
cap = GL.PluginLogCapture(hub, ["geolevel_db_manager"]).install()
logging.getLogger("core_logic.engine.least_squares").info("solve done")
logging.getLogger("geolevel_db_manager").warning("db slow")
logging.getLogger("unrelated").error("ignore me")
try:
    raise ValueError("boom")
except ValueError:
    logging.getLogger("GeoLevelGravi").exception("failed")
cap._on_message("from qgis", "GeoLevelPlugin", 2)
cap._on_message("db bridge copy", "Geo Level Gravi", 0)   # already via logging
msgs = [m for _, _, m in hub.items]
check("solve done" in msgs and "db slow" in msgs, "plugin loggers captured")
check(not any("ignore me" in m for m in msgs), "foreign logger ignored")
check(any("boom" in m and "Traceback" in m for m in msgs), "exception traceback kept")
check(("ERROR", "GeoLevelPlugin", "from qgis") in hub.items, "QgsMessageLog tag captured")
check("db bridge copy" not in msgs, "DB bridge tag not duplicated")
cap.uninstall()
logging.getLogger("core_logic").info("after")
check("after" not in [m for _, _, m in hub.items], "uninstall detaches")

with GL.timed("Parse", sink=hub.post):
    pass
check(hub.items[-1][0] == "TIMING" and hub.items[-1][2].startswith("Parse: "), "timed sink")

# -- export -------------------------------------------------------------- #
tmp = tempfile.mkdtemp()
p = GL.write_log_file(os.path.join(tmp, "run"), "a\nb\n", "K6177_24_11_2026_9_25")
body = open(p, encoding="utf-8").read()
check(p.endswith(".log") and "Project: K6177_24_11_2026_9_25" in body
      and body.rstrip().endswith("a\nb"), "write_log_file")
p2 = GL.write_log_file(os.path.join(tmp, "run.txt"), "קו 5", "")
check(p2.endswith(".txt") and "קו 5" in open(p2, encoding="utf-8").read(), "UTF-8 Hebrew .txt")

# -- project .log file --------------------------------------------------- #
pl = GL.ProjectLogFile()
lp = os.path.join(tmp, "proj.log")
pl.open(lp, "earlier line\n", "proj")
pl.write("WARNING", "x", "live line")
pl.open(lp, "ignored")                     # same path: no second header
pl.close(); pl.write("INFO", "x", "after close")
txt = open(lp, encoding="utf-8").read()
check(txt.startswith("Geo Level Gravi - plugin log\nStarted:") and "earlier line" in txt
      and "live line" in txt and "after close" not in txt and "ignored" not in txt,
      "project log file")
pl.open(lp, "", "proj")
check("Session " in open(lp, encoding="utf-8").read(), "reopen appends a session marker")

# -- drag-and-drop collection -------------------------------------------- #
os.makedirs(os.path.join(tmp, "in", "deep"))
for f in ("in/A.DAT", "in/b.raw", "in/c.txt", "in/deep/d.FA1", "in/deep/e.Fa0", "x.rez"):
    open(os.path.join(tmp, f), "w").close()
got = collect_measurement_files([os.path.join(tmp, "in"), os.path.join(tmp, "x.rez"),
                                 os.path.join(tmp, "in", "A.DAT"), "/no/such/file.DAT"])
names = [os.path.basename(g) for g in got]
check(names == ["A.DAT", "b.raw", "d.FA1", "e.Fa0", "x.rez"], f"collected {names}")
check(len(collect_measurement_files([os.path.join(tmp, "in")], max_files=2)) == 2, "max_files cap")

print("\nAll checks passed" if not failures else f"\n{len(failures)} FAILED")
sys.exit(1 if failures else 0)
