# Visual Explanation - Geodetic Tool Calculations

## Question 1: What is a "Setup"? (Visual)

### Leveling Line Example: Measuring from Benchmark A to Benchmark B

```
    A (BM)          TP1            TP2           B (BM)
     |               |               |              |
     |   ← 50m →    |    ← 80m →    |   ← 70m →   |
     |               |               |              |

    [Rod]      [Instrument]    [Rod]
              Position 1

                        [Rod]      [Instrument]    [Rod]
                                  Position 2

                                            [Rod]      [Instrument]    [Rod]
                                                      Position 3
```

### Setup Details:

**Setup 1 (Instrument at Position 1):**
- Backsight (BS) to A: Read 1.523 m
- Foresight (FS) to TP1: Read 1.234 m
- Distance back: 50 m
- Distance fore: 50 m
- Height difference: dH₁ = 1.523 - 1.234 = +0.289 m

**Setup 2 (Instrument at Position 2):**
- Backsight to TP1: Read 1.876 m
- Foresight to TP2: Read 1.432 m
- Distance back: 80 m
- Distance fore: 80 m
- Height difference: dH₂ = 1.876 - 1.432 = +0.444 m

**Setup 3 (Instrument at Position 3):**
- Backsight to TP2: Read 1.654 m
- Foresight to B: Read 1.345 m
- Distance back: 70 m
- Distance fore: 70 m
- Height difference: dH₃ = 1.654 - 1.345 = +0.309 m

**Line Summary:**
- **Setups Count**: 3
- **Total Distance**: 200 m (50+80+70)
- **Total Height Difference**: +1.042 m (0.289+0.444+0.309)
- **Start**: A
- **End**: B

---

## Question 2: Validation Analysis Table - Data Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                     VALIDATE ALL PROCESS                             │
└─────────────────────────────────────────────────────────────────────┘

INPUT: Loaded Files (self.lines)
   │
   ├─► Calculate Average Distance
   │   └─► Determine Unit: km or m
   │
   ├─► Detect Double-Run Pairs
   │   └─► Calculate Δh (Measured) = |dH_forward - dH_return|
   │
   └─► Batch Validation
       │
       ├─► For Each Line:
       │   ├─ Endpoint Check
       │   ├─ Naming Convention Check
       │   ├─ Data Completeness Check
       │   ├─ Line Length Check (vs Class Limits)
       │   ├─ Sight Distance Check
       │   ├─ Method Check (BF vs BFFB)
       │   ├─ Distance Balance Check
       │   └─ Tolerance Check
       │
       └─► Generate Table Row:
           ├─ File: filename
           ├─ Start: start_point
           ├─ End: end_point
           ├─ Setups: len(line.setups)  ← COUNT OF SETUPS!
           ├─ Distance: total_distance (km or m)
           ├─ dH: total_height_diff (m)
           ├─ Δh (Meas): misclosure for double-runs (mm)
           ├─ Status: PASS / FAIL + reason
           └─ Details: error/warning messages

OUTPUT: Validation Table Display
```

### Example Table Output:

| File | Start | End | Setups | Distance | dH [m] | Δh (Meas) [mm] | Status | Details |
|------|-------|-----|--------|----------|--------|----------------|--------|---------|
| A-B.DAT | A | B | 3 | 0.200 km | +1.04200 | 2.5 | ✓ PASS | All checks passed |
| B-C.RAW | B | C | 5 | 0.850 km | -0.52340 | - | ✗ FAIL: Tolerance | Misclosure 12.3mm exceeds tolerance |
| C-A.DAT | C | A | 2 | 0.150 km | -0.51800 | - | ✗ FAIL: Incomplete | Only 2 setups, minimum 3 expected |

**Setup Count = 3, 5, 2** → This is the number of instrument positions!

---

## Question 3: Network Adjustment - Visual Comparison

### Network Example:

```
      100.000 m (Fixed)
          A
         ╱ ╲
        ╱   ╲
    L1 ╱     ╲ L3
      ╱       ╲
     B    L2   C
      ╲       ╱
       ╲     ╱
        ╲   ╱
         ╲ ╱
          D
```

**Observations:**
- L1: A → B, dH = +2.450 m, dist = 500 m
- L2: B → C, dH = -1.200 m, dist = 800 m
- L3: A → C, dH = +1.248 m, dist = 600 m
- L4: B → D, dH = -3.500 m, dist = 400 m
- L5: C → D, dH = -2.305 m, dist = 700 m

**Unknowns:** H_B, H_C, H_D
**Fixed:** H_A = 100.000 m

---

### Method 1: Parametric Adjustment (Ax+L)

#### **Matrix Setup:**

**Design Matrix A (5 observations × 3 unknowns):**
```
           H_B   H_C   H_D
L1 (A→B): [ +1    0     0  ]    dH = H_B - H_A
L2 (B→C): [ -1   +1    0  ]    dH = H_C - H_B
L3 (A→C): [  0   +1    0  ]    dH = H_C - H_A
L4 (B→D): [ -1    0   +1  ]    dH = H_D - H_B
L5 (C→D): [  0   -1   +1  ]    dH = H_D - H_C

Each row represents: dH = Σ(coefficients × unknowns)
```

**Weight Matrix P (5×5 diagonal):**
```
P = diag(1/0.5, 1/0.8, 1/0.6, 1/0.4, 1/0.7)  # distances in km
  = diag(2.0, 1.25, 1.67, 2.5, 1.43)

Longer lines get lower weight (less reliable)
```

**Observation Vector L (observed - approximate):**
```
Initial approximation: H_B ≈ 102.5, H_C ≈ 101.3, H_D ≈ 99.0

L[0] = 2.450 - (102.5 - 100.0) = -0.050
L[1] = -1.200 - (101.3 - 102.5) = +0.100
L[2] = 1.248 - (101.3 - 100.0) = +0.048
L[3] = -3.500 - (99.0 - 102.5) = -0.000
L[4] = -2.305 - (99.0 - 101.3) = +0.005
```

**Normal Equations:**
```
N = A^T × P × A      (3×3 matrix)
U = A^T × P × L      (3×1 vector)

N × X = U

Solve for X = [ΔH_B, ΔH_C, ΔH_D] (corrections)
```

**Solution:**
```
X = N^(-1) × U

After solving:
ΔH_B = +0.002 m
ΔH_C = +0.001 m
ΔH_D = -0.001 m

Adjusted Heights:
H_B = 102.5 + 0.002 = 102.502 m
H_C = 101.3 + 0.001 = 101.301 m
H_D = 99.0 - 0.001 = 98.999 m
```

**Residuals:**
```
V = A × X - L

V[0] = +0.048 m → L1 adjusted by +48 mm
V[1] = -0.102 m → L2 adjusted by -102 mm
V[2] = +0.047 m → L3 adjusted by +47 mm
...
```

---

### Method 2: Conditional Adjustment (Bv+W)

#### **For Loop Networks:**

Consider a closed loop: A → B → C → A

**Condition: Loop must close**
```
dH(A→B) + dH(B→C) + dH(C→A) = 0

Observed:
L1 (A→B) = +2.450 m
L2 (B→C) = -1.200 m
L3 (C→A) = -1.252 m

Sum = 2.450 - 1.200 - 1.252 = -0.002 m

Misclosure w = -0.002 m (should be 0!)
```

**Condition Matrix B:**
```
B = [1, 1, 1]    (all three observations contribute to loop)

B × v + w = 0
[1, 1, 1] × [v1, v2, v3]^T + (-0.002) = 0
```

**Weight Matrix P:**
```
P = diag(1/0.5, 1/0.8, 1/0.6)  # distances in km
```

**Normal Equations:**
```
N = B × P^(-1) × B^T    (1×1 scalar)
u = -w = +0.002

Solve for k (correlate)
k = N^(-1) × u

Calculate residuals:
v = -P^(-1) × B^T × k
```

**Solution:**
```
Distribute the misclosure proportionally:

v1 = -0.0008 m (adjusted by 0.8 mm)
v2 = -0.0007 m (adjusted by 0.7 mm)
v3 = -0.0005 m (adjusted by 0.5 mm)

Adjusted observations:
L1' = 2.450 - 0.0008 = 2.4492 m
L2' = -1.200 - 0.0007 = -1.2007 m
L3' = -1.252 - 0.0005 = -1.2525 m

Check: 2.4492 - 1.2007 - 1.2525 = -0.004 ≈ 0 ✓
```

---

## Visual Comparison: Parametric vs Conditional

### Parametric Method (Most Common)
```
┌──────────────────────┐
│  OBSERVATIONS (L)    │  ← Measured height differences
└──────────────────────┘
         │
         ▼
┌──────────────────────┐
│  DESIGN MATRIX (A)   │  ← How observations relate to unknowns
└──────────────────────┘
         │
         ▼
┌──────────────────────┐
│  SOLVE: N×X = U      │  ← Normal equations
└──────────────────────┘
         │
         ▼
┌──────────────────────┐
│  UNKNOWNS (X)        │  ← Adjusted HEIGHTS directly
└──────────────────────┘
```

### Conditional Method (Loop Networks)
```
┌──────────────────────┐
│  OBSERVATIONS (L)    │  ← Measured height differences
└──────────────────────┘
         │
         ▼
┌──────────────────────┐
│  CONDITIONS (B)      │  ← Loop closure conditions
└──────────────────────┘
         │
         ▼
┌──────────────────────┐
│  SOLVE: N×k = u      │  ← Normal equations
└──────────────────────┘
         │
         ▼
┌──────────────────────┐
│  CORRELATES (k)      │  ← Lagrange multipliers
└──────────────────────┘
         │
         ▼
┌──────────────────────┐
│  RESIDUALS (v)       │  ← Adjusted OBSERVATIONS
└──────────────────────┘
         │
         ▼
┌──────────────────────┐
│  HEIGHTS             │  ← Computed from adjusted observations
└──────────────────────┘
```

---

## Iteration Process (Parametric Method)

### Why Iterate?

Because we start with approximate heights, and they may not be perfect.

```
Iteration 1:
    Approximate: H_B = 102.5, H_C = 101.3
    Compute corrections: ΔH_B = +0.002, ΔH_C = +0.001
    Update: H_B = 102.502, H_C = 101.301
    Max correction = 0.002 m

Iteration 2:
    Approximate: H_B = 102.502, H_C = 101.301
    Compute corrections: ΔH_B = +0.0000001, ΔH_C = +0.0000002
    Update: H_B = 102.5020001, H_C = 101.3010002
    Max correction = 0.0000002 m < 0.000001 m

CONVERGED! ✓
```

**Convergence Criterion:** Max correction < 1×10⁻⁶ m (1 micrometer)

---

## Statistical Outputs

### Mean Square Error of Unit Weight (σ₀)
```
σ₀ = sqrt((V^T × P × V) / (n_obs - n_unknowns))

Example:
V^T × P × V = 0.025
n_obs = 5, n_unknowns = 3
df = 5 - 3 = 2

σ₀ = sqrt(0.025 / 2) = sqrt(0.0125) = 0.112 mm/√km
```

### Classification Coefficient (K) - Israeli Standards
```
K = Σ|V_i| / sqrt(Σdistances_km)

If K ≤ 3 mm/√km  → Class H1
If K ≤ 5 mm/√km  → Class H2
If K ≤ 10 mm/√km → Class H3
...
```

### Standard Errors of Heights
```
For each unknown height:
σ_H = σ₀ × sqrt(Qxx[i,i])

where Qxx = (A^T × P × A)^(-1)

Example:
σ_H_B = 0.112 × sqrt(0.0045) = 0.112 × 0.067 = 0.0075 m = 7.5 mm
```

---

## Code Flow Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                    USER CLICKS "VALIDATE ALL"                 │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  gui/app.py:3365  _validate_all()                            │
│  ├─ Calculate average distance → determine unit (km/m)       │
│  ├─ Detect double-run pairs → calculate Δh (Measured)        │
│  └─ Create BatchValidator()                                  │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  validators/__init__.py:398  validate_batch()                │
│  └─ For each line: validator.validate(line)                  │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  validators/__init__.py:66  validate()                       │
│  ├─ _check_endpoint()      ← Line 95                         │
│  ├─ _check_naming()        ← Line 116                        │
│  ├─ _check_completeness()  ← Line 149 (counts setups!)       │
│  ├─ _check_line_length()   ← Line 171                        │
│  ├─ _check_sight_distances() ← Line 194                      │
│  ├─ _check_measurement_method() ← Line 244                   │
│  ├─ _check_distance_balance() ← Line 293                     │
│  └─ _check_tolerance()     ← Line 340                        │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│  gui/app.py:3444  Insert row into table                      │
│  └─ Display: File, Start, End, Setups, Distance, dH, Δh,     │
│              Status, Details                                  │
└──────────────────────────────────────────────────────────────┘
```

---

**End of Visual Explanation**
