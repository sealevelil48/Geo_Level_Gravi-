# Technical Answers for Management
## Geodetic Tool - Analysis & Network Adjustment Logic

**Date:** February 9, 2026
**Prepared for:** Management Review
**Subject:** Technical Explanation of Validation Analysis and Network Adjustment Calculations

---

## Question 1: Validation Analysis Section - Logic and "Setup" Explanation

### What is a "Setup"?

A **setup** (also called a "station setup") is a single measurement position where the leveling instrument is placed. At each setup, the surveyor:

1. **Backsight Reading (Rb)**: Reads the leveling rod on the point behind (where we came from)
2. **Foresight Reading (Rf)**: Reads the leveling rod on the point ahead (where we're going)
3. **Measures Distances**: Records horizontal distances to both rods
4. **Calculates Height Difference**: dH = Rb - Rf

**Code Reference:**
```
File: geodetic_tool/config/models.py
Lines: 32-50

@dataclass
class StationSetup:
    """Single station setup in a leveling measurement."""
    setup_number: int
    from_point: str
    to_point: str
    backsight_reading: float      # Rb in meters
    foresight_reading: float      # Rf in meters
    distance_back: float          # HD to backsight in meters
    distance_fore: float          # HD to foresight in meters
    temperature: Optional[float] = None
    height_diff: Optional[float] = None  # dH = Rb - Rf
    cumulative_height: Optional[float] = None
```

### Example:
If you're measuring from Benchmark A to Benchmark B with 3 instrument positions along the way:
- **Setup 1**: Instrument at position 1, read back to A, read forward to TP1
- **Setup 2**: Instrument at position 2, read back to TP1, read forward to TP2
- **Setup 3**: Instrument at position 3, read back to TP2, read forward to B

This line has **3 setups**.

---

### How We Get the Analysis Section Data in "Validate All" Table

The validation analysis table displays comprehensive information about each leveling line. Here's the logic:

#### **Code Reference:**
```
File: geodetic_tool/gui/app.py
Lines: 3365-3459 (method: _validate_all)
```

#### **Step-by-Step Logic:**

**1. Dynamic Distance Unit Selection (Lines 3376-3381)**
```python
# Calculate average distance across all lines
avg_distance = sum(line.total_distance for line in self.lines) / len(self.lines)

# If average > 1000m, use kilometers; otherwise meters
use_km = avg_distance > 1000.0
distance_unit = "km" if use_km else "m"

# Update column header dynamically
self.validation_tree.heading('Distance', text=f'Distance [{distance_unit}]')
```

**2. Double-Run Detection (Lines 3383-3394)**
```python
# Detect pairs of lines measured in forward and return directions
double_run_pairs = detect_double_runs(self.lines)
double_run_map = {}

# For each pair, calculate the misclosure (difference between forward and return)
for fwd, ret in double_run_pairs:
    result = analyzer.analyze_double_run(fwd, ret)
    if result['valid']:
        delta_h_mm = result['misclosure_mm']  # Difference in millimeters
        double_run_map[id(fwd)] = {'pair': ret, 'delta_h': delta_h_mm}
        double_run_map[id(ret)] = {'pair': fwd, 'delta_h': delta_h_mm}
```

**3. Batch Validation (Lines 3396-3398)**
```python
# Create validator with user's default class (H1-H6)
validator = BatchValidator()

# Validate all lines according to Israeli Survey Regulations
results = validator.validate_batch(self.lines)
```

**4. Status Classification (Lines 3400-3424)**
```python
for line, result in results:
    if result.is_valid:
        status_text = "✓ PASS"
        status_detail = "All checks passed"
    else:
        # Prioritize failure reasons:
        if not result.endpoint_valid:
            status_text = "✗ FAIL: Endpoint"
            status_detail = "Invalid endpoint (turning point or numeric)"
        elif not result.naming_valid:
            status_text = "✗ FAIL: Naming"
            status_detail = "Front-to-back naming error"
        elif not result.tolerance_valid:
            status_text = "✗ FAIL: Tolerance"
            status_detail = f"Misclosure {line.misclosure:.2f}mm exceeds tolerance"
        elif not result.data_complete:
            status_text = "✗ FAIL: Incomplete"
            status_detail = "Missing data or insufficient setups"
```

**5. Table Row Creation (Lines 3444-3454)**
```python
self.validation_tree.insert('', tk.END, values=(
    line.filename,                      # File name
    line.start_point,                   # Starting benchmark
    line.end_point,                     # Ending benchmark
    len(line.setups),                   # Number of setups (COUNT)
    distance_str,                       # Total distance (km or m)
    f"{line.total_height_diff:.5f}",   # Total height difference (m)
    delta_h_str,                        # Δh (Measured) for double-runs (mm)
    status_text,                        # PASS/FAIL status
    status_detail                       # Detailed reason
))
```

#### **What Validation Checks Are Performed:**

**Code Reference:**
```
File: geodetic_tool/validators/__init__.py
Lines: 66-93 (method: validate)
```

**Checks performed:**
1. **Endpoint Validation** (Lines 95-114): Ensures line ends on a named benchmark, not a turning point
2. **Naming Convention** (Lines 116-147): Detects front-to-back errors in filename vs. data
3. **Data Completeness** (Lines 149-169): Verifies minimum setups and valid readings
4. **Line Length** (Lines 171-192): Checks against class limits (H1-H6 requirements)
5. **Sight Distances** (Lines 194-242): Validates backsight/foresight distances
6. **Measurement Method** (Lines 244-291): Ensures BF or BFFB matches class requirements
7. **Distance Balance** (Lines 293-338): Checks backsight/foresight balance
8. **Tolerance** (Lines 340-382): Validates misclosure against class formula

---

## Question 2: Enhanced Network Adjustment - Parametric & Conditional Calculations

### Overview

The Enhanced Network Adjustment implements two methods of Least Squares Adjustment (LSA):
1. **Parametric Method (Ax+L)** - Most commonly used
2. **Conditional Method (Bv+W)** - Used for loop networks

---

### Method 1: Parametric Adjustment (Observation Equation Method)

#### **Mathematical Formulation:**

**Observation Equation:**
```
V = A × X - L
```

Where:
- **V** = Residuals vector (corrections to observations)
- **A** = Design matrix (coefficients relating observations to unknowns)
- **X** = Parameters vector (unknown heights to be determined)
- **L** = Observation vector (observed - computed height differences)

**Normal Equations:**
```
N × X = U

where:
    N = A^T × P × A    (Normal matrix)
    U = A^T × P × L    (Right-hand side)
    P = Weight matrix (diagonal, weights = 1/distance_km)
```

**Solution:**
```
X = N^(-1) × U
V = A × X - L
```

#### **Code Reference:**
```
File: geodetic_tool/engine/least_squares.py
Lines: 93-268 (class: LeastSquaresAdjuster, method: adjust)
```

#### **Step-by-Step Implementation:**

**Step 1: Setup (Lines 110-146)**
```python
# Separate fixed and unknown points
all_points = set()
for obs in observations:
    all_points.add(obs.from_point)
    all_points.add(obs.to_point)

fixed_ids = set(fixed_points.keys())
unknown_ids = all_points - fixed_ids
unknown_list = sorted(unknown_ids)

n_obs = len(observations)          # Number of observations
n_unknowns = len(unknown_list)     # Number of unknown heights

# Create point index mapping
point_index = {pid: i for i, pid in enumerate(unknown_list)}
```

**Step 2: Iterative Adjustment Loop (Lines 149-219)**
```python
for iteration in range(1, self.max_iterations + 1):
    # Build design matrix A (n_obs × n_unknowns)
    A = np.zeros((n_obs, n_unknowns))

    # Build observation vector L
    L = np.zeros(n_obs)

    # Build weight matrix P (diagonal)
    P = np.zeros((n_obs, n_obs))

    for i, obs in enumerate(observations):
        # Observed height difference
        observed_dh = obs.height_diff

        # Computed height difference (from approximate heights)
        computed_dh = current_heights[to_pt] - current_heights[from_pt]

        # Misclosure (observed - computed)
        L[i] = observed_dh - computed_dh

        # Design matrix coefficients
        # Since: dH = H_to - H_from
        # ∂(dH)/∂(H_to) = +1
        # ∂(dH)/∂(H_from) = -1

        if to_pt in point_index:
            A[i, point_index[to_pt]] = +1.0

        if from_pt in point_index:
            A[i, point_index[from_pt]] = -1.0

        # Weight = 1/distance (in km)
        dist_km = obs.distance / 1000.0
        P[i, i] = 1.0 / dist_km if dist_km > 0 else 1.0
```

**Step 3: Solve Normal Equations (Lines 191-207)**

**Code Reference:**
```
File: geodetic_tool/engine/adjustment_computations.py
Lines: 162-250 (method: run_linear_adjustment)
```

```python
# Form normal equations
N = A^T @ P @ A        # Normal matrix (n_unknowns × n_unknowns)
U = A^T @ P @ L        # Right-hand side (n_unknowns,)

# Check matrix stability
stability_info = check_matrix_stability(N)

# Solve for corrections
X = np.linalg.solve(N, U)

# Calculate residuals
V = A @ X - L
```

**Step 4: Apply Corrections and Check Convergence (Lines 209-219)**
```python
# Apply corrections to heights
max_correction = 0.0
for j, pid in enumerate(unknown_list):
    correction = X[j]
    current_heights[pid] += correction
    max_correction = max(max_correction, abs(correction))

# Check convergence
if max_correction < tolerance:  # tolerance = 1e-6 meters
    break  # Converged!
```

**Step 5: Calculate Statistics (Lines 221-248)**
```python
# Degrees of freedom
df = n_obs - n_unknowns

# Mean square error of unit weight
vtpv = V^T @ P @ V
mse_unit_weight = sqrt(vtpv / df)

# Cofactor matrix
Qxx = inv(N)

# MSE of adjusted heights
for j, pid in enumerate(unknown_list):
    mse_heights[pid] = mse_unit_weight * sqrt(Qxx[j, j])

# Classification coefficient (Israeli standards)
K = Σ|V_i| / sqrt(Σdistances_km)
```

#### **Example Calculation:**

**Given:**
- Line 1: A → B, observed dH = +2.450 m, distance = 500 m
- Line 2: B → C, observed dH = -1.200 m, distance = 800 m
- Line 3: A → C, observed dH = +1.248 m, distance = 600 m
- Fixed: H_A = 100.000 m
- Unknowns: H_B, H_C

**Setup:**
```
n_obs = 3
n_unknowns = 2 (H_B, H_C)

X = [H_B]  (unknowns to solve)
    [H_C]
```

**Design Matrix A:**
```
Observation 1 (A→B): dH = H_B - H_A  →  [+1,  0]
Observation 2 (B→C): dH = H_C - H_B  →  [-1, +1]
Observation 3 (A→C): dH = H_C - H_A  →  [ 0, +1]

A = [+1,  0]
    [-1, +1]
    [ 0, +1]
```

**Weight Matrix P:**
```
P = diag(1/0.5, 1/0.8, 1/0.6)  # distances in km
  = diag(2.0, 1.25, 1.67)
```

**Observation Vector L (iteration 1, assuming H_B ≈ 102.5, H_C ≈ 101.2):**
```
L[0] = observed(A→B) - computed(A→B) = 2.450 - (102.5 - 100.0) = -0.050
L[1] = observed(B→C) - computed(B→C) = -1.200 - (101.2 - 102.5) = +0.100
L[2] = observed(A→C) - computed(A→C) = 1.248 - (101.2 - 100.0) = +0.048

L = [-0.050]
    [+0.100]
    [+0.048]
```

**Normal Equations:**
```
N = A^T × P × A
U = A^T × P × L

Solve: N × X = U  →  X (corrections to H_B, H_C)
```

---

### Method 2: Conditional Adjustment (Condition Equation Method)

#### **Mathematical Formulation:**

**Condition Equation:**
```
B × v + w = 0
```

Where:
- **v** = Residuals vector (corrections to observations)
- **B** = Condition matrix (defines geometric conditions, e.g., loop closure)
- **w** = Misclosure vector (closure errors)

**Normal Equations:**
```
N × k = u

where:
    N = B × P^(-1) × B^T    (Normal matrix)
    u = -w                   (Right-hand side)
    k = Correlates (Lagrange multipliers)
```

**Solution:**
```
k = N^(-1) × u
v = -P^(-1) × B^T × k
```

#### **Code Reference:**
```
File: geodetic_tool/engine/least_squares.py
Lines: 303-490 (class: ConditionalAdjuster, method: adjust_loops)
```

#### **When to Use:**
- **Loop networks**: Each closed loop provides one condition equation
- **Cases with natural conditions**: When conditions are more intuitive than parameters
- **Redundant observations**: When you want to adjust observations directly

#### **Step-by-Step Implementation:**

**Step 1: Build Condition Matrix (Lines 379-392)**
```python
n_obs = len(observations)
n_conditions = len(loops)  # Number of closed loops

# Build condition matrix B
B = np.zeros((n_conditions, n_obs))

# Build misclosure vector w
w = np.zeros(n_conditions)

for loop_idx, loop in enumerate(loops):
    loop_misclosure = 0.0
    for line_idx in loop:
        B[loop_idx, line_idx] = +1.0  # (or -1.0 depending on direction)
        loop_misclosure += observations[line_idx].height_diff

    w[loop_idx] = loop_misclosure
```

**Step 2: Solve Conditional Equations (Lines 395-410)**

**Code Reference:**
```
File: geodetic_tool/engine/adjustment_computations.py
Lines: 252-340 (method: run_conditional_adjustment)
```

```python
# Form normal equations
N = B @ inv(P) @ B^T
u = -w

# Solve for correlates
k = np.linalg.solve(N, u)

# Calculate residuals
v = -inv(P) @ B^T @ k

# Calculate adjusted observations
adjusted_dh = observed_dh + v
```

**Step 3: Calculate Heights from Adjusted Observations (Lines 461-490)**
```python
# Use parametric adjuster with adjusted observations
adjuster = LeastSquaresAdjuster()
result = adjuster.adjust(adjusted_observations, fixed_points)
```

#### **Example:**

**Given a closed loop:**
- Line 1: A → B, dH₁ = +2.450 m
- Line 2: B → C, dH₂ = -1.200 m
- Line 3: C → A, dH₃ = -1.252 m

**Condition (loop must close):**
```
dH₁ + dH₂ + dH₃ = 0

Actual: 2.450 + (-1.200) + (-1.252) = -0.002 m
Misclosure w = -0.002 m
```

**Condition Matrix:**
```
B = [1, 1, 1]  (all observations contribute to loop condition)

w = [-0.002]   (misclosure)
```

**Solution:**
```
Distribute the -0.002 m misclosure proportionally based on weights
```

---

## Summary Table: Parametric vs Conditional

| Aspect | Parametric (Ax+L) | Conditional (Bv+W) |
|--------|-------------------|-------------------|
| **Unknowns** | Heights (parameters) | Correlates (Lagrange multipliers) |
| **Best For** | General networks | Loop networks |
| **Equations** | V = A×X - L | B×v + w = 0 |
| **Normal Equations** | N×X = U where N=A^T PA | N×k = u where N=B P^(-1) B^T |
| **Output** | Adjusted heights directly | Adjusted observations → heights |
| **Code Location** | least_squares.py:62-301 | least_squares.py:303-490 |
| **Implementation** | LeastSquaresAdjuster | ConditionalAdjuster |

---

## Key Files Reference Summary

| Question | File | Lines | Purpose |
|----------|------|-------|---------|
| **Q1: Validation Logic** | validators/__init__.py | 66-382 | Main validation logic |
| **Q1: Validate All GUI** | gui/app.py | 3365-3459 | GUI implementation |
| **Q1: Setup Definition** | config/models.py | 32-50 | StationSetup data model |
| **Q2: Parametric Method** | engine/least_squares.py | 62-301 | LeastSquaresAdjuster |
| **Q2: Conditional Method** | engine/least_squares.py | 303-490 | ConditionalAdjuster |
| **Q2: Matrix Computations** | engine/adjustment_computations.py | 162-340 | Core math algorithms |
| **Q2: Stability Checking** | engine/adjustment_computations.py | 61-160 | Matrix stability analysis |

---

## Additional Notes

### Weighting Strategy
We use **distance-based weighting**: `w = 1/distance_km`

This follows the principle that longer measurements are less precise, so they receive lower weights.

### Convergence Criteria
Iterations stop when maximum correction < 1×10⁻⁶ meters (1 micrometer)

### Israeli Survey Regulations Compliance
All calculations comply with **Directive ג2 (2021)**, including:
- Tolerance formulas per class (H1-H6)
- Distance limits
- Sight distance constraints
- Method requirements (BF vs BFFB)

---

**End of Document**
