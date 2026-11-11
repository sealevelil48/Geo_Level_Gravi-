"""
geo_level_gpkg.py
GeoPackage persistence for Geo Level Gravi projects.

One .gpkg file holds everything needed to reopen a project without
re-parsing the measurement files or querying the benchmark DB:

    geo_lines   LineString layer  -> "Geodetic Results" on the map
    geo_points  Point layer       -> "Geodetic Points"  on the map
    lv_lines    attribute table   one row per LevelingLine
    lv_setups   attribute table   one row per StationSetup
    lv_meta     attribute table   key/value project metadata (JSON values)

The two spatial layers are written with QgsVectorFileWriter (GPKG driver).
The attribute tables are written/read with sqlite3, which keeps the line and
setup serialisation testable outside QGIS (see test_gpkg_roundtrip.py).
"""

import json
import os
import sqlite3
from datetime import datetime

SCHEMA_VERSION = 2
LINES_LAYER = "geo_lines"
POINTS_LAYER = "geo_points"
DEFAULT_GPKG_NAME = "geo_level_results.gpkg"

_LINE_COLS = [
    ("line_idx", "INTEGER"),
    ("filename", "TEXT"),
    ("source_path", "TEXT"),
    ("start_point", "TEXT"),
    ("end_point", "TEXT"),
    ("method", "TEXT"),
    ("date", "TEXT"),
    ("instrument_id", "TEXT"),
    ("total_distance", "REAL"),
    ("total_height_diff", "REAL"),
    ("misclosure", "REAL"),
    ("status", "TEXT"),
    ("validation_errors", "TEXT"),
    ("is_used", "INTEGER"),
    ("original_direction", "TEXT"),
    ("manager_override", "INTEGER"),
    ("validation", "TEXT"),          # JSON ValidationResult (schema >= 2)
]

_SETUP_COLS = [
    ("line_idx", "INTEGER"),
    ("setup_number", "INTEGER"),
    ("from_point", "TEXT"),
    ("to_point", "TEXT"),
    ("backsight_reading", "REAL"),
    ("foresight_reading", "REAL"),
    ("distance_back", "REAL"),
    ("distance_fore", "REAL"),
    ("temperature", "REAL"),
    ("height_diff", "REAL"),
    ("cumulative_height", "REAL"),
    ("is_used", "INTEGER"),
]


# ---------------------------------------------------------------------------
# Pure serialisation (no QGIS)
# ---------------------------------------------------------------------------

def _enum_val(v):
    return v.value if hasattr(v, "value") else v


_VR_FLAGS = ("endpoint_valid", "naming_valid", "tolerance_valid", "data_complete")


def validation_to_dict(vr):
    """ValidationResult -> plain dict (JSON-serialisable)."""
    if vr is None:
        return None
    d = {"is_valid": bool(vr.is_valid),
         "errors": [str(e) for e in (vr.errors or [])],
         "warnings": [str(w) for w in (vr.warnings or [])]}
    for k in _VR_FLAGS:
        d[k] = bool(getattr(vr, k, True))
    return d


def validation_from_dict(d):
    """Inverse of validation_to_dict; None for anything malformed."""
    from core_logic.config.models import ValidationResult
    if not isinstance(d, dict) or not isinstance(d.get("is_valid"), bool):
        return None

    def _strs(v):
        return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []

    vr = ValidationResult(is_valid=d["is_valid"],
                          errors=_strs(d.get("errors")),
                          warnings=_strs(d.get("warnings")))
    for k in _VR_FLAGS:
        v = d.get(k, True)
        setattr(vr, k, v if isinstance(v, bool) else True)
    return vr


def lines_to_rows(lines, val_results=None):
    """LevelingLine list -> (line_rows, setup_rows) as lists of dicts.

    val_results: optional [(line, ValidationResult)] as returned by
    BatchValidator.validate_batch; stored per line in the 'validation' column
    (matched by object identity, so a stale list simply stores nothing).
    """
    vr_by_line = {}
    for item in (val_results or []):
        try:
            ln_obj, vr = item
        except (TypeError, ValueError):
            continue
        vr_by_line[id(ln_obj)] = vr
    line_rows, setup_rows = [], []
    for i, ln in enumerate(lines):
        date = getattr(ln, "date", None)
        if isinstance(date, datetime):
            date = date.isoformat()
        line_rows.append({
            "line_idx": i,
            "filename": ln.filename,
            "source_path": getattr(ln, "source_path", None),
            "start_point": ln.start_point,
            "end_point": ln.end_point,
            "method": _enum_val(ln.method),
            "date": None if date is None else str(date),
            "instrument_id": ln.instrument_id,
            "total_distance": ln.total_distance,
            "total_height_diff": ln.total_height_diff,
            "misclosure": ln.misclosure,
            "status": _enum_val(ln.status),
            "validation_errors": json.dumps(list(ln.validation_errors or []),
                                            ensure_ascii=False),
            "is_used": int(bool(getattr(ln, "is_used", True))),
            "original_direction": getattr(ln, "original_direction", "BF"),
            "manager_override": int(bool(getattr(ln, "manager_override", False))),
            "validation": (json.dumps(validation_to_dict(vr_by_line[id(ln)]),
                                      ensure_ascii=False)
                           if vr_by_line.get(id(ln)) is not None else None),
        })
        for s in ln.setups:
            setup_rows.append({
                "line_idx": i,
                "setup_number": s.setup_number,
                "from_point": s.from_point,
                "to_point": s.to_point,
                "backsight_reading": s.backsight_reading,
                "foresight_reading": s.foresight_reading,
                "distance_back": s.distance_back,
                "distance_fore": s.distance_fore,
                "temperature": s.temperature,
                "height_diff": s.height_diff,
                "cumulative_height": s.cumulative_height,
                "is_used": int(bool(getattr(s, "is_used", True))),
            })
    return line_rows, setup_rows


def rows_to_lines(line_rows, setup_rows):
    """Inverse of lines_to_rows -> list of LevelingLine."""
    return rows_to_project(line_rows, setup_rows)[0]


def rows_to_project(line_rows, setup_rows):
    """Inverse of lines_to_rows -> (lines, val_results or None).

    val_results is None unless every line row carries a stored result
    (schema 1 files have no 'validation' column). Untrusted input: every
    numeric column is coerced to finite float/int; non-compliant rows are
    silently skipped (a GeoPackage from someone else can have corrupted data).
    """
    from core_logic.config.models import LevelingLine, StationSetup, LineStatus

    def _safe_int(v, key):
        if isinstance(v, int) and not isinstance(v, bool):
            return v
        try:
            return int(v)
        except (ValueError, TypeError):
            return None

    by_line = {}
    for r in setup_rows:
        line_idx = r.get("line_idx")
        if line_idx is None:
            continue
        by_line.setdefault(line_idx, []).append(r)

    lines, vrs = [], []
    for r in sorted(line_rows, key=lambda x: x.get("line_idx", -1)):
        line_idx = _safe_int(r.get("line_idx"), "line_idx")
        if line_idx is None:
            continue
        setups = []
        setup_list = by_line.get(line_idx, [])
        for s in sorted(setup_list, key=lambda x: x.get("setup_number", 0)):
            setup_num = _safe_int(s.get("setup_number"), "setup_number")
            if setup_num is None:
                continue
            rdgs = [_finite(s.get(k)) for k in ("backsight_reading", "foresight_reading")]
            dists = [_finite(s.get(k)) for k in ("distance_back", "distance_fore")]
            if any(x is None for x in rdgs + dists):
                continue  # Skip malformed setup
            # Validate string fields BEFORE using them
            from_pt = s.get("from_point")
            to_pt = s.get("to_point")
            if not (isinstance(from_pt, (str, type(None))) and isinstance(to_pt, (str, type(None)))):
                continue  # Skip malformed setup
            # Validate optional float fields
            temp = _finite(s.get("temperature"))
            dh = _finite(s.get("height_diff"))
            cumh = _finite(s.get("cumulative_height"))
            # Use VALIDATED values, not raw dict access
            setups.append(StationSetup(
                setup_number=setup_num,
                from_point=from_pt or "",
                to_point=to_pt or "",
                backsight_reading=rdgs[0],
                foresight_reading=rdgs[1],
                distance_back=dists[0],
                distance_fore=dists[1],
                temperature=temp,
                height_diff=dh,
                cumulative_height=cumh,
                is_used=(bool(s.get("is_used")) if "is_used" in s
                         and isinstance(s["is_used"], (bool, int)) else True),
            ))
        fn = r.get("filename")
        if not isinstance(fn, str) or not fn.strip():
            continue  # Require filename
        sp = r.get("start_point")
        ep = r.get("end_point")
        if not (isinstance(sp, (str, type(None))) and isinstance(ep, (str, type(None)))):
            continue
        date = r.get("date")
        if date:
            try:
                date = datetime.fromisoformat(str(date))
            except (ValueError, TypeError):
                date = None
        try:
            status = LineStatus(r.get("status", "valid"))
        except (ValueError, KeyError):
            status = LineStatus.VALID
        vr_text = r.get("validation_errors")
        try:
            errors = (json.loads(vr_text) if isinstance(vr_text, str)
                      else (vr_text if isinstance(vr_text, list) else []))
            errors = [str(e) for e in errors]
        except (ValueError, TypeError):
            errors = []
        ln = LevelingLine(
            filename=fn.strip(),
            start_point=sp or "",
            end_point=ep or "",
            setups=setups,
            method=str(r.get("method") or "BF"),
            date=date,
            instrument_id=r.get("instrument_id") or None,
            total_distance=_finite(r.get("total_distance")) or 0.0,
            total_height_diff=_finite(r.get("total_height_diff")) or 0.0,
            misclosure=_finite(r.get("misclosure")),
            status=status,
            validation_errors=errors,
            is_used=(bool(r.get("is_used")) if "is_used" in r
                     and isinstance(r["is_used"], (bool, int)) else True),
            original_direction=str(r.get("original_direction") or "BF"),
            manager_override=bool(r.get("manager_override")),
        )
        ln.source_path = r.get("source_path")
        lines.append(ln)
        vr = None
        raw = r.get("validation")
        if raw:
            try:
                vr = validation_from_dict(json.loads(raw))
            except (TypeError, ValueError):
                vr = None
        vrs.append(vr)
    if lines and all(v is not None for v in vrs):
        return lines, list(zip(lines, vrs))
    return lines, None


# ---------------------------------------------------------------------------
# sqlite3 attribute tables
# ---------------------------------------------------------------------------

def _ensure_gpkg_core(conn):
    """Create the minimal GeoPackage core tables if the file is brand new."""
    has = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='gpkg_contents'"
    ).fetchone()
    if has:
        return
    conn.execute("PRAGMA application_id = 1196444487")   # 'GPKG'
    conn.execute("PRAGMA user_version = 10200")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS gpkg_spatial_ref_sys ("
        "srs_name TEXT NOT NULL, srs_id INTEGER NOT NULL PRIMARY KEY, "
        "organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL, "
        "definition TEXT NOT NULL, description TEXT)"
    )
    conn.executemany(
        "INSERT OR IGNORE INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
        [("Undefined cartesian SRS", -1, "NONE", -1, "undefined", None),
         ("Undefined geographic SRS", 0, "NONE", 0, "undefined", None),
         ("WGS 84 geodetic", 4326, "EPSG", 4326,
          'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
          'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]', None)],
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS gpkg_contents ("
        "table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL, "
        "identifier TEXT UNIQUE, description TEXT DEFAULT '', "
        "last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), "
        "min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE, srs_id INTEGER)"
    )


def _write_table(conn, name, cols, rows):
    conn.execute(f'DROP TABLE IF EXISTS "{name}"')
    col_sql = ", ".join(f'"{c}" {t}' for c, t in cols)
    conn.execute(f'CREATE TABLE "{name}" (fid INTEGER PRIMARY KEY AUTOINCREMENT, {col_sql})')
    names = [c for c, _ in cols]
    conn.executemany(
        f'INSERT INTO "{name}" ({", ".join(chr(34) + n + chr(34) for n in names)}) '
        f'VALUES ({", ".join("?" for _ in names)})',
        [tuple(r.get(n) for n in names) for r in rows],
    )
    conn.execute(
        "INSERT OR REPLACE INTO gpkg_contents (table_name, data_type, identifier, last_change) "
        "VALUES (?, 'attributes', ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
        (name, name),
    )


def write_attribute_tables(path, lines, meta, val_results=None):
    """(Re)write lv_lines / lv_setups / lv_meta inside the GeoPackage."""
    line_rows, setup_rows = lines_to_rows(lines, val_results)
    meta = dict(meta or {})
    meta["schema_version"] = SCHEMA_VERSION
    meta_rows = [{"key": k, "value": json.dumps(v, ensure_ascii=False)}
                 for k, v in meta.items()]
    conn = sqlite3.connect(path, timeout=10)
    try:
        _ensure_gpkg_core(conn)
        with conn:
            _write_table(conn, "lv_lines", _LINE_COLS, line_rows)
            _write_table(conn, "lv_setups", _SETUP_COLS, setup_rows)
            _write_table(conn, "lv_meta", [("key", "TEXT"), ("value", "TEXT")], meta_rows)
    finally:
        conn.close()


def has_project_tables(path):
    if not path or not os.path.isfile(path):
        return False
    try:
        conn = sqlite3.connect(path, timeout=10)
        try:
            n = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' "
                "AND name IN ('lv_lines','lv_setups','lv_meta')"
            ).fetchone()[0]
        finally:
            conn.close()
        return n == 3
    except sqlite3.Error:
        return False


def read_attribute_tables(path):
    """Return (lines, meta) rebuilt from the GeoPackage attribute tables."""
    lines, _vrs, meta = read_project_tables(path)
    return lines, meta


def read_project_tables(path):
    """Return (lines, val_results or None, meta) from the GeoPackage tables.

    *meta* is returned raw; pass it through sanitize_meta() before use.
    """
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        line_rows = [dict(r) for r in conn.execute("SELECT * FROM lv_lines")]
        setup_rows = [dict(r) for r in conn.execute("SELECT * FROM lv_setups")]
        meta = {}
        for r in conn.execute("SELECT key, value FROM lv_meta"):
            try:
                meta[r["key"]] = json.loads(r["value"])
            except (TypeError, ValueError):
                meta[r["key"]] = r["value"]
    finally:
        conn.close()
    lines, vrs = rows_to_project(line_rows, setup_rows)
    return lines, vrs, meta


# ---------------------------------------------------------------------------
# lv_meta sanitising (a GeoPackage received from someone else is untrusted)
# ---------------------------------------------------------------------------

CLASSES = ("H1", "H2", "H3", "H4", "H5", "H6")
RECORD_KEYS = ("ot_nekuda", "mispar_nekuda", "name", "gova_ort", "shem_darga_gova",
               "taarih_gova_ort", "ot_nekuda_kfula", "mispar_nekuda_kfula",
               "x", "y", "kfar_aher_name", "objectid", "hearot")
_RECORD_FLOAT = {"gova_ort", "x", "y"}
_RECORD_INT = {"mispar_nekuda", "mispar_nekuda_kfula", "objectid"}


def _finite(v):
    import math
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def sanitize_record(d):
    """Keep only persistent BenchmarkRecord keys with sane types (else None)."""
    if not isinstance(d, dict):
        return None
    out = {}
    for k in RECORD_KEYS:
        v = d.get(k)
        if k in _RECORD_FLOAT:
            v = _finite(v)
        elif k in _RECORD_INT:
            v = v if isinstance(v, int) and not isinstance(v, bool) else None
        elif v is not None and not isinstance(v, str):
            v = None
        out[k] = v
    return out


def _str_map(v):
    if not isinstance(v, dict):
        return {}
    return {k: x for k, x in v.items()
            if isinstance(k, str) and k.strip() and isinstance(x, str) and x.strip()}


def sanitize_meta(meta):
    """Type-check the lv_meta values read from a GeoPackage.

    Returns a dict that always holds every key below with the right type:
    leveling_class/class (H1..H6), point_overrides {str: dict},
    point_origin/pid_remap {str: str}, dat_files [str],
    resolved_points {str: record-dict | None}, output_dir str, schema_version int.
    """
    meta = meta if isinstance(meta, dict) else {}
    out = {}
    cls = meta.get("leveling_class", meta.get("class"))
    out["leveling_class"] = cls if cls in CLASSES else "H3"
    out["class"] = out["leveling_class"]

    overrides = {}
    ov = meta.get("point_overrides")
    if isinstance(ov, dict):
        for k, v in ov.items():
            if not (isinstance(k, str) and k.strip() and isinstance(v, dict)):
                continue
            if v.get("mode") is not None and not isinstance(v.get("mode"), str):
                continue
            v = dict(v)
            if "record" in v:
                rec = sanitize_record(v.get("record"))
                if rec is None:
                    v.pop("record")
                else:
                    v["record"] = rec
            if v.get("name") is not None and not isinstance(v.get("name"), str):
                v.pop("name")
            overrides[k] = v
    out["point_overrides"] = overrides
    out["point_origin"] = _str_map(meta.get("point_origin"))
    out["pid_remap"] = _str_map(meta.get("pid_remap"))

    files = meta.get("dat_files")
    out["dat_files"] = ([f for f in files if isinstance(f, str)]
                        if isinstance(files, list) else [])

    resolved = {}
    rp = meta.get("resolved_points")
    if isinstance(rp, dict):
        for k, v in rp.items():
            if not (isinstance(k, str) and k.strip()):
                continue
            if v is None:
                resolved[k] = None
            else:
                rec = sanitize_record(v)
                if rec is not None:
                    resolved[k] = rec
    out["resolved_points"] = resolved

    od = meta.get("output_dir")
    out["output_dir"] = od if isinstance(od, str) else ""
    sv = meta.get("schema_version")
    out["schema_version"] = sv if isinstance(sv, int) and not isinstance(sv, bool) else 1
    return out


# ---------------------------------------------------------------------------
# QGIS spatial layers
# ---------------------------------------------------------------------------

def merge_layers_to_memory(layers, geometry_kind, order_key=None, dedupe_field=None):
    """Copy features of *layers* into one memory layer.

    geometry_kind : "line" or "point" — only features of that geometry type
                    are copied (the lines GeoJSON also contains points).
    order_key     : optional callable(feature_attrs_dict) -> sort key.
    dedupe_field  : keep only the first feature per value of this field.
    Fields are the union of all source fields (by name).  Geometries are
    transformed into the CRS of the first layer.  Returns None when no
    feature of the requested type exists.
    """
    from qgis.core import (
        QgsVectorLayer, QgsFeature, QgsFields, QgsGeometry, QgsWkbTypes,
        QgsCoordinateTransform, QgsProject,
    )

    want = (QgsWkbTypes.LineGeometry if geometry_kind == "line"
            else QgsWkbTypes.PointGeometry)
    layers = [l for l in layers if l is not None and l.isValid()]
    if not layers:
        return None

    target_crs = layers[0].crs()
    fields = QgsFields()
    for lyr in layers:
        for f in lyr.fields():
            if f.name().lower() == "fid":
                continue
            if fields.indexOf(f.name()) < 0:
                fields.append(f)

    collected, seen, has_z = [], set(), False
    for lyr in layers:
        xform = None
        if lyr.crs().isValid() and target_crs.isValid() and lyr.crs() != target_crs:
            xform = QgsCoordinateTransform(lyr.crs(), target_crs, QgsProject.instance())
        names = lyr.fields().names()
        for feat in lyr.getFeatures():
            g = feat.geometry()
            if g is None or g.isNull() or g.type() != want:
                continue
            attrs = {n: feat[n] for n in names}
            if dedupe_field:
                key = attrs.get(dedupe_field)
                if key in seen:
                    continue
                seen.add(key)
            g = QgsGeometry(g)
            if g.isMultipart():
                g.convertToSingleType()
            if xform is not None:
                g.transform(xform)
            has_z = has_z or QgsWkbTypes.hasZ(g.wkbType())
            collected.append((attrs, g))

    if not collected:
        return None
    if order_key is not None:
        collected.sort(key=lambda item: order_key(item[0]))

    base = "LineString" if geometry_kind == "line" else "Point"
    if has_z:
        base += "Z"
    uri = base + (f"?crs={target_crs.authid()}" if target_crs.authid() else "")
    mem = QgsVectorLayer(uri, "tmp_" + geometry_kind, "memory")
    mem.dataProvider().addAttributes(fields.toList())
    mem.updateFields()

    out = []
    for attrs, g in collected:
        if has_z and not QgsWkbTypes.hasZ(g.wkbType()):
            g.get().addZValue(0.0)
        f = QgsFeature(mem.fields())
        f.setGeometry(g)
        for name, val in attrs.items():
            idx = mem.fields().indexOf(name)
            if idx >= 0:
                f.setAttribute(idx, val)
        out.append(f)
    mem.dataProvider().addFeatures(out)
    mem.updateExtents()
    return mem


def write_vector_layer(layer, path, layer_name, overwrite_file):
    """Write *layer* into the GeoPackage at *path* as *layer_name*."""
    from qgis.core import QgsVectorFileWriter, QgsProject

    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = layer_name
    opts.fileEncoding = "UTF-8"
    opts.actionOnExistingFile = (QgsVectorFileWriter.CreateOrOverwriteFile if overwrite_file
                                 else QgsVectorFileWriter.CreateOrOverwriteLayer)
    ctx = QgsProject.instance().transformContext()
    if hasattr(QgsVectorFileWriter, "writeAsVectorFormatV3"):
        res = QgsVectorFileWriter.writeAsVectorFormatV3(layer, path, ctx, opts)
    else:
        res = QgsVectorFileWriter.writeAsVectorFormatV2(layer, path, ctx, opts)
    err, msg = res[0], (res[1] if len(res) > 1 else "")
    if err != QgsVectorFileWriter.NoError:
        raise IOError(f"GeoPackage write failed for '{layer_name}': {msg}")


def write_project_gpkg(path, lines_layer, points_layer, lines, meta, val_results=None):
    """Write the full project GeoPackage at *path* (overwrites the file).

    Writes to a sibling temp file first and then moves it into place, so a
    failure never leaves a half-written project behind.  Raises
    PermissionError when the target is locked (caller picks another name).
    """
    base, ext = os.path.splitext(path)
    tmp = base + "~tmp" + (ext or ".gpkg")      # keep .gpkg for the GPKG driver

    def _cleanup():
        for p in (tmp, tmp + "-wal", tmp + "-shm"):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    _cleanup()
    try:
        first = True
        if lines_layer is not None:
            write_vector_layer(lines_layer, tmp, LINES_LAYER, overwrite_file=True)
            first = False
        if points_layer is not None:
            write_vector_layer(points_layer, tmp, POINTS_LAYER, overwrite_file=first)
        write_attribute_tables(tmp, lines, meta, val_results)
        os.replace(tmp, path)
    except BaseException:
        _cleanup()
        raise
    return path


def gpkg_layer(path, layer_name, display_name):
    """Open one GeoPackage table as an OGR vector layer (None if absent).

    The default style stored in the GPKG (layer_styles table) is NOT loaded:
    a project file received from someone else could embed layer actions or
    form init code.  The plugin applies its own renderer and labels anyway.
    """
    from qgis.core import QgsVectorLayer
    opts = QgsVectorLayer.LayerOptions()
    opts.loadDefaultStyle = False
    lyr = QgsVectorLayer(f"{path}|layername={layer_name}", display_name, "ogr", opts)
    return lyr if lyr.isValid() else None


def is_network_path(p) -> bool:
    """True for UNC / network / device paths that must never be touched.

    Covers \\\\server\\share, //server, mixed \\/server, the Win32 device
    prefixes \\\\?\\ and \\\\.\\ (incl. \\\\?\\UNC\\...), and the NT object
    prefix \\??\\.  Only a plain drive-letter path (X:...) or a relative path
    is considered local.  Touching a network path from an untrusted project
    file can make Windows send the user's NTLM credentials to a remote host,
    so callers refuse them.
    """
    if not p:
        return False
    # Unify separators first so mixed forms like '\/server' or '/\server'
    # cannot slip past the prefix test.
    s = str(p).strip().replace("/", "\\")
    if s.startswith("\\\\") or s.startswith("\\??\\"):
        return True
    import ntpath
    drive = ntpath.splitdrive(s)[0]
    return bool(drive) and not (len(drive) == 2 and drive[1] == ":" and drive[0].isalpha())
