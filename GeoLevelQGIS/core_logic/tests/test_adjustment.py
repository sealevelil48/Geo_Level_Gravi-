"""
Known-answer tests for the leveling adjustment engine.

Reference 1: "חשבון תיאום 1 - תרגיל 9 - תאום מותנה", example 1 (project file
Condition Adjustment_BvW.pdf): 10 lines, 3 benchmarks RFH-95/96/97, unknown
points A, B, C, D.  Published results:
    H_A = 121.344  H_B = 117.573  H_C = 110.190  H_D = 102.764  (m)
    sigma0^2 = 20.362, sigma0 = 4.512 (cm, weights as published)

Reference 2: an independent textbook solution (numpy, written in this file)
on a synthetic network, plus parametric == conditional equivalence.
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core_logic.config.models import MeasurementSummary  # noqa: E402
from core_logic.engine.least_squares import (  # noqa: E402
    ConditionalAdjuster, LeastSquaresAdjuster)
from core_logic.engine.network_prep import (  # noqa: E402
    NetworkDatumError, approximate_heights, independent_conditions,
    merge_observations, tolerance_mm)


def obs(f, t, dh, d, n=4, src=""):
    return MeasurementSummary(from_point=f, to_point=t, height_diff=dh, distance=d,
                              num_setups=n, bf_diff=0.0, year_month="", source_file=src)


# Exercise 9, example 1.  Directions chosen so every measured dH is positive;
# the published weights p_i are reproduced exactly with distance = 1000/p_i m
# (weight = 1 / L_km).
RFH_FIXED = {"RFH95": 142.645, "RFH96": 103.082, "RFH97": 92.242}
RFH_WEIGHTS = [2.1, 2.5, 2.7, 1.9, 1.8, 1.5, 2.5, 2.7, 2.9, 2.7]
RFH_LINES = [("A", "RFH95", 21.243), ("RFH96", "A", 18.224), ("B", "A", 3.759),
             ("B", "RFH95", 25.085), ("RFH97", "B", 25.340), ("C", "RFH95", 32.465),
             ("C", "B", 7.372), ("D", "C", 7.422), ("RFH97", "D", 10.525),
             ("D", "RFH96", 0.325)]
RFH_ADJUSTED_DH = [21.302, 18.261, 3.771, 25.073, 25.330, 32.455, 7.382, 7.426,
                   10.522, 0.318]
RFH_HEIGHTS = {"A": 121.344, "B": 117.573, "C": 110.190, "D": 102.764}


def rfh_observations():
    return [obs(f, t, dh, 1000.0 / p) for (f, t, dh), p in zip(RFH_LINES, RFH_WEIGHTS)]


def reference_solution(observations, fixed):
    unk = sorted({p for o in observations for p in (o.from_point, o.to_point)} - set(fixed))
    idx = {p: i for i, p in enumerate(unk)}
    A = np.zeros((len(observations), len(unk)))
    L = np.zeros(len(observations))
    P = np.zeros(len(observations))
    for i, o in enumerate(observations):
        L[i] = o.height_diff
        if o.from_point in idx:
            A[i, idx[o.from_point]] = -1
        else:
            L[i] += fixed[o.from_point]
        if o.to_point in idx:
            A[i, idx[o.to_point]] = 1
        else:
            L[i] -= fixed[o.to_point]
        P[i] = 1000.0 / o.distance
    N = A.T @ (P[:, None] * A)
    x = np.linalg.solve(N, A.T @ (P * L))
    v = A @ x - L
    return dict(zip(unk, x)), v


class TestTextbookExercise(unittest.TestCase):
    """Condition Adjustment_BvW.pdf, example 1."""

    def check(self, res):
        for p, h in RFH_HEIGHTS.items():
            self.assertAlmostEqual(res.adjusted_heights[p], h, delta=0.0015, msg=p)
        for p, h in RFH_FIXED.items():
            self.assertEqual(res.adjusted_heights[p], h)
        for row, adj in zip(res.observations, RFH_ADJUSTED_DH):
            self.assertAlmostEqual(row["adjusted_dh"], adj, delta=0.0015)
        # sigma0 = 4.512 cm with the published weights
        self.assertAlmostEqual(res.mse_unit_weight * 100.0, 4.512, delta=0.01)
        self.assertEqual(res.degrees_of_freedom, 6)

    def test_parametric(self):
        self.check(LeastSquaresAdjuster().adjust(rfh_observations(), RFH_FIXED))

    def test_conditional(self):
        self.check(ConditionalAdjuster().adjust(rfh_observations(), RFH_FIXED))

    def test_number_of_conditions(self):
        # 4 independent loops + (3 benchmarks - 1) = 6
        conds = independent_conditions(rfh_observations(), RFH_FIXED)
        self.assertEqual(sum(c.kind == "loop" for c in conds), 4)
        self.assertEqual(sum(c.kind == "benchmark" for c in conds), 2)


class TestSyntheticNetwork(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(7)
        self.truth = {"BM1": 100.0, "P2": 100.512, "P3": 99.267, "P4": 99.945,
                      "BM2": 101.300, "P5": 100.700}
        self.fixed = {"BM1": 100.0, "BM2": 101.300}
        topo = [("BM1", "P2", 500), ("P2", "P3", 300), ("P3", "P4", 400),
                ("P4", "BM1", 600), ("P4", "BM2", 700), ("BM2", "P5", 350),
                ("P5", "P2", 450), ("P3", "P5", 800)]
        self.obs = [obs(f, t, self.truth[t] - self.truth[f]
                        + rng.normal(0, 0.001 * np.sqrt(d / 1000)), d)
                    for f, t, d in topo]

    def test_parametric_matches_reference(self):
        ref, ref_v = reference_solution(self.obs, self.fixed)
        res = LeastSquaresAdjuster().adjust(self.obs, self.fixed)
        for p, h in ref.items():
            self.assertAlmostEqual(res.adjusted_heights[p], h, places=9)
        self.assertEqual(res.iteration, 1)
        for row, v in zip(res.observations, ref_v):
            self.assertAlmostEqual(row["residual_mm"], v * 1000, places=6)

    def test_conditional_equals_parametric(self):
        a = LeastSquaresAdjuster().adjust(self.obs, self.fixed)
        b = ConditionalAdjuster().adjust(self.obs, self.fixed)
        for p in a.adjusted_heights:
            self.assertAlmostEqual(a.adjusted_heights[p], b.adjusted_heights[p], places=9)
            self.assertAlmostEqual(a.mse_heights[p], b.mse_heights[p], places=9)
        self.assertAlmostEqual(a.mse_unit_weight, b.mse_unit_weight, places=9)

    def test_corrections_are_small(self):
        res = LeastSquaresAdjuster().adjust(self.obs, self.fixed)
        for p, c in res.corrections.items():
            self.assertLess(abs(c), 0.01, p)

    def test_rough_seed_gives_same_heights(self):
        """Step 2: approximate heights from a rough (0.1 m) value do not
        change the adjusted heights."""
        a = LeastSquaresAdjuster().adjust(self.obs, self.fixed)
        b = LeastSquaresAdjuster().adjust(self.obs, self.fixed,
                                          approximate_heights_in={"P3": 99.3, "P5": 100.6})
        for p in a.adjusted_heights:
            self.assertAlmostEqual(a.adjusted_heights[p], b.adjusted_heights[p], places=9)


class TestDatumCheck(unittest.TestCase):
    def test_disconnected_fixed_points_raise(self):
        # The situation in the field screenshots: fixed points 5709MPI/5714MPI
        # are not connected to any active line.
        o = [obs("3349MPI", "402U", 4.5, 1000), obs("402U", "PKT2", 0.5, 400),
             obs("3349MPI", "PKT1", 9.9, 600), obs("PKT1", "3349MPI", -9.9, 600)]
        for adj in (LeastSquaresAdjuster(), ConditionalAdjuster()):
            with self.assertRaises(NetworkDatumError) as cm:
                adj.adjust(o, {"5709MPI": 34.18, "5714MPI": 27.168})
            self.assertIn("3349MPI", str(cm.exception))
            self.assertIn("5709MPI", str(cm.exception))


class TestStep1Merge(unittest.TestCase):
    def test_forward_return_mean(self):
        # Directive G2 appendix C, first section: 1.9929 / -1.9927, 0.907 km
        merged, rep = merge_observations(
            [obs("A", "1", 1.9929, 907), obs("1", "A", -1.9927, 907)], "H1")
        self.assertEqual(len(merged), 1)
        self.assertAlmostEqual(merged[0].height_diff, 1.9928, places=6)
        self.assertEqual(merged[0].n_runs, 2)
        self.assertAlmostEqual(rep[0].discrepancy_mm, 0.2, places=6)
        # allowed = 3 mm * sqrt(0.907 km) = 2.86 mm (one-direction length)
        self.assertAlmostEqual(rep[0].allowed_mm, 3 * np.sqrt(0.907), places=6)
        self.assertTrue(rep[0].within_tolerance)

    def test_exceeded(self):
        _m, rep = merge_observations(
            [obs("A", "B", 1.000, 1000), obs("B", "A", -1.012, 1000)], "H3")
        self.assertAlmostEqual(rep[0].discrepancy_mm, -12.0, places=6)
        self.assertFalse(rep[0].within_tolerance)   # allowed 10 mm

    def test_same_direction_repeats(self):
        m, rep = merge_observations(
            [obs("A", "B", 1.000, 1000), obs("A", "B", 1.004, 1000),
             obs("B", "A", -1.002, 1000)], "H3")
        self.assertAlmostEqual(m[0].height_diff, 1.002, places=9)
        self.assertEqual(rep[0].n_runs, 3)

    def test_tolerance_formula(self):
        self.assertAlmostEqual(tolerance_mm(5464, "H1"), 7.0, places=1)


class TestStep2Approximate(unittest.TestCase):
    def test_from_known_and_relative(self):
        o = [obs("K", "X", 1.0, 100), obs("X", "Y", 2.0, 100),
             obs("Q", "R", -0.5, 100)]
        ap = approximate_heights(o, {}, {"K": 50.0})
        self.assertAlmostEqual(ap["Y"].height, 53.0)
        self.assertEqual(ap["Y"].seed, "K")
        self.assertEqual(ap["R"].seed_kind, "relative")
        self.assertAlmostEqual(ap["R"].height - ap["Q"].height, -0.5)


if __name__ == "__main__":
    unittest.main()


class TestSingleLineDirectiveExample(unittest.TestCase):
    """Directive G2 (2021), Appendix C example, H1 line A -> B."""

    def test_appendix_c(self):
        from core_logic.config.models import LevelingLine, StationSetup
        from core_logic.engine.network_prep import adjust_single_line
        secs = [(907, 1.9928), (822, 1.7197), (1304, 2.0400), (1162, -3.6503),
                (1271, 8.6983)]
        ln = LevelingLine(filename="AB", start_point="A", end_point="B", setups=[
            StationSetup(i + 1, "", "", 0, 0, d / 2, d / 2, height_diff=dh)
            for i, (d, dh) in enumerate(secs)])
        r = adjust_single_line(ln, 502.320, 513.114, "H1")
        self.assertAlmostEqual(r["misclosure_mm"], 6.4, delta=0.15)
        self.assertAlmostEqual(r["allowed_mm"], 7.0, delta=0.05)
        self.assertTrue(r["ok"])
        for row, h in zip(r["rows"][1:], [506.030, 508.069, 504.417, 513.114]):
            self.assertAlmostEqual(row["height"], h, delta=0.0006)


class TestGeoPackageMeta(unittest.TestCase):
    def test_file_benchmarks_and_last_adjustment_round_trip(self):
        import tempfile
        import geo_level_gpkg as gp
        from core_logic.config.models import LevelingLine
        path = os.path.join(tempfile.mkdtemp(), "p.gpkg")
        meta = {"leveling_class": "H2",
                "file_benchmarks": {"RFH95": 142.645, "bad": "x"},
                "last_adjustment": {"method": "parametric", "sigma0_mm": 1.2, "dof": 3,
                                    "heights": {"A": 121.344}, "evil": object.__name__}}
        gp.write_attribute_tables(path, [LevelingLine("L1", "A", "B")], meta)
        _lines, raw = gp.read_attribute_tables(path)
        m = gp.sanitize_meta(raw)
        self.assertEqual(m["file_benchmarks"], {"RFH95": 142.645})
        self.assertEqual(m["last_adjustment"]["heights"], {"A": 121.344})
        self.assertEqual(m["last_adjustment"]["dof"], 3)
        self.assertNotIn("evil", m["last_adjustment"])
        self.assertEqual(m["leveling_class"], "H2")
