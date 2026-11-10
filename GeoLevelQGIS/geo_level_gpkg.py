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

SCHEMA_VERSION = 1
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


def lines_to_rows(lines):
    """LevelingLine list -> (line_rows, setup_rows) as lists of dicts."""
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
    from core_logic.config.models import LevelingLine, StationSetup, LineStatus

    by_line = {}
    for r in setup_rows:
        by_line.setdefault(r["line_idx"], []).append(r)

    lines = []
    for r in sorted(line_rows, key=lambda x: x["line_idx"]):
        setups = []
        for s in sorted(by_line.get(r["line_idx"], []),
                        key=lambda x: x["setup_number"]):
            setups.append(StationSetup(
                setup_number=s["setup_number"],
                from_point=s["from_point"],
                to_point=s["to_point"],
                backsight_reading=s["backsight_reading"],
                foresight_reading=s["foresight_reading"],
                distance_back=s["distance_back"],
                distance_fore=s["distance_fore"],
                temperature=s["temperature"],
                height_diff=s["height_diff"],
                cumulative_height=s["cumulative_height"],
                is_used=bool(s["is_used"]),
            ))
        date = r["date"]
        if date:
            try:
                date = datetime.fromisoformat(date)
            except ValueError:
                pass
        try:
            status = LineStatus(r["status"])
        except ValueError:
            status = LineStatus.VALID
        try:
            errors = json.loads(r["validation_errors"] or "[]")
        except ValueError:
            errors = []
        ln = LevelingLine(
            filename=r["filename"],
            start_point=r["start_point"],
            end_point=r["end_point"],
            setups=setups,
            method=r["method"] or "BF",
            date=date or None,
            instrument_id=r["instrument_id"],
            total_distance=r["total_distance"] or 0.0,
            total_height_diff=r["total_height_diff"] or 0.0,
            misclosure=r["misclosure"],
            status=status,
            validation_errors=errors,
            is_used=bool(r["is_used"]),
            original_direction=r["original_direction"] or "BF",
            manager_override=bool(r["manager_override"]),
        )
        ln.source_path = r["source_path"]
        lines.append(ln)
    return lines


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


def write_attribute_tables(path, lines, meta):
    """(Re)write lv_lines / lv_setups / lv_meta inside the GeoPackage."""
    line_rows, setup_rows = lines_to_rows(lines)
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
    return rows_to_lines(line_rows, setup_rows), meta


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


def write_project_gpkg(path, lines_layer, points_layer, lines, meta):
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
        write_attribute_tables(tmp, lines, meta)
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
    """True for UNC / network paths (\\\\server\\share, //server, \\\\?\\UNC\\...).

    Touching such a path from an untrusted project file can make Windows send
    the user's NTLM credentials to a remote host, so callers refuse them.
    """
    if not p:
        return False
    # Unify separators first so mixed forms like '\/server' or '/\server'
    # cannot slip past the prefix test.
    s = str(p).strip().replace("/", "\\")
    if s.startswith("\\\\"):
        return True
    import ntpath
    drive = ntpath.splitdrive(s)[0]
    return bool(drive) and not (len(drive) == 2 and drive[1] == ":" and drive[0].isalpha())
