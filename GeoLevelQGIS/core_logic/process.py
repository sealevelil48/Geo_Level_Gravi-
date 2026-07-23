"""
core_logic/process.py
Bridge between the QGIS plugin UI and the geodetic_tool engine.

Public API
----------
process_geodetic_data(file_paths, leveling_class, output_dir)
    -> dict with keys: lines_geojson, line_style, point_style, summary
"""

import os
import sys
import logging
from pathlib import Path
from typing import List, Dict

log = logging.getLogger("GeoLevelGravi")


def _build_coord_manager(lines, db_manager):
    """
    Query real ITM coordinates for every unique point in `lines` via the DB
    manager, transform EPSG:2039 → EPSG:4326, and return a pre-populated
    CoordinateManager ready to pass to GeoJSONExporter.

    Falls back gracefully (returns empty CoordinateManager) when:
      - DB manager is not configured
      - PyQGIS is unavailable (standalone / unit-test context)
      - A point is not found in the DB

    CRS note: EPSG:2039 (ITM 2005) → EPSG:4326 (WGS84).
    The transform uses the 2-step pipeline from resources/2039_to_4326.wkt2:
      Step 1 — Inverse Transverse Mercator (ITM → Israel 1993 geographic)
      Step 2 — 7-parameter Bursa-Wolf shift (Israel 1993 → WGS 84)
               X=-48 m, Y=+55 m, Z=+52 m  (EPSG:1073)
    The WKT2 string is loaded and applied via transform.setCoordinateOperation()
    so PROJ executes this exact pipeline regardless of QGIS project datum settings.
    Falls back to the default QGIS EPSG:2039→4326 transform if the file is missing.
    """
    from core_logic.gis.geojson_export import CoordinateManager

    cm = CoordinateManager()

    if not db_manager.is_configured():
        log.warning(
            "_build_coord_manager: DB not configured — "
            "GeoJSON will use schematic coords"
        )
        return cm

    try:
        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsCoordinateTransform,
            QgsPointXY,
            QgsProject,
        )
    except ImportError:
        log.warning(
            "_build_coord_manager: PyQGIS not available — "
            "GeoJSON will use schematic coords"
        )
        return cm

    # Collect unique point IDs across all lines
    unique_ids = set()
    for line in lines:
        if line.start_point:
            unique_ids.add(line.start_point)
        if line.end_point:
            unique_ids.add(line.end_point)

    if not unique_ids:
        return cm

    # Seed centroid pool so resolve_benchmark() can disambiguate geographically
    db_manager.seed_from_qgis_layer(list(unique_ids))

    # EPSG:2039 (ITM 2005) → EPSG:4326 (WGS84)
    # Use the boss-approved 2-step WKT2 pipeline (Inverse TM + Bursa-Wolf 7-param).
    # setCoordinateOperation() pins PROJ to this exact pipeline, bypassing any
    # automatic datum-transform selection QGIS might otherwise apply.
    crs_itm   = QgsCoordinateReferenceSystem("EPSG:2039")
    crs_wgs   = QgsCoordinateReferenceSystem("EPSG:4326")
    transform = QgsCoordinateTransform(crs_itm, crs_wgs, QgsProject.instance())

    _wkt2_path = Path(__file__).parent.parent / "resources" / "2039_to_4326.wkt2"
    try:
        _wkt2 = _wkt2_path.read_text(encoding="utf-8")
        transform.setCoordinateOperation(_wkt2)
        log.info(
            "_build_coord_manager: loaded WKT2 pipeline from %s "
            "(Inverse ITM + Bursa-Wolf 7-param EPSG:1073)",
            _wkt2_path.name,
        )
    except Exception as _wkt2_err:
        log.warning(
            "_build_coord_manager: could not load WKT2 file (%s) — "
            "falling back to default QGIS EPSG:2039→4326 transform",
            _wkt2_err,
        )

    n_ok   = 0
    n_miss = 0

    for pid in unique_ids:
        record = db_manager.resolve_benchmark(pid)
        if record is None or record.x is None or record.y is None:
            log.warning("_build_coord_manager: no coordinates for point '%s'", pid)
            n_miss += 1
            continue

        try:
            # record.x = Easting (ITM), record.y = Northing (ITM)
            pt_itm = QgsPointXY(float(record.x), float(record.y))
            pt_wgs = transform.transform(pt_itm)
            # EPSG:4326: QgsPointXY.x() → Longitude, .y() → Latitude
            lon    = round(pt_wgs.x(), 8)
            lat    = round(pt_wgs.y(), 8)
            height = float(record.gova_ort) if record.gova_ort is not None else 0.0
            cm.add_point(pid, lon, lat, height)
            n_ok += 1
        except Exception as exc:
            log.warning(
                "_build_coord_manager: transform failed for '%s': %s", pid, exc
            )
            n_miss += 1

    log.info(
        "_build_coord_manager: resolved %d / %d points to WGS84 "
        "(%d not in DB / no geometry)",
        n_ok, len(unique_ids), n_miss,
    )
    return cm


def process_geodetic_data(
    file_paths: List[str],
    leveling_class: str,
    output_dir: str,
) -> Dict:
    """
    Parse, validate and export a list of measurement files.

    Parameters
    ----------
    file_paths    : absolute paths to .DAT / .RAW / .GSI files
    leveling_class: "H1" … "H6"
    output_dir    : folder where GeoJSON + QML files are written

    Returns
    -------
    dict
        lines_geojson : str   – path to the exported lines GeoJSON
        line_style    : str   – path to the lines QML style
        point_style   : str   – path to the points QML style
        summary       : dict  – {total, valid, invalid, warnings}
        lines         : list  – parsed LevelingLine objects
    """
    # ------------------------------------------------------------------ #
    # 1. Parse files
    # ------------------------------------------------------------------ #
    from core_logic.parsers.base_parser import create_parser
    from core_logic.validators import BatchValidator
    from core_logic.gis.geojson_export import export_network_to_geojson
    from core_logic.config.israel_survey_regulations import get_class_parameters_by_name

    lines = []
    parse_errors = []

    for fp in file_paths:
        try:
            parser = create_parser(fp)
            line = parser.parse(fp)
            lines.append(line)
            log.info("Parsed: %s  (%s → %s)", Path(fp).name,
                     line.start_point, line.end_point)
        except Exception as exc:
            msg = f"{Path(fp).name}: {exc}"
            parse_errors.append(msg)
            log.warning("Parse error – %s", msg)

    if not lines:
        raise RuntimeError(
            "No files could be parsed.\n" + "\n".join(parse_errors)
        )

    # ------------------------------------------------------------------ #
    # 2. Apply selected class to validator
    # ------------------------------------------------------------------ #
    class_num = int(leveling_class[1])          # "H3" -> 3
    validator = BatchValidator(leveling_class=class_num)
    results = validator.validate_batch(lines)

    valid_count   = sum(1 for _, r in results if r.is_valid)
    invalid_count = len(results) - valid_count
    warning_count = sum(len(r.warnings) for _, r in results)

    log.info("Validation: %d valid, %d invalid, %d warnings",
             valid_count, invalid_count, warning_count)

    # ------------------------------------------------------------------ #
    # 3. Export to GeoJSON + QML styles
    # ------------------------------------------------------------------ #
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    from geolevel_db_manager import get_db_manager
    coord_manager = _build_coord_manager(lines, get_db_manager())

    try:
        output_files = export_network_to_geojson(
            lines,
            output_dir,
            project_name="geo_level_result",
            coord_manager=coord_manager,
        )
    except PermissionError as exc:
        log.error(
            "GeoJSON export blocked — file is locked or permissions denied "
            "(%s). The QGIS line layer will be built directly from DB "
            "coordinates without a GeoJSON fallback.", exc
        )
        output_files = {"lines_geojson": "", "line_style": "", "point_style": ""}
    except OSError as exc:
        log.error(
            "GeoJSON export failed with IO error (%s). "
            "Continuing without GeoJSON output.", exc
        )
        output_files = {"lines_geojson": "", "line_style": "", "point_style": ""}

    # ------------------------------------------------------------------ #
    # 4. Export REZ summary file (same as standalone CLI "export" command)
    # ------------------------------------------------------------------ #
    from core_logic.exporters import export_rez
    rez_path = str(Path(output_dir) / "geo_level_result.rez")
    try:
        export_rez(rez_path, lines, project_name="geo_level_result")
        log.info("Exported REZ to %s", rez_path)
    except Exception as exc:
        log.warning("REZ export failed: %s", exc)
        rez_path = None

    return {
        "lines_geojson": output_files["lines_geojson"],
        "line_style":    output_files["line_style"],
        "point_style":   output_files["point_style"],
        "rez_path":      rez_path,
        "summary": {
            "total":    len(lines),
            "valid":    valid_count,
            "invalid":  invalid_count,
            "warnings": warning_count,
            "parse_errors": parse_errors,
        },
        "lines": lines,
    }
