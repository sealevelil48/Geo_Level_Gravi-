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

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from ..config.models import (
    LevelingLine, Benchmark, AdjustmentResult, MeasurementSummary
)
from ..config.settings import calculate_tolerance
from .adjustment_computations import AdjustmentComputations
from .ADJwarnings import (
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
            Qxx = np.linalg.inv(N)
            mse_heights = {}
            for j, pid in enumerate(unknown_list):
                mse_heights[pid] = mse_unit_weight * np.sqrt(Qxx[j, j])
        except:
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
    Conditional Adjustment (Bv+W method) for leveling networks.

    Implements the conditional equation method where:
        Bv + w = 0

    This method is suitable for:
    - Loop networks (each loop provides one condition)
    - Networks with redundant observations
    - Cases where conditions are more natural than parameters

    The adjustment minimizes v^T * P * v subject to the conditions.
    """

    def __init__(
        self,
        check_stability: bool = True,
        condition_number_threshold: float = 1e10
    ):
        """
        Initialize conditional adjuster.

        Args:
            check_stability: Whether to perform matrix stability checks
            condition_number_threshold: Threshold for ill-conditioned warning
        """
        self.check_stability = check_stability
        self.adj_comp = AdjustmentComputations(
            condition_number_threshold=condition_number_threshold
        )

    def adjust_loops(
        self,
        lines: List[LevelingLine],
        loops: List[List[int]],
        fixed_points: Optional[Dict[str, float]] = None
    ) -> AdjustmentResult:
        """
        Perform conditional adjustment on a loop network.

        Args:
            lines: List of LevelingLine objects
            loops: List of loops, where each loop is a list of line indices
            fixed_points: Optional dictionary of fixed benchmark heights

        Returns:
            AdjustmentResult with adjusted heights and statistics
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

        n_obs = len(observations)
        n_conditions = len(loops)

        # Build weight matrix P (diagonal, weight = 1/distance in km)
        P = np.zeros((n_obs, n_obs))
        for i, obs in enumerate(observations):
            dist_km = obs.distance / 1000.0
            if dist_km > 0:
                P[i, i] = 1.0 / dist_km
            else:
                P[i, i] = 1.0

        # Build condition matrix B and misclosure vector w
        B = np.zeros((n_conditions, n_obs))
        w = np.zeros(n_conditions)

        for loop_idx, loop in enumerate(loops):
            loop_misclosure = 0.0
            for line_idx in loop:
                if 0 <= line_idx < n_obs:
                    # Coefficient is +1 or -1 depending on direction
                    # For now, assume all positive (can be enhanced)
                    B[loop_idx, line_idx] = 1.0
                    loop_misclosure += observations[line_idx].height_diff

            w[loop_idx] = loop_misclosure

        # Perform conditional adjustment
        try:
            adj_result = self.adj_comp.run_conditional_adjustment(
                B, w, P, check_stability=self.check_stability
            )

            v = adj_result['v']  # Residuals
            k = adj_result['k']  # Correlates
            sigma_0 = adj_result['sigma_0']

        except SingularMatrixError as e:
            logger.error(f"Singular matrix in conditional adjustment: {e}")
            raise ValueError(f"Cannot perform conditional adjustment: {e}")
        except InsufficientObservationsError as e:
            logger.error(f"Insufficient observations: {e}")
            raise ValueError(f"Insufficient observations for adjustment: {e}")

        # Calculate adjusted observations
        adjusted_obs = []
        for i, obs in enumerate(observations):
            adjusted_dh = obs.height_diff + v[i]
            adjusted_obs.append({
                'from_point': obs.from_point,
                'to_point': obs.to_point,
                'observed_dh': obs.height_diff,
                'adjusted_dh': adjusted_dh,
                'residual': v[i],
                'distance': obs.distance
            })

        # Calculate heights if fixed points provided
        if fixed_points:
            heights = self._calculate_heights_from_adjusted(
                adjusted_obs, fixed_points
            )
        else:
            heights = {}

        # Build residuals dictionary
        residuals = {}
        for i, obs in enumerate(observations):
            key = f"{obs.from_point}-{obs.to_point}"
            residuals[key] = v[i] * 1000  # Convert to mm

        # Calculate statistics
        total_dist_km = sum(obs.distance for obs in observations) / 1000.0
        total_diff_mm = sum(abs(v[i]) * 1000 for i in range(n_obs))

        if total_dist_km > 0:
            k_coefficient = total_diff_mm / np.sqrt(total_dist_km)
        else:
            k_coefficient = 0.0

        # Build result
        result = AdjustmentResult(
            iteration=1,  # Conditional adjustment is non-iterative
            mse_unit_weight=sigma_0,
            adjusted_heights=heights,
            residuals=residuals,
            mse_heights={},  # Can be calculated if needed
            total_distance_km=total_dist_km,
            total_diff_mm=total_diff_mm,
            k_coefficient=k_coefficient
        )

        return result

    def _calculate_heights_from_adjusted(
        self,
        adjusted_obs: List[Dict],
        fixed_points: Dict[str, float]
    ) -> Dict[str, float]:
        """
        Calculate heights from adjusted observations using least squares.

        After conditional adjustment adjusts the observations,
        we can calculate heights using the adjusted observations.
        """
        # Use parametric adjuster with adjusted observations
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
