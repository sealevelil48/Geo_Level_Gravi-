"""
qgis_line_layer_builder.py
Builds a QgsVectorLayer (LineString, EPSG:2039) from a List[dict] of leveling
lines using BenchmarkDBManager for spatially-disambiguated coordinate resolution.

Replaces the naive spatial_cache dict approach. All point lookups go through
geolevel_db_manager.resolve_benchmark(), which applies:
  - _normalise_name()       — strips punctuation, uppercases (fixes 3349MPI vs 3349/MPI)
  - _resolve_by_proximity() — Spatial Median + K-Means k=1 centroid (fixes geographic dupes)

Unknown points (e.g. PKT1, PKT2) that are not in the DB are handled via a
topological estimation pass: their position is inferred from the measured
total_distance of connected lines and the known coordinates of their neighbours.

No pandas dependency — input is a plain Python List[dict].
"""

import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class QGISLineLayerBuilder:
    """
    Constructs a QGIS memory LineString layer from a list of leveling-line dicts.

    All coordinate resolution is delegated to BenchmarkDBManager.
    No spatial_cache dict. No local name matching. No pandas.

    Three-pass pipeline:
      Pass 1 — Base Resolution   : resolve all point IDs via DB manager.
      Pass 2 — Topo Estimation   : estimate unknown (PKT) points from their
                                   known neighbours and measured distances.
      Pass 3 — Feature Construction: build QgsFeature objects from resolved_coords.

    Input: List[dict] — each dict is one leveling line.

    Required keys:
        start_point  (str) — backsight benchmark identifier
        end_point    (str) — foresight benchmark identifier

    Optional keys (written as layer attributes if present):
        filename, total_distance, total_height_diff, status

    Usage:
        from qgis_line_layer_builder import QGISLineLayerBuilder
        lines = [
            {"start_point": "3349/MPI", "end_point": "PKT1",
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
          3. Pass 1 — resolve all IDs via DB manager into resolved_coords.
          4. Pass 2 — topologically estimate any IDs that returned None.
          5. Pass 3 — build QgsFeature objects from resolved_coords.

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
        # Global seeding — fire ONE batch SQL query for the centroid pool      #
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
        # Create memory layer                                                  #
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
        # Pass 1 — Base Resolution                                             #
        # Try to resolve every unique point ID via the DB manager.             #
        # resolved_coords: pid -> (Easting, Northing) in EPSG:2039             #
        # ------------------------------------------------------------------ #
        resolved_coords: Dict[str, Tuple[float, float]] = {}
        unknown_ids: List[str] = []

        for pid in unique_ids:
            rec = db_manager.resolve_benchmark(pid)
            if rec is not None and rec.x is not None and rec.y is not None:
                resolved_coords[pid] = (float(rec.x), float(rec.y))
            else:
                unknown_ids.append(pid)

        logger.info(
            "QGISLineLayerBuilder: Pass 1 — resolved %d / %d points; "
            "%d unknown (will attempt topological estimation): %s",
            len(resolved_coords), len(unique_ids), len(unknown_ids),
            unknown_ids or "none",
        )

        # ------------------------------------------------------------------ #
        # Pass 2 — Distance-Weighted Topological Estimation (PKT etc.)        #
        #                                                                      #
        # Collect neighbours and their measured distances, then choose:        #
        #   1 neighbour  → +X offset by total_distance                        #
        #   2 neighbours → linear interpolation along the chord at ratio       #
        #                  r = d1/(d1+d2)                                     #
        #   3+ neighbours → Inverse Distance Weighting (IDW, weight=1/d)      #
        # ------------------------------------------------------------------ #
        for unknown_id in unknown_ids:
            # neighbour_data: list of ((E, N), avg_distance)
            neighbour_data: List[Tuple[Tuple[float, float], float]] = []

            # Accumulate per-neighbour distances (same neighbour may appear
            # in multiple rows — average them for a cleaner estimate)
            dist_accumulator: Dict[str, List[float]] = {}
            coord_for: Dict[str, Tuple[float, float]] = {}

            for row in lines:
                s_id = str(row.get("start_point") or "").strip()
                e_id = str(row.get("end_point")   or "").strip()
                raw_d = row.get("total_distance")
                d = float(raw_d) if raw_d is not None else None

                if s_id == unknown_id and e_id in resolved_coords:
                    coord_for[e_id] = resolved_coords[e_id]
                    if d is not None:
                        dist_accumulator.setdefault(e_id, []).append(d)
                elif e_id == unknown_id and s_id in resolved_coords:
                    coord_for[s_id] = resolved_coords[s_id]
                    if d is not None:
                        dist_accumulator.setdefault(s_id, []).append(d)

            for nbr_id, xy in coord_for.items():
                dvals = dist_accumulator.get(nbr_id, [])
                avg_d = sum(dvals) / len(dvals) if dvals else 100.0
                neighbour_data.append((xy, avg_d))

            if not neighbour_data:
                logger.warning(
                    "QGISLineLayerBuilder: Pass 2 — '%s' has no known neighbours; "
                    "cannot estimate position — this point will be skipped in Pass 3",
                    unknown_id,
                )
                continue

            n = len(neighbour_data)

            if n == 1:
                # End-of-run: offset by d·cos(45°) / d·sin(45°) so the point
                # renders at the exact measured distance along a clean diagonal
                # vector rather than collapsing onto the known point.
                import math as _math
                (known_e, known_n), offset = neighbour_data[0]
                est_e = known_e + offset * _math.cos(_math.radians(45))
                est_n = known_n + offset * _math.sin(_math.radians(45))
                logger.info(
                    "QGISLineLayerBuilder: Pass 2 — '%s' single-neighbour 45° offset "
                    "(dist=%.1f m): E=%.1f N=%.1f",
                    unknown_id, offset, est_e, est_n,
                )

            elif n == 2:
                # Two known benchmarks: linear interpolation at distance ratio
                (x1, y1), d1 = neighbour_data[0]
                (x2, y2), d2 = neighbour_data[1]
                r = d1 / (d1 + d2) if (d1 + d2) > 0 else 0.5
                est_e = x1 + r * (x2 - x1)
                est_n = y1 + r * (y2 - y1)
                logger.info(
                    "QGISLineLayerBuilder: Pass 2 — '%s' linear interp "
                    "r=%.4f (d1=%.1f d2=%.1f): E=%.1f N=%.1f",
                    unknown_id, r, d1, d2, est_e, est_n,
                )

            else:
                # 3+ neighbours: Inverse Distance Weighting (IDW, w = 1/d)
                total_w = sum(1.0 / d for _, d in neighbour_data if d > 0)
                if total_w == 0:
                    est_e = sum(xy[0] for xy, _ in neighbour_data) / n
                    est_n = sum(xy[1] for xy, _ in neighbour_data) / n
                else:
                    est_e = sum((xy[0] / d) for xy, d in neighbour_data if d > 0) / total_w
                    est_n = sum((xy[1] / d) for xy, d in neighbour_data if d > 0) / total_w
                logger.info(
                    "QGISLineLayerBuilder: Pass 2 — '%s' IDW from %d neighbours: "
                    "E=%.1f N=%.1f",
                    unknown_id, n, est_e, est_n,
                )
                logger.info(
                    "QGISLineLayerBuilder: Pass 2 — '%s' estimated from single "
                    "neighbour with +X offset (dist=%.1f m): E=%.1f N=%.1f",
                    unknown_id, offset, est_e, est_n,
                )

            resolved_coords[unknown_id] = (est_e, est_n)

        # ------------------------------------------------------------------ #
        # Pass 3 — Feature Construction                                        #
        # Look up every start/end from resolved_coords (both DB and estimated).#
        # ------------------------------------------------------------------ #
        features  = []
        n_built   = 0
        n_skipped = 0

        for idx, row in enumerate(lines):
            start_id = str(row.get("start_point") or "").strip()
            end_id   = str(row.get("end_point")   or "").strip()

            if not start_id or not end_id:
                logger.warning(
                    "Row %d: missing start_point or end_point — skipping", idx
                )
                n_skipped += 1
                continue

            start_xy = resolved_coords.get(start_id)
            end_xy   = resolved_coords.get(end_id)

            if start_xy is None and end_xy is None:
                logger.warning(
                    "Row %d: both '%s' and '%s' unresolvable — skipping feature",
                    idx, start_id, end_id,
                )
                n_skipped += 1
                continue

            # Last-resort collapse: one endpoint still unknown after Pass 2
            if start_xy is None:
                logger.warning(
                    "Row %d: '%s' still unresolved after estimation — "
                    "collapsing to known end '%s' (E=%.1f N=%.1f)",
                    idx, start_id, end_id, end_xy[0], end_xy[1],
                )
                start_xy = end_xy
            elif end_xy is None:
                logger.warning(
                    "Row %d: '%s' still unresolved after estimation — "
                    "collapsing to known start '%s' (E=%.1f N=%.1f)",
                    idx, end_id, start_id, start_xy[0], start_xy[1],
                )
                end_xy = start_xy

            start_pt = QgsPointXY(start_xy[0], start_xy[1])
            end_pt   = QgsPointXY(end_xy[0],   end_xy[1])
            geom     = QgsGeometry.fromPolylineXY([start_pt, end_pt])

            raw_dist  = row.get("total_distance")
            raw_hdiff = row.get("total_height_diff")

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
            n_built += 1

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
            self.layer_name, n_built, n_skipped,
        )
        return layer
