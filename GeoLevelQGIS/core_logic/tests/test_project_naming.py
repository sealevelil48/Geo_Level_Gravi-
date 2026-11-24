"""Automatic project names: <Description>_DD_MM_YYYY_H_MM."""
import datetime as dt

from core_logic.parsers.fa0_parser import FA0Parser
from core_logic.project_naming import (
    describe_files, make_project_name, sanitize_name, timestamp_suffix)

WHEN = dt.datetime(2026, 11, 24, 9, 5)


def test_format_example():
    assert make_project_name("K6177-64PU-65PU", dt.datetime(2026, 11, 24, 9, 25)) \
        == "K6177-64PU-65PU_24_11_2026_9_25"
    assert timestamp_suffix(WHEN) == "24_11_2026_9_05"
    assert timestamp_suffix(dt.datetime(2026, 1, 2, 23, 59)) == "02_01_2026_23_59"


def test_sanitize_windows_names():
    assert sanitize_name('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
    assert sanitize_name("  line  12 \t x ") == "line_12_x"
    assert sanitize_name("name...") == "name"
    assert sanitize_name("CON") == "CON_"
    assert sanitize_name("קו 5/ב") == "קו_5_ב"          # Hebrew is kept
    assert sanitize_name("x" * 200) == "x" * 80
    assert sanitize_name("???") == ""
    assert make_project_name("???", WHEN) == "GeoLevel_24_11_2026_9_05"


def test_describe_files_prefers_header_title(tmp_path):
    fa0 = tmp_path / "net.FA0"
    dat = tmp_path / "KMA58.DAT"
    titles = {str(fa0): "K6177-64PU-65PU", str(dat): ""}
    assert describe_files([str(dat), str(fa0)], titles) == "K6177-64PU-65PU"
    assert describe_files([str(dat)], titles) == "KMA58"
    assert describe_files([], {}) == "GeoLevel"


def test_fa0_header_title():
    assert FA0Parser.header_title("36   2   K6177-64PU-65PU   9") == "K6177-64PU-65PU"
    assert FA0Parser.header_title("4 1 North net 9") == "North net"


def test_process_returns_titles(tmp_path):
    from core_logic.process import process_geodetic_data
    fa0 = tmp_path / "demo.FA0"
    fa0.write_text(
        "3   1   DEMO-NET/1   9\n"
        "P1       100.000\n"
        "P2       .\n"
        "P3       .\n"
        "P1      P2       1.23450   0.500  4\n"
        "P2      P3      -0.50000   0.400  4\n"
        "9\n", encoding="cp1255")
    res = process_geodetic_data([str(fa0)], "H3", str(tmp_path), parse_only=True)
    title = res["file_titles"][str(fa0.resolve())]
    assert title == "DEMO-NET/1"
    assert res["parsed"]["file_titles"] == res["file_titles"]
    assert make_project_name(describe_files([str(fa0)], res["file_titles"]), WHEN) \
        == "DEMO-NET_1_24_11_2026_9_05"


def test_rez_title_roundtrip(tmp_path):
    from core_logic.config.models import LevelingLine
    from core_logic.exporters import export_rez
    from core_logic.parsers.rez_parser import RezParser
    ln = LevelingLine(filename="L1", start_point="A", end_point="B")
    ln.total_height_diff, ln.total_distance = 1.2345, 812.4
    path = tmp_path / "out.rez"
    export_rez(str(path), [ln], project_name="Haifa East")
    p = RezParser()
    p.parse_many(str(path))
    assert p.title == "Haifa East"


def test_project_folder_and_zip(tmp_path):
    import zipfile
    from core_logic.project_naming import project_folder, zip_folder
    name = "K6177-64PU-65PU_24_11_2026_9_57"
    f = project_folder(str(tmp_path), name)
    assert f == str(tmp_path / name) and (tmp_path / name).is_dir()
    assert project_folder(f, name) == f                    # never nested twice
    (tmp_path / name / f"{name}.glp").write_text("{}")
    (tmp_path / name / "exports").mkdir()
    (tmp_path / name / "exports" / "a.FA1").write_text("x")
    inside = tmp_path / name / "bundle.zip"
    assert zip_folder(f, str(inside)) == 2                  # the zip itself skipped
    names = sorted(zipfile.ZipFile(inside).namelist())
    assert names == sorted([f"{name}/exports/a.FA1", f"{name}/{name}.glp"])
