"""
geodetic_tool.db.benchmark_db
Centralized PostgreSQL benchmark resolver - standalone port of the QGIS
plugin's geolevel_db_manager.py (GeoLevelGraviQGIS branch).  Same batching,
caching, search, network matching and spatial-median centroid; only the
password source differs (see geodetic_tool.db.credentials).

Single source of truth for fixed-point height and coordinate lookup across:
  - LSA (Network Adjustment)
  - Enhanced LSA
  - Detect Double-Runs
  - Find Loops

Password is NEVER stored in settings.json.  Standalone order: environment
variable GEOLEVEL_DB_PASSWORD, the OS keyring (when the 'keyring' package is
installed and the user chose "remember"), the password typed this session,
then libpq's own pgpass.conf / trust authentication.

Coordinate convention (Survey of Israel DB):
  DB column  x  →  Northing  (ITM 2005 Y-axis)
  DB column  y  →  Easting   (ITM 2005 X-axis)
  BenchmarkRecord.x → Easting  (standard GIS convention)
  BenchmarkRecord.y → Northing (standard GIS convention)
"""

import copy
import math
import re
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# QGIS log bridge — routes INFO/WARNING from this module to the QGIS Log
# Messages panel so engineers can read spatial-median and centroid messages
# without opening a terminal.  Installed lazily on first import inside QGIS;
# silently skipped when the module is used outside QGIS (tests, CLI).
# ---------------------------------------------------------------------------

class _QgsLogHandler(logging.Handler):
    """
    Forwards Python logging records to QgsMessageLog.

    Level mapping:
        WARNING / ERROR / CRITICAL  →  Qgis.Warning
        INFO                        →  Qgis.Info
        DEBUG                       →  silently dropped (too noisy for panel)
    """
    TAG = "Geo Level Gravi"

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from qgis.core import QgsMessageLog, Qgis
            msg = self.format(record)
            if record.levelno >= logging.WARNING:
                level = Qgis.Warning
            else:
                level = Qgis.Info
            QgsMessageLog.logMessage(msg, self.TAG, level)
        except Exception:
            pass  # never crash the plugin just because logging failed


def _install_qgs_log_handler() -> None:
    """Attach the QGIS log handler to this module's logger (idempotent)."""
    if any(isinstance(h, _QgsLogHandler) for h in logger.handlers):
        return
    handler = _QgsLogHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(funcName)s: %(message)s"))
    logger.addHandler(handler)


_install_qgs_log_handler()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkRecord:
    """One benchmark point row returned from the DB."""
    ot_nekuda: str
    mispar_nekuda: int
    name: str
    gova_ort: Optional[float]           # Orthometric height (m) — may be NULL
    shem_darga_gova: Optional[str]      # Height accuracy class label
    taarih_gova_ort: Optional[str]      # Date of height determination (str repr)
    ot_nekuda_kfula: Optional[str]      # Alternate letter code
    mispar_nekuda_kfula: Optional[int]  # Alternate number
    x: Optional[float]                  # Easting  (ITM 2005, EPSG:2039) — standard GIS x
    y: Optional[float]                  # Northing (ITM 2005, EPSG:2039) — standard GIS y
    kfar_aher_name: Optional[str] = None  # City / locality name from kfar_aher table
    objectid: Optional[int] = None        # Stable DB row key (dedup / persistence)
    hearot: Optional[str] = None          # Remarks, e.g. "= 849L" for twin monuments

    # --- Transient scoring annotations (set by score_candidates / twins) ---
    twin_of: Optional[str] = None         # Name of the record this one is a spatial twin of
    twin_dist_m: Optional[float] = None   # Distance (m) to that twin
    dist_to_anchor_m: Optional[float] = None  # Distance to field anchor (m)
    topo_ok: Optional[bool] = None        # Consistent with measured line lengths?
    topo_ratio: Optional[float] = None    # worst DB-distance / measured-length ratio
    topo_ref: Optional[str] = None        # partner point giving that ratio

    def key(self) -> str:
        """Stable identity key for deduplication."""
        if self.objectid is not None:
            return f"oid:{self.objectid}"
        xs = f"{self.x:.3f}" if self.x is not None else "?"
        ys = f"{self.y:.3f}" if self.y is not None else "?"
        return f"{self.name}\x00{xs}\x00{ys}"


# Two DB points closer than this are considered the same physical monument
TWIN_TOLERANCE_M = 1.0
# Topology bound: straight distance between line endpoints must not exceed
# the leveled line length × factor + slack.
TOPO_FACTOR = 1.1
TOPO_SLACK_M = 100.0


def _dist(a_x: float, a_y: float, b_x: float, b_y: float) -> float:
    return math.hypot(a_x - b_x, a_y - b_y)


def group_twins(records: List["BenchmarkRecord"],
                tol_m: float = TWIN_TOLERANCE_M) -> List[List["BenchmarkRecord"]]:
    """Cluster records whose coordinates lie within *tol_m* of each other.

    Single-linkage clustering: two records end up in the same cluster when a
    chain of < tol_m hops connects them.  Records with no coordinates form a
    cluster of their own.  Order of clusters follows the first appearance in
    *records* (so a pre-sorted list keeps its ranking).
    """
    clusters: List[List[BenchmarkRecord]] = []
    for rec in records:
        if rec.x is None or rec.y is None:
            clusters.append([rec])
            continue
        home = None
        for cl in clusters:
            if any(o.x is not None and o.y is not None
                   and _dist(rec.x, rec.y, o.x, o.y) < tol_m for o in cl):
                if home is None:
                    home = cl
                    cl.append(rec)
                else:  # rec bridges two clusters — merge
                    home.extend(cl)
                    cl.clear()
        clusters = [c for c in clusters if c]
        if home is None:
            clusters.append([rec])
    return clusters


def cluster_heights_disagree(cluster: List["BenchmarkRecord"], tol_m: float = 0.001) -> bool:
    """True when records of one < 1 m cluster carry different heights
    (e.g. two DB rows for the same monument with a re-levelled gova_ort)."""
    hs = [r.gova_ort for r in cluster]
    if len(cluster) < 2:
        return False
    if any(h is None for h in hs):
        return not all(h is None for h in hs)
    return max(hs) - min(hs) > tol_m


def cluster_needs_choice(cluster: List["BenchmarkRecord"]) -> bool:
    """A cluster the user must resolve: different names or different heights."""
    return cluster_has_distinct_names(cluster) or cluster_heights_disagree(cluster)


def cluster_has_distinct_names(cluster: List["BenchmarkRecord"]) -> bool:
    """True when a twin cluster holds ≥2 differently named DB points."""
    names = {re.sub(r'[^A-Z0-9א-ת]', '', (r.name or '').upper()) for r in cluster}
    return len(names) > 1


@dataclass
class NetworkResolution:
    """Result of BenchmarkDBManager.resolve_network()."""
    known: Dict[str, Tuple[float, float]]      # pid -> (E, N) located points
    chosen: Dict[str, "BenchmarkRecord"]       # pid -> auto/fixed record
    status: Dict[str, str]                     # pid -> status keyword
    notes: Dict[str, str]                      # pid -> human-readable reason
    iterations: int = 0

    AUTO = ("fixed", "anchor", "matched")

    def unresolved(self, pids: List[str]) -> List[str]:
        return [p for p in pids if self.status.get(p) not in self.AUTO + ("estimate",)]


# ---------------------------------------------------------------------------
# SQL templates
# ---------------------------------------------------------------------------

# Per-point query — 6 name-format variants, all case/space insensitive.
# Coordinates come from the PostGIS geometry column (geom_full, EPSG:2039)
# via ST_X / ST_Y so we always get true ITM 2005 metres regardless of whether
# the raw x/y columns contain WGS84 degrees or ITM metres.
_SQL_COLUMNS = """
    ot_nekuda,
    mispar_nekuda,
    name,
    gova_ort,
    shem_darga_gova,
    taarih_gova_ort,
    ot_nekuda_kfula,
    mispar_nekuda_kfula,
    ST_X(geom_full) AS x,
    ST_Y(geom_full) AS y,
    kfar_aher_name,
    objectid,
    hearot
"""

# Spatial twins — every DB point within %s metres of a given ITM location.
# Catches monuments recorded under two names whose coordinates differ only
# after the decimal point (e.g. 250/A vs 849/L, 0.30 m apart).
_SQL_TWINS = """
SELECT {cols}
FROM {table}
WHERE geom_full IS NOT NULL
  AND ST_DWithin(geom_full, ST_SetSRID(ST_MakePoint(%s, %s), 2039), %s)
"""

_SQL_TEMPLATE = """
SELECT {cols}
FROM {table}
WHERE
    UPPER(TRIM(CAST(mispar_nekuda AS TEXT) || '/' || COALESCE(CAST(ot_nekuda AS TEXT), ''))) = UPPER(TRIM(%s))
 OR UPPER(TRIM(CAST(mispar_nekuda AS TEXT) || COALESCE(CAST(ot_nekuda AS TEXT), '')))        = UPPER(TRIM(%s))
 OR UPPER(TRIM(COALESCE(CAST(ot_nekuda AS TEXT), '') || CAST(mispar_nekuda AS TEXT)))        = UPPER(TRIM(%s))
 OR UPPER(TRIM(name))                                                                        = UPPER(TRIM(%s))
 OR (mispar_nekuda_kfula IS NOT NULL AND
     UPPER(TRIM(CAST(mispar_nekuda_kfula AS TEXT) || '/' || COALESCE(CAST(ot_nekuda_kfula AS TEXT), ''))) = UPPER(TRIM(%s)))
 OR (mispar_nekuda_kfula IS NOT NULL AND
     UPPER(TRIM(CAST(mispar_nekuda_kfula AS TEXT) || COALESCE(CAST(ot_nekuda_kfula AS TEXT), '')))        = UPPER(TRIM(%s)))
"""

# Batch name lookup: ONE scan of the table for any number of names.  The six
# match keys are returned too, so each row is mapped back to the exact search
# name(s) it matched (same six forms as _SQL_TEMPLATE).
_SQL_KEY_EXPRS = (
    "UPPER(TRIM(CAST(mispar_nekuda AS TEXT) || '/' || COALESCE(CAST(ot_nekuda AS TEXT), '')))",
    "UPPER(TRIM(CAST(mispar_nekuda AS TEXT) || COALESCE(CAST(ot_nekuda AS TEXT), '')))",
    "UPPER(TRIM(COALESCE(CAST(ot_nekuda AS TEXT), '') || CAST(mispar_nekuda AS TEXT)))",
    "UPPER(TRIM(name))",
    "CASE WHEN mispar_nekuda_kfula IS NULL THEN NULL ELSE UPPER(TRIM(CAST(mispar_nekuda_kfula AS TEXT) || '/' || COALESCE(CAST(ot_nekuda_kfula AS TEXT), ''))) END",
    "CASE WHEN mispar_nekuda_kfula IS NULL THEN NULL ELSE UPPER(TRIM(CAST(mispar_nekuda_kfula AS TEXT) || COALESCE(CAST(ot_nekuda_kfula AS TEXT), ''))) END",
)
_SQL_BATCH_NAMES = (
    "SELECT * FROM (SELECT {cols}, "
    + ", ".join(f"{e} AS k{i}" for i, e in enumerate(_SQL_KEY_EXPRS))
    + " FROM {table}) q WHERE "
    + " OR ".join(f"k{i} = ANY(%s)" for i in range(len(_SQL_KEY_EXPRS)))
)

# Spatial twins of many locations in one query (row index i -> its twins).
_SQL_TWINS_BATCH = """
SELECT q.i, {cols}
FROM unnest(%s::float8[], %s::float8[]) WITH ORDINALITY AS q(qx, qy, i)
JOIN {table} t
  ON t.geom_full IS NOT NULL
 AND ST_DWithin(t.geom_full, ST_SetSRID(ST_MakePoint(q.qx, q.qy), 2039), %s)
"""

# Batch query — fetches ITM 2005 coordinates for ALL DAT point names in one
# round-trip. Uses ST_X/ST_Y(geom_full) so coordinates are always EPSG:2039
# metres regardless of the raw x/y column values in the table.
_SQL_BATCH_COORDS = """
SELECT ST_X(geom_full) AS x, ST_Y(geom_full) AS y
FROM {table}
WHERE geom_full IS NOT NULL
  AND (
    UPPER(TRIM(CAST(mispar_nekuda AS TEXT) || '/' || ot_nekuda)) = ANY(%s)
 OR UPPER(TRIM(CAST(mispar_nekuda AS TEXT) || ot_nekuda))        = ANY(%s)
 OR UPPER(TRIM(ot_nekuda || CAST(mispar_nekuda AS TEXT)))        = ANY(%s)
 OR UPPER(TRIM(name))                                            = ANY(%s)
  )
"""

# Name of the QGIS control-points layer used to seed the project centroid
_SEED_LAYER_NAME = "נקודות בקרה"


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

_NON_ALNUM = re.compile(r"[^A-Z0-9]")


_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


class BenchmarkDBManager:
    """
    Manages PostgreSQL connectivity and point resolution with session caching
    and spatial disambiguation.

    Centroid strategy (in priority order):
      1. seed_from_qgis_layer() pre-populates _resolved_coords from the verified
         map canvas geometry before any DB query fires.
      2. If seeding yields 0 points, _build_global_pool_centroid() fires ONE batch
         SQL query for ALL DAT point names, applies Spatial Median outlier rejection
         (inner-75% percentile), then runs K-Means k=1 on the filtered core points
         to find the true project centroid ('center of area' method).
      3. If the global pool also fails (no DB coords), fall back to the candidates
         of the single ambiguous point being resolved (last resort).

    Thread-safety: intended for single-threaded QGIS main thread use only.
    """

    def __init__(self):
        self._params: Dict[str, Any] = {}
        self._cache: Dict[str, Optional[BenchmarkRecord]] = {}
        # Maps resolved point names → (Easting, Northing) for centroid calculation
        self._resolved_coords: Dict[str, Tuple[float, float]] = {}
        # Stored by seed_from_qgis_layer for use by the global pool fallback
        self._all_dat_point_names: List[str] = []
        # Cached result of _build_global_pool_centroid (built at most once per session)
        self._global_pool_centroid: Optional[Tuple[float, float]] = None
        # Field anchor established from unambiguous measured points
        self._anchor: Optional[Tuple[float, float]] = None
        # Cache of spatial-twin lookups keyed by record key
        self._twin_cache: Dict[str, List[BenchmarkRecord]] = {}
        # Raw DB rows per search name (the registry does not change during a
        # session) - every name is queried at most once.
        self._query_cache: Dict[str, List[BenchmarkRecord]] = {}
        # One reused connection (opening one costs a TCP + auth round trip)
        self._conn = None
        # Index of the control-points layer: (layer id, feature count) -> {norm name: (E, N)}
        self._layer_index_key = None
        self._layer_index = None   # (feats, inverted) of the control-points layer

    # ------------------------------------------------------------------ #
    # Configuration
    # ------------------------------------------------------------------ #

    def configure(self, host: str, port: int, dbname: str,
                  user: str, table: str, authcfg: str) -> None:
        """Store connection params and clear session cache.

        *table* is placed into SQL text, so it must be a plain [schema.]table
        identifier (e.g. ``public.cp_table``); anything else raises ValueError."""
        table = (table or "").strip()
        if table and not _TABLE_RE.match(table):
            raise ValueError(f"Invalid table name {table!r}: use [schema.]table "
                             "(letters, digits, underscore)")
        self._params = {
            "host": host,
            "port": int(port),
            "dbname": dbname,
            "user": user,
            "table": table,
            "authcfg": authcfg,
        }
        self.clear_session_cache()
        logger.info("DB manager configured: %s@%s/%s table=%s",
                    user, host, dbname, table)

    @property
    def anchor(self) -> Optional[Tuple[float, float]]:
        return self._anchor

    def is_configured(self) -> bool:
        """True when host, database and table are set.  (The QGIS plugin also
        requires a QGIS authcfg; standalone the password comes from
        geodetic_tool.db.credentials or libpq, so none is needed.)"""
        p = self._params
        return bool(p.get("host") and p.get("dbname") and p.get("table"))

    def clear_session_cache(self) -> None:
        """Reset all session state. Call on New Project."""
        self._cache.clear()
        self._resolved_coords.clear()
        self._all_dat_point_names = []
        self._global_pool_centroid = None
        self._anchor = None
        self._twin_cache.clear()
        self._query_cache.clear()
        self._layer_index_key = None
        self._layer_index = None
        self.close_connection()
        logger.debug("DB session cache cleared")

    # ------------------------------------------------------------------ #
    # Name normalisation helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalise_name(name: str) -> str:
        """Strip ALL non-alphanumeric characters and uppercase.

        Handles every known separator variant:
          '3349/MPI' → '3349MPI'
          'MPI-3349' → 'MPI3349'
          '3349 MPI' → '3349MPI'
          '3349_MPI' → '3349MPI'
          '3349.MPI' → '3349MPI'
        """
        return _NON_ALNUM.sub('', name.strip().upper())

    @staticmethod
    def _pick_name_fields(field_names: List[str]) -> List[str]:
        """Return ALL layer fields likely to contain a point name/ID, in priority order.

        Returns a list so the seeder can try every candidate and union the matches,
        rather than committing to a single field that might be wrong.
        """
        preferred = [f for f in ("name", "point_id", "id") if f in field_names]
        extras = [f for f in field_names
                  if ("name" in f or "id" in f) and f not in preferred]
        return preferred + extras

    # ------------------------------------------------------------------ #
    # Layer seeding — must be called before any resolve_benchmark() call
    # ------------------------------------------------------------------ #

    def _index_seed_layer(self, layer, fields, split, split_k):
        """Scan the control-points layer once.

        Attributes only (no geometry): returns (feats, inverted, coords) where
        feats[i] = (ordered [(norm, key)], feature id), inverted maps a
        normalised name to feature indices, and coords caches the (E, N) of
        features fetched later by id (None = no usable geometry)."""
        feats, inverted = [], {}
        norm = self._normalise_name
        try:
            from qgis.core import QgsFeatureRequest
            req = QgsFeatureRequest()
            wanted = list(fields)
            if split:
                wanted += ["mispar_nekuda", "ot_nekuda"]
            if split_k:
                wanted += ["mispar_nekuda_kfula", "ot_nekuda_kfula"]
            try:
                req.setSubsetOfAttributes(wanted, layer.fields())
                req.setFlags(QgsFeatureRequest.NoGeometry)
            except Exception:
                pass
            it = layer.getFeatures(req)
        except Exception:
            it = layer.getFeatures()

        def _joined(feature, a, b):
            try:
                m_val, o_val = feature[a], feature[b]
            except Exception:
                return []
            if m_val is None or o_val is None:
                return []
            m_str, o_str = str(m_val).strip(), str(o_val).strip()
            # "m/o" and "o/m" normalise like "mo" / "om", which come first
            # and therefore always win: two forms are enough.
            return [m_str + o_str, o_str + m_str]

        for feature in it:
            forms = []
            for field in fields:
                try:
                    raw_val = feature[field]
                except Exception:
                    continue
                if raw_val is None:
                    continue
                forms.append((norm(str(raw_val)), str(raw_val).strip().upper()))
            if split:
                forms += [(norm(c), c.upper())
                          for c in _joined(feature, "mispar_nekuda", "ot_nekuda")]
            if split_k:
                forms += [(norm(c), c.upper())
                          for c in _joined(feature, "mispar_nekuda_kfula", "ot_nekuda_kfula")]
            if not forms:
                continue
            fi = len(feats)
            feats.append((forms, feature.id()))
            for nn in {f[0] for f in forms}:
                inverted.setdefault(nn, []).append(fi)
        logger.info("seed_from_qgis_layer: indexed %d control points", len(feats))
        return feats, inverted, {}

    @staticmethod
    def _seed_coords(layer, fids, cache) -> None:
        """Fill *cache* {fid: (E, N) | None} for *fids* with one request."""
        todo = [f for f in fids if f not in cache]
        if not todo:
            return
        try:
            from qgis.core import QgsFeatureRequest
            it = layer.getFeatures(QgsFeatureRequest().setFilterFids(todo))
        except Exception:
            want = set(todo)
            it = (f for f in layer.getFeatures() if f.id() in want)
        for feature in it:
            xy = None
            try:
                geom = feature.geometry()
                if geom is not None and not geom.isEmpty():
                    pt = geom.asPoint()
                    xy = (pt.x(), pt.y())            # EPSG:2039 canvas E / N
                    if xy == (0.0, 0.0):
                        xy = None
            except Exception:
                xy = None
            cache[feature.id()] = xy
        for f in todo:
            cache.setdefault(f, None)

    def seed_from_qgis_layer(self, point_names: List[str]) -> int:
        """
        Pre-populate _resolved_coords from the 'נקודות בקרה' QGIS layer.

        Stores all provided point names for use by the global-pool fallback.
        Iterates all name-like fields in the layer features; for every feature
        whose value normalise-matches any DAT point name, stores its canvas
        (Easting, Northing) into _resolved_coords.

        Emits a WARNING if 0 points are seeded, listing the exact names searched
        and fields tried, so the mismatch can be debugged from the QGIS log.

        Args:
            point_names: All unique start_point / end_point values from loaded lines.

        Returns:
            Number of points successfully seeded.
        """
        # Always store for global-pool fallback, even if seeding fails
        self._all_dat_point_names = [n for n in point_names if n]

        seeded = 0
        norm_names = {self._normalise_name(n) for n in self._all_dat_point_names}
        logger.debug("seed_from_qgis_layer: %d unique DAT names, norm_names sample=%s",
                     len(norm_names), sorted(norm_names)[:10])

        try:
            from qgis.core import QgsProject

            # Find the control-points layer by exact name
            layer = None
            for lyr in QgsProject.instance().mapLayers().values():
                if lyr.name() == _SEED_LAYER_NAME:
                    layer = lyr
                    break

            if layer is None:
                logger.warning(
                    "seed_from_qgis_layer: layer '%s' NOT FOUND in project. "
                    "Centroid will fall back to global DB pool K-Means.",
                    _SEED_LAYER_NAME
                )
                return 0

            # Collect ALL candidate name fields (not just the first one)
            raw_field_names = [f.name() for f in layer.fields()]
            lower_field_names = [f.lower() for f in raw_field_names]
            candidate_fields_lower = self._pick_name_fields(lower_field_names)

            if not candidate_fields_lower:
                logger.warning(
                    "seed_from_qgis_layer: no name/id fields found in layer '%s'. "
                    "Available fields: %s",
                    _SEED_LAYER_NAME, raw_field_names
                )
                return 0

            # Map lowercase field names back to actual (case-preserved) field names
            lower_to_actual = dict(zip(lower_field_names, raw_field_names))
            candidate_fields_actual = [lower_to_actual[f] for f in candidate_fields_lower
                                       if f in lower_to_actual]

            logger.info(
                "seed_from_qgis_layer: layer '%s' found (%d features). "
                "Trying fields: %s",
                _SEED_LAYER_NAME, layer.featureCount(), candidate_fields_actual
            )

            # Detect split-column fields present in this layer
            all_raw_lower = set(lower_field_names)
            has_mispar    = "mispar_nekuda"        in all_raw_lower
            has_ot        = "ot_nekuda"            in all_raw_lower
            has_mispar_k  = "mispar_nekuda_kfula"  in all_raw_lower
            has_ot_k      = "ot_nekuda_kfula"      in all_raw_lower

            if has_mispar and has_ot:
                logger.info(
                    "seed_from_qgis_layer: split-column fields detected "
                    "(mispar_nekuda + ot_nekuda) — will concatenate for matching"
                )

            # One scan per layer state, then dictionary lookups: loading
            # calls this up to three times and the layer holds every control
            # point of the country.  Same matching order as before: per
            # feature, single fields first, then the split-column forms.
            index_key = (layer.id(), layer.featureCount(),
                         tuple(candidate_fields_actual))
            if self._layer_index_key != index_key or self._layer_index is None:
                self._layer_index = self._index_seed_layer(
                    layer, candidate_fields_actual,
                    has_mispar and has_ot, has_mispar_k and has_ot_k)
                self._layer_index_key = index_key
            feats, inverted, coords = self._layer_index

            hit_feats = set()
            for nn in norm_names:
                hit_feats.update(inverted.get(nn, ()))
            hits = sorted(hit_feats)
            self._seed_coords(layer, [feats[fi][1] for fi in hits], coords)
            for fi in hits:
                forms, fid = feats[fi]
                xy = coords.get(fid)
                if xy is None:
                    continue
                matched_key = next((key for norm, key in forms if norm in norm_names), None)
                if matched_key is None:
                    continue
                easting, northing = xy
                self._resolved_coords[matched_key] = (easting, northing)
                seeded += 1
                logger.debug("Seeded '%s' from layer: E=%.1f N=%.1f",
                             matched_key, easting, northing)

        except Exception as exc:
            logger.warning("seed_from_qgis_layer failed with exception: %s", exc)

        if seeded > 0:
            logger.info("Seeded %d point(s) from '%s' into DB centroid",
                        seeded, _SEED_LAYER_NAME)
        else:
            logger.warning(
                "seed_from_qgis_layer: 0 points seeded from '%s'. "
                "norm_names searched (first 20): %s. "
                "Falling back to global DB pool centroid.",
                _SEED_LAYER_NAME, sorted(norm_names)[:20]
            )

        return seeded

    # ------------------------------------------------------------------ #
    # Global pool centroid — single batch query for all DAT points
    # ------------------------------------------------------------------ #

    def _build_global_pool_centroid(self) -> Tuple[float, float]:
        """
        Build a robust project centroid from ALL DAT point names using one
        batch SQL query, then apply K-Means k=2 to discard rogue duplicates.

        Result is cached — at most one DB round-trip per session.
        """
        if self._global_pool_centroid is not None:
            return self._global_pool_centroid

        table = self._params.get("table", "")
        if not table or not self._all_dat_point_names:
            self._global_pool_centroid = (0.0, 0.0)
            return self._global_pool_centroid

        # Build the list of all normalised DAT names for the ANY(%s) binding
        norm_keys = list({n.strip().upper() for n in self._all_dat_point_names if n})

        logger.info(
            "Global pool: batch query for %d unique DAT names in table '%s'",
            len(norm_keys), table
        )

        sql = _SQL_BATCH_COORDS.format(table=table)
        # All four %s in the batch query get the same list
        params = (norm_keys, norm_keys, norm_keys, norm_keys)

        rows = self._fetch(sql, params, what="global pool")
        if not rows:
            self._global_pool_centroid = (0.0, 0.0)
            return self._global_pool_centroid

        # ST_X(geom_full) → Easting, ST_Y(geom_full) → Northing (EPSG:2039)
        coords = []
        for db_x, db_y in rows:
            if db_x is None or db_y is None:
                continue
            easting  = float(db_x)   # ST_X → Easting
            northing = float(db_y)   # ST_Y → Northing
            if easting != 0.0 or northing != 0.0:
                coords.append((easting, northing))

        logger.info("Global pool: got %d spatial candidates from DB", len(coords))

        if not coords:
            logger.warning("Global pool: 0 candidates with coordinates — "
                           "cannot establish project centroid from DB")
            self._global_pool_centroid = (0.0, 0.0)
            return self._global_pool_centroid

        centroid = self._kmeans1_centroid(coords)
        self._global_pool_centroid = centroid
        return centroid

    # ------------------------------------------------------------------ #
    # Connection (password from geodetic_tool.db.credentials — never stored)
    # ------------------------------------------------------------------ #

    def close_connection(self) -> None:
        conn, self._conn = getattr(self, "_conn", None), None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _shared_connection(self):
        """The session's reused connection (opened on first use)."""
        conn = getattr(self, "_conn", None)
        if conn is None or getattr(conn, "closed", 1):
            conn = self._get_connection()
            try:
                conn.autocommit = True      # read-only lookups: no open transaction
            except Exception:
                pass
            self._conn = conn
        return conn

    def _get_connection(self):
        """
        Open a psycopg2 connection.
        Password comes from geodetic_tool.db.credentials at call time; never stored.
        """
        try:
            import psycopg2
        except ImportError as exc:
            raise RuntimeError(
                "psycopg2 is not installed. Install it with: "
                "pip install psycopg2-binary"
            ) from exc

        password = self._retrieve_password()
        kwargs = dict(
            host=self._params["host"],
            port=self._params["port"],
            dbname=self._params["dbname"],
            user=self._params.get("user", "") or None,
            connect_timeout=5,
        )
        if password:
            kwargs["password"] = password      # else libpq: pgpass.conf / trust
        return psycopg2.connect(**kwargs)

    def _retrieve_password(self) -> str:
        """Password for the configured user (never read from settings.json)."""
        from .credentials import get_password
        return get_password(self._params) or ""

    def test_connection(self) -> Tuple[bool, str]:
        """
        Attempt a connection and immediately close it.

        Returns:
            (True, success_message) or (False, error_message)
        """
        if not self._params.get("host"):
            return False, "No host configured."
        try:
            conn = self._get_connection()
            conn.close()
            msg = "Connected to '{}' on {}:{}.".format(
                self._params.get("dbname", ""),
                self._params.get("host", ""),
                self._params.get("port", 5432),
            )
            logger.info("Test connection: %s", msg)
            return True, msg
        except Exception as exc:
            logger.warning("Test connection failed: %s", exc)
            return False, str(exc)

    # ------------------------------------------------------------------ #
    # Benchmark resolution — public API
    # ------------------------------------------------------------------ #

    def resolve_benchmark(self, point_name: str) -> Optional[BenchmarkRecord]:
        """
        Look up a benchmark by name in the national registry.

        Handles:
          - Session-level caching (re-queries only once per point per project)
          - Multi-format name matching (slash, no-slash, reversed, double-name)
          - Spatial disambiguation when multiple records share the same name

        Returns:
            BenchmarkRecord if found, None otherwise.
            Never raises — all errors are logged and None is returned.
        """
        if not self.is_configured():
            return None

        key = point_name.strip().upper()

        if key in self._cache:
            return self._cache[key]

        candidates = self._query_candidates(key)

        if not candidates:
            logger.debug("DB lookup: 0 candidates for '%s'", key)
            self._cache[key] = None
            return None

        if len(candidates) == 1:
            rec = candidates[0]
            if rec.x is not None and rec.y is not None:
                self._resolved_coords[key] = (rec.x, rec.y)
            self._cache[key] = rec
            return rec

        # Multiple candidates — resolve by spatial proximity to project centroid
        logger.info("Resolving '%s' by proximity: %d candidates found", key, len(candidates))
        rec = self._resolve_by_proximity(candidates, key)
        self._cache[key] = rec
        return rec

    def get_candidates(self, point_name: str,
                       include_twins: bool = True) -> List[BenchmarkRecord]:
        """Return all DB candidates for *point_name* across ASCII and Hebrew variants.

        Expands the raw field ID with PointNormalizer.generate_search_candidates(),
        queries every variant, deduplicates by DB identity and (optionally)
        appends near-coincident spatial twins (< TWIN_TOLERANCE_M).

        Unlike resolve_benchmark(), this method never auto-selects by proximity
        and never writes to the cache.  It is used by the point verification /
        Point Settings dialogs so the engineer can choose the correct match.
        """
        if not self.is_configured():
            return []
        return self.get_candidates_batch([point_name], include_twins)[point_name]

    def get_candidates_batch(self, point_names: List[str],
                             include_twins: bool = True) -> Dict[str, List[BenchmarkRecord]]:
        """
        Fetch all DB candidates for multiple point names.

        Each input name is expanded into ASCII/Hebrew variants; every unique
        variant is queried once.  Records are mapped back to an input name by
        *which search variant returned them* (not by comparing rec.name), so
        matches found through the _kfula alternate-name columns are kept.

        When *include_twins* is True, every hit is checked for other DB points
        within TWIN_TOLERANCE_M (same monument, different name, coordinates
        differing only after the decimal point).  Twins are appended and tagged
        via ``twin_of`` / ``twin_dist_m`` so the dialog can force a choice.

        Returned records are per-pid copies, so scoring annotations written by
        score_candidates() for one pid never leak into another.

        Returns:
            Dict mapping each input point_name -> List[BenchmarkRecord] (may be empty).
        """
        result: Dict[str, List[BenchmarkRecord]] = {pn: [] for pn in point_names}
        if not self.is_configured() or not point_names:
            return result

        try:
            from ..engine.point_normalizer import PointNormalizer
        except Exception:
            PointNormalizer = None

        name_to_search_names: Dict[str, List[str]] = {}
        for pn in point_names:
            variants: List[str] = []
            if PointNormalizer:
                try:
                    variants = list(PointNormalizer.generate_search_candidates(pn))
                except Exception:
                    variants = []
            variants = [v.strip().upper() for v in variants if v and v.strip()]
            base = pn.strip().upper()
            if base not in variants:
                variants.insert(0, base)
            name_to_search_names[pn] = variants

        all_unique = sorted({v for vs in name_to_search_names.values() for v in vs})
        logger.debug("Batch query: %d unique search names from %d input points",
                     len(all_unique), len(point_names))

        self.prefetch(all_unique)                 # one table scan for all variants
        hits_by_search: Dict[str, List[BenchmarkRecord]] = {
            sn: self._query_candidates(sn) for sn in all_unique
        }

        if include_twins:              # twins of every hit, one query for all
            self.prefetch_twins([r for vs in hits_by_search.values() for r in vs])
        for pn in point_names:
            seen: set = set()
            matches: List[BenchmarkRecord] = []
            for sv in name_to_search_names[pn]:
                for rec in hits_by_search.get(sv, []):
                    k = rec.key()
                    if k not in seen:
                        seen.add(k)
                        matches.append(copy.copy(rec))

            if include_twins:
                for rec in list(matches):
                    for tw in self.get_spatial_twins(rec):
                        k = tw.key()
                        if k not in seen:
                            seen.add(k)
                            matches.append(copy.copy(tw))
                self._annotate_twins(matches)

            result[pn] = matches

        return result

    def search(self, query: str, limit: int = 60) -> List[BenchmarkRecord]:
        """Free-text DB search for the Point Verification / Point Settings
        dialogs: exact name forms first (ASCII / Hebrew variants), then any
        point whose name (with or without '/') or locality contains *query*.
        Returns at most *limit* records (duplicates removed)."""
        q = (query or "").strip().upper()
        if not q or not self.is_configured():
            return []
        out, seen = [], set()
        digits = re.sub(r"\s", "", q)       # "1/17" is NOT number-only
        for rec in self.get_candidates(q, include_twins=False):
            # number-only query: "1" + "17" glued also reads "117" - skip it
            if digits.isdigit() and str(rec.mispar_nekuda or "").strip() != digits:
                continue
            if rec.key() not in seen:
                seen.add(rec.key())
                out.append(rec)
        table = self._params.get("table", "")
        if table and len(out) < limit:
            clean = q.replace("%", "").replace("_", "")
            compact = re.sub(r"[\s/\-.]", "", clean)
            k_slash, k_flat, k_rev, k_name = _SQL_KEY_EXPRS[:4]
            num = "UPPER(TRIM(CAST(mispar_nekuda AS TEXT)))"
            # A number-only query ("117") is a point NUMBER: match the number
            # column (117 -> 117/A, 1117/B), never the glued "number+letters"
            # form, where "1/17" would also read "117".  Queries with letters
            # ("117MSA", "117/MSA", "MSA117") also match the glued forms.
            conds, params = [f"{k_slash} LIKE %s", f"{k_name} LIKE %s",
                             f"UPPER(COALESCE(kfar_aher_name, '')) LIKE %s"], \
                            [f"%{clean}%", f"%{clean}%", f"%{clean}%"]
            if re.sub(r"\s", "", clean).isdigit():
                # number-only: the exact number, then numbers STARTING with it
                # ("117" -> 117/A, 1170/B; never 6177, which only contains it)
                conds = [f"{num} LIKE %s",
                         f"UPPER(COALESCE(kfar_aher_name, '')) LIKE %s"]
                params = [f"{compact}%", f"%{clean}%"]
                rank = f"CASE WHEN {num} = %s THEN 0 WHEN {num} LIKE %s THEN 1 ELSE 3 END"
                rparams = [compact, f"{compact}%"]
            else:
                conds += [f"{k_flat} LIKE %s", f"{k_rev} LIKE %s"]
                params += [f"%{compact}%", f"%{compact}%"]
                rank = (f"CASE WHEN {k_flat} = %s OR {k_rev} = %s OR {k_slash} = %s THEN 0 "
                        f"WHEN {k_flat} LIKE %s OR {k_slash} LIKE %s OR {k_rev} LIKE %s THEN 1 "
                        f"WHEN {k_flat} LIKE %s OR {k_slash} LIKE %s OR {k_rev} LIKE %s THEN 2 "
                        f"ELSE 3 END")
                rparams = [compact, compact, clean,
                           f"{compact}%", f"{clean}%", f"{compact}%",
                           f"%{compact}%", f"%{clean}%", f"%{compact}%"]
            # exact number first, then starts-with, contains, locality-only;
            # shorter names first ("117/A" before "1117/A")
            sql = (f"SELECT {_SQL_COLUMNS} FROM {table} WHERE {' OR '.join(conds)} "
                   f"ORDER BY {rank}, length(name), name LIMIT %s")
            rows = self._fetch(sql, tuple(params + rparams + [int(limit)]),
                               what=f"search '{q}'")
            for rec in self._rows_to_records(rows):
                if rec.key() not in seen and len(out) < limit:
                    seen.add(rec.key())
                    out.append(rec)
        return out

    def prefetch_twins(self, records, tol_m: float = TWIN_TOLERANCE_M) -> None:
        """Spatial twins of many records in one query (fills _twin_cache)."""
        todo, seen = [], set()
        for r in records:
            if r.x is None or r.y is None:
                continue
            ck = f"{r.key()}\x00{tol_m}"
            if ck in self._twin_cache or ck in seen:
                continue
            seen.add(ck)
            todo.append((ck, r))
        table = self._params.get("table", "")
        if not todo or not table:
            return
        sql = _SQL_TWINS_BATCH.format(cols=_SQL_COLUMNS, table=table)
        rows = self._fetch(sql, ([float(r.x) for _, r in todo], [float(r.y) for _, r in todo],
                                 float(tol_m)), what=f"twins of {len(todo)} points")
        if not rows and self._conn is None:
            return                       # query failed: leave for per-record lookup
        by_i: Dict[int, List[BenchmarkRecord]] = {i: [] for i in range(1, len(todo) + 1)}
        for row in rows:
            by_i.setdefault(int(row[0]), []).extend(self._rows_to_records([row[1:]]))
        for i, (ck, r) in enumerate(todo, 1):
            own = r.key()
            self._twin_cache[ck] = [t for t in by_i.get(i, []) if t.key() != own]

    def get_spatial_twins(self, rec: BenchmarkRecord,
                          tol_m: float = TWIN_TOLERANCE_M) -> List[BenchmarkRecord]:
        """Return other DB points lying within *tol_m* metres of *rec*.

        Uses PostGIS ST_DWithin on geom_full (EPSG:2039 metres).  A distance
        test is used rather than truncating coordinates to whole metres, which
        would miss pairs straddling an integer boundary (e.g. .98 vs 1.02).
        """
        if rec.x is None or rec.y is None:
            return []
        ck = f"{rec.key()}\x00{tol_m}"
        if ck in self._twin_cache:
            return self._twin_cache[ck]

        table = self._params.get("table", "")
        if not table:
            return []
        sql = _SQL_TWINS.format(cols=_SQL_COLUMNS, table=table)
        rows = self._fetch(sql, (float(rec.x), float(rec.y), float(tol_m)),
                           what=f"twins of '{rec.name}'")
        own = rec.key()
        twins = [r for r in self._rows_to_records(rows) if r.key() != own]
        if twins:
            logger.info("Spatial twins of '%s' (< %.2f m): %s", rec.name, tol_m,
                        ", ".join(f"{t.name} ({_dist(rec.x, rec.y, t.x, t.y):.2f} m)"
                                  for t in twins if t.x is not None))
        self._twin_cache[ck] = twins
        return twins

    @staticmethod
    def _annotate_twins(records: List[BenchmarkRecord]) -> None:
        """Tag records that share a < 1 m cluster with a differently named point."""
        for cl in group_twins([r for r in records if r.x is not None and r.y is not None]):
            if not cluster_has_distinct_names(cl):
                continue
            for r in cl:
                others = [o for o in cl if o is not r and o.name != r.name]
                if not others:
                    continue
                nearest = min(others, key=lambda o: _dist(r.x, r.y, o.x, o.y))
                r.twin_of = nearest.name
                r.twin_dist_m = _dist(r.x, r.y, nearest.x, nearest.y)

    # ------------------------------------------------------------------ #
    # Field anchor + "normal radius" scoring
    # ------------------------------------------------------------------ #

    @staticmethod
    def _cluster_center(cluster: List[BenchmarkRecord]) -> Tuple[float, float]:
        return (sum(r.x for r in cluster) / len(cluster),
                sum(r.y for r in cluster) / len(cluster))

    def _unique_location(self, recs: List[BenchmarkRecord]) -> Optional[Tuple[float, float]]:
        """Location of a point whose candidates all sit in ONE < 1 m cluster."""
        spatial = [r for r in recs if r.x is not None and r.y is not None]
        if not spatial:
            return None
        clusters = group_twins(spatial)
        if len(clusters) != 1:
            return None
        return self._cluster_center(clusters[0])

    def accept_record(self, point_name: str, rec: Optional[BenchmarkRecord]) -> None:
        """Pin *rec* as the resolution of *point_name* for this session.

        Writes the cache (so resolve_benchmark() never re-picks) and the
        resolved-coords pool used for the anchor.  ``rec=None`` pins the point
        as 'not in DB' so downstream code estimates it topologically.
        """
        key = point_name.strip().upper()
        self._cache[key] = rec
        if rec is not None and rec.x is not None and rec.y is not None:
            self._resolved_coords[key] = (rec.x, rec.y)
        else:
            self._resolved_coords.pop(key, None)

    def session_snapshot(self, point_names) -> Dict[str, Optional[BenchmarkRecord]]:
        """Return {name: cached record or None} for every name already resolved
        this session.  Names never looked up are omitted (no DB query is made).
        Used to persist the exact point picks into the project GeoPackage."""
        out: Dict[str, Optional[BenchmarkRecord]] = {}
        for name in point_names or []:
            if not isinstance(name, str) or not name.strip():
                continue
            key = name.strip().upper()
            if key in self._cache:
                out[name] = self._cache[key]
        return out

    def forget(self, point_name: str) -> None:
        """Drop any unconfirmed auto-pick for *point_name* (cache + anchor pool)."""
        key = point_name.strip().upper()
        self._cache.pop(key, None)
        norm = self._normalise_name(point_name)
        # Layer seeding stores coords under the raw layer value (e.g. '250/A'
        # for field id '250A') — drop every key that normalises to the same id.
        for k in [k for k in self._resolved_coords if self._normalise_name(k) == norm]:
            self._resolved_coords.pop(k, None)

    def establish_anchor(self, batch: Dict[str, List[BenchmarkRecord]],
                         extra_coords: Optional[List[Tuple[float, float]]] = None,
                         include_resolved: bool = True,
                         ) -> Optional[Tuple[float, float]]:
        """Establish the field anchor (project area centre) from measurements.

        Sources, merged then robust-centred with the Spatial Median + k=1 pipeline:
          * points already resolved this session / seeded from the map layer;
          * every measured point whose DB candidates collapse to ONE location.
        Fallback when nothing is unambiguous: robust centre of all candidates.

        The anchor is stored on the manager and used by score_candidates()
        and _resolve_by_proximity().
        """
        coords: List[Tuple[float, float]] = (
            list(self._resolved_coords.values()) if include_resolved else []
        )
        coords.extend(extra_coords or [])
        for recs in batch.values():
            loc = self._unique_location(recs)
            if loc is not None:
                coords.append(loc)

        source = "unambiguous points"
        if not coords:
            coords = [(r.x, r.y) for recs in batch.values() for r in recs
                      if r.x is not None and r.y is not None]
            source = "all candidates (no unambiguous point)"

        if not coords:
            self._anchor = None
            logger.warning("establish_anchor: no coordinates available - anchor unavailable")
            return None

        self._anchor = self._kmeans1_centroid(coords)
        logger.info("Field anchor from %s (%d coords): E=%.1f N=%.1f",
                    source, len(coords), self._anchor[0], self._anchor[1])
        return self._anchor

    def score_candidates(self, pid: str, recs: List[BenchmarkRecord],
                         lines: list,
                         batch: Optional[Dict[str, List[BenchmarkRecord]]] = None,
                         known: Optional[Dict[str, Tuple[float, float]]] = None,
                         ) -> List[BenchmarkRecord]:
        """Annotate and rank the candidates of *pid*.

        Sets on every record:
          dist_to_anchor_m - distance to the field anchor (None if no anchor)
          topo_ok          - for every measured line pid<->partner whose partner
                             location is known, the straight distance from the
                             candidate to the partner is <= length*1.1 + 100 m.
                             None when no line can be checked.

        Returns the records sorted: topology-consistent first, then nearest to the anchor.
        """
        batch = batch or {}
        partners: List[Tuple[Tuple[float, float], float, str]] = []
        for ln in lines or []:
            s, e = getattr(ln, "start_point", None), getattr(ln, "end_point", None)
            if s == pid and e and e != pid:
                other = e
            elif e == pid and s and s != pid:
                other = s
            else:
                continue
            length = getattr(ln, "total_distance", None)
            if not length or length <= 0:
                continue
            loc = None
            if known is not None:
                # Network resolution already decided which partners are known.
                loc = known.get(other)
            elif other in batch:
                # Partner is being resolved/edited too: only trust it when its
                # candidates collapse to one location (never its current pick).
                loc = self._unique_location(batch[other])
            else:
                cached = self._cache.get(other.strip().upper())
                if cached is not None and cached.x is not None and cached.y is not None:
                    loc = (cached.x, cached.y)
            if loc is not None:
                partners.append((loc, float(length), other))

        anchor = self._anchor
        for r in recs:
            if r.x is None or r.y is None:
                r.dist_to_anchor_m = None
                r.topo_ok = None
                r.topo_ratio = None
                r.topo_ref = None
                continue
            if anchor is not None:
                r.dist_to_anchor_m = _dist(r.x, r.y, anchor[0], anchor[1])
            else:
                r.dist_to_anchor_m = None
            if partners:
                r.topo_ok = all(
                    _dist(r.x, r.y, px, py) <= length * TOPO_FACTOR + TOPO_SLACK_M
                    for (px, py), length, _n in partners
                )
                ratio, ref = max(
                    ((_dist(r.x, r.y, px, py) / length, n) for (px, py), length, n in partners),
                    key=lambda t: t[0],
                )
                r.topo_ratio, r.topo_ref = ratio, ref
            else:
                r.topo_ok = None
                r.topo_ratio = None
                r.topo_ref = None

        def _rank(r: BenchmarkRecord):
            return (
                r.topo_ok is False,
                r.x is None,
                r.dist_to_anchor_m if r.dist_to_anchor_m is not None else float("inf"),
            )

        return sorted(recs, key=_rank)

    # ------------------------------------------------------------------ #
    # Network resolution — match DB distances against measured distances
    # ------------------------------------------------------------------ #

    @staticmethod
    def _drop_far_anchors(seeds, known, edges, cands, status, notes) -> None:
        import heapq
        import statistics
        pts = [(r.x, r.y) for recs in cands.values() for r in recs
               if r.x is not None and r.y is not None]
        if not pts:
            return
        cx = statistics.median(x for x, _ in pts)
        cy = statistics.median(y for _, y in pts)

        def path_lengths(src):
            dist = {src: 0.0}
            heap = [(0.0, src)]
            while heap:
                d0, n = heapq.heappop(heap)
                if d0 > dist.get(n, float("inf")):
                    continue
                for nb, L in edges.get(n, {}).items():
                    nd = d0 + L
                    if nd < dist.get(nb, float("inf")):
                        dist[nb] = nd
                        heapq.heappush(heap, (nd, nb))
            return dist

        while True:
            located = dict(known)
            located.update(seeds)
            bad = {}
            for p, loc in seeds.items():
                for q, d in path_lengths(p).items():
                    if q == p or q not in located or q in edges.get(p, {}):
                        continue
                    o = located[q]
                    if _dist(loc[0], loc[1], o[0], o[1]) > d * TOPO_FACTOR + TOPO_SLACK_M:
                        bad.setdefault(p, []).append(q)
            if not bad:
                return
            # drop the contradicting anchor farthest from the consensus
            worst = max(bad, key=lambda p: (_dist(seeds[p][0], seeds[p][1], cx, cy),
                                            len(bad[p])))
            seeds.pop(worst)
            status[worst] = "conflict"
            notes[worst] = ("The only DB location contradicts the measured path to "
                            + ", ".join(bad[worst][:3])
                            + " - wrong namesake or point missing in DB")

    @staticmethod
    def build_edges(lines: list, exclude=()) -> Dict[str, Dict[str, float]]:
        """Measured connections between line endpoints: pid -> {neighbour: length_m}.

        The leveled length of a line is never shorter than the straight
        distance between its endpoints, so it bounds where the neighbour can
        be.  When several lines join the same pair the shortest length (the
        tightest bound) is kept.
        """
        edges: Dict[str, Dict[str, float]] = {}
        for ln in lines or []:
            s, e = getattr(ln, "start_point", None), getattr(ln, "end_point", None)
            length = getattr(ln, "total_distance", None)
            if not s or not e or s == e or not length or length <= 0:
                continue
            if s in exclude or e in exclude:
                continue
            for a, b in ((s, e), (e, s)):
                nb = edges.setdefault(a, {})
                nb[b] = min(nb.get(b, float("inf")), float(length))
        return edges

    @staticmethod
    def _fits(loc: Tuple[float, float],
              constraints: List[Tuple[Tuple[float, float], float, str]]) -> bool:
        return all(_dist(loc[0], loc[1], p[0], p[1]) <= L * TOPO_FACTOR + TOPO_SLACK_M
                   for p, L, _n in constraints)

    def resolve_network(self, pids: List[str], lines: list,
                        batch: Dict[str, List[BenchmarkRecord]],
                        fixed: Optional[Dict[str, Optional[BenchmarkRecord]]] = None,
                        exclude=()) -> "NetworkResolution":
        """Resolve duplicated names by matching DB distances to measured distances.

        1. Known locations: user/saved choices (*fixed*), already-loaded points
           in the session cache, and **anchors**: points whose DB candidates
           collapse to one location (no duplicate elsewhere).
        2. Anchors are cross-checked against each other through the measured
           lines; an anchor that contradicts its measured neighbours is dropped
           (status 'conflict').
        3. Propagation, repeated until no more progress: for every open point
           linked by a measured line to a known point, keep the candidate
           locations whose straight distance to every known neighbour is
           <= leveled length x 1.1 + 100 m.  Exactly one surviving location ->
           matched, and it becomes a known point for the next ones.

        Statuses: fixed, estimate, anchor, matched, identity (location known but
        twin/duplicate records need a human choice), ambiguous, conflict,
        no_link (no measured line to a known point yet), not_in_db, no_coords.
        """
        fixed = fixed or {}
        exclude = set(exclude or ())
        edges = self.build_edges(lines, exclude)
        known: Dict[str, Tuple[float, float]] = {}
        chosen: Dict[str, BenchmarkRecord] = {}
        status: Dict[str, str] = {}
        notes: Dict[str, str] = {}
        cands: Dict[str, List[BenchmarkRecord]] = {}

        for pid, rec in fixed.items():
            if rec is not None and rec.x is not None and rec.y is not None:
                known[pid] = (rec.x, rec.y)
                chosen[pid] = rec
                status[pid] = "fixed"
            else:
                status[pid] = "estimate"

        # Points loaded from earlier files (not part of this resolution)
        pid_set = set(pids)
        for nb in edges:
            if nb in pid_set or nb in fixed:
                continue
            rec = self._cache.get(nb.strip().upper())
            if rec is not None and rec.x is not None and rec.y is not None:
                known[nb] = (rec.x, rec.y)

        def _clusters(pid):
            return group_twins([r for r in cands[pid]])

        seeds: Dict[str, Tuple[float, float]] = {}
        for pid in pids:
            if pid in status or pid in exclude:
                continue
            recs = list(batch.get(pid, []))
            if not recs:
                status[pid] = "not_in_db"
                notes[pid] = "Not found in the DB - enter the point name, or keep PKT estimation"
                continue
            spatial = [r for r in recs if r.x is not None and r.y is not None]
            cands[pid] = spatial
            if not spatial:
                status[pid] = "no_coords"
                notes[pid] = "DB candidates have no coordinates - choose manually"
                continue
            if len(spatial) == len(recs):
                loc = self._unique_location(spatial)
                if loc is not None:
                    seeds[pid] = loc

        # --- cross-check anchors against each other and known points -------
        while True:
            drop = []
            for p, loc in seeds.items():
                good = bad = 0
                bad_nb = []
                for nb, L in edges.get(p, {}).items():
                    other = seeds.get(nb) or known.get(nb)
                    if other is None:
                        continue
                    if _dist(loc[0], loc[1], other[0], other[1]) <= L * TOPO_FACTOR + TOPO_SLACK_M:
                        good += 1
                    else:
                        bad += 1
                        bad_nb.append(nb)
                if bad and bad >= good:
                    drop.append((p, bad_nb))
            if not drop:
                break
            for p, bad_nb in drop:
                seeds.pop(p, None)
                status[p] = "conflict"
                notes[p] = ("The only DB location contradicts the measured distance to "
                            + ", ".join(bad_nb) + " - wrong namesake or point missing in DB")

        # --- anchors not linked by a direct line: compare along the measured
        # path (no radius).  A far namesake that is the only DB location of a
        # point is caught here; of two contradicting anchors the one farther
        # from the consensus of all DB candidates is dropped.
        self._drop_far_anchors(seeds, known, edges, cands, status, notes)

        for p, loc in seeds.items():
            known[p] = loc
            cl = _clusters(p)[0]
            if cluster_needs_choice(cl):
                status[p] = "identity"
                notes[p] = "Twin DB points at the same location - choose which name/height"
            else:
                chosen[p] = cl[0]
                status[p] = "anchor"
                notes[p] = "Anchor: unique DB location"

        # --- propagate along measured lines --------------------------------
        open_pids = [p for p in pids if p in cands and p not in known
                     and status.get(p) not in ("conflict",)]
        iterations = 0
        while open_pids:
            iterations += 1
            progress = False
            for pid in list(open_pids):
                cons = [(known[nb], L, nb) for nb, L in edges.get(pid, {}).items()
                        if nb in known]
                if not cons:
                    status[pid] = "no_link"
                    notes[pid] = "No measured line to a known point yet"
                    continue
                fit = [cl for cl in _clusters(pid) if self._fits(self._cluster_center(cl), cons)]
                via = ", ".join(sorted({n for _p, _L, n in cons}))
                if len(fit) == 1:
                    known[pid] = self._cluster_center(fit[0])
                    open_pids.remove(pid)
                    progress = True
                    has_coordless = len(cands[pid]) != len(batch.get(pid, []))
                    if cluster_needs_choice(fit[0]) or has_coordless:
                        status[pid] = "identity"
                        notes[pid] = ("Located by measured distances to %s; twin/duplicate "
                                      "records there - choose which one" % via)
                    else:
                        chosen[pid] = fit[0][0]
                        status[pid] = "matched"
                        notes[pid] = "Matched by measured distances to " + via
                elif not fit:
                    status[pid] = "conflict"
                    notes[pid] = ("No DB candidate fits the measured distance to " + via
                                  + " - point may be missing from the DB")
                else:
                    status[pid] = "ambiguous"
                    notes[pid] = ("%d DB locations fit the measured distances to %s"
                                  % (len(fit), via))
            if not progress or iterations > len(pids) + 1:
                break

        n_auto = sum(1 for p in pids if status.get(p) in ("anchor", "matched"))
        logger.info("Network resolution: %d/%d points resolved automatically in %d "
                    "iteration(s); statuses=%s", n_auto, len(pids), iterations,
                    {p: status.get(p) for p in pids})
        return NetworkResolution(known=known, chosen=chosen, status=status,
                                 notes=notes, iterations=iterations)

    @staticmethod
    def pick_unambiguous(recs: List[BenchmarkRecord]) -> Optional[BenchmarkRecord]:
        """Return the one acceptable candidate, or None if the user must choose.

        Acceptable = has coordinates, not topology-inconsistent.
        Exactly ONE < 1 m cluster may remain, and it must not mix different
        names (a 250/A + 849/L twin always goes to the user) nor different
        heights (duplicate rows of one monument).  Candidates without
        coordinates also send the point to the user.
        """
        # Any candidate without coordinates cannot be ruled out spatially.
        if any(r.x is None or r.y is None for r in recs):
            return None
        ok = [r for r in recs if r.topo_ok is not False]
        if not ok:
            return None
        clusters = group_twins(ok)
        if len(clusters) != 1 or cluster_needs_choice(clusters[0]):
            return None
        return clusters[0][0]

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _fetch(self, sql: str, params: tuple, what: str = "") -> list:
        """Run *sql* on the shared connection and return all rows; [] on any
        error (logged).  A dropped connection is reopened once."""
        for attempt in (1, 2):
            try:
                conn = self._shared_connection()
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchall()
            except Exception as exc:
                broken = getattr(getattr(self, "_conn", None), "closed", 1)
                self.close_connection()
                if attempt == 1 and broken:
                    continue            # server closed an idle connection: retry
                logger.warning("DB query failed for %s: %s", what, exc)
                return []
        return []

    @staticmethod
    def _rows_to_records(rows: list) -> List[BenchmarkRecord]:
        results = []
        for row in rows:
            (ot_nekuda, mispar_nekuda, name,
             gova_ort, shem_darga_gova, taarih_gova_ort,
             ot_nekuda_kfula, mispar_nekuda_kfula,
             db_x, db_y, kfar_aher_name, objectid, hearot) = row

            # ST_X(geom_full) -> Easting, ST_Y(geom_full) -> Northing (EPSG:2039)
            results.append(BenchmarkRecord(
                ot_nekuda=ot_nekuda or "",
                mispar_nekuda=int(mispar_nekuda) if mispar_nekuda is not None else 0,
                name=name or "",
                gova_ort=float(gova_ort) if gova_ort is not None else None,
                shem_darga_gova=shem_darga_gova,
                taarih_gova_ort=str(taarih_gova_ort) if taarih_gova_ort else None,
                ot_nekuda_kfula=ot_nekuda_kfula,
                mispar_nekuda_kfula=(int(mispar_nekuda_kfula)
                                     if mispar_nekuda_kfula is not None else None),
                x=float(db_x) if db_x is not None else None,
                y=float(db_y) if db_y is not None else None,
                kfar_aher_name=kfar_aher_name or None,
                objectid=int(objectid) if objectid is not None else None,
                hearot=str(hearot) if hearot else None,
            ))
        return results

    def _query_candidates(self, point_name: str) -> List[BenchmarkRecord]:
        """DB records matching *point_name* in any of the six name forms
        (cached for the session; uncached names go through prefetch)."""
        key = (point_name or "").strip().upper()
        if key not in self._query_cache:
            self.prefetch([key])
        return [copy.copy(r) for r in self._query_cache.get(key, [])]

    def prefetch(self, point_names, chunk: int = 500) -> None:
        """Look up every not-yet-cached name in ONE table scan per *chunk*
        names (instead of one query - and one connection - per name)."""
        table = self._params.get("table", "")
        keys = sorted({(n or "").strip().upper() for n in point_names
                       if n and (n or "").strip()} - set(self._query_cache))
        if not table or not keys:
            return
        ncols = len(_SQL_COLUMNS.strip().split(","))
        sql = _SQL_BATCH_NAMES.format(cols=_SQL_COLUMNS, table=table)
        for start in range(0, len(keys), chunk):
            part = keys[start:start + chunk]
            want = set(part)
            rows = self._fetch(sql, (part,) * len(_SQL_KEY_EXPRS),
                               what=f"{len(part)} names")
            found: Dict[str, List[BenchmarkRecord]] = {k: [] for k in part}
            for row in rows:
                rec = self._rows_to_records([row[:ncols]])[0]
                for k in {v for v in row[ncols:] if v in want}:
                    found[k].append(rec)
            self._query_cache.update(found)
        logger.debug("prefetch: %d name(s) looked up", len(keys))

    def _resolve_by_proximity(self, candidates: List[BenchmarkRecord],
                               key: str) -> Optional[BenchmarkRecord]:
        """
        Select the candidate nearest to the field anchor (or project centroid).
        Never returns None for a non-empty candidate list.
        Updates _resolved_coords with the winner's (Easting, Northing).
        """
        spatial = [r for r in candidates if r.x is not None and r.y is not None]
        if not spatial:
            logger.warning(
                "Ambiguous point '%s': none of %d candidates have coordinates - "
                "returning first", key, len(candidates)
            )
            return candidates[0]

        if self._anchor is not None:
            cx, cy = self._anchor
        else:
            cx, cy = self._get_project_centroid(fallback=spatial)

        def d(r):
            return _dist(r.x, r.y, cx, cy)

        winner = min(spatial, key=d)

        self._resolved_coords[key] = (winner.x, winner.y)
        logger.debug(
            "Proximity winner for '%s': name=%s E=%.1f N=%.1f (anchor E=%.1f N=%.1f)",
            key, winner.name, winner.x, winner.y, cx, cy
        )
        return winner

    def _get_project_centroid(self, fallback: List[BenchmarkRecord]) -> Tuple[float, float]:
        """
        Compute the project centroid, using the best available source:

        Priority:
          1. _resolved_coords (from layer seeding or prior unambiguous lookups)
             → K-Means k=2 if ≥3 points, else plain mean
          2. Global pool centroid (one batch DB query for all DAT point names)
             → K-Means k=2 on ALL spatial candidates, discards rogue cluster
          3. Candidates of the current single ambiguous point (last resort)
        """
        coords = list(self._resolved_coords.values())

        if not coords:
            # Layer seeding failed — try global pool (single batch DB query)
            if self._all_dat_point_names:
                cx, cy = self._build_global_pool_centroid()
                if cx != 0.0 or cy != 0.0:
                    return cx, cy
            # Last resort: use only the candidates of this single ambiguous point
            valid = [(r.x, r.y) for r in fallback if r.x is not None and r.y is not None]
            coords = valid if valid else []

        if not coords:
            return 0.0, 0.0

        return self._kmeans1_centroid(coords)

    def _spatial_median_filter(
        self, coords: List[Tuple[float, float]]
    ) -> List[Tuple[float, float]]:
        """
        Step 2 of the Spatial Median + k=1 pipeline.

        Rejects outliers by:
          1. Computing the spatial median (numpy median on each axis independently —
             immune to extreme spatial outliers).
          2. Computing the Euclidean distance of every coordinate to that median.
          3. Keeping only points whose distance is <= the 75th-percentile distance
             (inner 75 % of the distribution). This isolates the dense 'center area'
             and discards rogue same-name duplicates from other cities/regions.

        Returns the filtered list. If filtering would leave fewer than 2 points, the
        original list is returned unchanged (degenerate dataset — cannot be trimmed).
        """
        import numpy as np

        if len(coords) < 4:
            logger.info(
                "Spatial median filter: only %d point(s) — skipping outlier rejection",
                len(coords),
            )
            return coords

        arr = np.array(coords, dtype=float)

        # Spatial median: median on each axis independently
        med_e = float(np.median(arr[:, 0]))
        med_n = float(np.median(arr[:, 1]))
        logger.info(
            "Spatial median: E=%.1f  N=%.1f  (computed from %d candidates)",
            med_e, med_n, len(coords),
        )

        # Euclidean distances to the median
        dists = np.sqrt((arr[:, 0] - med_e) ** 2 + (arr[:, 1] - med_n) ** 2)

        # 75th-percentile cutoff — keeps the inner dense cluster
        p75 = float(np.percentile(dists, 75))
        logger.info(
            "Distance distribution: min=%.1f  median=%.1f  p75=%.1f  max=%.1f",
            float(dists.min()), float(np.median(dists)), p75, float(dists.max()),
        )

        mask = dists <= p75
        filtered = arr[mask].tolist()
        n_dropped = int((~mask).sum())

        if len(filtered) < 2:
            logger.warning(
                "Spatial median filter: would retain only %d point(s) after "
                "p75 cut — returning all %d points unfiltered",
                len(filtered), len(coords),
            )
            return coords

        logger.info(
            "Spatial median filter: retained %d / %d points "
            "(dropped %d outlier(s) beyond p75=%.1f m)",
            len(filtered), len(coords), n_dropped, p75,
        )
        return [(float(r[0]), float(r[1])) for r in filtered]

    def _kmeans1_centroid(self, coords: List[Tuple[float, float]]) -> Tuple[float, float]:
        """
        Steps 2–3 of the Spatial Median + k=1 pipeline.

        1. Calls _spatial_median_filter() to reject outliers (inner-75% retention).
        2. Whitens the filtered coordinates with scipy.cluster.vq.whiten so Easting
           and Northing contribute equally to the distance metric.
        3. Runs scipy.cluster.vq.kmeans(whitened, 1) — strictly k=1 as specified.
        4. De-whitens to restore true ITM 2005 scale.

        Falls back to plain mean when:
          - fewer than 2 points after filtering
          - scipy is unavailable

        Returns:
            (Easting, Northing) of the k=1 centroid in ITM 2005 metres.
        """
        if not coords:
            return 0.0, 0.0

        if len(coords) == 1:
            return coords[0]

        # Step 2: outlier rejection via spatial median filter
        core = self._spatial_median_filter(coords)

        # Step 3: k-means with ONE cluster converges to the arithmetic mean
        # (whitening is undone by the de-whitening), so the mean is computed
        # directly - same result, without importing scipy (~0.3 s on first
        # use inside QGIS).
        cx = sum(c[0] for c in core) / len(core)
        cy = sum(c[1] for c in core) / len(core)
        logger.info("k=1 centroid (Spatial Median + mean, %d core / %d total "
                    "candidates): E=%.1f  N=%.1f", len(core), len(coords), cx, cy)
        return cx, cy


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_instance: Optional[BenchmarkDBManager] = None


def get_db_manager() -> BenchmarkDBManager:
    """
    Return the global BenchmarkDBManager instance.
    On first call, auto-loads connection params from SettingsManager.
    """
    global _instance
    if _instance is None:
        _instance = BenchmarkDBManager()
        try:
            from ..config.settings_manager import get_settings_manager
            params = get_settings_manager().get_db_connection()
            if params:
                _instance.configure(
                    host=params.get("host", ""),
                    port=int(params.get("port", 5432)),
                    dbname=params.get("dbname", ""),
                    user=params.get("user", ""),
                    table=params.get("table", ""),
                    authcfg=params.get("authcfg", ""),
                )
        except Exception as exc:
            logger.warning("Could not auto-load DB params from settings: %s", exc)
    return _instance
