# Least Squares Adjustment (LSA) Guide

**Geodetic Tool v1.1 - LSA Implementation**
**Date:** January 5, 2026
**Status:** ✅ FULLY IMPLEMENTED

---

## 📋 Overview

This guide covers the Least Squares Adjustment (LSA) implementations in the Geodetic Leveling Tool. The tool now supports both major adjustment methods used in geodetic surveying:

1. **Parametric Adjustment (Ax+L)** - Observation Equation Method
2. **Conditional Adjustment (Bv+W)** - Condition Equation Method

Both methods include:
- Matrix stability checking
- Ill-conditioned matrix warnings
- Singular matrix detection
- Residual analysis and visualization
- Statistical quality metrics

---

## 🎯 Adjustment Methods

### 1. Parametric Adjustment (Ax+L)

**Mathematical Model:**
```
V = A*X - L

Where:
  V = residuals vector (n_obs x 1)
  A = design matrix (n_obs x n_unknowns)
  X = corrections to approximate parameters (n_unknowns x 1)
  L = observed minus computed values (n_obs x 1)

Normal Equations:
  N*X = U
  where N = A^T * P * A
        U = A^T * P * L
        P = weight matrix (diagonal, weights = 1/distance_km)
```

**Use Cases:**
- Networks with many unknown points
- General leveling network adjustment
- When you want to estimate point heights directly

**Implementation:** `LeastSquaresAdjuster` class

---

### 2. Conditional Adjustment (Bv+W)

**Mathematical Model:**
```
Bv + w = 0

Where:
  v = residuals vector (n_obs x 1)
  B = condition matrix (n_conditions x n_obs)
  w = misclosure vector (n_conditions x 1)

Normal Equations:
  N*k = u
  where N = B * P^-1 * B^T
        u = -w
        k = correlates (Lagrange multipliers)

Residuals:
  v = -P^-1 * B^T * k
```

**Use Cases:**
- Loop networks (each loop provides one condition)
- Networks with natural geometric conditions
- When conditions are simpler to formulate than parameters

**Implementation:** `ConditionalAdjuster` class

---

## 🔧 Module Structure

### New Files

```
geodetic_tool/engine/
├── ADJwarnings.py              # Custom warnings and errors
├── adjustment_computations.py  # Core computation engine
└── least_squares.py            # Enhanced with both methods
```

### Key Classes

#### `ADJwarnings.py`
- `IllConditionedMatrixWarning` - Warns of numerical instability
- `SingularMatrixError` - Raised when matrix is non-invertible
- `InsufficientObservationsError` - Not enough observations
- `ConvergenceError` - Iterative adjustment failed to converge
- `WeightMatrixError` - Invalid weight matrix
- `InvalidNetworkError` - Network configuration issues

#### `AdjustmentComputations`
Core computation engine with:
- `check_matrix_stability()` - Analyzes matrix condition
- `run_linear_adjustment()` - Executes Ax+L adjustment
- `run_conditional_adjustment()` - Executes Bv+W adjustment
- `plot_residuals()` - Visualizes residual distribution

#### `LeastSquaresAdjuster`
Enhanced parametric adjuster:
- Iterative adjustment with convergence checking
- Matrix stability checking (optional)
- Distance-based weighting
- M.S.E. and K coefficient calculation

#### `ConditionalAdjuster`
New conditional adjuster:
- Loop-based adjustment
- Non-iterative solution
- Automatic height calculation from adjusted observations

---

## 📚 Usage Examples

### Example 1: Parametric Adjustment (Ax+L)

```python
from geodetic_tool.engine import LeastSquaresAdjuster
from geodetic_tool.config.models import MeasurementSummary

# Define observations (measured height differences)
observations = [
    MeasurementSummary(
        from_point="BM1",
        to_point="BM2",
        height_diff=10.524,  # meters
        distance=1500.0,     # meters
        num_setups=3,
        bf_diff=0.0,
        year_month="0126",
        source_file="line1.DAT"
    ),
    MeasurementSummary(
        from_point="BM2",
        to_point="BM3",
        height_diff=-5.123,
        distance=2000.0,
        num_setups=4,
        bf_diff=0.0,
        year_month="0126",
        source_file="line2.DAT"
    ),
    # ... more observations
]

# Define fixed points (known benchmark heights)
fixed_points = {
    "BM1": 100.000  # meters above datum
}

# Create adjuster with stability checking
adjuster = LeastSquaresAdjuster(
    max_iterations=10,
    tolerance=1e-6,
    check_stability=True  # Enable matrix stability checks
)

# Perform adjustment
result = adjuster.adjust(observations, fixed_points)

# Access results
print(f"Adjusted height of BM2: {result.adjusted_heights['BM2']:.4f} m")
print(f"Adjusted height of BM3: {result.adjusted_heights['BM3']:.4f} m")
print(f"M.S.E. of unit weight: {result.mse_unit_weight:.4f}")
print(f"K coefficient: {result.k_coefficient:.2f} mm/√km")
print(f"Converged in {result.iteration} iterations")

# Check residuals
for obs_id, residual_mm in result.residuals.items():
    print(f"Residual {obs_id}: {residual_mm:.2f} mm")
```

---

### Example 2: Adjustment from LevelingLine Objects

```python
from geodetic_tool.engine import LeastSquaresAdjuster
from geodetic_tool.config.project_manager import ProjectManager

# Load project
pm = ProjectManager()
project = pm.load_project("my_survey.json")

# Define fixed points
fixed_points = {
    "BM1": 100.000,
    "BM5": 105.500
}

# Adjust using only "used" lines
adjuster = LeastSquaresAdjuster()
result = adjuster.adjust_from_lines(
    project.get_used_lines(),  # Only lines with is_used=True
    fixed_points
)

# Print adjusted heights
for point_id, height in result.adjusted_heights.items():
    mse = result.mse_heights.get(point_id, 0)
    print(f"{point_id}: {height:.4f} m ± {mse:.4f} m")
```

---

### Example 3: Conditional Adjustment (Bv+W) for Loops

```python
from geodetic_tool.engine import ConditionalAdjuster, LoopAnalyzer
from geodetic_tool.config.project_manager import ProjectManager

# Load project
pm = ProjectManager()
project = pm.load_project("loop_network.json")

# Detect loops
analyzer = LoopAnalyzer(project.lines)
loops = analyzer.find_loops()

# Convert loops to line indices
loop_indices = []
for loop in loops:
    # Each loop contains LevelingLine objects
    # Get their indices in the project.lines list
    indices = [project.lines.index(line) for line in loop.lines]
    loop_indices.append(indices)

# Define fixed points
fixed_points = {
    "BM1": 100.000
}

# Create conditional adjuster
adjuster = ConditionalAdjuster(check_stability=True)

# Perform adjustment
result = adjuster.adjust_loops(
    project.get_used_lines(),
    loop_indices,
    fixed_points
)

# Results
print(f"Conditional adjustment completed")
print(f"Sigma_0: {result.mse_unit_weight:.4f}")
print(f"K coefficient: {result.k_coefficient:.2f} mm/√km")

for point_id, height in result.adjusted_heights.items():
    print(f"{point_id}: {height:.4f} m")
```

---

### Example 4: Advanced - Using AdjustmentComputations Directly

```python
from geodetic_tool.engine import AdjustmentComputations
import numpy as np

# Create computation engine
adj_comp = AdjustmentComputations(
    max_iterations=10,
    tolerance=1e-6,
    condition_number_threshold=1e10
)

# Example: 3 observations, 2 unknowns
# Observations: Height differences between points
# Unknowns: Heights of points 2 and 3 (point 1 is fixed)

# Design matrix A (3 obs x 2 unknowns)
# Row 1: dH(1->2) = H2 - H1, so coefficients are [1, 0] for [H2, H3]
# Row 2: dH(2->3) = H3 - H2, so coefficients are [-1, 1]
# Row 3: dH(1->3) = H3 - H1, so coefficients are [0, 1]
A = np.array([
    [ 1.0,  0.0],  # dH(1->2)
    [-1.0,  1.0],  # dH(2->3)
    [ 0.0,  1.0]   # dH(1->3)
])

# Observation vector L (observed minus approximate)
# Assuming H1=100m fixed, approximate H2=110m, H3=105m
# Observed: dH(1->2)=10.5m, dH(2->3)=-5.2m, dH(1->3)=5.3m
L = np.array([
    10.5 - (110 - 100),  # 10.5 - 10 = 0.5
    -5.2 - (105 - 110),  # -5.2 - (-5) = -0.2
     5.3 - (105 - 100)   # 5.3 - 5 = 0.3
])

# Weight matrix P (diagonal, weight = 1/distance_km)
# Distances: 1.5km, 2.0km, 3.5km
P = np.diag([1/1.5, 1/2.0, 1/3.5])

# Perform adjustment
result = adj_comp.run_linear_adjustment(A, L, P, check_stability=True)

# Results
X = result['X']  # Corrections to approximate values
V = result['V']  # Residuals
sigma_0 = result['sigma_0']
stability_info = result.get('stability_info', {})

print(f"Corrections: {X}")
print(f"Adjusted H2: {110 + X[0]:.4f} m")
print(f"Adjusted H3: {105 + X[1]:.4f} m")
print(f"Residuals (m): {V}")
print(f"Sigma_0: {sigma_0:.4f}")

if stability_info:
    print(f"\nMatrix Stability:")
    print(f"  Determinant: {stability_info.get('determinant', 'N/A'):.2e}")
    print(f"  Condition Number: {stability_info.get('condition_number', 'N/A'):.2e}")
    print(f"  Rank: {stability_info.get('rank', 'N/A')}")
    print(f"  Is Singular: {stability_info.get('is_singular', False)}")
    print(f"  Is Ill-Conditioned: {stability_info.get('is_ill_conditioned', False)}")
```

---

### Example 5: Residual Plotting

```python
from geodetic_tool.engine import LeastSquaresAdjuster, AdjustmentComputations
import numpy as np

# Perform adjustment (as in Example 1)
adjuster = LeastSquaresAdjuster()
result = adjuster.adjust(observations, fixed_points)

# Extract residuals as numpy array
residual_values = np.array(list(result.residuals.values())) / 1000  # mm to m
observation_ids = list(result.residuals.keys())

# Create computation engine for plotting
adj_comp = AdjustmentComputations()

# Plot residuals
adj_comp.plot_residuals(
    residuals=residual_values,
    observation_ids=observation_ids,
    title="Leveling Network Adjustment - Residuals",
    output_path="./residuals_plot.png"  # Optional: save to file
)

# Plot will show:
# 1. Bar chart of residuals (outliers in red)
# 2. Histogram of residual distribution
# 3. ±2σ reference lines
```

---

## ⚙️ Matrix Stability Checking

### Why It Matters

Matrix stability is critical for reliable adjustment results. An ill-conditioned or singular matrix can cause:
- Numerical instability in solutions
- Unreliable parameter estimates
- Large errors in adjusted values
- Convergence failures

### What Gets Checked

The `check_matrix_stability()` function analyzes:

1. **Determinant**: Near-zero values indicate singularity
2. **Condition Number**: High values (>1e10) indicate ill-conditioning
3. **Rank**: Lower than matrix size indicates rank deficiency
4. **Singularity**: Whether the matrix is invertible

### Stability Thresholds

```python
# Default thresholds
SINGULAR_THRESHOLD = 1e-15  # Determinant below this = singular
ILL_CONDITIONED_THRESHOLD = 1e10  # Condition number above this = ill-conditioned
```

### Handling Warnings and Errors

```python
from geodetic_tool.engine import (
    LeastSquaresAdjuster,
    SingularMatrixError,
    IllConditionedMatrixWarning
)
import warnings

try:
    # Enable stability checking
    adjuster = LeastSquaresAdjuster(check_stability=True)

    # Catch ill-conditioned warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = adjuster.adjust(observations, fixed_points)

        # Check for warnings
        for warning in w:
            if issubclass(warning.category, IllConditionedMatrixWarning):
                print(f"Warning: {warning.message}")
                print("Consider:")
                print("  - Checking for nearly dependent observations")
                print("  - Improving network geometry")
                print("  - Rescaling parameters")

except SingularMatrixError as e:
    print(f"Error: Singular matrix detected - {e}")
    print("Possible causes:")
    print("  - Linearly dependent observations")
    print("  - Insufficient constraints (no fixed points)")
    print("  - Disconnected network components")
    print("  - Free points with no observations")
```

---

## 📊 Quality Metrics

### Standard Error of Unit Weight (σ₀)

Indicates the quality of observations:

```
σ₀ = sqrt(V^T * P * V / df)

where df = n_observations - n_unknowns (degrees of freedom)
```

**Interpretation:**
- σ₀ ≈ 1: Observations match expected precision
- σ₀ < 1: Observations better than expected (weights too low)
- σ₀ > 1: Observations worse than expected (possible blunders)

### Classification Coefficient (K)

Measures leveling quality according to standards:

```
K = Σ|v| / sqrt(L)

where:
  v = residuals (mm)
  L = total distance (km)
```

**Classification (Israel Standards):**
- K < 0.2 mm/√km: Order 1 (highest precision)
- K < 0.5 mm/√km: Order 2
- K < 1.0 mm/√km: Order 3
- K < 2.0 mm/√km: Order 4

### Standard Errors of Heights

Standard error for each adjusted height:

```
σ_Hi = σ₀ * sqrt(Q_ii)

where Q_ii is the i-th diagonal element of the cofactor matrix Q = N^-1
```

---

## 🔍 Troubleshooting

### Issue: Singular Matrix Error

**Symptoms:**
```
SingularMatrixError: Normal Matrix N is singular with determinant 1.23e-17
```

**Causes & Solutions:**

1. **No fixed points**
   - Solution: Provide at least one fixed benchmark

2. **Disconnected network**
   - Solution: Ensure all points are connected by observations

3. **Linearly dependent observations**
   - Example: Three lines forming a dependent set
   - Solution: Remove redundant observations or check for data errors

4. **Free points**
   - Solution: All unknown points must have at least one observation

---

### Issue: Ill-Conditioned Matrix Warning

**Symptoms:**
```
IllConditionedMatrixWarning: Normal Matrix N is ill-conditioned with condition number 5.67e12
```

**Causes & Solutions:**

1. **Poor network geometry**
   - Solution: Improve network design with better distribution of observations

2. **Large scale differences**
   - Example: Mixing mm-level and m-level observations
   - Solution: Normalize or rescale observations

3. **Nearly dependent observations**
   - Solution: Check for duplicate or nearly duplicate lines

---

### Issue: Non-Convergence

**Symptoms:**
```
Adjustment completed but required all 10 iterations
```

**Causes & Solutions:**

1. **Poor approximate values**
   - Solution: Provide better initial approximations

2. **Blunders in data**
   - Solution: Check for outliers in observations

3. **Tight tolerance**
   - Solution: Relax convergence tolerance

---

## 🎮 Integration with GUI

The enhanced LSA features can be accessed through the GUI in future updates. For now, use them programmatically:

```python
from geodetic_tool.gui.app import GeodeticToolGUI
from geodetic_tool.engine import LeastSquaresAdjuster

# Example: Add adjustment to GUI workflow
class EnhancedGUI(GeodeticToolGUI):
    def _perform_network_adjustment(self):
        """Enhanced network adjustment with stability checking."""
        if not self.lines:
            return

        # Get fixed points from user
        fixed_points = self._get_fixed_points_dialog()

        # Create adjuster
        adjuster = LeastSquaresAdjuster(check_stability=True)

        try:
            # Perform adjustment
            result = adjuster.adjust_from_lines(
                self.lines,
                fixed_points
            )

            # Display results
            self._show_adjustment_results(result)

        except SingularMatrixError as e:
            self._show_error(f"Adjustment failed: {e}")
```

---

## 📝 API Reference

### LeastSquaresAdjuster

```python
class LeastSquaresAdjuster:
    def __init__(
        self,
        max_iterations: int = 10,
        tolerance: float = 1e-6,
        check_stability: bool = True
    )

    def adjust(
        self,
        observations: List[MeasurementSummary],
        fixed_points: Dict[str, float],
        approximate_heights: Optional[Dict[str, float]] = None
    ) -> AdjustmentResult

    def adjust_from_lines(
        self,
        lines: List[LevelingLine],
        fixed_points: Dict[str, float]
    ) -> AdjustmentResult
```

### ConditionalAdjuster

```python
class ConditionalAdjuster:
    def __init__(
        self,
        check_stability: bool = True,
        condition_number_threshold: float = 1e10
    )

    def adjust_loops(
        self,
        lines: List[LevelingLine],
        loops: List[List[int]],
        fixed_points: Optional[Dict[str, float]] = None
    ) -> AdjustmentResult
```

### AdjustmentComputations

```python
class AdjustmentComputations:
    def __init__(
        self,
        max_iterations: int = 10,
        tolerance: float = 1e-6,
        condition_number_threshold: float = 1e10
    )

    def check_matrix_stability(
        self,
        matrix: np.ndarray,
        matrix_name: str = "Matrix"
    ) -> Dict[str, float]

    def run_linear_adjustment(
        self,
        A: np.ndarray,
        L: np.ndarray,
        P: np.ndarray,
        check_stability: bool = True
    ) -> Dict[str, np.ndarray]

    def run_conditional_adjustment(
        self,
        B: np.ndarray,
        w: np.ndarray,
        P: np.ndarray,
        check_stability: bool = True
    ) -> Dict[str, np.ndarray]

    def plot_residuals(
        self,
        residuals: np.ndarray,
        observation_ids: Optional[List[str]] = None,
        title: str = "Residuals Plot",
        output_path: Optional[str] = None
    )
```

---

## ✅ Summary

The Geodetic Tool now includes comprehensive LSA capabilities:

**Features:**
- ✅ Parametric Adjustment (Ax+L)
- ✅ Conditional Adjustment (Bv+W)
- ✅ Matrix stability checking
- ✅ Ill-conditioned matrix warnings
- ✅ Singular matrix detection
- ✅ Residual analysis and plotting
- ✅ Quality metrics (σ₀, K coefficient)
- ✅ Standard errors of adjusted heights
- ✅ Integration with existing project system

**Backward Compatibility:**
- ✅ Existing `LeastSquaresAdjuster` API unchanged
- ✅ New features are opt-in (check_stability parameter)
- ✅ All existing code continues to work

**Ready for Production:**
- ✅ Comprehensive error handling
- ✅ Detailed warnings and diagnostics
- ✅ Professional-grade matrix analysis
- ✅ Industry-standard algorithms

---

**For more information:**
- [NEW_FEATURES_GUIDE.md](NEW_FEATURES_GUIDE.md) - Version 1.1 features
- [README.md](README.md) - Main documentation
- [QUICK_REFERENCE.md](QUICK_REFERENCE.md) - Quick reference
