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
    QGIS/PROJ natively contains the exact 2-step pipeline for this pair:
      Step 1 — Inverse Transverse Mercator (ITM → Israel 1993 geographic)
      Step 2 — 7-parameter Bursa-Wolf shift (Israel 1993 → WGS 84)
               X=-48 m, Y=+55 m, Z=+52 m  (EPSG:1073)
    A blank QgsCoordinateTransformContext() lets PROJ select this operation
    automatically without tying the transform to the active QGIS project or
    triggering "preset transform not available" GUI popups.
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
            QgsCoordinateTransformContext,
            QgsPointXY,
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
    # Blank context: PROJ selects the native EPSG:1073 pipeline automatically
    # (Inverse ITM + Bursa-Wolf 7-param, X=-48 Y=+55 Z=+52 m).
    crs_itm   = QgsCoordinateReferenceSystem("EPSG:2039")
    crs_wgs   = QgsCoordinateReferenceSystem("EPSG:4326")
    transform = QgsCoordinateTransform(crs_itm, crs_wgs, QgsCoordinateTransformContext())

    # ------------------------------------------------------------------ #
    # Pass 1 — DB resolution: ITM coords for known benchmarks             #
    # itm_coords[pid] = (easting, northing, height, is_estimated)         #
    # ------------------------------------------------------------------ #
    itm_coords = {}   # pid -> (E, N, height, is_estimated)
    unknown_ids = []

    # One batched DB lookup for every name not resolved yet this session
    if hasattr(db_manager, "prefetch"):
        db_manager.prefetch(sorted(unique_ids))
    for pid in unique_ids:
        record = db_manager.resolve_benchmark(pid)
        if record is not None and record.x is not None and record.y is not None:
            height = float(record.gova_ort) if record.gova_ort is not None else 0.0
            itm_coords[pid] = (float(record.x), float(record.y), height, False)
        else:
            unknown_ids.append(pid)

    log.info(
        "_build_coord_manager: Pass 1 — resolved %d / %d from DB; "
        "%d unknown (PKT estimation): %s",
        len(itm_coords), len(unique_ids), len(unknown_ids),
        unknown_ids or "none",
    )

    # ------------------------------------------------------------------ #
    # Pass 2 — topological estimation (Dijkstra on measured lengths) for   #
    # points missing from the DB, also through chains of unknown points.   #
    # ------------------------------------------------------------------ #
    from core_logic.gis.topo_estimator import estimate_positions
    known_xy = {pid: (v[0], v[1]) for pid, v in itm_coords.items()}
    for pid, (est_e, est_n) in estimate_positions(lines, known_xy).items():
        itm_coords[pid] = (est_e, est_n, 0.0, True)

    # ------------------------------------------------------------------ #
    # Transform all ITM coords to WGS84 and load into CoordinateManager  #
    # Estimated (PKT) points are tagged so QML can render them distinctly #
    # ------------------------------------------------------------------ #
    n_ok = n_miss = 0
    for pid, (east, north, height, is_estimated) in itm_coords.items():
        try:
            pt_wgs = transform.transform(QgsPointXY(east, north))
            lon = round(pt_wgs.x(), 8)
            lat = round(pt_wgs.y(), 8)
            cm.add_point(pid, lon, lat, height)
            # Tag estimated points so downstream QML can color them distinctly
            if is_estimated:
                cm.coordinates[pid] = (lon, lat, height, "PKT")
            n_ok += 1
        except Exception as exc:
            log.warning(
                "_build_coord_manager: transform failed for '%s': %s", pid, exc
            )
            n_miss += 1

    log.info(
        "_build_coord_manager: %d point(s) transformed to WGS84 "
        "(%d DB, %d estimated, %d failed)",
        n_ok,
        n_ok - len([v for v in itm_coords.values() if v[3]]),
        len([v for v in itm_coords.values() if v[3]]),
        n_miss,
    )
    return cm


def export_layers(lines, output_dir: str, project_name: str = "geo_level_result") -> Dict:
    """Re-export GeoJSON + QML for in-memory *lines* (no file parsing).

    Used by the Point Settings screen to rebuild the map after a point was
    re-selected.  Coordinates come from the DB manager session cache, so the
    engineer's choices are honoured.

    Returns dict with lines_geojson, points_geojson, line_style, point_style
    (paths are "" when the export was blocked).
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    from core_logic.gis.geojson_export import export_network_to_geojson
    from geolevel_db_manager import get_db_manager
    coord_manager = _build_coord_manager(lines, get_db_manager())
    try:
        return export_network_to_geojson(
            lines, output_dir, project_name=project_name, coord_manager=coord_manager,
        )
    except OSError as exc:   # PermissionError is a subclass
        log.error("GeoJSON re-export failed (%s) — continuing without GeoJSON", exc)
        return {"lines_geojson": "", "points_geojson": "", "line_style": "", "point_style": ""}


def process_geodetic_data(
    file_paths: List[str],
    leveling_class: str,
    output_dir: str,
    name_overrides: dict = None,
    parse_only: bool = False,
    preparsed: Dict = None,
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
        parsed        : dict  – raw parse {lines, file_benchmarks, parse_errors}
                                (deep copy, before *name_overrides*); pass it
                                back as *preparsed* to skip re-reading files

    parse_only : stop after parsing (+ name overrides): no validation, DB
                 coordinate resolution, GeoJSON or REZ.  Used by the plugin's
                 first pass, whose only job is feeding Point Verification.
    preparsed  : reuse an earlier ``result["parsed"]`` instead of reading
                 *file_paths* again.  Its lines are taken over (not copied):
                 pass each snapshot once.
    """
    import pickle
    # ------------------------------------------------------------------ #
    # 1. Parse files
    # ------------------------------------------------------------------ #
    from core_logic.parsers.base_parser import create_parser
    from core_logic.validators import BatchValidator
    from core_logic.gis.geojson_export import export_network_to_geojson
    from core_logic.config.israel_survey_regulations import get_class_parameters_by_name

    lines = []
    parse_errors = []

    file_benchmarks = {}   # FIXED points of .FA0 / .FA1 files (header order)
    file_approx = {}       # other heights listed there (approximate)
    file_titles = {}       # abs path -> header description (FA0/FA1/REZ) or ""
    if preparsed is not None:
        lines = preparsed["lines"]
        file_benchmarks = dict(preparsed.get("file_benchmarks") or {})
        file_approx = dict(preparsed.get("file_approx") or {})
        file_titles = dict(preparsed.get("file_titles") or {})
        parse_errors = list(preparsed.get("parse_errors") or [])
        file_paths = []
    for fp in file_paths:
        try:
            parser = create_parser(fp)
            if parser is None:
                raise ValueError("unsupported file type")
            parsed = parser.parse_many(fp)
            for line in parsed:
                line.source_path = os.path.abspath(fp)
                lines.append(line)
            for pid, h in (getattr(parser, "benchmarks", None) or {}).items():
                file_benchmarks[pid] = h
            for pid, h in (getattr(parser, "approx_heights", None) or {}).items():
                file_approx[pid] = h
            file_titles[os.path.abspath(fp)] = str(getattr(parser, "title", "") or "")
            if len(parsed) == 1:
                log.info("Parsed: %s  (%s → %s)", Path(fp).name,
                         parsed[0].start_point, parsed[0].end_point)
            else:
                log.info("Parsed: %s  (%d lines, %d known heights)", Path(fp).name,
                         len(parsed), len(getattr(parser, "benchmarks", {}) or {}))
        except Exception as exc:
            msg = f"{Path(fp).name}: {exc}"
            parse_errors.append(msg)
            log.warning("Parse error – %s", msg)

    if not lines:
        raise RuntimeError(
            "No files could be parsed.\n" + "\n".join(parse_errors)
        )

    snapshot = None
    if parse_only:
        try:      # pickle round-trip = deep copy, ~3x faster than copy.deepcopy
            pristine = pickle.loads(pickle.dumps(lines, pickle.HIGHEST_PROTOCOL))
        except Exception:
            import copy
            pristine = copy.deepcopy(lines)
        snapshot = {"lines": pristine, "file_benchmarks": dict(file_benchmarks),
                    "file_approx": dict(file_approx),
                    "file_titles": dict(file_titles),
                    "parse_errors": list(parse_errors)}

    # Apply verified DB name overrides (e.g. ASCII → Hebrew) before any
    # validation, coordinate resolution, or GeoJSON export runs.
    if name_overrides:
        for ln in lines:
            if ln.start_point in name_overrides:
                ln.start_point = name_overrides[ln.start_point]
            if ln.end_point in name_overrides:
                ln.end_point = name_overrides[ln.end_point]

    renamed_benchmarks = {(name_overrides or {}).get(k, k): v
                          for k, v in file_benchmarks.items()}
    renamed_approx = {(name_overrides or {}).get(k, k): v for k, v in file_approx.items()}
    if parse_only:
        return {
            "lines_geojson": "", "points_geojson": "", "line_style": "",
            "point_style": "", "rez_path": None,
            "file_benchmarks": renamed_benchmarks, "file_approx": renamed_approx,
            "file_titles": file_titles,
            "summary": {"total": len(lines), "valid": 0, "invalid": 0,
                        "warnings": 0, "parse_errors": parse_errors},
            "lines": lines,
            "parsed": snapshot,
        }

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
        output_files = {"lines_geojson": "", "points_geojson": "", "line_style": "", "point_style": ""}
    except OSError as exc:
        log.error(
            "GeoJSON export failed with IO error (%s). "
            "Continuing without GeoJSON output.", exc
        )
        output_files = {"lines_geojson": "", "points_geojson": "", "line_style": "", "point_style": ""}

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
        "lines_geojson":  output_files["lines_geojson"],
        "points_geojson": output_files.get("points_geojson", ""),
        "line_style":     output_files["line_style"],
        "point_style":    output_files["point_style"],
        "rez_path":       rez_path,
        # Known heights read from .FA0 / .FA1 files (renamed like the lines)
        "file_benchmarks": renamed_benchmarks, "file_approx": renamed_approx,
        "file_titles": file_titles,
        "summary": {
            "total":    len(lines),
            "valid":    valid_count,
            "invalid":  invalid_count,
            "warnings": warning_count,
            "parse_errors": parse_errors,
        },
        "lines": lines,
        "parsed": snapshot,
    }
