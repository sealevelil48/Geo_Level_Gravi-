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
(H1 3, H2 5, H3 10, H4 20, H5 30, H6 60 mm; values edited in Class
Parameters apply). The mean of n runs gets weight n/L. With three or more runs
δ is the spread (max − min) of the runs reduced to the forward direction.

**Detect Double-Runs** uses the same numbers (`analyze_double_runs()`):
unused lines are skipped, the first run of a section is the forward run, the
class is the line's own class (else the project class), and "class met" is the
best class whose k·√L the δ meets.

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

## FA0 / FA1 files (legacy layout)

Both are written exactly as the legacy adjustment program writes them;
`K6177-64PU-65PU` is reproduced byte for byte (FA0) and line for line (FA1).

* **Header** `npoints  nfixed  name`: the first *nfixed* listed points are the
  fixed points. Other heights in the list are approximate heights.
  **Auto-Select** takes exactly these fixed points when the loaded files
  define them; DB heights are only offered when no loaded file does.
* **Point IDs** keep the legacy layout: number right-aligned in 4 characters,
  then the letters (`  64PU`, ` 117`, `2751ב`). Heights are aligned on the
  decimal point; unknown heights are `.`.
* **Observation rows** keep the measured columns read from FA0/REZ: setups,
  B-F difference, MMYY and raw file (`24   0.87  0320  973.raw`). The last row
  ends with `9`.
* **FA1** prints two iterations, as the legacy program does: DATA (input
  heights: fixed, approximate, unknown = 0.000), then `DH-(H2-H1)` against
  those heights, ITERATION NO. 1 (ADJUSTED / APPROX / DIF / M.S.E.), then DATA
  with the adjusted heights, `DH-(H2-H1)` against them, and ITERATION NO. 2.
  **ALWD = 3 mm·√L** (as in the legacy FA1, whatever the class), DELTA =
  |DH-(H2-H1)| − ALWD, `**` when DELTA > 0; SDIF = Σ|DH-(H2-H1)| in mm,
  COEFF_CLASS K = SDIF/√SDIST. M.S.E. values are in metres.

## Outputs
Summary (σ₀, degrees of freedom, BM-to-BM misclosures against k·√L),
observations after Step 1, approximate heights, adjusted heights with σ and
95% interval, residuals, matrix diagnostics. Export to FA0 (known heights),
FA1 (adjusted heights) and a PDF report.

## Verification
`test_adjustment.py` (`core_logic/tests/` in the plugin, `geodetic_tool/tests/` in the standalone app) reproduces example 1 of
*Condition Adjustment (Bv + w)* (the PDF is in `docs/reference/` on the `GeoLevelGraviQGIS` branch):
fixed points RFH-95/96/97, giving A = 121.344, B = 117.573, C = 110.190,
D = 102.764 and σ₀ = 4.512. Both methods agree to 0.1 mm. The tests also cover
Step 1 averaging, Step 2, the datum error and single-line adjustment.

`test_double_runs.py` reproduces the worked example of Directive ג2,
Appendix C (line A–1–2–3–4–B, H1): every δ, mean ΔH, the line sum 10.8004 m
and f_H = 6.4 mm. Note: that example's "allowed" column prints 2·√L
(±1.9 mm for 0.907 km) while its own header and Appendix B 4.1 give
3 mm·√L for H1; the engine follows the formula (±2.9 mm there). All five
sections pass either way.
