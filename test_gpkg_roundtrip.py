"""
Standalone test for GeoPackage project persistence (geo_level_gpkg.py).

Run:  python test_gpkg_roundtrip.py
      (inside the OSGeo4W / QGIS Python the spatial-layer part runs too:
       "C:\\Program Files\\QGIS 3.34.4\\bin\\python-qgis-ltr.bat" test_gpkg_roundtrip.py)

Checks:
  1. LevelingLine/StationSetup -> lv_lines/lv_setups/lv_meta -> objects is lossless.
  2. Attribute tables are written into a valid GeoPackage (gpkg_contents rows).
  3. Rewriting the tables in place (save after edits) replaces old rows.
  4. With PyQGIS: geo_lines/geo_points layers are written, reopened through
     the OGR provider, and the attribute tables survive alongside them.
"""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "GeoLevelQGIS"))

import geo_level_gpkg as gp  # noqa: E402
from core_logic.config.models import (  # noqa: E402
    LevelingLine, StationSetup, LineStatus, ValidationResult)

FAILS = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def make_lines():
    l1 = LevelingLine(
        filename="L101", start_point="250/A", end_point="ב-17",
        setups=[
            StationSetup(1, "250/A", "1", 1.2345, 0.9876, 30.1, 29.9, temperature=21.5),
            StationSetup(2, "1", "ב-17", 1.1111, 1.5555, 28.0, 28.4, is_used=False),
        ],
        method="BFFB", date=datetime(2026, 3, 1, 8, 30), instrument_id="DiNi-03",
        total_distance=116.4, total_height_diff=-0.1975, misclosure=0.0012,
        status=LineStatus.VALID_BY_MANAGER, validation_errors=["סגירה חורגת", "x"],
        is_used=True, original_direction="BF", manager_override=True,
    )
    l1.source_path = r"C:\survey\day1\L101.DAT"
    l2 = LevelingLine(filename="L102", start_point="ב-17", end_point="849/L",
                      setups=[StationSetup(1, "ב-17", "849/L", 1.0, 1.1, 20.0, 20.0)],
                      status=LineStatus.EXCEEDED_TOLERANCE, is_used=False)
    l2.calculate_totals()
    return [l1, l2]


def same_line(a, b):
    keys = ["filename", "start_point", "end_point", "method", "date", "instrument_id",
            "total_distance", "total_height_diff", "misclosure", "status",
            "validation_errors", "is_used", "original_direction", "manager_override",
            "source_path"]
    for k in keys:
        if getattr(a, k) != getattr(b, k):
            return f"{a.filename}.{k}: {getattr(a, k)!r} != {getattr(b, k)!r}"
    if len(a.setups) != len(b.setups):
        return f"{a.filename}: setup count"
    for sa, sb in zip(a.setups, b.setups):
        if vars(sa) != vars(sb):
            return f"{a.filename}: setup {sa.setup_number} {vars(sa)} != {vars(sb)}"
    return None


def test_tables(tmpdir):
    print("[1] attribute tables round-trip")
    lines = make_lines()
    path = os.path.join(tmpdir, "tables_only.gpkg")
    meta = {"class": "H2", "point_overrides": {"250/A": {"mode": "estimate"}},
            "point_origin": {"ב-17": "B17"}}
    gp.write_attribute_tables(path, lines, meta)
    check(gp.has_project_tables(path), "has_project_tables() is True")
    back, meta2 = gp.read_attribute_tables(path)
    check(len(back) == 2, "two lines read back")
    for a, b in zip(lines, back):
        diff = same_line(a, b)
        check(diff is None, f"line {a.filename} identical" + (f" ({diff})" if diff else ""))
    check(meta2.get("class") == "H2", "meta class")
    check(meta2.get("point_overrides") == meta["point_overrides"], "meta point_overrides")
    check(meta2.get("point_origin") == meta["point_origin"], "meta point_origin (Hebrew)")
    check(meta2.get("schema_version") == gp.SCHEMA_VERSION, "meta schema_version")

    conn = sqlite3.connect(path)
    app_id = conn.execute("PRAGMA application_id").fetchone()[0]
    rows = dict(conn.execute("SELECT table_name, data_type FROM gpkg_contents").fetchall())
    conn.close()
    check(app_id == 1196444487, "GeoPackage application_id set")
    check(rows.get("lv_lines") == "attributes" and rows.get("lv_setups") == "attributes",
          "tables registered in gpkg_contents")

    print("[2] in-place rewrite replaces rows")
    lines[0].status = LineStatus.INVALID_ENDPOINT
    lines[0].manager_override = False
    gp.write_attribute_tables(path, lines[:1], meta)
    back, _ = gp.read_attribute_tables(path)
    check(len(back) == 1, "old rows removed")
    check(back[0].status == LineStatus.INVALID_ENDPOINT and not back[0].manager_override,
          "edited status/override persisted")


def make_vrs(lines):
    v1 = ValidationResult(is_valid=False)
    v1.add_error("סגירה חורגת מהסבולת")
    v1.add_warning("only 2 setups")
    v1.tolerance_valid = False
    v1.naming_valid = False
    v2 = ValidationResult(is_valid=True)
    v2.data_complete = False
    return [(lines[0], v1), (lines[1], v2)]


def test_validation_and_meta(tmpdir):
    print("[2b] validation results + GeoPackage-first meta")
    lines = make_lines()
    vrs = make_vrs(lines)
    path = os.path.join(tmpdir, "v2.gpkg")
    meta = {
        "leveling_class": "H4", "class": "H4",
        "dat_files": [r"C:\survey\day1\L101.DAT", r"C:\survey\ב\L102.DAT"],
        "pid_remap": {"2599B": "2599/ב", "250A": "250/A"},
        "point_origin": {"2599/ב": "2599B"},
        "point_overrides": {"2599B": {"mode": "use_selected",
                                      "record": {"name": "2599/ב", "x": 200000.5,
                                                 "y": 600000.25, "gova_ort": 12.345,
                                                 "objectid": 77, "evil": "<script>"}}},
        "resolved_points": {"2599/ב": {"name": "2599/ב", "x": 200000.5, "y": 600000.25,
                                       "gova_ort": 12.345, "objectid": 77},
                            "PKT1": None},
    }
    gp.write_attribute_tables(path, lines, meta, vrs)
    back, back_vrs, raw = gp.read_project_tables(path)
    check(back_vrs is not None and len(back_vrs) == 2, "validation results read back")
    if back_vrs:
        for i, ((la, va), (lb, vb)) in enumerate(zip(vrs, back_vrs)):
            check(lb is back[i] and vars(va) == vars(vb),
                  f"ValidationResult for {la.filename} identical and paired")
    m = gp.sanitize_meta(raw)
    check(m["leveling_class"] == "H4" and m["class"] == "H4", "leveling_class")
    check(m["dat_files"] == meta["dat_files"], "dat_files (Hebrew path)")
    check(m["pid_remap"] == meta["pid_remap"], "pid_remap")
    check(m["point_origin"] == meta["point_origin"], "point_origin")
    rp = m["resolved_points"]
    check(rp.get("PKT1", "missing") is None, "estimated point kept as None")
    check(rp.get("2599/ב", {}).get("x") == 200000.5 and rp["2599/ב"].get("objectid") == 77,
          "resolved record round-trip")
    rec = m["point_overrides"]["2599B"]["record"]
    check("evil" not in rec and rec["name"] == "2599/ב", "override record keys filtered")
    check(m["schema_version"] == gp.SCHEMA_VERSION == 2, "schema_version 2")

    # Stale val_results (other line objects) store nothing -> None on read
    gp.write_attribute_tables(path, lines, meta, make_vrs(make_lines()))
    _, none_vrs, _ = gp.read_project_tables(path)
    check(none_vrs is None, "validation from other line objects not stored")

    print("[2c] schema-1 GeoPackage still loads")
    old = os.path.join(tmpdir, "v1.gpkg")
    gp.write_attribute_tables(old, lines, {"class": "H2"}, vrs)
    conn = sqlite3.connect(old)
    with conn:
        cols = [c for c, _ in gp._LINE_COLS if c not in ("validation", "manager_override",
                                                          "original_direction")]
        conn.execute("CREATE TABLE t AS SELECT fid, " + ", ".join(cols) + " FROM lv_lines")
        conn.execute("DROP TABLE lv_lines")
        conn.execute("ALTER TABLE t RENAME TO lv_lines")
        conn.execute("UPDATE lv_meta SET value='1' WHERE key='schema_version'")
    conn.close()
    back1, vrs1, raw1 = gp.read_project_tables(old)
    m1 = gp.sanitize_meta(raw1)
    check(len(back1) == 2 and vrs1 is None, "v1 file: lines read, no validation")
    check(back1[0].status == LineStatus.VALID_BY_MANAGER, "v1 file: status kept")
    check(m1["leveling_class"] == "H2" and m1["schema_version"] == 1
          and m1["resolved_points"] == {} and m1["dat_files"] == [], "v1 meta defaults")

    print("[2d] sanitize_meta rejects junk from untrusted files")
    junk = {
        "leveling_class": "H9; DROP", "point_overrides": {"a": "str", "": {"mode": "x"},
                                                          "b": {"mode": 5},
                                                          "c": {"mode": "rename", "name": 3}},
        "point_origin": {"x": 1, "y": "Y"}, "pid_remap": ["not", "a", "dict"],
        "dat_files": "C:\\x.dat",
        "resolved_points": {"p": {"x": float("nan"), "y": "600000", "name": 7},
                            "q": "bad", "": None},
        "output_dir": 12, "schema_version": True,
    }
    j = gp.sanitize_meta(junk)
    check(j["leveling_class"] == "H3", "bad class -> H3")
    check(list(j["point_overrides"]) == ["c"] and "name" not in j["point_overrides"]["c"],
          "bad overrides dropped")
    check(j["point_origin"] == {"y": "Y"} and j["pid_remap"] == {}, "bad maps dropped")
    check(j["dat_files"] == [] and j["output_dir"] == "" and j["schema_version"] == 1,
          "bad scalars defaulted")
    p = j["resolved_points"].get("p") or {}
    check(set(j["resolved_points"]) == {"p"} and p.get("x") is None and p.get("y") is None
          and p.get("name") is None, "NaN / str coords and bad entries dropped")
    check(gp.sanitize_meta("nope")["leveling_class"] == "H3", "non-dict meta handled")


def test_spatial(tmpdir):
    try:
        from qgis.core import (QgsApplication, QgsVectorLayer, QgsFeature,
                               QgsGeometry, QgsPointXY, QgsField)
        from qgis.PyQt.QtCore import QVariant
    except ImportError:
        print("[3] PyQGIS not available - spatial-layer test skipped")
        return
    print("[3] spatial layers through QgsVectorFileWriter")
    app = QgsApplication([], False)
    app.initQgis()
    try:
        # Two source layers (as when files are appended) with different
        # schemas, plus a stray point in the line layer (mixed GeoJSON case).
        a = QgsVectorLayer("LineString?crs=EPSG:2039", "Geodetic Results", "memory")
        a.dataProvider().addAttributes([QgsField("filename", QVariant.String),
                                        QgsField("status", QVariant.String)])
        a.updateFields()
        f = QgsFeature(a.fields())
        f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(200000, 600000),
                                                  QgsPointXY(200100, 600050)]))
        f.setAttributes(["L102", "VALID"])
        a.dataProvider().addFeatures([f])

        b = QgsVectorLayer("LineString?crs=EPSG:2039", "Geodetic Results", "memory")
        b.dataProvider().addAttributes([QgsField("filename", QVariant.String),
                                        QgsField("distance_m", QVariant.Double)])
        b.updateFields()
        g = QgsFeature(b.fields())
        g.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(200100, 600050),
                                                  QgsPointXY(200300, 600100)]))
        g.setAttributes(["L101", 116.4])
        b.dataProvider().addFeatures([g])

        pts = QgsVectorLayer("Point?crs=EPSG:2039", "Geodetic Points", "memory")
        pts.dataProvider().addAttributes([QgsField("point_id", QVariant.String),
                                          QgsField("status", QVariant.String)])
        pts.updateFields()
        feats = []
        for pid, x in (("250/A", 200000), ("ב-17", 200100), ("ב-17", 200100)):
            p = QgsFeature(pts.fields())
            p.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, 600000)))
            p.setAttributes([pid, "benchmark"])
            feats.append(p)
        pts.dataProvider().addFeatures(feats)

        order = {"L101": 0, "L102": 1}
        mem_lines = gp.merge_layers_to_memory(
            [a, b], "line", order_key=lambda r: order.get(r.get("filename"), 9),
            dedupe_field="filename")
        mem_pts = gp.merge_layers_to_memory([pts], "point", dedupe_field="point_id")
        check(mem_lines.featureCount() == 2, "merged line layer has 2 features")
        check(mem_pts.featureCount() == 2, "duplicate point removed")

        path = os.path.join(tmpdir, "geo_level_results.gpkg")
        lines = make_lines()
        gp.write_project_gpkg(path, mem_lines, mem_pts, lines, {"class": "H3"})
        check(not os.path.exists(path.replace(".gpkg", "~tmp.gpkg")), "temp file removed")

        gl = gp.gpkg_layer(path, gp.LINES_LAYER, "Geodetic Results")
        gpt = gp.gpkg_layer(path, gp.POINTS_LAYER, "Geodetic Points")
        check(gl is not None and gl.featureCount() == 2, "geo_lines reopened via OGR")
        check(gpt is not None and gpt.featureCount() == 2, "geo_points reopened via OGR")
        if gl is not None:
            names = [ft["filename"] for ft in gl.getFeatures()]
            check(names == ["L101", "L102"], f"line order follows dock order ({names})")
            check(gl.crs().authid() == "EPSG:2039", "CRS preserved")
            check(set(gl.fields().names()) >= {"filename", "status", "distance_m"},
                  "union of source fields kept")
            # Attribute edits on the GPKG layer must commit (override repaint)
            gl.startEditing()
            idx = gl.fields().indexOf("status")
            for ft in gl.getFeatures():
                gl.changeAttributeValue(ft.id(), idx, "VALID_BY_MANAGER")
            check(gl.commitChanges(), "attribute edit committed to GPKG")
        check(gp.has_project_tables(path), "attribute tables next to spatial layers")
        back, meta = gp.read_attribute_tables(path)
        check(len(back) == 2 and same_line(lines[0], back[0]) is None,
              "lines read back from full GPKG")
        # Save while the map layer is open: in-place table rewrite
        gp.write_attribute_tables(path, lines[:1], {"class": "H4"})
        back, meta = gp.read_attribute_tables(path)
        check(len(back) == 1 and meta.get("class") == "H4",
              "in-place table rewrite while layer open")
        # Autosave path: validation results written in place while open
        gp.write_attribute_tables(path, lines, {"leveling_class": "H5"}, make_vrs(lines))
        back, vrs, raw = gp.read_project_tables(path)
        check(len(back) == 2 and vrs is not None
              and gp.sanitize_meta(raw)["leveling_class"] == "H5",
              "in-place autosave with validation while layer open")
        check(gl.featureCount() == 2, "spatial layer intact after autosave")
        del gl, gpt
    finally:
        app.exitQgis()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        test_tables(d)
        test_validation_and_meta(d)
        test_spatial(d)
    print()
    if FAILS:
        print(f"{len(FAILS)} check(s) FAILED")
        sys.exit(1)
    print("All checks passed")
