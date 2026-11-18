# Technical Breakdown: Enhanced LSA Engine

## Overview

The Enhanced LSA (Least Squares Adjustment) engine in GeoLevelQGIS implements two complementary adjustment methods for leveling networks:

1. **Parametric (Observation Equation) Method**: V = A·X − L
2. **Conditional (Constraint Equation) Method**: B·v + W = 0

Both methods solve for unknown point heights given measurements and fixed benchmarks. The choice between them affects how the problem is formulated and solved, though both yield consistent results when properly implemented.

---

## Part 1: Parametric Adjustment (V = A·X − L)

### 1.1 Problem Formulation

**Observation Equation Model:**
```
V = A·X − L

Where:
  V  = residuals vector        (n_obs × 1)
  A  = design matrix           (n_obs × n_unknowns)
  X  = parameter corrections   (n_unknowns × 1)
  L  = misclosure vector       (n_obs × 1)
```

**Physical Interpretation:**
- Each observation (leveling line) produces a measured height difference dH_measured
- We have approximate (initial) heights for all unknown points
- The computed height difference is: dH_computed = H_to(approx) − H_from(approx)
- The misclosure is: L = dH_measured − dH_computed
- We solve for corrections ΔH to approximate heights: H_new = H_approx + ΔH
- Residuals represent the final adjustment: V = actual error remaining after solving

### 1.2 Design Matrix (A) Construction

**Location:** `core_logic/engine/least_squares.py:175-214` (LeastSquaresAdjuster.adjust())

**Algorithm:**
```python
# For each observation i (leveling line)
A[i, j] = ∂(dH_i) / ∂(H_j)

# If line i goes from point 'from_pt' to point 'to_pt':
# dH_i = H_to - H_from

# Therefore:
if to_pt in unknown_points:
    A[i, j_to] = +1.0      # Partial derivative w.r.t. H_to
if from_pt in unknown_points:
    A[i, j_from] = -1.0    # Partial derivative w.r.t. H_from
```

**Code Reference (least_squares.py:198-207):**
```python
# Design matrix coefficients
# dH = H_to - H_from
# Partial derivative w.r.t. H_to = +1
# Partial derivative w.r.t. H_from = -1

if to_pt in point_index:
    A[i, point_index[to_pt]] = 1.0

if from_pt in point_index:
    A[i, point_index[from_pt]] = -1.0
```

**Matrix Structure Example:**

Given 3 observations (lines) and 2 unknowns (H_B, H_C) with H_A fixed:
```
Lines:
  1. A → B:  measured dH = 10.5 m
  2. B → C:  measured dH = 5.3 m
  3. C → A:  measured dH = -15.8 m

A matrix (3 × 2):
     H_B   H_C
  1:  +1     0     (affects B only)
  2:   0    +1     (affects C only, C-B = C-0 - B so row 2 = [0, +1])
  3:  -1    -1     (C-A = C-0 - (A+offset) = affects both B and C)

Actually for line 3 (C → A):
  dH = H_A - H_C = (fixed) - H_C
  ∂dH/∂H_B = 0
  ∂dH/∂H_C = -1
  So row 3 = [0, -1]
```

### 1.3 Observation Vector (L) Construction

**Location:** `core_logic/engine/least_squares.py:179-196`

**Formula:**
```
L[i] = measured_dH[i] − computed_dH[i]

Where:
  measured_dH[i] = line.total_height_diff (from input file)
  computed_dH[i] = H_to(current) − H_from(current)
```

**Code Reference (least_squares.py:189-196):**
```python
observed_dh = obs.height_diff           # Measured dH

# Computed height difference using current approximate heights
computed_dh = current_heights.get(to_pt, 0) - current_heights.get(from_pt, 0)

# Misclosure (observation residual before adjustment)
L[i] = observed_dh - computed_dh
```

### 1.4 Weight Matrix (P) Construction

**Location:** `core_logic/engine/least_squares.py:182-214`

**Formula:** Distance-based weighting
```
P[i, i] = 1 / distance_km

Where:
  distance_km = line.total_distance / 1000.0
```

**Physical Meaning:**
- Longer lines are less precise (down-weighted)
- Shorter lines are more precise (up-weighted)
- P is diagonal: observations are independent

**Code Reference (least_squares.py:209-214):**
```python
# Weight (inverse of distance in km)
dist_km = obs.distance / 1000.0
if dist_km > 0:
    P[i, i] = 1.0 / dist_km
else:
    P[i, i] = 1.0
```

### 1.5 Normal Equations: N·X = U

**Location:** `core_logic/engine/adjustment_computations.py:159-266` (run_linear_adjustment())

**Derivation from Least Squares Principle:**

Minimize: Φ = V^T · P · V = (A·X − L)^T · P · (A·X − L)

Taking derivative w.r.t. X and setting to zero:
```
∂Φ/∂X = 2·A^T·P·(A·X − L) = 0

Rearranging:
A^T·P·A·X = A^T·P·L

Or: N·X = U

Where:
  N = A^T·P·A     (normal matrix, n_unknowns × n_unknowns)
  U = A^T·P·L     (right-hand side vector, n_unknowns × 1)
  X = N^−1·U      (solution: parameter corrections)
```

**Code Reference (adjustment_computations.py:217-218):**
```python
# Form normal equations
N = A.T @ P @ A
U = A.T @ P @ L
```

**Solving (adjustment_computations.py:227-232):**
```python
try:
    X = np.linalg.solve(N, U)   # Gaussian elimination
    result['X'] = X
except np.linalg.LinAlgError as e:
    raise SingularMatrixError(f"Cannot solve normal equations: {e}")
```

### 1.6 Iterative Refinement

**Location:** `core_logic/engine/least_squares.py:174-245`

**Algorithm:**
```
For iteration = 1, 2, ..., max_iterations:
  1. Build A, L, P using current approximate heights
  2. Form N = A^T·P·A, U = A^T·P·L
  3. Solve X = N^−1·U
  4. Update heights: H_new = H_current + X
  5. Check convergence: max(|X|) < tolerance
  6. If converged, break
```

**Code Reference (least_squares.py:174-245):**
```python
for iteration in range(1, self.max_iterations + 1):
    # Build A, L, P
    A = np.zeros((n_obs, n_unknowns))
    L = np.zeros(n_obs)
    P = np.zeros((n_obs, n_obs))
    
    # [... populate A, L, P using current_heights ...]
    
    # Solve
    adj_result = self.adj_comp.run_linear_adjustment(A, L, P, ...)
    dX = adj_result['X']
    
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
```

**Why Iteration?**

The design matrix A is linearized around approximate heights. If approximates are far from true values, first iteration may be inaccurate. Iteration refines until convergence. Typically converges in 2-3 iterations for well-behaved networks.

### 1.7 Parametric Adjustment Results

**Post-solve Statistics:**

**Residuals (adjustment_computations.py:234-236):**
```python
V = A @ X - L

# This represents: how far we're off after the optimal corrections
# V should be small if the network is consistent
```

**Variance Factor / Unit Weight Standard Error (adjustment_computations.py:239-242):**
```python
degrees_of_freedom = n_obs - n_unknowns

VtPV = V.T @ P @ V                      # Weighted sum of squared residuals
sigma_0_squared = VtPV / degrees_of_freedom
sigma_0 = np.sqrt(sigma_0_squared)      # Expressed in meters

# Key statistic: ratio of actual fit to theoretical expectation
# Used to classify achieved accuracy (mm/√km for K coefficient)
```

**Cofactor Matrix (adjustment_computations.py:246-253):**
```python
Qxx = np.linalg.inv(N)                  # Cofactor matrix of unknowns

# Standard error of each adjusted height
std_errors = sigma_0 * np.sqrt(np.diag(Qxx))

# Stores as mse_heights (Mean Square Error)
```

**Code Location:** `core_logic/engine/least_squares.py:258-266`
```python
try:
    N = A.T @ P @ A
    Qxx = np.linalg.inv(N)
    mse_heights = {}
    for j, pid in enumerate(unknown_list):
        mse_heights[pid] = mse_unit_weight * np.sqrt(Qxx[j, j])
except Exception:
    mse_heights = {pid: 0.0 for pid in unknown_list}
```

**K Coefficient (Classification Metric) (least_squares.py:268-275):**
```python
total_dist_km = sum(obs.distance for obs in observations) / 1000.0
total_diff_mm = sum(abs(V[i]) * 1000 for i in range(n_obs))

if total_dist_km > 0:
    k_coefficient = total_diff_mm / np.sqrt(total_dist_km)
else:
    k_coefficient = 0.0

# k_coefficient [mm/√km] is compared to class tolerances:
# H1: 3 mm/√km, H2: 5 mm/√km, ..., H6: 60 mm/√km
```

---

## Part 2: Conditional Adjustment (B·v + W = 0)

### 2.1 Problem Formulation

**Condition Equation Model:**
```
B·v + W = 0

Where:
  B  = condition matrix        (n_conditions × n_obs)
  v  = observation residuals   (n_obs × 1)
  W  = misclosure vector       (n_conditions × 1)

Rearranged:
  v = -B^−T · W   (if B is invertible, which it's not)

Proper formulation via Lagrange multipliers:
  Normal equations: N·k = u
  Where:
    N = B·P^−1·B^T   (n_conditions × n_conditions)
    u = -W
    k = correlates (Lagrange multipliers)
    v = -P^−1·B^T·k
```

### 2.2 Loop Detection

**Location:** `core_logic/engine/loop_detector.py`

**Definition of a Loop (Closed Path):**
A loop is a sequence of leveling lines where:
- Start point = End point (path closes)
- No line is traversed twice
- Sum of height differences around loop ≠ 0 in real measurements (misclosure)

**Algorithm: Depth-First Search (DFS)**

**Code Reference (loop_detector.py:143-199):**

```python
def find_all_loops(self, max_loop_size: int = 10) -> List[Loop]:
    """Find all closed loops using DFS."""
    loops = []
    visited_loops: Set[frozenset] = set()
    
    for start_point in self.points:
        found_loops = self._find_loops_from_point(start_point, max_loop_size)
        for loop in found_loops:
            loop_signature = frozenset(id(line) for line in loop.lines)
            if loop_signature not in visited_loops:
                visited_loops.add(loop_signature)
                loop.calculate_misclosure()
                loops.append(loop)
    return loops

def _find_loops_from_point(self, start: str, max_size: int) -> List[Loop]:
    """Find all loops starting from a given point."""
    loops = []
    
    def dfs(current: str, path: List[str], used_lines: List[LevelingLine], depth: int):
        if depth > max_size:
            return
        
        for neighbor, line in self.get_neighbors(current):
            if line in used_lines:
                continue  # Don't reuse lines
            
            # Found a loop back to start
            if neighbor == start and len(used_lines) >= 2:
                new_path = path + [neighbor]
                new_lines = used_lines + [line]
                loop = Loop(lines=new_lines.copy(), points=new_path.copy())
                loops.append(loop)
                continue
            
            # Continue DFS if neighbor not in path (except start)
            if neighbor not in path[1:]:
                dfs(neighbor, path + [neighbor], used_lines + [line], depth + 1)
    
    dfs(start, [start], [], 0)
    return loops
```

**Data Structure: Loop Class (loop_detector.py:16-111)**
```python
@dataclass
class Loop:
    lines: List[LevelingLine]           # Ordered lines forming the loop
    points: List[str]                   # Ordered points [P1, P2, ..., Pn, P1]
    total_distance: float = 0.0         # Sum of line distances
    misclosure: float = 0.0             # Sum of height diffs (should ≈ 0)
    allowable_tolerance: float = 0.0    # mm
    
    @property
    def is_closed(self) -> bool:
        return len(self.points) > 2 and self.points[0] == self.points[-1]
```

**Minimum Spanning Set of Loops:**

For a network with V vertices, E edges, and C connected components:
```
Number of independent loops = E − V + C = Cyclomatic complexity
```

**Code Reference (loop_detector.py:201-221):**
```python
def find_minimum_loops(self) -> List[Loop]:
    """Find the set of independent minimum loops (basis loops)."""
    all_loops = self.find_all_loops()
    all_loops.sort(key=lambda x: x.num_lines)  # Prefer smaller loops
    
    num_vertices = len(self.points)
    num_edges = len(self.lines)
    num_components = self._count_components()
    
    expected_loops = num_edges - num_vertices + num_components
    return all_loops[:max(expected_loops, len(all_loops))]
```

### 2.3 B Matrix (Condition Matrix) Construction

**Location:** `core_logic/engine/least_squares.py:385-413` (ConditionalAdjuster.adjust_loops())

**Definition:**
```
B[c, i] ∈ {−1, 0, +1}

Where:
  c = condition index (one per independent loop or BM path)
  i = observation index (one per line)
  
B[c, i] = +1  if line i is traversed forward in condition c
B[c, i] = −1  if line i is traversed backward in condition c
B[c, i] =  0  if line i is not part of condition c
```

**Loop Condition Rows:**

**Code Reference (least_squares.py:389-404):**
```python
for loop_idx_list in loops:
    row = np.zeros(n_obs)
    w_val = 0.0
    for li in loop_idx_list:
        if isinstance(li, tuple):
            idx, sign = li      # (line_index, ±1 sign)
        else:
            idx, sign = li, 1
        row[idx] = float(sign)
        w_val += sign * lines[idx].total_height_diff
    B_rows.append(row)
    W_vals.append(w_val)
```

**Interpretation:**
- Condition for each loop: B[c, :] · (L + v) = 0
- Where L is initial observation vector (measured dH per line)
- v is residual adjustment
- Sum of adjusted height differences around loop must be zero

**Example:**

Loop A→B→C→A with 3 lines:
```
Line 1: A→B, measured dH = 10.5 m
Line 2: B→C, measured dH = 5.3 m
Line 3: C→A, measured dH = -15.8 m

If traversed forward all three:
B = [+1, +1, +1]
W = 10.5 + 5.3 + (-15.8) = -0.0 (ideal) = misclosure

Condition: v₁ + v₂ + v₃ + (−0.0) = 0
→ v₁ + v₂ + v₃ = 0.0
```

**Benchmark Path Condition Rows:**

**Code Reference (least_squares.py:485-539):**
```python
def _build_path_conditions(self, lines, bm_ids, fixed_points, n_obs):
    """For each pair of adjacent fixed BMs, add a path condition."""
    conditions = []
    visited_pairs = set()
    
    for bm_start in bm_ids:
        # BFS to find shortest path to each other BM
        queue = deque([(bm_start, [], [])])
        seen = {bm_start}
        while queue:
            node, p_idx, p_sgn = queue.popleft()
            for neighbor, li, sign in adj.get(node, []):
                if li in p_idx:
                    continue
                new_idx = p_idx + [li]
                new_sgn = p_sgn + [sign]
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
```

**Interpretation:**
- For each pair of fixed benchmarks (BMs) with known heights H₁, H₂
- Condition: sum of adjusted dH along path = H₂ − H₁
- B row: [sign₁, sign₂, sign₃, ...] for each line in path
- W value: H₂ − H₁ − sum(initial dH along path)

**Example:**

Fixed benchmarks: A (height 100.0 m), C (height 125.0 m)
Path A→B→C:
```
Line 1: A→B, measured dH = 10.5 m
Line 2: B→C, measured dH = 14.5 m
Expected: 10.5 + 14.5 = 25.0 m ✓

B = [+1, +1]
W = (125.0 − 100.0) − (10.5 + 14.5) = 25.0 − 25.0 = 0.0

Condition: v₁ + v₂ + 0.0 = 0
→ v₁ + v₂ = 0
```

### 2.4 W Vector (Misclosure Vector) Construction

**Location:** `core_logic/engine/least_squares.py:389-404` (for loops) and `least_squares.py:529-533` (for paths)

**Definition:**
```
W[c] = observed misclosure for condition c

For a loop:
W_loop = Σ(signed dH along loop)
       = sum of measured height differences around loop

For a path between fixed BMs:
W_path = (H_end_fixed − H_start_fixed) − Σ(signed dH along path)
```

**Code Reference — Loop Misclosure (loop_detector.py:80-106):**
```python
def calculate_misclosure(self, target_class: int = 3):
    """Calculate the misclosure of the loop."""
    total_dh = 0.0
    total_dist = 0.0
    
    for line in self.lines:
        total_dh += line.total_height_diff
        total_dist += line.total_distance
    
    self.misclosure = total_dh
    self.total_distance = total_dist
```

**Physical Interpretation:**
- In perfect measurement: misclosure = 0
- Real measurements have errors → misclosure ≠ 0
- Conditional adjustment distributes this misclosure optimally across observations

**Example — Loop A→B→C→A:**
```
Measured: 10.5 + 5.3 + (−15.9) = −0.1 m

W = −0.1 m
→ Condition: v₁ + v₂ + v₃ − (−0.1) = 0
→ v₁ + v₂ + v₃ = −0.1
  (residuals must sum to exactly the observed misclosure)
```

### 2.5 Normal Equations: N·k = u

**Location:** `core_logic/engine/adjustment_computations.py:268-372` (run_conditional_adjustment())

**Derivation:**

Start with condition equations: B·v + W = 0

Using method of Lagrange multipliers, minimize:
```
Φ = v^T · P · v + 2·λ^T · (B·v + W)

Where λ = correlates (Lagrange multipliers)

Taking derivative w.r.t. v:
∂Φ/∂v = 2·P·v + 2·B^T·λ = 0
→ v = −P^−1·B^T·λ

Substituting back into constraints B·v + W = 0:
B·(−P^−1·B^T·λ) + W = 0
−B·P^−1·B^T·λ + W = 0
B·P^−1·B^T·λ = W

Rearranging:
N·k = u

Where:
  N = B·P^−1·B^T    (condition normal matrix)
  u = W
  k = λ (correlates)
```

**Code Reference (adjustment_computations.py:326-338):**
```python
# Form normal equations: N*k = u
P_inv = np.linalg.inv(P)
# (Special case: if P is diagonal, just invert diagonal)
if np.count_nonzero(P - np.diag(np.diagonal(P))) == 0:
    P_inv = np.diag(1.0 / np.diagonal(P))

N = B @ P_inv @ B.T
u = -w
```

**Solving (adjustment_computations.py:346-352):**
```python
try:
    k = np.linalg.solve(N, u)
    result['k'] = k
except np.linalg.LinAlgError as e:
    raise SingularMatrixError(f"Cannot solve conditional normal equations: {e}")
```

### 2.6 Residual Calculation

**Location:** `core_logic/engine/adjustment_computations.py:354-356`

**Formula:**
```
v = −P^−1 · B^T · k

Where:
  k = correlates from solving N·k = u
  v = residual corrections to each observation
```

**Code Reference:**
```python
# Calculate residuals
v = -P_inv @ B.T @ k
result['v'] = v
```

**Physical Interpretation:**
- v[i] is the adjustment to line i's observed dH
- Small v[i] means line fits the loop constraint well
- Large v[i] means that line contains most of the misclosure

### 2.7 Adjusted Height Differences and Propagation

**Location:** `core_logic/engine/least_squares.py:447-457`

**Adjusted dH Vector:**
```
L_adj = L + v

Where:
  L = observed height differences
  v = residual adjustments
  L_adj = adjusted height differences
```

**Code Reference (least_squares.py:447):**
```python
# ── 5. Adjusted height differences ─────────────────────────
L_adj = np.array([ln.total_height_diff for ln in lines]) + v
```

**Height Propagation (BFS):**

**Location:** `core_logic/engine/least_squares.py:541-570` (_propagate_heights())

**Algorithm:**
```
heights = fixed_points (start with known benchmarks)
Build adjacency: point → list of (neighbor, adjusted_dH)
BFS from each fixed point:
  For each neighbor not yet visited:
    H[neighbor] = H[current] + adjusted_dH
    Mark as visited
    Add to queue
```

**Code Reference:**
```python
def _propagate_heights(self, lines, L_adj, fixed_points):
    """Propagate adjusted height differences from fixed points."""
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
```

### 2.8 Conditional Adjustment Results

**Variance Factor (adjustment_computations.py:358-363):**
```python
vtPv = v.T @ P @ v
sigma_0_squared = vtPv / degrees_of_freedom
sigma_0 = np.sqrt(sigma_0_squared)

# degrees_of_freedom = n_obs − n_conditions
```

**K Coefficient (least_squares.py:465-468):**
```python
total_dist_km = sum(ln.total_distance for ln in lines) / 1000.0
total_diff_mm = sum(abs(float(v[i])) * 1000.0 for i in range(n_obs))
k_coeff = (total_diff_mm / np.sqrt(total_dist_km)
           if total_dist_km > 0 else 0.0)
```

---

## Part 3: Handling Double-Runs

### 3.1 What is a Double-Run?

A double-run is two leveling lines between the same points, traversed in opposite directions:
- Forward: P₁ → P₂ with measured dH_f
- Return:  P₂ → P₁ with measured dH_r

**Ideal condition:** dH_f + dH_r ≈ 0 (misclosure should be near zero)

### 3.2 Detection

**Location:** `core_logic/engine/loop_detector.py:348-377` (detect_double_runs())

**Code:**
```python
def detect_double_runs(lines: List[LevelingLine]) -> List[Tuple[LevelingLine, LevelingLine]]:
    """Detect double-run pairs."""
    pairs = []
    used = set()
    
    for i, line1 in enumerate(lines):
        if i in used:
            continue
        
        for j, line2 in enumerate(lines[i+1:], i+1):
            if j in used:
                continue
            
            # Check if these form a double-run
            if (line1.start_point == line2.end_point and 
                line1.end_point == line2.start_point):
                pairs.append((line1, line2))
                used.add(i)
                used.add(j)
                break
    
    return pairs
```

### 3.3 Analysis and Treatment

**Location:** `core_logic/engine/loop_detector.py:267-333` (analyze_double_run())

**Key Metrics:**
```python
def analyze_double_run(self, line1, line2, target_class=3) -> Dict:
    dh_forward = line1.total_height_diff
    dh_return = line2.total_height_diff
    
    # For double-run, sum should be close to 0
    misclosure = dh_forward + dh_return
    misclosure_mm = misclosure * 1000
    
    # Calculate mean dH (average of forward and return)
    mean_dh = (dh_forward - dh_return) / 2
    
    total_distance = line1.total_distance + line2.total_distance
    dist_km = total_distance / 1000.0
    
    # Check against class tolerance
    params = get_class_parameters(target_class)
    tolerance_mm = params.get_tolerance_mm(dist_km)
```

### 3.4 In LSA Context

Double-runs form **minimal 2-line loops**.

In conditional adjustment:
- Each double-run creates one loop condition: v₁ + v₂ + misclosure = 0
- n_loops = number of unique double-run pairs
- They reduce redundancy (fewer independent conditions needed)

In parametric adjustment:
- Double-runs add observations → increase degrees of freedom
- Help constrain the network uniquely

---

## Part 4: Integration into Normal Equations

### 4.1 Parametric Method Integration

**Steps:**
1. Initialize approximate heights from fixed points (BFS propagation) (least_squares.py:141-172)
2. **For each iteration:**
   - Build A, L, P using current heights
   - Form N = A^T·P·A, U = A^T·P·L (adjustment_computations.py:217-218)
   - Solve X = N^−1·U (adjustment_computations.py:228)
   - Update heights: H = H + X (least_squares.py:237-240)
   - Check convergence (least_squares.py:243-245)
3. Calculate final statistics: σ₀, residuals, K (least_squares.py:247-292)

**UI Invocation:**

`geo_level_enhanced_lsa_dialog.py:437-467` (_run_parametric()):
```python
def _run_parametric(self, fixed_points):
    # Convert lines to observations
    observations = [MeasurementSummary(...) for line in self.lines]
    
    # Create adjuster
    adjuster = LeastSquaresAdjuster(
        max_iterations=self.spin_iter.value(),
        tolerance=self.spin_tol.value(),
        check_stability=self.chk_stability.isChecked()
    )
    
    # Run adjustment
    result = adjuster.adjust(observations, fixed_points)
    
    # Display results
    self._populate_results(result, fixed_points, method="Parametric (Ax - L)")
```

### 4.2 Conditional Method Integration

**Steps:**
1. Detect basis loops (minimum independent set) (loop_detector.py:201-221)
2. Build signed loop indices: (line_index, ±1 sign) (geo_level_enhanced_lsa_dialog.py:487-504)
3. Build path conditions for fixed BM pairs (least_squares.py:407-413)
4. Combine: B_rows ← loop rows + path rows (least_squares.py:386-420)
5. Build W vector: misclosures + BM path differences (least_squares.py:389-413)
6. Form N = B·P^−1·B^T, u = −W (adjustment_computations.py:326-338)
7. Solve k = N^−1·u (adjustment_computations.py:346-352)
8. Calculate v = −P^−1·B^T·k (adjustment_computations.py:354-356)
9. Propagate heights from adjusted dH (least_squares.py:454-457)
10. Calculate statistics (least_squares.py:460-478)

**UI Invocation:**

`geo_level_enhanced_lsa_dialog.py:469-531` (_run_conditional()):
```python
def _run_conditional(self, fixed_points):
    active_lines = [ln for ln in self.lines if self._is_eligible(ln)]
    
    # Detect basis loops
    analyzer = LoopAnalyzer(active_lines)
    loops = analyzer.find_basis_loops()
    
    # Build signed loop indices
    line_id_map = {id(ln): i for i, ln in enumerate(active_lines)}
    loop_indices = []
    for loop in loops:
        signed = []
        prev_pt = loop.points[0]
        for ln in loop.lines:
            idx = line_id_map[id(ln)]
            # Determine sign based on line direction vs loop traversal
            sign = (+1 if ln.start_point == prev_pt else -1)
            signed.append((idx, sign))
            prev_pt = ln.end_point
        loop_indices.append(signed)
    
    # Run conditional adjuster
    adjuster = ConditionalAdjuster(check_stability=...)
    result = adjuster.adjust_loops(active_lines, loop_indices, fixed_points)
    self._populate_results(result, fixed_points, method="Conditional (Bv + W)")
```

---

## Part 5: Key Python Classes and Files

### Core Files

| File | Purpose | Key Functions |
|------|---------|---------------|
| `core_logic/engine/loop_detector.py` | Loop/double-run detection | `LoopAnalyzer`, `Loop`, `NetworkGraph`, `detect_double_runs()` |
| `core_logic/engine/least_squares.py` | Parametric & conditional adjusters | `LeastSquaresAdjuster`, `ConditionalAdjuster` |
| `core_logic/engine/adjustment_computations.py` | Matrix algebra & stability | `AdjustmentComputations.run_linear_adjustment()`, `.run_conditional_adjustment()` |
| `core_logic/engine/line_adjustment.py` | Single-line adjustment (not network) | `LineAdjuster` |
| `core_logic/config/models.py` | Data structures | `LevelingLine`, `AdjustmentResult`, `MeasurementSummary` |
| `geo_level_enhanced_lsa_dialog.py` | Enhanced LSA UI & orchestration | `GeoLevelEnhancedLSADialog._run_parametric()`, `._run_conditional()` |

### Key Classes

**LeastSquaresAdjuster (least_squares.py:59-328)**
- `adjust(observations, fixed_points)` → parametric method
- Handles: iteration, convergence, error statistics
- Returns: `AdjustmentResult`

**ConditionalAdjuster (least_squares.py:330-594)**
- `adjust_loops(lines, loops, fixed_points)` → conditional method
- Builds B, W, solves N·k = u, computes v, propagates heights
- Returns: `AdjustmentResult`

**AdjustmentComputations (adjustment_computations.py:28-437)**
- `run_linear_adjustment(A, L, P)` → solves N·X = U
- `run_conditional_adjustment(B, w, P)` → solves N·k = u
- `check_matrix_stability(matrix)` → validates solution

**LoopAnalyzer (loop_detector.py:243-346)**
- `find_loops()` → all loops (DFS)
- `find_basis_loops()` → minimum independent set
- `analyze_double_run(line1, line2)` → validate double-run

---

## Part 6: Matrix Stability and Error Handling

**Singular Matrix Detection:**

Location: `core_logic/engine/adjustment_computations.py:58-157` (check_matrix_stability())

Checks:
1. Determinant: `|det| < 1e-15` → singular
2. Condition number: `cond > 1e10` → ill-conditioned (warning)
3. Rank: `rank < size` → rank deficiency
4. Raises `SingularMatrixError` if singular

**Error Classes:**

`core_logic/engine/ADJwarnings.py`:
- `SingularMatrixError` — N or M non-invertible
- `InsufficientObservationsError` — not enough observations/conditions
- `IllConditionedMatrixWarning` — numerically unstable
- `ConvergenceError` — iteration didn't converge

---

## Summary Table

| Method | Equation | Key Matrix | Normal Form | Solution | Pros | Cons |
|--------|----------|-----------|------------|----------|------|------|
| **Parametric** | V = A·X − L | A (n×u) | N·X = U | X = N⁻¹·U | Simple; direct unknowns | Requires iteration; seeding |
| **Conditional** | B·v + W = 0 | B (r×n) | N·k = u | k = N⁻¹·u; v = −P⁻¹·B^T·k | Single solution; loop-based | Requires loop detection; more complex |

---

## References

1. Directive ג2 (2021) — Israeli Survey Regulations
2. Mikhail, E. M., & Ackermann, F. (1976). Observations and Least Squares
3. Wolf, P. R., & Ghilani, C. D. (2006). Adjustment Computations: Spatial Data Analysis

