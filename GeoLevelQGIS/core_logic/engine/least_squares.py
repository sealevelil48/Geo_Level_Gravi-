"""
Least Squares Adjustment of leveling networks.

Two equivalent methods are provided; for the same observations, weights and
fixed points they give identical heights (tested in core_logic/tests).

1. Parametric (observation equations)      V = A X - L
   ---------------------------------------------------
   Lb : measured height differences (constant)
   L0 : contribution of the fixed heights, by direction of measurement
          FROM fixed, TO unknown : L0 = -H_from
          TO fixed, FROM unknown : L0 = +H_to
          both fixed             : L0 = H_to - H_from
          neither                : L0 = 0
   L  = Lb - L0                       (built once)
   N  = A' P A,   U = A' P L,   X = N^-1 U      (X = adjusted heights)
   V  = A X - L,  sigma0^2 = V' P V / (n - u),  Sxx = sigma0^2 N^-1

   Leveling observations are linear in the heights, so X is obtained in one
   direct solution (no iteration).  The approximate heights from Step 2 are
   reported, and corrections = X - approximate.

2. Conditional (condition equations)       B v + w = 0
   ---------------------------------------------------
   r = n_loops + (n_fixed - 1) per connected group, built from the
   fundamental loops of a spanning forest (always independent).
   w = B Lb - c,   M = B P^-1 B',   k = -M^-1 w,   v = P^-1 B' k
   adjusted dH = Lb + v; heights carried from the fixed points.

Before either method runs, every connected group of points must contain a
fixed point (datum check) - otherwise a clear NetworkDatumError is raised.
"""
from typing import Dict, List, Optional, Sequence, Tuple
import logging

import numpy as np

from core_logic.config.models import LevelingLine, AdjustmentResult, MeasurementSummary
from core_logic.engine.ADJwarnings import SingularMatrixError, InsufficientObservationsError
from core_logic.engine.network_prep import (
    NetworkDatumError, approximate_heights, check_datum, independent_conditions,
    observation_weight, tolerance_mm, _obs_fields,
)

logger = logging.getLogger(__name__)

__all__ = ["LeastSquaresAdjuster", "ConditionalAdjuster", "NetworkDatumError",
           "simple_adjustment", "lines_to_observations"]


def lines_to_observations(lines: Sequence[LevelingLine]) -> List[MeasurementSummary]:
    """LevelingLine list -> MeasurementSummary list (one per line)."""
    out = []
    for ln in lines:
        out.append(MeasurementSummary(
            from_point=ln.start_point, to_point=ln.end_point,
            height_diff=ln.total_height_diff, distance=ln.total_distance,
            num_setups=ln.num_setups,
            bf_diff=getattr(ln, "bf_diff", None) or 0.0,
            year_month=getattr(ln, "year_month", "") or "",
            source_file=getattr(ln, "source_file", "") or ln.filename,
        ))
    return out


def _clean(observations: Sequence) -> list:
    keep = []
    for o in observations:
        f, t, *_ = _obs_fields(o)
        if not f or not t:
            logger.warning("Skipping observation with a missing end point: %s", o)
        elif f == t:
            logger.warning("Skipping observation from a point to itself: %s", f)
        else:
            keep.append(o)
    return keep


def _k_coefficient(v_m: np.ndarray, total_km: float) -> Tuple[float, float]:
    total_diff_mm = float(np.sum(np.abs(v_m)) * 1000.0)
    k = total_diff_mm / np.sqrt(total_km) if total_km > 0 else 0.0
    return total_diff_mm, float(k)


def _benchmark_misclosures(observations, fixed_points, leveling_class) -> List[Dict]:
    """Misclosure between each pair of fixed points along the spanning tree
    (for a single line between two benchmarks this is the line misclosure f)."""
    rows = []
    for c in independent_conditions(observations, fixed_points):
        if c.kind != "benchmark":
            continue
        measured = sum(s * _obs_fields(observations[i])[2] for i, s in c.terms)
        length = sum(_obs_fields(observations[i])[3] for i, _ in c.terms)
        f_mm = (measured - c.constant) * 1000.0
        allowed = tolerance_mm(length, leveling_class)
        rows.append({"from": c.points[0], "to": c.points[1],
                     "measured_dh": measured, "known_dh": c.constant,
                     "misclosure_mm": f_mm, "length_m": length,
                     "allowed_mm": allowed, "ok": abs(f_mm) <= allowed})
    return rows


class LeastSquaresAdjuster:
    """Parametric least-squares adjustment (V = A X - L)."""

    def __init__(self, max_iterations: int = 1, tolerance: float = 1e-6,
                 check_stability: bool = True, weight_mode: str = "distance",
                 leveling_class=3, **_ignored):
        # max_iterations / tolerance are kept for backward compatibility only:
        # the leveling model is linear and is solved directly.
        self.check_stability = check_stability
        self.weight_mode = weight_mode
        self.leveling_class = leveling_class

    def adjust(self, observations: Sequence, fixed_points: Dict[str, float],
               approximate_heights_in: Optional[Dict[str, float]] = None,
               known_heights: Optional[Dict[str, float]] = None,
               **_ignored) -> AdjustmentResult:
        observations = _clean(observations)
        if not observations:
            raise ValueError("No observations provided")
        if not fixed_points:
            raise ValueError("At least one fixed point is required")
        fixed_points = {str(k).strip(): float(v) for k, v in fixed_points.items()}

        check_datum(observations, fixed_points)

        points = sorted({p for o in observations for p in _obs_fields(o)[:2]})
        unknowns = [p for p in points if p not in fixed_points]
        if not unknowns:
            raise ValueError("All points are fixed - nothing to adjust")
        col = {p: j for j, p in enumerate(unknowns)}
        n, u = len(observations), len(unknowns)

        approx = approximate_heights(observations, fixed_points, known_heights)
        approx_h = {p: a.height for p, a in approx.items()}
        if approximate_heights_in:
            approx_h.update({k: float(v) for k, v in approximate_heights_in.items()
                             if k in col})

        # --- Lb, L0, L, A, P (all built once) ---
        Lb = np.zeros(n)
        L0 = np.zeros(n)
        A = np.zeros((n, u))
        p = np.zeros(n)
        for i, o in enumerate(observations):
            f, t, dh, _d, *_ = _obs_fields(o)
            Lb[i] = dh
            ff, tf = f in fixed_points, t in fixed_points
            if ff and tf:
                L0[i] = fixed_points[t] - fixed_points[f]
            elif ff:
                L0[i] = -fixed_points[f]
            elif tf:
                L0[i] = fixed_points[t]
            if t in col:
                A[i, col[t]] = 1.0
            if f in col:
                A[i, col[f]] = -1.0
            p[i] = observation_weight(o, self.weight_mode)
        L = Lb - L0

        N = A.T @ (p[:, None] * A)
        U = A.T @ (p * L)
        rank = np.linalg.matrix_rank(N)
        if rank < u:
            raise SingularMatrixError(
                f"Normal matrix is singular (rank {rank} < {u} unknowns).")
        cond = float(np.linalg.cond(N))
        if self.check_stability and cond > 1e12:
            logger.warning("Normal matrix is ill-conditioned (cond = %.2e)", cond)

        X = np.linalg.solve(N, U)
        V = A @ X - L
        dof = n - u
        vtpv = float(V @ (p * V))
        sigma0 = float(np.sqrt(vtpv / dof)) if dof > 0 else 0.0
        Qxx = np.linalg.inv(N)

        heights = dict(fixed_points)
        heights.update({pid: float(X[col[pid]]) for pid in unknowns})
        mse = {pid: float(sigma0 * np.sqrt(Qxx[col[pid], col[pid]])) for pid in unknowns}
        for pid in fixed_points:
            if pid in points:
                mse[pid] = 0.0
        corrections = {pid: heights[pid] - approx_h.get(pid, heights[pid]) for pid in unknowns}

        total_km = sum(_obs_fields(o)[3] for o in observations) / 1000.0
        total_diff_mm, k = _k_coefficient(V, total_km)
        residuals, table = {}, []
        for i, o in enumerate(observations):
            f, t, dh, d, *_ = _obs_fields(o)
            residuals[f"{f}-{t}"] = float(V[i] * 1000.0)
            table.append({"from": f, "to": t, "measured_dh": dh, "distance": d,
                          "weight": float(p[i]), "residual_mm": float(V[i] * 1000.0),
                          "adjusted_dh": dh + float(V[i]),
                          "n_runs": getattr(o, "n_runs", 1),
                          "num_setups": getattr(o, "num_setups", 0),
                          "source": getattr(o, "source_file", "")})

        logger.info("Parametric LSA: n=%d u=%d dof=%d sigma0=%.3f mm/sqrt(km)",
                    n, u, dof, sigma0 * 1000)
        return AdjustmentResult(
            iteration=1, mse_unit_weight=sigma0, adjusted_heights=heights,
            residuals=residuals, mse_heights=mse, total_distance_km=total_km,
            total_diff_mm=total_diff_mm, k_coefficient=k,
            method="parametric", approximate_heights=approx_h,
            corrections=corrections, fixed_points=dict(fixed_points),
            degrees_of_freedom=dof, observations=table,
            benchmark_misclosures=_benchmark_misclosures(
                observations, fixed_points, self.leveling_class),
            weight_mode=self.weight_mode,
        )

    def adjust_from_lines(self, lines: List[LevelingLine],
                          fixed_points: Dict[str, float]) -> AdjustmentResult:
        return self.adjust(lines_to_observations(lines), fixed_points)


class ConditionalAdjuster:
    """Conditional least-squares adjustment (B v + w = 0)."""

    def __init__(self, check_stability: bool = True, weight_mode: str = "distance",
                 leveling_class=3, condition_number_threshold: float = 1e12,
                 **_ignored):
        self.check_stability = check_stability
        self.weight_mode = weight_mode
        self.leveling_class = leveling_class
        self.cond_threshold = condition_number_threshold

    def adjust(self, observations: Sequence, fixed_points: Dict[str, float],
               known_heights: Optional[Dict[str, float]] = None) -> AdjustmentResult:
        observations = _clean(observations)
        if not observations:
            raise ValueError("No observations provided")
        if not fixed_points:
            raise ValueError("At least one fixed point is required")
        fixed_points = {str(k).strip(): float(v) for k, v in fixed_points.items()}
        check_datum(observations, fixed_points)

        conds = independent_conditions(observations, fixed_points)
        n = len(observations)
        Lb = np.array([_obs_fields(o)[2] for o in observations])
        p = np.array([observation_weight(o, self.weight_mode) for o in observations])
        points = sorted({q for o in observations for q in _obs_fields(o)[:2]})
        unknowns = [q for q in points if q not in fixed_points]

        if conds:
            B = np.zeros((len(conds), n))
            c = np.zeros(len(conds))
            for r, cd in enumerate(conds):
                for i, s in cd.terms:
                    B[r, i] += s
                c[r] = cd.constant
            w = B @ Lb - c
            Pinv = 1.0 / p
            M = B @ (Pinv[:, None] * B.T)
            if np.linalg.matrix_rank(M) < len(conds):
                raise SingularMatrixError("Condition matrix M is singular.")
            if self.check_stability and np.linalg.cond(M) > self.cond_threshold:
                logger.warning("Condition matrix M is ill-conditioned")
            k = -np.linalg.solve(M, w)
            v = Pinv * (B.T @ k)
        else:
            # No redundancy (open traverse from one fixed point)
            v = np.zeros(n)
        r = len(conds)
        sigma0 = float(np.sqrt(float(v @ (p * v)) / r)) if r > 0 else 0.0
        L_adj = Lb + v

        # Heights carried from the fixed points with the adjusted dH
        adjusted_obs = []
        for i, o in enumerate(observations):
            f, t, _dh, d, *_ = _obs_fields(o)
            adjusted_obs.append(MeasurementSummary(
                from_point=f, to_point=t, height_diff=float(L_adj[i]), distance=d,
                num_setups=0, bf_diff=0.0, year_month="", source_file=""))
        carried = approximate_heights(adjusted_obs, fixed_points)
        heights = dict(fixed_points)
        heights.update({q: carried[q].height for q in unknowns})

        # Height precision: same cofactors as the parametric solution
        col = {q: j for j, q in enumerate(unknowns)}
        A = np.zeros((n, len(unknowns)))
        for i, o in enumerate(observations):
            f, t, *_ = _obs_fields(o)
            if t in col:
                A[i, col[t]] = 1.0
            if f in col:
                A[i, col[f]] = -1.0
        mse = {q: 0.0 for q in fixed_points if q in points}
        if unknowns:
            Qxx = np.linalg.inv(A.T @ (p[:, None] * A))
            mse.update({q: float(sigma0 * np.sqrt(Qxx[col[q], col[q]])) for q in unknowns})

        approx = {q: a.height for q, a in approximate_heights(observations, fixed_points,
                                                             known_heights).items()}
        total_km = sum(_obs_fields(o)[3] for o in observations) / 1000.0
        total_diff_mm, kc = _k_coefficient(v, total_km)
        residuals, table = {}, []
        for i, o in enumerate(observations):
            f, t, dh, d, *_ = _obs_fields(o)
            residuals[f"{f}-{t}"] = float(v[i] * 1000.0)
            table.append({"from": f, "to": t, "measured_dh": dh, "distance": d,
                          "weight": float(p[i]), "residual_mm": float(v[i] * 1000.0),
                          "adjusted_dh": float(L_adj[i]),
                          "n_runs": getattr(o, "n_runs", 1),
                          "num_setups": getattr(o, "num_setups", 0),
                          "source": getattr(o, "source_file", "")})
        logger.info("Conditional LSA: n=%d r=%d sigma0=%.3f mm/sqrt(km)",
                    n, r, sigma0 * 1000)
        return AdjustmentResult(
            iteration=1, mse_unit_weight=sigma0, adjusted_heights=heights,
            residuals=residuals, mse_heights=mse, total_distance_km=total_km,
            total_diff_mm=total_diff_mm, k_coefficient=kc,
            method="conditional", approximate_heights=approx,
            corrections={q: heights[q] - approx.get(q, heights[q]) for q in unknowns},
            fixed_points=dict(fixed_points), degrees_of_freedom=r,
            observations=table,
            benchmark_misclosures=_benchmark_misclosures(
                observations, fixed_points, self.leveling_class),
            weight_mode=self.weight_mode,
        )

    def adjust_loops(self, lines: List[LevelingLine], loops=None,
                     fixed_points: Optional[Dict[str, float]] = None) -> AdjustmentResult:
        """Backward-compatible entry point: conditions are now always built
        internally from an independent loop basis (*loops* is ignored)."""
        return self.adjust(lines_to_observations(lines), fixed_points or {})


def simple_adjustment(observations: List[Dict], fixed_points: Dict[str, float]) -> Dict[str, float]:
    summaries = [MeasurementSummary(
        from_point=o["from_point"], to_point=o["to_point"], height_diff=o["height_diff"],
        distance=o["distance"], num_setups=o.get("num_setups", 1), bf_diff=0.0,
        year_month="", source_file="") for o in observations]
    return LeastSquaresAdjuster().adjust(summaries, fixed_points).adjusted_heights
