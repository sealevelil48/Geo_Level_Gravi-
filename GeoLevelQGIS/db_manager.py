"""
db_manager.py
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
# SQL template — 6 name-format variants, all case/space insensitive
# ---------------------------------------------------------------------------

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

# Name of the QGIS control-points layer used to seed the project centroid
_SEED_LAYER_NAME = "נקודות בקרה"


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class BenchmarkDBManager:
    """
    Manages PostgreSQL connectivity and point resolution with session caching
    and spatial disambiguation.

    Centroid strategy:
      1. seed_from_qgis_layer() pre-populates _resolved_coords from the map canvas
         before any DB query fires.
      2. _get_project_centroid() uses scipy K-Means k=2 on those coordinates:
         splits into two clusters, returns the centroid of the larger (project)
         cluster, discarding rogue far-away duplicates.
      3. _resolve_by_proximity() picks the DB candidate nearest that centroid.

    Thread-safety: intended for single-threaded QGIS main thread use only.
    """

    def __init__(self):
        self._params: Dict[str, Any] = {}
        self._cache: Dict[str, Optional[BenchmarkRecord]] = {}
        # Maps resolved point names → (Easting, Northing) for centroid calculation
        self._resolved_coords: Dict[str, Tuple[float, float]] = {}

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
        """Reset lookup cache and accumulated project coordinates. Call on New Project."""
        self._cache.clear()
        self._resolved_coords.clear()
        logger.debug("DB session cache cleared")

    # ------------------------------------------------------------------ #
    # Layer seeding — must be called before any resolve_benchmark() call
    # ------------------------------------------------------------------ #

    def seed_from_qgis_layer(self, point_names: List[str]) -> int:
        """
        Pre-populate _resolved_coords from the 'נקודות בקרה' QGIS layer.

        Iterates features in the control-points layer; for every feature whose
        name/ID attribute matches one of the supplied point names, stores its
        (Easting, Northing) geometry in _resolved_coords.

        This establishes the project centroid from on-screen, visually-verified
        geometry BEFORE any DB query fires, preventing false centroids.

        Args:
            point_names: All unique start_point / end_point values from loaded lines.

        Returns:
            Number of points successfully seeded.
        """
        seeded = 0
        try:
            from qgis.core import QgsProject

            # Build a normalised lookup set from caller's point names
            norm_names = {self._normalise_name(n) for n in point_names if n}

            layer = None
            for lyr in QgsProject.instance().mapLayers().values():
                if lyr.name() == _SEED_LAYER_NAME:
                    layer = lyr
                    break

            if layer is None:
                logger.debug("seed_from_qgis_layer: layer '%s' not found in project",
                             _SEED_LAYER_NAME)
                return 0

            # Identify which attribute field to match point names against
            field_names = [f.name().lower() for f in layer.fields()]
            match_field = self._pick_name_field(field_names)
            if match_field is None:
                logger.warning("seed_from_qgis_layer: no name/id field found in '%s'",
                               _SEED_LAYER_NAME)
                return 0

            actual_field = layer.fields()[field_names.index(match_field)].name()

            for feature in layer.getFeatures():
                try:
                    raw_val = feature[actual_field]
                    if raw_val is None:
                        continue
                    norm_val = self._normalise_name(str(raw_val))
                    if norm_val not in norm_names:
                        continue

                    geom = feature.geometry()
                    if geom is None or geom.isEmpty():
                        continue

                    pt = geom.asPoint()
                    easting  = pt.x()   # QGIS canvas x = Easting in EPSG:2039
                    northing = pt.y()   # QGIS canvas y = Northing in EPSG:2039

                    if easting == 0.0 and northing == 0.0:
                        continue

                    # Store under the original (non-normalised) key as upper-stripped
                    key = str(raw_val).strip().upper()
                    self._resolved_coords[key] = (easting, northing)
                    seeded += 1
                    logger.debug("Seeded '%s' from layer: E=%.1f N=%.1f", key, easting, northing)

                except Exception as feat_exc:
                    logger.debug("seed_from_qgis_layer: skipping feature — %s", feat_exc)
                    continue

        except Exception as exc:
            logger.warning("seed_from_qgis_layer failed: %s", exc)

        if seeded:
            logger.info("Seeded %d point(s) from '%s' into DB centroid", seeded, _SEED_LAYER_NAME)
        return seeded

    @staticmethod
    def _normalise_name(name: str) -> str:
        """Uppercase, strip whitespace and slashes for loose name matching."""
        return name.strip().upper().replace("/", "").replace("-", "").replace(" ", "")

    @staticmethod
    def _pick_name_field(field_names: List[str]) -> Optional[str]:
        """Return the best field name to use for point-name matching."""
        for preferred in ("name", "point_id", "id"):
            if preferred in field_names:
                return preferred
        for fn in field_names:
            if "name" in fn or "id" in fn:
                return fn
        return None

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
        logger.info(
            "Resolving '%s' by proximity: %d candidates found", key, len(candidates)
        )
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
        Compute the project centroid using K-Means k=2 on all seeded coordinates.

        Strategy:
          - If fewer than 3 known points: plain mean (not enough data for clustering).
          - If 3 or more: run scipy kmeans2 with k=2, return centroid of the LARGER
            cluster. This discards the smaller outlier group (e.g. a rogue duplicate
            in a different region of Israel that accidentally entered _resolved_coords).
          - Falls back to plain mean if scipy is unavailable.

        Coordinates are whitened (divided by per-axis std-dev) before clustering so
        Easting and Northing axes contribute equally to the distance metric.
        """
        coords = list(self._resolved_coords.values())  # [(Easting, Northing), ...]

        if not coords:
            # No layer seeding occurred — bootstrap from candidate set itself
            valid = [(r.x, r.y) for r in fallback if r.x is not None and r.y is not None]
            coords = valid if valid else []

        if not coords:
            return 0.0, 0.0

        if len(coords) < 3:
            # Too few points for k=2 clustering — use plain mean
            return (sum(c[0] for c in coords) / len(coords),
                    sum(c[1] for c in coords) / len(coords))

        # ── K-Means k=2: split into two clusters, pick the larger one ──────────
        try:
            import numpy as np
            from scipy.cluster.vq import kmeans2

            arr = np.array(coords, dtype=float)

            # Whiten: normalise each axis by its std-dev so neither E nor N
            # dominates the distance metric
            std = arr.std(axis=0)
            std[std == 0] = 1.0      # guard against degenerate (all same value)
            whitened = arr / std

            centroids_w, labels = kmeans2(whitened, 2, iter=20, minit='points')

            # Identify the larger cluster
            count0 = int((labels == 0).sum())
            count1 = int((labels == 1).sum())
            winner_label = 0 if count0 >= count1 else 1

            # De-whiten: multiply back by std to return to original ITM 2005 scale
            cx = float(centroids_w[winner_label][0] * std[0])
            cy = float(centroids_w[winner_label][1] * std[1])

            logger.debug(
                "K-Means centroid (k=2, cluster %d, size %d/%d): E=%.1f N=%.1f",
                winner_label, max(count0, count1), len(coords), cx, cy
            )
            return cx, cy

        except Exception as exc:
            logger.warning(
                "scipy kmeans2 unavailable (%s) — falling back to plain mean", exc
            )
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
