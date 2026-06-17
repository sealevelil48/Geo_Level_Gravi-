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
    x: Optional[float]                  # ITM 2005 Easting  (EPSG:2039)
    y: Optional[float]                  # ITM 2005 Northing (EPSG:2039)


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


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class BenchmarkDBManager:
    """
    Manages PostgreSQL connectivity and point resolution with session caching
    and spatial disambiguation.

    Thread-safety: intended for single-threaded QGIS main thread use only.
    """

    def __init__(self):
        self._params: Dict[str, Any] = {}
        self._cache: Dict[str, Optional[BenchmarkRecord]] = {}
        # Maps resolved (unambiguous) point names → (x, y) for centroid calc
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

        # Multiple candidates — resolve by spatial proximity
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
             x, y) = row
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
                x=float(x) if x is not None else None,
                y=float(y) if y is not None else None,
            ))
        return results

    def _resolve_by_proximity(self, candidates: List[BenchmarkRecord],
                               key: str) -> Optional[BenchmarkRecord]:
        """
        Select the candidate nearest to the project centroid.
        Updates _resolved_coords with the winner's coordinates.
        """
        spatial = [r for r in candidates if r.x is not None and r.y is not None]
        if not spatial:
            logger.warning(
                "Ambiguous point '%s': none of %d candidates have x/y — returning first",
                key, len(candidates)
            )
            return candidates[0]

        cx, cy = self._get_project_centroid(fallback=spatial)
        winner = min(spatial, key=lambda r: math.sqrt((r.x - cx) ** 2 + (r.y - cy) ** 2))

        if winner.x is not None and winner.y is not None:
            self._resolved_coords[key] = (winner.x, winner.y)

        logger.debug(
            "Proximity winner for '%s': name=%s x=%.1f y=%.1f (centroid=%.1f,%.1f)",
            key, winner.name, winner.x, winner.y, cx, cy
        )
        return winner

    def _get_project_centroid(self, fallback: List[BenchmarkRecord]) -> Tuple[float, float]:
        """
        Compute project centroid from previously resolved points.
        Falls back to the geometric centre of the candidate set on the first call.
        """
        if self._resolved_coords:
            xs = [v[0] for v in self._resolved_coords.values()]
            ys = [v[1] for v in self._resolved_coords.values()]
            return sum(xs) / len(xs), sum(ys) / len(ys)

        # Edge case: first resolution in session and it's already ambiguous
        valid = [r for r in fallback if r.x is not None and r.y is not None]
        if valid:
            return (sum(r.x for r in valid) / len(valid),
                    sum(r.y for r in valid) / len(valid))
        return 0.0, 0.0


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
