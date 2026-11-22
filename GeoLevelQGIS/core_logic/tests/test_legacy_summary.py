"""Legacy FA0/FA1 layout and decoding (same tests as main's test_db_standalone.py)."""
import os
import tempfile
import unittest

from core_logic.parsers import create_parser
from core_logic.parsers.base_parser import decode_survey_text


LEGACY_FA0 = """  5   2                      NET                                  9
    6177          13.138
      65PU        28.694
    2751á        129.256
    4468            .
    6196          24.823
6177    2751á      5.00000   400.  10   0.50  0520           1.raw
2751á    65PU     10.55600   500.  12  -0.20  0520           1.raw
65PU    4468      -3.95100   300.   8   0.10  0520           1.raw
4468    6196       0.08000   200.   6   0.00  0520           1.raw
6196    6177     -11.68500   393.  11   1.00  0520           1.raw         9
"""

LEGACY_FA1 = """
                                                           DATA
                                                          ******

                                             5   2                      NET                    9



                                                           DATA
                                                          ******
                                              1  6177          13.138
                                              2    65PU        28.694
                                              3  2751á        129.256
                                              4  4468           0.000
                                              5  6196          24.823
0
1
         FROM   TO        DH        DISTANCE   DH-(H2-H1)     ALWD      DELTA    G/á   KOD  YEARS  DURATION   NO.SH
        -----------------------------------------------------------------------------------------------------------
      6177     2751á      5.00000     400.    -0.00100    0.002000    0.001000        0    0- 0     10
      2751á     65PU     10.55600     500.     0.00100    0.002000    0.001000        0    0- 0     12
,                 ===========================================================================
1                                              ITERATION  NO. 1
                                  NO.      ADJUSTED     APPROX          DIF         M.S.E.
                              1 6177        13.13800
                              2   65PU      28.69400
                              3 2751á      129.20000   129.25600     -0.05600     0.002000
                                                           DATA
                                                          ******
                                              1  6177          13.138
                                              2    65PU        28.694
                                              3  2751á        129.200
         FROM   TO        DH        DISTANCE   DH-(H2-H1)     ALWD      DELTA    G/á   KOD  YEARS  DURATION   NO.SH
      6177     2751á      5.00000     400.    -0.00100    0.002000    0.001000        0    0- 0     10
      2751á     65PU     10.55600     500.     0.00100    0.002000    0.001000        0    0- 0     12
"""


class TestLegacySummaryFiles(unittest.TestCase):
    def _write(self, name, text, encoding):
        d = tempfile.mkdtemp()
        p = os.path.join(d, name)
        with open(p, "w", encoding=encoding, newline="\r\n") as f:
            f.write(text)
        return p

    def test_decoding(self):
        self.assertEqual(decode_survey_text("2751á".encode("utf-8"), ["cp1255"])[0],
                         "2751ב")                 # latin-1 look-alike repaired
        self.assertEqual(decode_survey_text("2751ב".encode("cp1255"), ["cp1255"])[0],
                         "2751ב")
        self.assertEqual(decode_survey_text("א á".encode("utf-8"), ["cp1255"])[0],
                         "א á")              # real Hebrew present: untouched

    def test_fa0_header_fixed_count(self):
        for enc, text in (("utf-8", LEGACY_FA0),
                          ("cp1255", LEGACY_FA0.replace("á", "ב"))):
            p = create_parser(self._write("net.FA0", text, enc))
            lines = p.parse_many(p_path := self._write("net.FA0", text, enc))
            self.assertEqual(len(lines), 5)
            p = create_parser(p_path)
            p.parse_many(p_path)
            self.assertEqual(p.benchmarks, {"6177": 13.138, "65PU": 28.694})
            self.assertEqual(p.approx_heights, {"2751ב": 129.256, "6196": 24.823})
            self.assertIn("2751ב", {l.end_point for l in lines})

    def test_fa1_first_table_and_fixed(self):
        path = self._write("net.FA1", LEGACY_FA1, "utf-8")
        p = create_parser(path)
        lines = p.parse_many(path)
        self.assertEqual(len(lines), 2)               # the repeated table is skipped
        self.assertEqual(p.benchmarks, {"6177": 13.138, "65PU": 28.694})
        self.assertEqual(p.approx_heights, {"2751ב": 129.256, "6196": 24.823})

    def test_exporters_write_fixed_count_first(self):
        from core_logic.config.models import Benchmark, MeasurementSummary
        from core_logic.engine.least_squares import LeastSquaresAdjuster
        from core_logic.exporters import FA0Exporter, FA1Exporter
        obs = [MeasurementSummary(from_point=f, to_point=t, height_diff=dh, distance=d,
                                  num_setups=5, bf_diff=0.0, year_month="1125",
                                  source_file="x.raw")
               for f, t, dh, d in (("ZZ1", "A", 1.0, 500.0), ("A", "B", 2.0, 400.0),
                                   ("B", "AA9", -0.5, 300.0), ("AA9", "ZZ1", -2.501, 600.0))]
        fixed = {"ZZ1": 10.0, "AA9": 12.5, "B": 13.0}
        bms = [Benchmark(point_id=k, height=v) for k, v in fixed.items()]
        d = tempfile.mkdtemp()
        FA0Exporter().export(os.path.join(d, "n.FA0"), bms, obs, "n.rez")
        res = LeastSquaresAdjuster().adjust(obs, fixed)
        FA1Exporter().export(os.path.join(d, "n.FA1"), bms, obs, res, "n.rez")
        for ext in ("FA0", "FA1"):
            p = create_parser(os.path.join(d, f"n.{ext}"))
            p.parse_many(os.path.join(d, f"n.{ext}"))
            self.assertEqual(p.benchmarks, fixed, ext)


if __name__ == "__main__":
    unittest.main()
