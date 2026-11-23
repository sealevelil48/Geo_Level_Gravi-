"""
Round-trip tests for the summary-file readers.

Files are written with the plugin's own exporters (FA0Exporter, FA1Exporter,
REZExporter), read back through the normal import path (create_parser ->
parse_many), and every line in the file must come back as its own editable
line, with the known heights kept.  Also checks the GeoPackage round trip of
the imported lines (lv_lines / lv_setups tables).
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core_logic.config.models import (  # noqa: E402
    Benchmark, LevelingLine, LineStatus, MeasurementSummary)
from core_logic.engine.least_squares import LeastSquaresAdjuster  # noqa: E402
from core_logic.exporters import FA0Exporter, FA1Exporter, REZExporter  # noqa: E402
from core_logic.parsers.base_parser import FileFormat, create_parser, detect_file_format  # noqa: E402

DATA = [("5709MPI", "5710MPI", -1.23456, 812.4, 8),
        ("5710MPI", "5709MPI", 1.23511, 815.0, 8),
        ("5710MPI", "PKT2", 0.51234, 402.0, 4),
        ("PKT2", "402U", 2.00012, 1200.7, 12),
        ("402U", "3349MPI", -4.50021, 590.6, 6),
        ("3349MPI", "5709MPI", 3.27010, 1500.0, 14)]
KNOWN = {"5709MPI": 34.180}
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def _obs():
    return [MeasurementSummary(from_point=f, to_point=t, height_diff=dh, distance=d,
                               num_setups=n, bf_diff=0.15, year_month="1125",
                               source_file=f"ASK{i:02d}.DAT")
            for i, (f, t, dh, d, n) in enumerate(DATA)]


def _lines_from_obs():
    out = []
    for i, (f, t, dh, d, n) in enumerate(DATA):
        ln = LevelingLine(filename=f"ASK{i:02d}", start_point=f, end_point=t,
                          total_distance=d, total_height_diff=dh)
        out.append(ln)
    return out


class SummaryImportBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def check_lines(self, lines, expect_setups=True):
        self.assertEqual(len(lines), len(DATA), "one line per row in the file")
        for ln, (f, t, dh, d, n) in zip(lines, DATA):
            self.assertIsInstance(ln, LevelingLine)
            self.assertEqual((ln.start_point, ln.end_point), (f, t))
            self.assertAlmostEqual(ln.total_height_diff, dh, places=5)
            self.assertAlmostEqual(ln.total_distance, d, delta=0.6)   # FA0 rounds to 1 m
            self.assertTrue(ln.is_summary)
            self.assertAlmostEqual(sum(s.height_diff for s in ln.setups), dh, places=9)
            if expect_setups:
                self.assertEqual(ln.num_setups, n)
        self.assertEqual(len({ln.filename for ln in lines}), len(lines), "unique names")


class TestFA0(SummaryImportBase):
    def test_round_trip(self):
        path = os.path.join(self.tmp, "net.FA0")
        FA0Exporter().export(path, [Benchmark(point_id=k, height=v) for k, v in KNOWN.items()],
                             _obs(), "net.rez")
        self.assertEqual(detect_file_format(path), FileFormat.FA0)
        p = create_parser(path)
        lines = p.parse_many(path)
        self.check_lines(lines)
        self.assertEqual(p.benchmarks, KNOWN)
        self.assertIn("ASK00", lines[0].filename)

    def test_point_name_starting_with_digits_41(self):
        """Rows starting with '41'/'11' used to be detected as Leica GSI."""
        path = os.path.join(self.tmp, "gsi_like.fa0")
        with open(path, "w", encoding="cp1255") as f:
            f.write("  2   2            x.rez                                   9\n")
            f.write("    4101          12.000\n    1105               .\n")
            f.write("4101    1105         0.50000    300.   4   0.00  1125   A.DAT\n 9\n")
        self.assertEqual(detect_file_format(path), FileFormat.FA0)
        lines = create_parser(path).parse_many(path)
        self.assertEqual((lines[0].start_point, lines[0].end_point), ("4101", "1105"))

    def test_empty(self):
        with self.assertRaises(ValueError):
            create_parser(os.path.join(DATA_DIR, "empty.FA0")).parse_many(
                os.path.join(DATA_DIR, "empty.FA0"))


class TestREZ(SummaryImportBase):
    def test_round_trip(self):
        path = os.path.join(self.tmp, "net.rez")
        lines_out = _lines_from_obs()
        for ln, (*_x, n) in zip(lines_out, DATA):
            from core_logic.parsers.summary_common import summary_line
            ln.setups = summary_line("x", 1, ln.start_point, ln.end_point,
                                     ln.total_height_diff, ln.total_distance, n).setups
        lines_out[2].status = LineStatus.EXCEEDED_TOLERANCE
        lines_out[2].is_used = True
        REZExporter().export(path, lines_out, "net")
        self.assertEqual(detect_file_format(path), FileFormat.REZ)
        lines = create_parser(path).parse_many(path)
        self.check_lines(lines)
        self.assertEqual(lines[2].status, LineStatus.EXCEEDED_TOLERANCE)

    def test_empty(self):
        with self.assertRaises(ValueError):
            create_parser(os.path.join(DATA_DIR, "empty.rez")).parse_many(
                os.path.join(DATA_DIR, "empty.rez"))


class TestFA1(SummaryImportBase):
    def test_round_trip(self):
        obs = _obs()
        res = LeastSquaresAdjuster().adjust(obs, KNOWN)
        path = os.path.join(self.tmp, "net.FA1")
        FA1Exporter().export(path, [Benchmark(point_id=k, height=v) for k, v in KNOWN.items()],
                             obs, res, "net.rez")
        self.assertEqual(detect_file_format(path), FileFormat.FA1)
        p = create_parser(path)
        lines = p.parse_many(path)
        # the legacy FA1 layout prints NO.SH 0: setup counts come from the FA0
        self.check_lines(lines, expect_setups=False)
        self.assertEqual(p.benchmarks, KNOWN)


class TestEditingAndGeoPackage(SummaryImportBase):
    def test_toggle_and_gpkg_round_trip(self):
        import geo_level_gpkg as gp
        path = os.path.join(self.tmp, "net.FA0")
        FA0Exporter().export(path, [], _obs(), "net.rez")
        lines = create_parser(path).parse_many(path)
        lines[0].toggle_direction()
        lines[1].is_used = False
        lines[2].manager_override = True
        lines[2].status = LineStatus.VALID_BY_MANAGER
        gpkg = os.path.join(self.tmp, "p.gpkg")
        gp.write_attribute_tables(gpkg, lines, {"leveling_class": "H3"})
        back, meta = gp.read_attribute_tables(gpkg)
        self.assertEqual(len(back), len(lines))
        for a, b in zip(lines, back):
            self.assertEqual((a.filename, a.start_point, a.end_point),
                             (b.filename, b.start_point, b.end_point))
            self.assertAlmostEqual(a.total_height_diff, b.total_height_diff, places=9)
            self.assertEqual(a.num_setups, b.num_setups)
            self.assertEqual(a.is_used, b.is_used)
            self.assertEqual(a.manager_override, b.manager_override)
            self.assertEqual(a.status, b.status)
            self.assertTrue(b.is_summary)
        self.assertEqual(back[0].start_point, "5710MPI")


if __name__ == "__main__":
    unittest.main()
