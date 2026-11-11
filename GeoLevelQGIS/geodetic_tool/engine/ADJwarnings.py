"""
ADJ Warnings Module

Custom warnings and errors for adjustment computations.
Provides specialized error classes for matrix operations and adjustment issues.
"""


class IllConditionedMatrixWarning(UserWarning):
    """
    Warning raised when a matrix is ill-conditioned.

    An ill-conditioned matrix has a large condition number, which can lead to
    numerical instability in solutions. This typically indicates:
    - Nearly dependent equations
    - Poor scaling of parameters
    - Network geometry issues
    """
    pass


class SingularMatrixError(Exception):
    """
    Error raised when a matrix is singular (non-invertible).

    A singular matrix indicates:
    - Linearly dependent equations
    - Insufficient constraints
    - Rank deficiency in the design matrix
    - Network configuration issues (disconnected components, free points)
    """
    pass


class InsufficientObservationsError(Exception):
    """
    Error raised when there are insufficient observations for adjustment.

    The number of observations must be greater than the number of unknowns
    for the adjustment to be over-determined and provide redundancy.
    """
    pass


class ConvergenceError(Exception):
    """
    Error raised when iterative adjustment fails to converge.

    Convergence failure can indicate:
    - Poor initial approximations
    - Blunders in observations
    - Network configuration issues
    - Numerical instability
    """
    pass


class WeightMatrixError(Exception):
    """
    Error raised when weight matrix is invalid.

    Weight matrix must be:
    - Symmetric positive definite
    - All diagonal elements positive
    - Properly dimensioned
    """
    pass


class InvalidNetworkError(Exception):
    """
    Error raised when network configuration is invalid.

    Invalid network can result from:
    - Disconnected network components
    - No fixed points (datum defect)
    - Conflicting constraints
    """
    pass
