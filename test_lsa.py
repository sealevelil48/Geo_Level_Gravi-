"""
Test script for LSA (Least Squares Adjustment) implementation

Demonstrates both Parametric (Ax+L) and Conditional (Bv+W) adjustment methods.
"""
import numpy as np
from geodetic_tool.engine import (
    LeastSquaresAdjuster,
    ConditionalAdjuster,
    AdjustmentComputations
)
from geodetic_tool.config.models import MeasurementSummary, LevelingLine


def test_parametric_adjustment():
    """Test Parametric Adjustment (Ax+L)"""
    print("\n" + "="*70)
    print("TEST 1: PARAMETRIC ADJUSTMENT (Ax+L)")
    print("="*70)

    # Example network:
    # BM1 (100.000m, fixed) -> BM2 (unknown) -> BM3 (unknown)
    # With additional observation BM1 -> BM3 for redundancy

    observations = [
        MeasurementSummary(
            from_point="BM1",
            to_point="BM2",
            height_diff=10.524,  # meters
            distance=1500.0,     # meters
            num_setups=3,
            bf_diff=0.0,
            year_month="0126",
            source_file="line1.DAT",
            is_used=True
        ),
        MeasurementSummary(
            from_point="BM2",
            to_point="BM3",
            height_diff=-5.123,
            distance=2000.0,
            num_setups=4,
            bf_diff=0.0,
            year_month="0126",
            source_file="line2.DAT",
            is_used=True
        ),
        MeasurementSummary(
            from_point="BM1",
            to_point="BM3",
            height_diff=5.402,
            distance=3500.0,
            num_setups=7,
            bf_diff=0.0,
            year_month="0126",
            source_file="line3.DAT",
            is_used=True
        ),
    ]

    # Fixed point (known benchmark)
    fixed_points = {
        "BM1": 100.000  # meters
    }

    # Create adjuster with stability checking
    adjuster = LeastSquaresAdjuster(
        max_iterations=10,
        tolerance=1e-6,
        check_stability=True
    )

    print("\nNetwork Configuration:")
    print(f"  Fixed point: BM1 = {fixed_points['BM1']:.3f} m")
    print(f"  Unknown points: BM2, BM3")
    print(f"  Observations: {len(observations)}")
    print(f"  Redundancy: {len(observations) - 2} (degrees of freedom)")

    # Perform adjustment
    print("\nPerforming adjustment...")
    result = adjuster.adjust(observations, fixed_points)

    # Display results
    print("\n" + "-"*70)
    print("RESULTS:")
    print("-"*70)
    print(f"Converged in {result.iteration} iterations")
    print(f"\nAdjusted Heights:")
    for point_id in ["BM1", "BM2", "BM3"]:
        if point_id in result.adjusted_heights:
            height = result.adjusted_heights[point_id]
            mse = result.mse_heights.get(point_id, 0.0)
            print(f"  {point_id}: {height:.4f} m ± {mse*1000:.2f} mm")

    print(f"\nResiduals:")
    for obs_id, residual_mm in result.residuals.items():
        print(f"  {obs_id}: {residual_mm:+.2f} mm")

    print(f"\nQuality Metrics:")
    print(f"  Sigma_0 (Standard error of unit weight): {result.mse_unit_weight:.4f}")
    print(f"  K coefficient: {result.k_coefficient:.2f} mm/sqrt(km)")
    print(f"  Total distance: {result.total_distance_km:.3f} km")

    return result


def test_conditional_adjustment():
    """Test Conditional Adjustment (Bv+W)"""
    print("\n" + "="*70)
    print("TEST 2: CONDITIONAL ADJUSTMENT (Bv+W)")
    print("="*70)

    # Example loop network:
    # BM1 -> BM2 -> BM3 -> BM1 (forms a closed loop)

    lines = [
        LevelingLine(
            filename="line1.DAT",
            start_point="BM1",
            end_point="BM2",
            method="BF",
            total_distance=1500.0,
            total_height_diff=10.524,
            is_used=True
        ),
        LevelingLine(
            filename="line2.DAT",
            start_point="BM2",
            end_point="BM3",
            method="BF",
            total_distance=2000.0,
            total_height_diff=-5.123,
            is_used=True
        ),
        LevelingLine(
            filename="line3.DAT",
            start_point="BM3",
            end_point="BM1",
            method="BF",
            total_distance=1800.0,
            total_height_diff=-5.399,  # Should close to ~0 with BM1->BM2->BM3
            is_used=True
        ),
    ]

    # Define loop (all three lines form one loop)
    loops = [[0, 1, 2]]  # Indices of lines in the loop

    # Fixed point
    fixed_points = {
        "BM1": 100.000
    }

    print("\nLoop Network Configuration:")
    print(f"  Fixed point: BM1 = {fixed_points['BM1']:.3f} m")
    print(f"  Loop: BM1 -> BM2 -> BM3 -> BM1")
    print(f"  Number of observations: {len(lines)}")
    print(f"  Number of conditions (loops): {len(loops)}")

    # Calculate loop misclosure
    loop_misclosure = sum(lines[i].total_height_diff for i in loops[0])
    print(f"  Loop misclosure: {loop_misclosure*1000:.2f} mm")

    # Create conditional adjuster
    adjuster = ConditionalAdjuster(check_stability=True)

    print("\nPerforming conditional adjustment...")
    result = adjuster.adjust_loops(lines, loops, fixed_points)

    # Display results
    print("\n" + "-"*70)
    print("RESULTS:")
    print("-"*70)
    print(f"\nAdjusted Heights:")
    for point_id in sorted(result.adjusted_heights.keys()):
        height = result.adjusted_heights[point_id]
        print(f"  {point_id}: {height:.4f} m")

    print(f"\nResiduals:")
    for obs_id, residual_mm in result.residuals.items():
        print(f"  {obs_id}: {residual_mm:+.2f} mm")

    print(f"\nQuality Metrics:")
    print(f"  Sigma_0 (Standard error of unit weight): {result.mse_unit_weight:.4f}")
    print(f"  K coefficient: {result.k_coefficient:.2f} mm/sqrt(km)")
    print(f"  Total distance: {result.total_distance_km:.3f} km")

    # Verify loop closure after adjustment
    adjusted_misclosure = sum(
        result.residuals[f"{lines[i].start_point}-{lines[i].end_point}"] / 1000
        for i in loops[0]
    )
    print(f"\nLoop misclosure after adjustment: {adjusted_misclosure*1000:.4f} mm")

    return result


def test_matrix_stability():
    """Test matrix stability checking"""
    print("\n" + "="*70)
    print("TEST 3: MATRIX STABILITY CHECKING")
    print("="*70)

    adj_comp = AdjustmentComputations()

    # Test 1: Well-conditioned matrix
    print("\nTest 3.1: Well-conditioned matrix")
    A_good = np.array([
        [1.0, 0.0],
        [0.0, 1.0],
        [1.0, 1.0]
    ])
    N_good = A_good.T @ A_good
    stability = adj_comp.check_matrix_stability(N_good, "Well-conditioned Matrix")
    print(f"Result: Condition number = {stability.get('condition_number', 0):.2e}")

    # Test 2: Ill-conditioned matrix (nearly singular)
    print("\nTest 3.2: Ill-conditioned matrix")
    A_bad = np.array([
        [1.0, 0.0],
        [1.0, 1e-10],  # Nearly dependent on first row
    ])
    N_bad = A_bad.T @ A_bad
    try:
        stability = adj_comp.check_matrix_stability(N_bad, "Ill-conditioned Matrix")
        print(f"Result: Condition number = {stability.get('condition_number', 0):.2e}")
        print(f"Warning: Matrix is ill-conditioned!")
    except Exception as e:
        print(f"Expected behavior: {e}")

    print("\nMatrix stability checks completed.")


def test_direct_computation():
    """Test using AdjustmentComputations directly"""
    print("\n" + "="*70)
    print("TEST 4: DIRECT MATRIX COMPUTATION")
    print("="*70)

    # Simple example: 3 observations, 2 unknowns
    print("\nNetwork: BM1 (fixed at 100m) -> BM2 (unknown) -> BM3 (unknown)")
    print("         with direct observation BM1 -> BM3")

    # Design matrix
    A = np.array([
        [ 1.0,  0.0],  # BM1->BM2: dH = H2 - H1
        [-1.0,  1.0],  # BM2->BM3: dH = H3 - H2
        [ 0.0,  1.0]   # BM1->BM3: dH = H3 - H1
    ])

    # Observed minus approximate (assuming H2≈110m, H3≈105m)
    # Observed: 10.5m, -5.2m, 5.3m
    # Approximate: 10m, -5m, 5m
    L = np.array([0.5, -0.2, 0.3])

    # Weight matrix (based on distances: 1.5km, 2.0km, 3.5km)
    P = np.diag([1/1.5, 1/2.0, 1/3.5])

    print(f"\nDesign matrix A ({A.shape}):")
    print(A)
    print(f"\nObservation vector L: {L}")
    print(f"\nWeight matrix P diagonal: {np.diag(P)}")

    # Compute
    adj_comp = AdjustmentComputations()
    result = adj_comp.run_linear_adjustment(A, L, P, check_stability=True)

    X = result['X']
    V = result['V']
    sigma_0 = result['sigma_0']

    print(f"\n" + "-"*70)
    print("RESULTS:")
    print("-"*70)
    print(f"Corrections X: {X}")
    print(f"Adjusted H2: {110 + X[0]:.4f} m")
    print(f"Adjusted H3: {105 + X[1]:.4f} m")
    print(f"Residuals V: {V}")
    print(f"Sigma_0: {sigma_0:.4f}")

    if 'stability_info' in result:
        stability = result['stability_info']
        print(f"\nMatrix Stability:")
        print(f"  Condition number: {stability.get('condition_number', 0):.2e}")
        print(f"  Determinant: {stability.get('determinant', 0):.2e}")
        print(f"  Rank: {stability.get('rank', 0)}")


def main():
    """Run all tests"""
    print("="*70)
    print("LEAST SQUARES ADJUSTMENT (LSA) TEST SUITE")
    print("Geodetic Tool v1.1")
    print("="*70)

    try:
        # Test 1: Parametric adjustment
        test_parametric_adjustment()

        # Test 2: Conditional adjustment
        test_conditional_adjustment()

        # Test 3: Matrix stability
        test_matrix_stability()

        # Test 4: Direct computation
        test_direct_computation()

        print("\n" + "="*70)
        print("ALL TESTS COMPLETED SUCCESSFULLY")
        print("="*70 + "\n")

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
