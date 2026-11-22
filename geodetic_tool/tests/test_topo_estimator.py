"""Dijkstra position estimator for points missing from the DB."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from geodetic_tool.config.models import LevelingLine  # noqa: E402
from geodetic_tool.gis.topo_estimator import estimate_positions  # noqa: E402


def L(s, e, d):
    return LevelingLine(filename=f"{s}-{e}", start_point=s, end_point=e, total_distance=d)


class TestTopoEstimator(unittest.TestCase):
    def test_chain_between_two_anchors(self):
        # Anchor A (0,0) -> PKT1 -> PKT2 -> Anchor B (1000,0); 200 + 300 + 500 m
        lines = [L("A", "PKT1", 200), L("PKT1", "PKT2", 300), L("PKT2", "B", 500)]
        est = estimate_positions(lines, {"A": (0.0, 0.0), "B": (1000.0, 0.0)})
        self.assertAlmostEqual(est["PKT1"][0], 200.0)
        self.assertAlmostEqual(est["PKT2"][0], 500.0)
        self.assertAlmostEqual(est["PKT2"][1], 0.0)

    def test_dict_rows_and_single_anchor(self):
        rows = [{"start_point": "A", "end_point": "X", "total_distance": 100},
                {"start_point": "X", "end_point": "Y", "total_distance": 100}]
        est = estimate_positions(rows, {"A": (0.0, 0.0)})
        self.assertAlmostEqual((est["Y"][0] ** 2 + est["Y"][1] ** 2) ** 0.5, 200.0)

    def test_long_distances_no_radius_limit(self):
        lines = [L("N", "PKT", 150_000), L("PKT", "S", 150_000)]
        est = estimate_positions(lines, {"N": (200000.0, 750000.0), "S": (200000.0, 450000.0)})
        self.assertAlmostEqual(est["PKT"][1], 600000.0)

    def test_three_direct_neighbours(self):
        # previously crashed (NameError) in the layer builder
        lines = [L("A", "P", 100), L("B", "P", 300), L("C", "P", 500)]
        est = estimate_positions(lines, {"A": (0.0, 0.0), "B": (400.0, 0.0), "C": (0.0, 900.0)})
        self.assertAlmostEqual(est["P"][0], 100.0)

    def test_disconnected_not_estimated(self):
        est = estimate_positions([L("Q", "R", 10)], {"A": (0.0, 0.0)})
        self.assertEqual(est, {})


if __name__ == "__main__":
    unittest.main()
