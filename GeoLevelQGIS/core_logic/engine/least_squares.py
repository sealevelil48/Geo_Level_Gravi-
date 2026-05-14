"""
Least Squares Adjustment Module

Implements least squares adjustment for leveling networks.

Provides two adjustment methods:
1. Parametric (Observation Equation) Method: V = A*X - L
2. Conditional (Condition Equation) Method: Bv + w = 0

Parametric Method (Ax+L):
    V = A*X - L

    Where:
        V = residuals vector
        A = design matrix (coefficients)
        X = parameter vector (unknown heights)
        L = observation vector (measured height differences)

    Normal equations:
        N*X = U
        where N = A.T @ P @ A
              U = A.T @ P @ L
              P = weight matrix (diagonal, weights = 1/distance)

Conditional Method (Bv+W):
    Bv + w = 0

    Where:
        v = residuals vector
        B = condition matrix
        w = misclosure vector

    Normal equations:
        N*k = u
        where N = B * P^-1 * B^T
              u = -w
              k = correlates (Lagrange multipliers)
"""
from typing import List, Dict, Optional, Tuple
from pathlib import Path
import numpy as np
import logging

from core_logic.config.models import (
    LevelingLine, Benchmark, AdjustmentResult, MeasurementSummary
)
from core_logic.config.settings import calculate_tolerance
from core_logic.engine.adjustment_computations import AdjustmentComputations
from core_logic.engine.ADJwarnings import (
    SingularMatrixError,
    InsufficientObservationsError,
    ConvergenceError
)


logger = logging.getLogger(__name__)


class LeastSquaresAdjuster:
    """
    Least Squares Adjustment for leveling networks.

    Implements the parametric method (Ax+L) with distance-based weighting
    and matrix stability checking.
    """

    def __init__(
        self,
        max_iterations: int = 10,
        tolerance: float = 1e-6,
        check_stability: bool = True
    ):
        """
        Initialize the adjuster.

        Args:
            max_iterations: Maximum number of iterations
            tolerance: Convergence tolerance (meters)
            check_stability: Whether to perform matrix stability checks
        """
        self.max_iterations = max_iterations
        self.tolerance = tolerance
        self.check_stability = check_stability
        self.a0 = 0.001  # Reference standard error (mm/sqrt(km))
        self.adj_comp = AdjustmentComputations(
            max_iterations=max_iterations,
            tolerance=tolerance
        )
    
    def adjust(
        self,
        observations: List[MeasurementSummary],
        fixed_points: Dict[str, float],
        approximate_heights: Optional[Dict[str, float]] = None
    ) -> AdjustmentResult:
        """
        Perform least squares adjustment.
        
        Args:
            observations: List of measurement summaries (observed height diffs)
            fixed_points: Dictionary of fixed benchmark heights {point_id: height}
            approximate_heights: Initial approximate heights for unknown points
            
        Returns:
            AdjustmentResult with adjusted heights and statistics
        """
        if not observations:
            raise ValueError("No observations provided")
        
        if not fixed_points:
            raise ValueError("At least one fixed point required")
        
        # Get all unique points
        all_points = set()
        for obs in observations:
            all_points.add(obs.from_point)
            all_points.add(obs.to_point)
        
        # Separate fixed and unknown points
        fixed_ids = set(fixed_points.keys())
        unknown_ids = all_points - fixed_ids
        unknown_list = sorted(unknown_ids)
        
        n_obs = len(observations)
        n_unknowns = len(unknown_list)
        
        if n_unknowns == 0:
            raise ValueError("All points are fixed - nothing to adjust")
        
        # Create point index mapping
        point_index = {pid: i for i, pid in enumerate(unknown_list)}
        
        # Initialize approximate heights
        if approximate_heights is None:
            approximate_heights = {}
        
        # Estimate approximate heights for unknowns
        current_heights = dict(fixed_points)
        for pid in unknown_list:
            if pid in approximate_heights:
                current_heights[pid] = approximate_heights[pid]
            else:
                current_heights[pid] = 0.0  # Will be updated iteratively
        
        # Iterative adjustment
        for iteration in range(1, self.max_iterations + 1):
            # Build design matrix A (n_obs x n_unknowns)
            A = np.zeros((n_obs, n_unknowns))
            
            # Build observation vector L (observed - computed)
            L = np.zeros(n_obs)
            
            # Build weight matrix P (diagonal, weight = 1/distance in km)
            P = np.zeros((n_obs, n_obs))
            
            for i, obs in enumerate(observations):
                from_pt = obs.from_point
                to_pt = obs.to_point
                
                # Observed height difference
                observed_dh = obs.height_diff
                
                # Computed height difference
                computed_dh = current_heights.get(to_pt, 0) - current_heights.get(from_pt, 0)
                
                # Misclosure
                L[i] = observed_dh - computed_dh
                
                # Design matrix coefficients
                # dH = H_to - H_from
                # Partial derivative w.r.t. H_to = +1
                # Partial derivative w.r.t. H_from = -1
                
                if to_pt in point_index:
                    A[i, point_index[to_pt]] = 1.0
                
                if from_pt in point_index:
                    A[i, point_index[from_pt]] = -1.0
                
                # Weight (inverse of distance in km)
                dist_km = obs.distance / 1000.0
                if dist_km > 0:
                    P[i, i] = 1.0 / dist_km
                else:
                    P[i, i] = 1.0
            
            # Use AdjustmentComputations for solving with stability checking
            try:
                adj_result = self.adj_comp.run_linear_adjustment(
                    A, L, P, check_stability=self.check_stability
                )
                dX = adj_result['X']

                # Store additional info for final iteration
                if iteration == self.max_iterations or True:  # Always store for last
                    sigma_0_final = adj_result.get('sigma_0', None)
                    stability_info = adj_result.get('stability_info', None)

            except SingularMatrixError as e:
                logger.error(f"Singular matrix in iteration {iteration}: {e}")
                raise ValueError(f"Singular normal equation matrix: {e}")
            except Exception as e:
                logger.error(f"Error solving normal equations: {e}")
                raise
            
            # Apply corrections
            max_correction = 0.0
            for j, pid in enumerate(unknown_list):
                correction = dX[j]
                current_heights[pid] += correction
                max_correction = max(max_correction, abs(correction))
            
            # Check convergence
            if max_correction < self.tolerance:
                logger.info(f"Converged after {iteration} iterations")
                break
        
        # Calculate residuals
        V = A @ dX - L
        
        # Calculate M.S.E. of unit weight
        degrees_of_freedom = n_obs - n_unknowns
        if degrees_of_freedom > 0:
            vtpv = V.T @ P @ V
            mse_unit_weight = np.sqrt(vtpv / degrees_of_freedom)
        else:
            mse_unit_weight = 0.0
        
        # Calculate M.S.E. of adjusted heights
        try:
            N = A.T @ P @ A  # recompute N for Qxx (same as inside loop)
            Qxx = np.linalg.inv(N)
            mse_heights = {}
            for j, pid in enumerate(unknown_list):
                mse_heights[pid] = mse_unit_weight * np.sqrt(Qxx[j, j])
        except Exception:
            mse_heights = {pid: 0.0 for pid in unknown_list}
        
        # Calculate classification coefficient K
        total_dist_km = sum(obs.distance for obs in observations) / 1000.0
        total_diff_mm = sum(abs(V[i]) * 1000 for i in range(n_obs))
        
        if total_dist_km > 0:
            k_coefficient = total_diff_mm / np.sqrt(total_dist_km)
        else:
            k_coefficient = 0.0
        
        # Build residuals dictionary
        residuals = {}
        for i, obs in enumerate(observations):
            key = f"{obs.from_point}-{obs.to_point}"
            residuals[key] = V[i] * 1000  # Convert to mm
        
        # Build result
        result = AdjustmentResult(
            iteration=iteration,
            mse_unit_weight=mse_unit_weight,
            adjusted_heights=current_heights,
            residuals=residuals,
            mse_heights=mse_heights,
            total_distance_km=total_dist_km,
            total_diff_mm=total_diff_mm,
            k_coefficient=k_coefficient
        )
        
        return result
    
    def adjust_from_lines(
        self,
        lines: List[LevelingLine],
        fixed_points: Dict[str, float]
    ) -> AdjustmentResult:
        """
        Perform adjustment directly from LevelingLine objects.
        
        Args:
            lines: List of LevelingLine objects
            fixed_points: Dictionary of fixed benchmark heights
            
        Returns:
            AdjustmentResult
        """
        # Convert lines to observations
        observations = []
        for line in lines:
            obs = MeasurementSummary(
                from_point=line.start_point,
                to_point=line.end_point,
                height_diff=line.total_height_diff,
                distance=line.total_distance,
                num_setups=line.num_setups,
                bf_diff=0.0,
                year_month="",
                source_file=line.filename
            )
            observations.append(obs)
        
        return self.adjust(observations, fixed_points)


class ConditionalAdjuster:
    """
    Conditional Adjustment (Bv + W = 0) for leveling networks.

    Conditions = independent loops + independent paths between fixed benchmarks.
    n_conditions = n_loops + (n_BM - 1)

    Steps:
        1. Build weight matrix P (diagonal, P[i,i] = 1/dist_km)
        2. Build condition matrix B (+1/-1/0) and misclosure vector W
        3. M = B * P^-1 * B^T
        4. v = -P^-1 * B^T * M^-1 * W
        5. L_adjusted = L + v
        6. sigma_0^2 = v^T P v / r
        7. Propagate adjusted dH from fixed points to get all heights
    """

    def __init__(self, check_stability: bool = True,
                 condition_number_threshold: float = 1e10):
        self.check_stability = check_stability
        self.adj_comp = AdjustmentComputations(
            condition_number_threshold=condition_number_threshold
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def adjust_loops(
        self,
        lines: List[LevelingLine],
        loops: List[List[int]],
        fixed_points: Optional[Dict[str, float]] = None
    ) -> AdjustmentResult:
        """
        Perform conditional adjustment.

        Args:
            lines       : list of LevelingLine objects (active lines only)
            loops       : list of loop index lists (from find_basis_loops)
            fixed_points: {point_id: height} for known benchmarks

        Returns:
            AdjustmentResult
        """
        n_obs = len(lines)
        fixed_points = fixed_points or {}

        # ── 1. Weight matrix P (diagonal, P[i,i] = 1/dist_km) ──────────
        P = np.zeros((n_obs, n_obs))
        for i, line in enumerate(lines):
            dist_km = line.total_distance / 1000.0
            P[i, i] = 1.0 / dist_km if dist_km > 0 else 1.0

        # ── 2. Build B and W ────────────────────────────────────────────
        # Condition rows from loops
        B_rows = []
        W_vals = []

        for loop_idx_list in loops:
            row = np.zeros(n_obs)
            w_val = 0.0
            for li in loop_idx_list:
                if 0 <= li < n_obs:
                    # Sign: +1 if line direction matches loop traversal,
                    # -1 if reversed.  We store the sign in the loop list
                    # as a signed index (negative = reversed).
                    if isinstance(li, tuple):
                        idx, sign = li
                    else:
                        idx, sign = li, 1
                    row[idx] = float(sign)
                    w_val += sign * lines[idx].total_height_diff
            B_rows.append(row)
            W_vals.append(w_val)

        # Condition rows from paths between fixed benchmarks
        bm_ids = sorted(fixed_points.keys())
        path_conditions = self._build_path_conditions(
            lines, bm_ids, fixed_points, n_obs
        )
        for row, w_val in path_conditions:
            B_rows.append(row)
            W_vals.append(w_val)

        if not B_rows:
            raise InsufficientObservationsError(
                "No conditions could be formed (no loops and no BM paths)."
            )

        B = np.array(B_rows)          # (r x n_obs)
        W = np.array(W_vals)          # (r,)
        r = len(B_rows)               # redundancy / degrees of freedom

        if r >= n_obs:
            raise InsufficientObservationsError(
                "Conditions (%d) >= observations (%d). System is over-determined."
                % (r, n_obs)
            )

        # ── 3. Core math: M = B P^-1 B^T ───────────────────────────────
        P_inv = np.diag(1.0 / np.diag(P))   # diagonal inverse
        M = B @ P_inv @ B.T                  # (r x r)

        if self.check_stability:
            self.adj_comp.check_matrix_stability(M, "Condition Normal Matrix M")

        try:
            M_inv = np.linalg.inv(M)
        except np.linalg.LinAlgError as exc:
            raise SingularMatrixError("Condition matrix M is singular: " + str(exc))

        # ── 4. Residuals: v = -P^-1 B^T M^-1 W ────────────────────────
        k = M_inv @ W                        # correlates (r,)
        v = -P_inv @ B.T @ k                 # observation residuals (n_obs,)

        # ── 5. Adjusted height differences ─────────────────────────────
        L_adj = np.array([ln.total_height_diff for ln in lines]) + v

        # ── 6. sigma_0^2 = v^T P v / r ─────────────────────────────────
        vtPv = float(v @ P @ v)
        sigma_0_sq = vtPv / r if r > 0 else 0.0
        sigma_0 = float(np.sqrt(max(sigma_0_sq, 0.0)))

        # ── 7. Propagate heights from fixed points ──────────────────────
        adjusted_heights = self._propagate_heights(
            lines, L_adj, fixed_points
        )

        # ── Build result ────────────────────────────────────────────────
        residuals = {}
        for i, line in enumerate(lines):
            key = line.start_point + "-" + line.end_point
            residuals[key] = float(v[i]) * 1000.0   # mm

        total_dist_km = sum(ln.total_distance for ln in lines) / 1000.0
        total_diff_mm = sum(abs(float(v[i])) * 1000.0 for i in range(n_obs))
        k_coeff = (total_diff_mm / np.sqrt(total_dist_km)
                   if total_dist_km > 0 else 0.0)

        return AdjustmentResult(
            iteration=1,
            mse_unit_weight=sigma_0,
            adjusted_heights=adjusted_heights,
            residuals=residuals,
            mse_heights={},
            total_distance_km=total_dist_km,
            total_diff_mm=total_diff_mm,
            k_coefficient=float(k_coeff)
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _build_path_conditions(
        self,
        lines: List[LevelingLine],
        bm_ids: List[str],
        fixed_points: Dict[str, float],
        n_obs: int
    ) -> List[Tuple]:
        """
        Build condition rows for independent paths between fixed benchmarks.
        For each pair of adjacent fixed BMs connected through the network,
        the condition is: sum(signed dH along path) - (H_end - H_start) = 0
        Returns list of (row_array, w_value) tuples.
        """
        if len(bm_ids) < 2:
            return []

        # Build adjacency: point -> list of (neighbor, line_index, sign)
        adj = {}
        for i, line in enumerate(lines):
            s, e = line.start_point, line.end_point
            adj.setdefault(s, []).append((e, i, +1))
            adj.setdefault(e, []).append((s, i, -1))

        conditions = []
        visited_pairs = set()

        for bm_start in bm_ids:
            # BFS to find shortest path to each other BM
            from collections import deque
            queue = deque([(bm_start, [], [])])  # (current, path_indices, path_signs)
            seen = {bm_start}
            while queue:
                node, p_idx, p_sgn = queue.popleft()
                for neighbor, li, sign in adj.get(node, []):
                    if li in p_idx:   # don't reuse lines
                        continue
                    new_idx  = p_idx  + [li]
                    new_sgn  = p_sgn  + [sign]
                    if neighbor in fixed_points and neighbor != bm_start:
                        pair_key = tuple(sorted([bm_start, neighbor]))
                        if pair_key not in visited_pairs:
                            visited_pairs.add(pair_key)
                            row = np.zeros(n_obs)
                            w_val = 0.0
                            for idx, sg in zip(new_idx, new_sgn):
                                row[idx] = float(sg)
                                w_val += sg * lines[idx].total_height_diff
                            # Subtract known height difference
                            w_val -= (fixed_points[neighbor] - fixed_points[bm_start])
                            conditions.append((row, w_val))
                    elif neighbor not in seen:
                        seen.add(neighbor)
                        queue.append((neighbor, new_idx, new_sgn))

        return conditions

    def _propagate_heights(
        self,
        lines: List[LevelingLine],
        L_adj: np.ndarray,
        fixed_points: Dict[str, float]
    ) -> Dict[str, float]:
        """
        Propagate adjusted height differences from fixed points
        to compute all unknown point heights.
        Uses BFS from each fixed point.
        """
        heights = dict(fixed_points)
        # Build adjacency with adjusted dH
        adj = {}
        for i, line in enumerate(lines):
            s, e = line.start_point, line.end_point
            dh = float(L_adj[i])
            adj.setdefault(s, []).append((e,  dh))
            adj.setdefault(e, []).append((s, -dh))

        from collections import deque
        queue = deque(fixed_points.keys())
        while queue:
            node = queue.popleft()
            for neighbor, dh in adj.get(node, []):
                if neighbor not in heights:
                    heights[neighbor] = heights[node] + dh
                    queue.append(neighbor)

        return heights

    # Keep backward-compat helper used by _calculate_heights_from_adjusted
    def _calculate_heights_from_adjusted(
        self,
        adjusted_obs: List[Dict],
        fixed_points: Dict[str, float]
    ) -> Dict[str, float]:
        summaries = []
        for obs in adjusted_obs:
            summary = MeasurementSummary(
                from_point=obs['from_point'],
                to_point=obs['to_point'],
                height_diff=obs['adjusted_dh'],
                distance=obs['distance'],
                num_setups=1,
                bf_diff=0.0,
                year_month="",
                source_file=""
            )
            summaries.append(summary)
        adjuster = LeastSquaresAdjuster(check_stability=False)
        result = adjuster.adjust(summaries, fixed_points)
        return result.adjusted_heights


def simple_adjustment(
    observations: List[Dict],
    fixed_points: Dict[str, float]
) -> Dict[str, float]:
    """
    Simple convenience function for least squares adjustment.

    Args:
        observations: List of dicts with keys: from_point, to_point, height_diff, distance
        fixed_points: Dictionary of fixed heights

    Returns:
        Dictionary of adjusted heights
    """
    # Convert to MeasurementSummary
    summaries = []
    for obs in observations:
        summary = MeasurementSummary(
            from_point=obs['from_point'],
            to_point=obs['to_point'],
            height_diff=obs['height_diff'],
            distance=obs['distance'],
            num_setups=obs.get('num_setups', 1),
            bf_diff=0.0,
            year_month="",
            source_file=""
        )
        summaries.append(summary)

    adjuster = LeastSquaresAdjuster()
    result = adjuster.adjust(summaries, fixed_points)

    return result.adjusted_heights
