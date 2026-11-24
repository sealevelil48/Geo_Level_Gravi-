"""Log format, project .log file, Export Log and logging capture."""
import datetime as dt
import logging

from geodetic_tool.project_log import (
    ProjectLogFile, format_entry, install_handler, write_log_file)


def test_format_entry():
    line = format_entry("WARNING", "fa0_parser", "a\nb", dt.datetime(2026, 11, 24, 9, 5, 7))
    assert line.startswith("09:05:07 WARNING  [fa0_parser] a")
    assert line.split("\n")[1].strip() == "b"


def test_project_log_file(tmp_path):
    p = tmp_path / "proj.log"
    lf = ProjectLogFile()
    lf.open(str(p), "earlier\n", "proj")
    lf.write("INFO", "app", "live")
    lf.open(str(p), "ignored")
    lf.close()
    lf.write("INFO", "app", "after close")
    txt = p.read_text(encoding="utf-8")
    assert txt.startswith("Geodetic Leveling Tool - log\nStarted:")
    assert "Project: proj" in txt and "earlier" in txt and "live" in txt
    assert "ignored" not in txt and "after close" not in txt
    lf.open(str(p))
    assert "Session " in p.read_text(encoding="utf-8")


def test_export_and_capture(tmp_path):
    out = write_log_file(str(tmp_path / "x"), "קו 5\n", "P")
    assert out.endswith(".log") and "קו 5" in open(out, encoding="utf-8").read()
    got = []
    h = install_handler(lambda *a: got.append(a))
    try:
        logging.getLogger("geodetic_tool.engine.z").info("hello")
        logging.getLogger("other").warning("no")
    finally:
        logging.getLogger("geodetic_tool").removeHandler(h)
    assert ("INFO", "z", "hello") in got and not any(m == "no" for *_, m in got)
