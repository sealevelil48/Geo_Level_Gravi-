"""
qgis_line_layer_builder.py
Builds a QgsVectorLayer (LineString, EPSG:2039) from a List[dict] of leveling
lines using BenchmarkDBManager for spatially-disambiguated coordinate resolution.

Replaces the naive spatial_cache dict approach. All point lookups go through
geolevel_db_manager.resolve_benchmark(), which applies:
  - _normalise_name()       — strips punctuation, uppercases (fixes 3349MPI vs 3349/MPI)
  - _resolve_by_proximity() — Spatial Median + K-Means k=1 centroid (fixes geographic dupes)

No pandas dependency — input is a plain Python List[dict].
"""

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


class QGISLineLayerBuilder:
    """
    Constructs a QGIS memory LineString layer from a list of leveling-line dicts.

    All coordinate resolution is delegated to BenchmarkDBManager.
    No spatial_cache dict. No local name matching. No pandas.

    Input: List[dict] — each dict is one leveling line.

    Required keys:
        start_point  (str) — backsight benchmark identifier
        end_point    (str) — foresight benchmark identifier

    Optional keys (written as layer attributes if present):
        filename, total_distance, total_height_diff, status

    Usage:
        from qgis_line_layer_builder import QGISLineLayerBuilder
        lines = [
            {"start_point": "3349/MPI", "end_point": "3350/MPI",
             "filename": "run1.DAT", "total_distance": 1.5,
             "total_height_diff": 0.012, "status": "VALID"},
        ]
        builder = QGISLineLayerBuilder(layer_name="Survey Lines 2026")
        layer = builder.process_line_features(lines, crs="EPSG:2039")
        if layer:
            QgsProject.instance().addMapLayer(layer)
    """

    def __init__(self, layer_name: str = "Leveling Lines"):
        self.layer_name = layer_name

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def process_line_features(self, lines: List[dict], crs: str = "EPSG:2039"):
        """
        Build and return a QgsVectorLayer (LineString) from a list of dicts.

        Args:
            lines: List of dicts, each with at minimum keys 'start_point'
                   and 'end_point'. Optional keys: 'filename',
                   'total_distance', 'total_height_diff', 'status'.
            crs:   EPSG code string (default 'EPSG:2039' = ITM 2005).

        Steps:
          1. Collect all unique point IDs and call seed_from_qgis_layer() so
             the manager can build its global centroid pool in one batch query.
          2. Create a memory LineString layer with the target CRS.
          3. For each dict, resolve start/end coordinates via
             resolve_benchmark() and build QgsFeature geometry.

        Returns:
            QgsVectorLayer on success, None if QGIS API is unavailable or
            the memory layer cannot be created.
        """
        try:
            from qgis.core import (
                QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY, QgsField,
            )
            from PyQt5.QtCore import QVariant
        except ImportError as exc:
            logger.error(
                "QGISLineLayerBuilder: QGIS/PyQt5 unavailable — %s", exc
            )
            return None

        try:
            from geolevel_db_manager import get_db_manager
        except ImportError as exc:
            logger.error(
                "QGISLineLayerBuilder: geolevel_db_manager not importable — %s", exc
            )
            return None

        db_manager = get_db_manager()

        if not db_manager.is_configured():
            logger.warning(
                "QGISLineLayerBuilder: BenchmarkDBManager is not configured. "
                "All resolve_benchmark() calls will return None and features "
                "will be skipped. Configure the DB connection first."
            )

        # ------------------------------------------------------------------ #
        # Step 1 — Global seeding                                              #
        # Collect every unique identifier in the list and hand them to         #
        # seed_from_qgis_layer() so the manager can build its centroid pool.   #
        # This fires at most ONE batch SQL query (cached after first call).     #
        # ------------------------------------------------------------------ #
        raw_ids: List[str] = []
        for row in lines:
            for key in ("start_point", "end_point"):
                val = row.get(key)
                if val is not None:
                    raw_ids.append(str(val))
        unique_ids = list({s.strip() for s in raw_ids if s.strip()})

        logger.info(
            "QGISLineLayerBuilder: seeding DB manager with %d unique point IDs "
            "extracted from %d input rows",
            len(unique_ids), len(lines),
        )
        seeded = db_manager.seed_from_qgis_layer(unique_ids)
        logger.info(
            "QGISLineLayerBuilder: seed_from_qgis_layer returned %d matched point(s) "
            "from QGIS layer",
            seeded,
        )

        # ------------------------------------------------------------------ #
        # Step 2 — Create memory layer                                         #
        # ------------------------------------------------------------------ #
        uri = f"LineString?crs={crs}"
        layer = QgsVectorLayer(uri, self.layer_name, "memory")
        if not layer.isValid():
            logger.error(
                "QGISLineLayerBuilder: QgsVectorLayer creation failed "
                "(uri='%s')", uri
            )
            return None

        provider = layer.dataProvider()
        provider.addAttributes([
            QgsField("start_point",       QVariant.String),
            QgsField("end_point",         QVariant.String),
            QgsField("filename",          QVariant.String),
            QgsField("total_distance",    QVariant.Double),
            QgsField("total_height_diff", QVariant.Double),
            QgsField("status",            QVariant.String),
        ])
        layer.updateFields()
        logger.info(
            "QGISLineLayerBuilder: memory layer '%s' created (crs=%s)",
            self.layer_name, crs,
        )

        # ------------------------------------------------------------------ #
        # Step 3 — Resolve coordinates and build QgsFeature objects            #
        # ------------------------------------------------------------------ #
        features = []
        n_resolved = 0
        n_skipped  = 0

        for idx, row in enumerate(lines):
            start_id = str(row.get("start_point") or "").strip()
            end_id   = str(row.get("end_point")   or "").strip()

            if not start_id or not end_id:
                logger.warning(
                    "Row %d: missing start_point or end_point — skipping", idx
                )
                n_skipped += 1
                continue

            start_rec = db_manager.resolve_benchmark(start_id)
            end_rec   = db_manager.resolve_benchmark(end_id)

            # Extract coordinates (BenchmarkRecord.x = Easting, .y = Northing)
            def _coords(rec, label):
                """Return (E, N) or None if unavailable."""
                if rec is None:
                    logger.warning(
                        "Row %d: resolve_benchmark('%s') returned None "
                        "(not in DB) — will attempt coordinate inheritance",
                        idx, label,
                    )
                    return None
                if rec.x is None or rec.y is None:
                    logger.warning(
                        "Row %d: benchmark '%s' resolved but has no coordinates "
                        "(x=%s, y=%s) — will attempt coordinate inheritance",
                        idx, label, rec.x, rec.y,
                    )
                    return None
                return (rec.x, rec.y)

            start_xy = _coords(start_rec, start_id)
            end_xy   = _coords(end_rec,   end_id)

            if start_xy is None and end_xy is None:
                # Both endpoints unresolvable — no safe location to anchor to
                logger.warning(
                    "Row %d: both '%s' and '%s' unresolvable — skipping feature",
                    idx, start_id, end_id,
                )
                n_skipped += 1
                continue

            # Inherit: if one endpoint is an intermediate/new point (e.g. PKT1)
            # with no DB coordinates, collapse it to the known neighbour so the
            # visual line stays within the project area instead of flying to Egypt.
            if start_xy is None:
                logger.warning(
                    "Row %d: '%s' has no coords — inheriting from known end '%s' "
                    "(E=%.1f N=%.1f); line will render as zero-length point marker",
                    idx, start_id, end_id, end_xy[0], end_xy[1],
                )
                start_xy = end_xy
            elif end_xy is None:
                logger.warning(
                    "Row %d: '%s' has no coords — inheriting from known start '%s' "
                    "(E=%.1f N=%.1f); line will render as zero-length point marker",
                    idx, end_id, start_id, start_xy[0], start_xy[1],
                )
                end_xy = start_xy

            # BenchmarkRecord.x = Easting, .y = Northing (EPSG:2039)
            start_pt = QgsPointXY(start_xy[0], start_xy[1])
            end_pt   = QgsPointXY(end_xy[0],   end_xy[1])
            geom     = QgsGeometry.fromPolylineXY([start_pt, end_pt])

            # Safe numeric extraction — no pandas, plain Python
            raw_dist   = row.get("total_distance")
            raw_hdiff  = row.get("total_height_diff")

            feat = QgsFeature()
            feat.setGeometry(geom)
            feat.setAttributes([
                start_id,
                end_id,
                str(row.get("filename") or ""),
                float(raw_dist)  if raw_dist  is not None else None,
                float(raw_hdiff) if raw_hdiff is not None else None,
                str(row.get("status") or ""),
            ])
            features.append(feat)
            n_resolved += 1

            logger.debug(
                "Row %d: '%s' (E=%.1f N=%.1f) → '%s' (E=%.1f N=%.1f)",
                idx,
                start_id, start_xy[0], start_xy[1],
                end_id,   end_xy[0],   end_xy[1],
            )

        provider.addFeatures(features)
        layer.updateExtents()

        logger.info(
            "QGISLineLayerBuilder: layer '%s' complete — "
            "%d feature(s) built, %d row(s) skipped",
            self.layer_name, n_resolved, n_skipped,
        )
        return layer
