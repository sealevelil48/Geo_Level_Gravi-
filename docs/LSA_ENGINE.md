# Leveling adjustment engine

Applies to the QGIS plugin (`GeoLevelQGIS/core_logic/engine/`) and the
standalone app (`geodetic_tool/engine/`). Both use the same code:
`network_prep.py` for the preliminary steps and `least_squares.py` for the
adjustments.

## Preliminary steps

### Step 1: one observation per section
`merge_observations()` groups repeated runs of the same section, in either
direction. For a forward/return pair the section enters the adjustment once:

```
dH = (dH_fwd − dH_ret) / 2          (return run reversed to the forward direction)
δ  = dH_fwd + dH_ret                checked against k·√L
```

L is the **one-direction** length in km and k is the class coefficient
(H1 3, H2 5, H3 10, H4 20, H5 30, H6 60 mm). The mean of n runs gets weight n/L.

### Step 2: approximate heights
`approximate_heights()` runs a multi-source Dijkstra from the fixed points
(otherwise from any known height, otherwise from a relative 0.000) and adds
the height differences along the shortest path. These are the H⁰ values shown
in the window and the starting point of the parametric solution.

### Datum check
`check_datum()` raises `NetworkDatumError` when a connected group of lines
contains no fixed point. A singular solution is never returned silently.

## Weights

`observation_weight(obs, mode)`:

| mode | p |
|---|---|
| `distance` (default, Directive ג2) | n_runs / L_km |
| `setups` | n_runs / n_setups |
| `equal` | n_runs |

## Parametric adjustment: V = A·X − L

* **Lb**: measured height differences.
* **L0**: contribution of fixed points, by direction:
  FROM fixed → −H_from, TO fixed → +H_to, both fixed → H_to − H_from.
* **L = Lb − L0**.
* Normal equations N = AᵀPA, U = AᵀPL, solved once with **X = N⁻¹U**. The
  model is linear in heights, so there is no iteration.
* V = A·X − L, σ₀² = VᵀPV / (n − u), σ(H) = σ₀·√(N⁻¹)ᵢᵢ, 95% = 1.96·σ.

## Conditional adjustment: B·v + w = 0

* **Conditions**: the fundamental loops of a spanning forest of the network
  (independent by construction, a GF(2) basis), plus (n_fixed − 1) paths
  between fixed points in each connected group.
* M = B·P⁻¹·Bᵀ, k = −M⁻¹·w, v = P⁻¹·Bᵀ·k. Heights are then propagated from
  the fixed points with the corrected observations.
* Gives the same heights and σ₀ as the parametric method.

## Single-line adjustment
`adjust_single_line(line, h_start, h_end, class)` distributes the misclosure
between two known heights in proportion to distance (Directive ג2, App. C),
and checks it against k·√L.

## Outputs
Summary (σ₀, degrees of freedom, BM-to-BM misclosures against k·√L),
observations after Step 1, approximate heights, adjusted heights with σ and
95% interval, residuals, matrix diagnostics. Export to FA0 (known heights),
FA1 (adjusted heights) and a PDF report.

## Verification
`test_adjustment.py` (`core_logic/tests/` in the plugin, `geodetic_tool/tests/` in the standalone app) reproduces example 1 of
*Condition Adjustment (Bv + w)* (in `docs/reference/` on the plugin branch):
fixed points RFH-95/96/97, giving A = 121.344, B = 117.573, C = 110.190,
D = 102.764 and σ₀ = 4.512. Both methods agree to 0.1 mm. The tests also cover
Step 1 averaging, Step 2, the datum error and single-line adjustment.
