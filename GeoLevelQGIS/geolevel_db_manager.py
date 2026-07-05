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

# Per-point query — 6 name-format variants, all case/space insensitive
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
    x,
    y
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

# Batch query — fetches (x, y) for ALL DAT point names in one round-trip.
# Used by the global-pool centroid fallback when layer seeding yields 0 points.
# Each %s is bound to a Python list of uppercase point-name strings via psycopg2.
_SQL_BATCH_COORDS = """
SELECT x, y
FROM {table}
WHERE x IS NOT NULL AND y IS NOT NULL
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
         SQL query for ALL DAT point names and runs K-Means k=2 on the combined
         spatial candidates to find the true project cluster.
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

            already_seeded: set = set()  # avoid double-counting same feature

            for feature in layer.getFeatures():
                feat_id = feature.id()
                if feat_id in already_seeded:
                    continue

                matched_key = None
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

        # Apply coordinate swap: DB x = Northing, DB y = Easting
        coords = []
        for db_x, db_y in rows:
            if db_x is None or db_y is None:
                continue
            easting  = float(db_y)   # DB y → Easting
            northing = float(db_x)   # DB x → Northing
            if easting != 0.0 or northing != 0.0:
                coords.append((easting, northing))

        logger.info("Global pool: got %d spatial candidates from DB", len(coords))

        if not coords:
            logger.warning("Global pool: 0 candidates with coordinates — "
                           "cannot establish project centroid from DB")
            self._global_pool_centroid = (0.0, 0.0)
            return self._global_pool_centroid

        centroid = self._kmeans_centroid(coords)
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

            # *** COORDINATE SWAP ***
            # In the Survey of Israel DB:
            #   column x → Northing (ITM Y-axis)
            #   column y → Easting  (ITM X-axis)
            # BenchmarkRecord.x stores Easting, .y stores Northing (standard GIS).
            easting  = float(db_y) if db_y is not None else None
            northing = float(db_x) if db_x is not None else None

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

        return self._kmeans_centroid(coords)

    def _kmeans_centroid(self, coords: List[Tuple[float, float]]) -> Tuple[float, float]:
        """
        Compute K-Means k=2 centroid on a list of (Easting, Northing) pairs.

        Returns the centroid of the LARGER cluster, discarding the smaller
        outlier group (e.g. rogue same-name duplicates in other regions).

        Falls back to plain mean when:
          - fewer than 3 points (not enough for k=2)
          - scipy is unavailable

        Axes are whitened before clustering so Easting and Northing contribute
        equally to the distance metric.
        """
        if len(coords) < 3:
            return (sum(c[0] for c in coords) / len(coords),
                    sum(c[1] for c in coords) / len(coords))

        try:
            import numpy as np
            from scipy.cluster.vq import kmeans2

            arr = np.array(coords, dtype=float)
            std = arr.std(axis=0)
            std[std == 0] = 1.0      # guard: degenerate axis (all values identical)
            whitened = arr / std

            centroids_w, labels = kmeans2(whitened, 2, iter=20, minit='points')

            count0 = int((labels == 0).sum())
            count1 = int((labels == 1).sum())
            winner_label = 0 if count0 >= count1 else 1

            # De-whiten: multiply back by std to return to original ITM 2005 scale
            cx = float(centroids_w[winner_label][0] * std[0])
            cy = float(centroids_w[winner_label][1] * std[1])

            logger.info(
                "K-Means centroid (k=2, larger cluster=%d, size=%d/%d total=%d): "
                "E=%.1f N=%.1f",
                winner_label, max(count0, count1), min(count0, count1),
                len(coords), cx, cy
            )
            return cx, cy

        except Exception as exc:
            logger.warning("scipy kmeans2 failed (%s) — falling back to plain mean", exc)
            return (sum(c[0] for c in coords) / len(coords),
                    sum(c[1] for c in coords) / len(coords))


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
