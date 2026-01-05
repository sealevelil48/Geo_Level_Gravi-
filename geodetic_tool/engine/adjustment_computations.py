"""
Adjustment Computations Module

Enhanced least squares adjustment with matrix stability checking,
residual analysis, and visualization capabilities.

Implements both:
- Parametric (Observation Equation) Method: V = A*X - L
- Conditional (Condition Equation) Method: Bv + w = 0
"""
from typing import List, Dict, Optional, Tuple
import numpy as np
import logging
import warnings
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from .ADJwarnings import (
    IllConditionedMatrixWarning,
    SingularMatrixError,
    InsufficientObservationsError,
    ConvergenceError,
    WeightMatrixError
)

logger = logging.getLogger(__name__)


class AdjustmentComputations:
    """
    Enhanced adjustment computations with stability checking and analysis.

    Provides robust implementations of:
    - Linear (Parametric) Adjustment: Ax + L = v
    - Conditional Adjustment: Bv + w = 0
    - Matrix stability analysis
    - Residual plotting and analysis
    """

    def __init__(
        self,
        max_iterations: int = 10,
        tolerance: float = 1e-6,
        condition_number_threshold: float = 1e10
    ):
        """
        Initialize adjustment computations.

        Args:
            max_iterations: Maximum iterations for iterative adjustment
            tolerance: Convergence tolerance (meters)
            condition_number_threshold: Threshold for ill-conditioned warning
        """
        self.max_iterations = max_iterations
        self.tolerance = tolerance
        self.condition_number_threshold = condition_number_threshold
        self.logger = logging.getLogger(__name__)

    def check_matrix_stability(
        self,
        matrix: np.ndarray,
        matrix_name: str = "Matrix"
    ) -> Dict[str, float]:
        """
        Check stability and conditioning of a matrix.

        Args:
            matrix: Matrix to check
            matrix_name: Name for logging/error messages

        Returns:
            Dictionary with stability metrics:
            - condition_number: Condition number of the matrix
            - rank: Numerical rank
            - determinant: Determinant value
            - is_singular: Boolean indicating singularity
            - is_ill_conditioned: Boolean indicating ill-conditioning

        Raises:
            SingularMatrixError: If matrix is singular
            Warns:
                IllConditionedMatrixWarning: If matrix is ill-conditioned
        """
        stability_info = {}

        # Check if matrix is square
        if matrix.shape[0] != matrix.shape[1]:
            self.logger.warning(f"{matrix_name} is not square: {matrix.shape}")
            stability_info['is_square'] = False
            return stability_info

        stability_info['is_square'] = True

        # Calculate determinant
        try:
            det = np.linalg.det(matrix)
            stability_info['determinant'] = det

            # Check for singularity
            if abs(det) < 1e-15:
                stability_info['is_singular'] = True
                self.logger.error(f"{matrix_name} is singular (det={det:.2e})")
                raise SingularMatrixError(
                    f"{matrix_name} is singular with determinant {det:.2e}. "
                    "This indicates linearly dependent equations or insufficient constraints."
                )
            else:
                stability_info['is_singular'] = False
        except np.linalg.LinAlgError as e:
            self.logger.error(f"Failed to compute determinant of {matrix_name}: {e}")
            stability_info['is_singular'] = True
            raise SingularMatrixError(f"{matrix_name} is singular: {e}")

        # Calculate condition number
        try:
            cond = np.linalg.cond(matrix)
            stability_info['condition_number'] = cond

            # Check for ill-conditioning
            if cond > self.condition_number_threshold:
                stability_info['is_ill_conditioned'] = True
                warning_msg = (
                    f"{matrix_name} is ill-conditioned with condition number {cond:.2e}. "
                    f"Results may be numerically unstable. Consider:\n"
                    f"  - Checking for nearly dependent observations\n"
                    f"  - Improving network geometry\n"
                    f"  - Rescaling parameters"
                )
                self.logger.warning(warning_msg)
                warnings.warn(warning_msg, IllConditionedMatrixWarning)
            else:
                stability_info['is_ill_conditioned'] = False

        except np.linalg.LinAlgError as e:
            self.logger.error(f"Failed to compute condition number of {matrix_name}: {e}")
            stability_info['condition_number'] = np.inf
            stability_info['is_ill_conditioned'] = True

        # Calculate rank
        try:
            rank = np.linalg.matrix_rank(matrix)
            stability_info['rank'] = rank

            if rank < matrix.shape[0]:
                self.logger.warning(
                    f"{matrix_name} is rank deficient: rank={rank}, size={matrix.shape[0]}"
                )
        except Exception as e:
            self.logger.error(f"Failed to compute rank of {matrix_name}: {e}")

        # Log stability summary
        self.logger.info(f"{matrix_name} Stability Check:")
        self.logger.info(f"  Shape: {matrix.shape}")
        self.logger.info(f"  Determinant: {stability_info.get('determinant', 'N/A'):.2e}")
        self.logger.info(f"  Condition Number: {stability_info.get('condition_number', 'N/A'):.2e}")
        self.logger.info(f"  Rank: {stability_info.get('rank', 'N/A')}")

        return stability_info

    def run_linear_adjustment(
        self,
        A: np.ndarray,
        L: np.ndarray,
        P: np.ndarray,
        check_stability: bool = True
    ) -> Dict[str, np.ndarray]:
        """
        Run parametric (linear) adjustment: V = A*X - L

        Normal equations: N*X = U
        where N = A^T * P * A
              U = A^T * P * L

        Args:
            A: Design matrix (n_obs x n_unknowns)
            L: Observation vector (observed - approximate) (n_obs,)
            P: Weight matrix (n_obs x n_obs), diagonal
            check_stability: Whether to perform matrix stability checks

        Returns:
            Dictionary containing:
            - X: Adjusted parameters (corrections to approximate values)
            - V: Residuals vector
            - N: Normal equation matrix
            - Qxx: Cofactor matrix of parameters
            - sigma_0: Standard error of unit weight
            - stability_info: Matrix stability information (if checked)

        Raises:
            ValueError: If matrix dimensions incompatible
            InsufficientObservationsError: If not enough observations
            SingularMatrixError: If normal matrix is singular
        """
        # Validate dimensions
        n_obs, n_unknowns = A.shape

        if L.shape[0] != n_obs:
            raise ValueError(
                f"Dimension mismatch: A has {n_obs} rows but L has {L.shape[0]} elements"
            )

        if P.shape != (n_obs, n_obs):
            raise ValueError(
                f"Weight matrix P must be ({n_obs}, {n_obs}), got {P.shape}"
            )

        # Check for sufficient observations
        degrees_of_freedom = n_obs - n_unknowns
        if degrees_of_freedom <= 0:
            raise InsufficientObservationsError(
                f"Insufficient observations: {n_obs} observations for {n_unknowns} unknowns. "
                f"Need at least {n_unknowns + 1} observations."
            )

        self.logger.info(f"Linear Adjustment: {n_obs} obs, {n_unknowns} unknowns, {degrees_of_freedom} DOF")

        # Form normal equations
        N = A.T @ P @ A
        U = A.T @ P @ L

        # Check matrix stability if requested
        result = {}
        if check_stability:
            stability_info = self.check_matrix_stability(N, "Normal Matrix N")
            result['stability_info'] = stability_info

        # Solve normal equations
        try:
            X = np.linalg.solve(N, U)
            result['X'] = X
        except np.linalg.LinAlgError as e:
            self.logger.error(f"Failed to solve normal equations: {e}")
            raise SingularMatrixError(f"Cannot solve normal equations: {e}")

        # Calculate residuals
        V = A @ X - L
        result['V'] = V

        # Calculate variance factor (sigma_0^2)
        VtPV = V.T @ P @ V
        sigma_0_squared = VtPV / degrees_of_freedom
        sigma_0 = np.sqrt(sigma_0_squared)
        result['sigma_0'] = sigma_0
        result['sigma_0_squared'] = sigma_0_squared

        # Calculate cofactor matrix
        try:
            Qxx = np.linalg.inv(N)
            result['Qxx'] = Qxx
            result['N'] = N

            # Calculate standard errors of parameters
            std_errors = sigma_0 * np.sqrt(np.diag(Qxx))
            result['std_errors'] = std_errors

        except np.linalg.LinAlgError as e:
            self.logger.warning(f"Failed to invert N for cofactor matrix: {e}")
            result['Qxx'] = None
            result['std_errors'] = None

        # Log results
        self.logger.info(f"Adjustment completed:")
        self.logger.info(f"  Sigma_0: {sigma_0:.4f}")
        self.logger.info(f"  Max residual: {np.max(np.abs(V)):.4f}")
        self.logger.info(f"  RMS residual: {np.sqrt(np.mean(V**2)):.4f}")

        return result

    def run_conditional_adjustment(
        self,
        B: np.ndarray,
        w: np.ndarray,
        P: np.ndarray,
        check_stability: bool = True
    ) -> Dict[str, np.ndarray]:
        """
        Run conditional adjustment: Bv + w = 0

        Normal equations: N*k = u
        where N = B * P^-1 * B^T
              u = -w

        Residuals: v = -P^-1 * B^T * k

        Args:
            B: Condition matrix (n_conditions x n_obs)
            w: Misclosure vector (n_conditions,)
            P: Weight matrix (n_obs x n_obs), diagonal
            check_stability: Whether to perform matrix stability checks

        Returns:
            Dictionary containing:
            - k: Correlates (Lagrange multipliers)
            - v: Residuals vector
            - N: Normal equation matrix
            - sigma_0: Standard error of unit weight
            - stability_info: Matrix stability information (if checked)

        Raises:
            ValueError: If matrix dimensions incompatible
            InsufficientObservationsError: If system is under-determined
            SingularMatrixError: If normal matrix is singular
        """
        # Validate dimensions
        n_conditions, n_obs = B.shape

        if w.shape[0] != n_conditions:
            raise ValueError(
                f"Dimension mismatch: B has {n_conditions} rows but w has {w.shape[0]} elements"
            )

        if P.shape != (n_obs, n_obs):
            raise ValueError(
                f"Weight matrix P must be ({n_obs}, {n_obs}), got {P.shape}"
            )

        # Check for sufficient redundancy
        degrees_of_freedom = n_obs - n_conditions
        if degrees_of_freedom <= 0:
            raise InsufficientObservationsError(
                f"Insufficient observations: {n_obs} observations for {n_conditions} conditions. "
                f"System is under-determined."
            )

        self.logger.info(f"Conditional Adjustment: {n_obs} obs, {n_conditions} conditions, {degrees_of_freedom} DOF")

        # Form normal equations: N*k = u
        # N = B * P^-1 * B^T
        try:
            P_inv = np.linalg.inv(P)
        except np.linalg.LinAlgError:
            # If P is diagonal, just invert diagonal elements
            if np.count_nonzero(P - np.diag(np.diagonal(P))) == 0:
                P_inv = np.diag(1.0 / np.diagonal(P))
            else:
                raise WeightMatrixError("Weight matrix P is singular and non-diagonal")

        N = B @ P_inv @ B.T
        u = -w

        # Check matrix stability if requested
        result = {}
        if check_stability:
            stability_info = self.check_matrix_stability(N, "Normal Matrix N (Conditional)")
            result['stability_info'] = stability_info

        # Solve for correlates
        try:
            k = np.linalg.solve(N, u)
            result['k'] = k
        except np.linalg.LinAlgError as e:
            self.logger.error(f"Failed to solve normal equations: {e}")
            raise SingularMatrixError(f"Cannot solve conditional normal equations: {e}")

        # Calculate residuals
        v = -P_inv @ B.T @ k
        result['v'] = v

        # Calculate variance factor
        vtPv = v.T @ P @ v
        sigma_0_squared = vtPv / degrees_of_freedom
        sigma_0 = np.sqrt(sigma_0_squared)
        result['sigma_0'] = sigma_0
        result['sigma_0_squared'] = sigma_0_squared
        result['N'] = N

        # Log results
        self.logger.info(f"Conditional adjustment completed:")
        self.logger.info(f"  Sigma_0: {sigma_0:.4f}")
        self.logger.info(f"  Max residual: {np.max(np.abs(v)):.4f}")
        self.logger.info(f"  RMS residual: {np.sqrt(np.mean(v**2)):.4f}")

        return result

    def plot_residuals(
        self,
        residuals: np.ndarray,
        observation_ids: Optional[List[str]] = None,
        title: str = "Residuals Plot",
        output_path: Optional[str] = None
    ):
        """
        Plot residuals for visual analysis.

        Creates a bar chart of residuals to identify:
        - Outliers (large residuals)
        - Systematic patterns
        - Distribution characteristics

        Args:
            residuals: Array of residuals
            observation_ids: Optional labels for observations
            title: Plot title
            output_path: Optional path to save the plot
        """
        try:
            import matplotlib.pyplot as plt
        except ImportError:
            self.logger.warning("matplotlib not available - cannot plot residuals")
            return

        n_obs = len(residuals)
        if observation_ids is None:
            observation_ids = [f"Obs {i+1}" for i in range(n_obs)]

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))

        # Bar plot of residuals
        colors = ['red' if abs(v) > 2*np.std(residuals) else 'blue' for v in residuals]
        ax1.bar(range(n_obs), residuals * 1000, color=colors, alpha=0.7)
        ax1.axhline(y=0, color='black', linestyle='-', linewidth=0.8)
        ax1.axhline(y=2*np.std(residuals)*1000, color='red', linestyle='--', linewidth=0.8, label='±2σ')
        ax1.axhline(y=-2*np.std(residuals)*1000, color='red', linestyle='--', linewidth=0.8)
        ax1.set_xlabel('Observation')
        ax1.set_ylabel('Residual (mm)')
        ax1.set_title(f'{title} - Residual Distribution')
        ax1.legend()
        ax1.grid(True, alpha=0.3)

        # Histogram
        ax2.hist(residuals * 1000, bins=20, alpha=0.7, color='blue', edgecolor='black')
        ax2.axvline(x=0, color='red', linestyle='--', linewidth=1.5, label='Zero')
        ax2.set_xlabel('Residual (mm)')
        ax2.set_ylabel('Frequency')
        ax2.set_title('Residual Histogram')
        ax2.legend()
        ax2.grid(True, alpha=0.3)

        plt.tight_layout()

        if output_path:
            plt.savefig(output_path, dpi=300, bbox_inches='tight')
            self.logger.info(f"Residuals plot saved to {output_path}")
        else:
            plt.show()

        plt.close()
