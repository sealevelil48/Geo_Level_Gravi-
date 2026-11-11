# Spatial Median + K-Means k=1 Centroid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current K-Means k=2 centroid logic in `geolevel_db_manager.py` with a Spatial Median outlier-rejection pass followed by a strict K-Means k=1 centroid, matching the boss's "center of area" mathematical specification.

**Architecture:** The existing `_kmeans_centroid()` method is the single point of change for `_build_global_pool_centroid` and `_get_project_centroid`. We replace it with two focused methods: `_spatial_median_filter()` (outlier rejection via 75th-percentile distance cutoff on the spatial median) and `_kmeans1_centroid()` (scipy k=1 on the filtered core). The public API surface of the class is unchanged — only the internal math changes. Verbose `logging.INFO` outputs are added so engineers can trace the outlier count, retention rate, and final centroid from the QGIS log.

**Tech Stack:** Python 3, `numpy` (already a dependency), `scipy.cluster.vq` (already imported), QGIS plugin context.

---

## File Map

| File | Change |
|------|--------|
| `GeoLevelQGIS/geolevel_db_manager.py` | Replace `_kmeans_centroid()` with `_spatial_median_filter()` + `_kmeans1_centroid()`; update `_build_global_pool_centroid()` and `_get_project_centroid()` docstrings to reflect k=1; add INFO logs throughout |

No other files are touched.

---

## Task 1: Replace `_kmeans_centroid` with two focused methods

**Files:**
- Modify: `GeoLevelQGIS/geolevel_db_manager.py`

This is a pure in-place rewrite of the `_kmeans_centroid` method (lines 607–655 in the current file) plus a new `_spatial_median_filter` helper added just before it. The call-sites `_get_project_centroid` (line 605) and `_build_global_pool_centroid` (line 382) already call `self._kmeans_centroid(coords)` — after the rename the call is `self._kmeans1_centroid(coords)`.

- [ ] **Step 1: Delete `_kmeans_centroid` and insert the two replacement methods**

Replace the entire `_kmeans_centroid` method body (everything from `def _kmeans_centroid` down to and including the closing `except` block of that method) with the following two methods:

```python
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
            # Not enough points to apply percentile filtering meaningfully
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

            # Guard: degenerate axis (all values identical) → whiten would divide by 0
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
```

- [ ] **Step 2: Update the two call-sites from `_kmeans_centroid` → `_kmeans1_centroid`**

In `_get_project_centroid` (near line 605), change:
```python
        return self._kmeans_centroid(coords)
```
to:
```python
        return self._kmeans1_centroid(coords)
```

In `_build_global_pool_centroid` (near line 382), change:
```python
        centroid = self._kmeans_centroid(coords)
```
to:
```python
        centroid = self._kmeans1_centroid(coords)
```

- [ ] **Step 3: Update the class-level docstring to reflect k=1**

In `BenchmarkDBManager`'s class docstring, change the line:
```
         SQL query and runs K-Means k=2 on the combined
         spatial candidates to find the true project cluster.
```
to:
```
         SQL query, applies Spatial Median outlier rejection (inner-75%
         percentile), then runs K-Means k=1 on the filtered core points
         to find the true project centroid ('center of area' method).
```

- [ ] **Step 4: Verify no other references to `_kmeans_centroid` remain**

```bash
grep -rn "_kmeans_centroid" GeoLevelQGIS/
```
Expected: **no output** (zero matches).

- [ ] **Step 5: Quick smoke-test — import the module standalone**

Run from the repo root (no QGIS needed — just verifies Python syntax and that the method exists):

```bash
cd "c:\Users\user01\Downloads\geodetic_tool_v1.1"
python -c "
import sys, types
# Stub QGIS so module can be imported outside QGIS
qgis_stub = types.ModuleType('qgis')
qgis_stub.core = types.ModuleType('qgis.core')
sys.modules['qgis'] = qgis_stub
sys.modules['qgis.core'] = qgis_stub.core

sys.path.insert(0, 'GeoLevelQGIS')
import geolevel_db_manager as m

mgr = m.BenchmarkDBManager()

# Test _spatial_median_filter
coords = [(100.0, 200.0), (101.0, 201.0), (102.0, 200.5),
          (103.0, 199.0), (5000.0, 9000.0)]   # last one is a clear outlier
filtered = mgr._spatial_median_filter(coords)
assert len(filtered) < len(coords), 'Outlier was NOT dropped'
print('_spatial_median_filter OK — dropped', len(coords) - len(filtered), 'outlier(s)')

# Test _kmeans1_centroid returns a 2-tuple of floats
cx, cy = mgr._kmeans1_centroid(coords)
assert isinstance(cx, float) and isinstance(cy, float)
print('_kmeans1_centroid OK — centroid E=%.1f N=%.1f' % (cx, cy))

print('All assertions passed.')
"
```

Expected output (values will vary by scipy version):
```
_spatial_median_filter OK — dropped 1 outlier(s)
_kmeans1_centroid OK — centroid E=... N=...
All assertions passed.
```

- [ ] **Step 6: Commit**

```bash
git add GeoLevelQGIS/geolevel_db_manager.py
git commit -m "refactor: Spatial Median + k=1 centroid (boss's 'center of area' spec)

Replace K-Means k=2 with a two-stage pipeline:
  1. _spatial_median_filter — rejects outliers beyond the 75th-percentile
     Euclidean distance from the spatial median (immune to extreme outliers).
  2. _kmeans1_centroid — scipy kmeans k=1 on the filtered core points.

Adds verbose logging.INFO so engineers can trace candidate counts,
outlier drop counts, p75 cutoff distance, and final centroid coordinates
from the QGIS log panel.

Refs: boss review 2026-07-06"
```

---

## Self-Review Checklist

- [x] **Spec coverage**
  - Step 1 — Global Fetch: unchanged (existing `_build_global_pool_centroid` batch SQL already does this).
  - Step 2 — Spatial Median + 75th-percentile filter: implemented in `_spatial_median_filter`.
  - Step 3 — k=1 K-Means with whiten/de-whiten: implemented in `_kmeans1_centroid`.
  - Step 4 — `_resolve_by_proximity` uses the new centroid: yes, it calls `_get_project_centroid` which calls `_kmeans1_centroid` — no change needed to `_resolve_by_proximity` itself.
  - Verbose `logging.INFO`: present in both new methods (candidate count, outlier drop count, p75 value, final E/N).

- [x] **Placeholder scan**: no TBD/TODO/fill-in-later text anywhere in this plan.

- [x] **Type consistency**: `_spatial_median_filter` accepts and returns `List[Tuple[float, float]]`; `_kmeans1_centroid` accepts `List[Tuple[float, float]]` and returns `Tuple[float, float]` — matches every call-site.
