"""
geolevel_db_manager.py
Centralized PostgreSQL benchmark resolver for Geo Level Gravi.

Single source of truth for fixed-point height and coordinate lookup across:
  - LSA (Network Adjustment)
  - Enhanced LSA
  - Detect Double-Runs
  - Find Loops

Password is NEVER stored in settings.json. Authentication is delegated entirely
to QGIS Authentication Manager (QgsAuthManager) via an authcfg ID.

Coordinate convention (Survey of Israel DB):
  DB column  x  →  Northing  (ITM 2005 Y-axis)
  DB column  y  →  Easting   (ITM 2005 X-axis)
  BenchmarkRecord.x → Easting  (standard GIS convention)
  BenchmarkRecord.y → Northing (standard GIS convention)
"""

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


# ---------------------------------------------------------------------------
# SQL templates
# ---------------------------------------------------------------------------

# Per-point query — 6 name-format variants, all case/space insensitive.
# Coordinates come from the PostGIS geometry column (geom_full, EPSG:2039)
# via ST_X / ST_Y so we always get true ITM 2005 metres regardless of whether
# the raw x/y columns contain WGS84 degrees or ITM metres.
_SQL_TEMPLATE = """
SELECT
    ot_nekuda,
    mispar_nekuda,
    name,
    gova_ort,
    shem_darga_gova,
    taarih_gova_ort,
    ot_nekuda_kfula,
    mispar_nekuda_kfula,
    ST_X(geom_full) AS x,
    ST_Y(geom_full) AS y
FROM {table}
WHERE
    UPPER(TRIM(CAST(mispar_nekuda AS TEXT) || '/' || ot_nekuda)) = UPPER(TRIM(%s))
 OR UPPER(TRIM(CAST(mispar_nekuda AS TEXT) || ot_nekuda))        = UPPER(TRIM(%s))
 OR UPPER(TRIM(ot_nekuda || CAST(mispar_nekuda AS TEXT)))        = UPPER(TRIM(%s))
 OR UPPER(TRIM(name))                                            = UPPER(TRIM(%s))
 OR (ot_nekuda_kfula IS NOT NULL AND mispar_nekuda_kfula IS NOT NULL AND
     UPPER(TRIM(CAST(mispar_nekuda_kfula AS TEXT) || '/' || ot_nekuda_kfula)) = UPPER(TRIM(%s)))
 OR (ot_nekuda_kfula IS NOT NULL AND mispar_nekuda_kfula IS NOT NULL AND
     UPPER(TRIM(CAST(mispar_nekuda_kfula AS TEXT) || ot_nekuda_kfula))        = UPPER(TRIM(%s)))
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

    # ------------------------------------------------------------------ #
    # Configuration
    # ------------------------------------------------------------------ #

    def configure(self, host: str, port: int, dbname: str,
                  user: str, table: str, authcfg: str) -> None:
        """Store connection params and clear session cache."""
        self._params = {
            "host": host,
            "port": int(port),
            "dbname": dbname,
            "user": user,
            "table": table,
            "authcfg": authcfg,
        }
        self.clear_session_cache()
        logger.info("DB manager configured: %s@%s/%s table=%s", user, host, dbname, table)

    def is_configured(self) -> bool:
        """True only when both table name and authcfg are set."""
        return bool(self._params.get("table") and self._params.get("authcfg"))

    def clear_session_cache(self) -> None:
        """Reset all session state. Call on New Project."""
        self._cache.clear()
        self._resolved_coords.clear()
        self._all_dat_point_names = []
        self._global_pool_centroid = None
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
        return re.sub(r'[^A-Z0-9]', '', name.strip().upper())

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

            already_seeded: set = set()  # avoid double-counting same feature

            for feature in layer.getFeatures():
                feat_id = feature.id()
                if feat_id in already_seeded:
                    continue

                matched_key = None

                # --- Strategy A: single-field match (name, point_id, etc.) ---
                for field in candidate_fields_actual:
                    try:
                        raw_val = feature[field]
                    except Exception:
                        continue
                    if raw_val is None:
                        continue
                    norm_val = self._normalise_name(str(raw_val))
                    if norm_val in norm_names:
                        matched_key = str(raw_val).strip().upper()
                        break

                # --- Strategy B: split-column concatenation ---
                # Handles DB layers where benchmark name is stored in two
                # columns: mispar_nekuda (number) and ot_nekuda (letter code).
                # Tries all four canonical join forms:
                #   "3349MPI", "3349/MPI", "MPI3349", "MPI/3349"
                if matched_key is None and has_mispar and has_ot:
                    try:
                        m_val = feature["mispar_nekuda"]
                        o_val = feature["ot_nekuda"]
                        if m_val is not None and o_val is not None:
                            m_str = str(m_val).strip()
                            o_str = str(o_val).strip()
                            for candidate in (
                                m_str + o_str,
                                m_str + "/" + o_str,
                                o_str + m_str,
                                o_str + "/" + m_str,
                            ):
                                if self._normalise_name(candidate) in norm_names:
                                    matched_key = candidate.upper()
                                    break
                    except Exception:
                        pass

                # --- Strategy C: _kfula split-column concatenation ---
                if matched_key is None and has_mispar_k and has_ot_k:
                    try:
                        mk_val = feature["mispar_nekuda_kfula"]
                        ok_val = feature["ot_nekuda_kfula"]
                        if mk_val is not None and ok_val is not None:
                            mk_str = str(mk_val).strip()
                            ok_str = str(ok_val).strip()
                            for candidate in (
                                mk_str + ok_str,
                                mk_str + "/" + ok_str,
                                ok_str + mk_str,
                                ok_str + "/" + mk_str,
                            ):
                                if self._normalise_name(candidate) in norm_names:
                                    matched_key = candidate.upper()
                                    break
                    except Exception:
                        pass

                if matched_key is None:
                    continue

                try:
                    geom = feature.geometry()
                    if geom is None or geom.isEmpty():
                        continue

                    pt = geom.asPoint()
                    easting  = pt.x()   # QGIS canvas x = Easting in EPSG:2039
                    northing = pt.y()   # QGIS canvas y = Northing in EPSG:2039

                    if easting == 0.0 and northing == 0.0:
                        continue

                    self._resolved_coords[matched_key] = (easting, northing)
                    already_seeded.add(feat_id)
                    seeded += 1
                    logger.debug("Seeded '%s' from layer: E=%.1f N=%.1f",
                                 matched_key, easting, northing)

                except Exception as feat_exc:
                    logger.debug("seed_from_qgis_layer: skipping feature %d — %s",
                                 feat_id, feat_exc)

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

        try:
            conn = self._get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    rows = cur.fetchall()
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Global pool batch query failed: %s", exc)
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
    # Connection (password retrieved from QgsAuthManager — never stored)
    # ------------------------------------------------------------------ #

    def _get_connection(self):
        """
        Open a psycopg2 connection.
        Password is fetched from QgsAuthManager at call time; never stored.
        """
        try:
            import psycopg2
        except ImportError as exc:
            raise RuntimeError(
                "psycopg2 is not available. Install it inside OSGeo4W Shell: "
                "pip install psycopg2-binary"
            ) from exc

        password = self._retrieve_password()

        return psycopg2.connect(
            host=self._params["host"],
            port=self._params["port"],
            dbname=self._params["dbname"],
            user=self._params.get("user", ""),
            password=password,
            connect_timeout=5,
        )

    def _retrieve_password(self) -> str:
        """Fetch password from QGIS Authentication Manager using stored authcfg ID."""
        authcfg = self._params.get("authcfg", "")
        if not authcfg:
            return ""
        try:
            from qgis.core import QgsApplication, QgsAuthMethodConfig
            cfg = QgsAuthMethodConfig()
            ok = QgsApplication.authManager().loadAuthenticationConfig(authcfg, cfg, True)
            if ok:
                return cfg.config("password") or ""
            logger.warning("QgsAuthManager: could not load authcfg '%s'", authcfg)
        except Exception as exc:
            logger.warning("QgsAuthManager unavailable (%s) — connecting without password", exc)
        return ""

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

    def get_candidates(self, point_name: str) -> List[BenchmarkRecord]:
        """Return all raw DB candidates for *point_name* without spatial disambiguation.

        Unlike resolve_benchmark(), this method never auto-selects by proximity
        and never writes to the cache.  It is used by the pre-calculation point
        verification dialog so the engineer can choose the correct spatial
        duplicate manually.
        """
        if not self.is_configured():
            return []
        key = point_name.strip().upper()
        return self._query_candidates(key)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _query_candidates(self, point_name: str) -> List[BenchmarkRecord]:
        """Execute the multi-format SQL and return a list of BenchmarkRecord."""
        table = self._params.get("table", "")
        if not table:
            return []

        sql = _SQL_TEMPLATE.format(table=table)
        params = (point_name,) * 6   # same value for all 6 %s placeholders

        try:
            conn = self._get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    rows = cur.fetchall()
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("DB query failed for '%s': %s", point_name, exc)
            return []

        results = []
        for row in rows:
            (ot_nekuda, mispar_nekuda, name,
             gova_ort, shem_darga_gova, taarih_gova_ort,
             ot_nekuda_kfula, mispar_nekuda_kfula,
             db_x, db_y) = row

            # ST_X(geom_full) → Easting  (EPSG:2039 X-axis, ITM)
            # ST_Y(geom_full) → Northing (EPSG:2039 Y-axis, ITM)
            # No column swap needed — PostGIS ST_X/ST_Y respect the CRS axes.
            easting  = float(db_x) if db_x is not None else None
            northing = float(db_y) if db_y is not None else None

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
                x=easting,
                y=northing,
            ))
        return results

    def _resolve_by_proximity(self, candidates: List[BenchmarkRecord],
                               key: str) -> Optional[BenchmarkRecord]:
        """
        Select the candidate nearest to the K-Means project centroid.
        Updates _resolved_coords with the winner's (Easting, Northing).
        """
        spatial = [r for r in candidates if r.x is not None and r.y is not None]
        if not spatial:
            logger.warning(
                "Ambiguous point '%s': none of %d candidates have coordinates — "
                "returning first", key, len(candidates)
            )
            return candidates[0]

        cx, cy = self._get_project_centroid(fallback=spatial)
        winner = min(spatial, key=lambda r: math.sqrt((r.x - cx) ** 2 + (r.y - cy) ** 2))

        if winner.x is not None and winner.y is not None:
            self._resolved_coords[key] = (winner.x, winner.y)

        logger.debug(
            "Proximity winner for '%s': name=%s E=%.1f N=%.1f (centroid E=%.1f N=%.1f)",
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

        try:
            import numpy as np
            from scipy.cluster.vq import whiten, kmeans

            arr = np.array(core, dtype=float)

            # Guard: degenerate axis (all values identical) → whiten divides by 0
            std = arr.std(axis=0)
            if std[0] == 0.0 or std[1] == 0.0:
                cx = float(arr[:, 0].mean())
                cy = float(arr[:, 1].mean())
                logger.info(
                    "k=1 centroid (degenerate axis, plain mean, %d core points): "
                    "E=%.1f  N=%.1f",
                    len(core), cx, cy,
                )
                return cx, cy

            # Step 3: whiten → kmeans k=1 → de-whiten
            whitened = whiten(arr)          # divides each column by its std
            centroid_w, distortion = kmeans(whitened, 1)

            cx = float(centroid_w[0][0] * std[0])
            cy = float(centroid_w[0][1] * std[1])

            logger.info(
                "k=1 centroid (Spatial Median + K-Means k=1, "
                "%d core / %d total candidates, distortion=%.2f): "
                "E=%.1f  N=%.1f",
                len(core), len(coords), float(distortion), cx, cy,
            )
            return cx, cy

        except Exception as exc:
            logger.warning(
                "scipy kmeans k=1 failed (%s) — falling back to plain mean "
                "of %d core points",
                exc, len(core),
            )
            cx = sum(c[0] for c in core) / len(core)
            cy = sum(c[1] for c in core) / len(core)
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
            from core_logic.config.settings_manager import get_settings_manager
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
