"""
Standalone benchmark-DB integration: point verification (resolver), password
handling, table-name guard, ITM -> WGS84, legacy FA0/FA1 layout and decoding.

No database server needed: FakeDB emulates the batched SQL of the manager.
"""
import math
import os
import tempfile
import unittest
from types import SimpleNamespace

from geodetic_tool.db import credentials
from geodetic_tool.db.benchmark_db import BenchmarkDBManager
from geodetic_tool.db.resolution import (PointResolver, record_from_dict, record_to_dict,
                                         rename_points_in_lines, resolve_coordinates)
from geodetic_tool.gis import itm
from geodetic_tool.parsers import create_parser
from geodetic_tool.parsers.base_parser import decode_survey_text

# (ot, mispar, name, gova, darga, taarih, ot_k, mispar_k, x=E, y=N, kfar, objectid, hearot)
ROWS = [
    ("A", 250, "250/A", -196.04, "1", None, None, None, 252502.894, 753232.284, "Ginosar", 1, None),
    ("A", 250, "250/A", 95.120, "2", None, None, None, 237100.0, 713400.0, "Gilboa", 2, None),
    ("X", 9001, "9001/X", -150.0, "3", None, None, None, 253800.0, 751800.0, "Ginosar", 3, None),
    ("ב", 2751, "2751/ב", 129.2, "3", None, None, None, 254000.0, 752500.0, "Ginosar", 4, None),
]


def _forms(r):
    ot, mispar, name = r[0], r[1], r[2]
    f = {f"{mispar}/{ot}", f"{mispar}{ot}", f"{ot}{mispar}", name}
    return {s.strip().upper() for s in f}


class FakeDB(BenchmarkDBManager):
    def __init__(self):
        super().__init__()
        self._params = {"host": "fake", "dbname": "fake", "table": "cp_table",
                        "user": "u", "port": 5432, "authcfg": ""}
        self.queries = 0

    def _fetch(self, sql, params, what=""):
        self.queries += 1
        if "unnest" in sql:
            xs, ys, tol = params
            return [(i,) + tuple(r) for i, (x, y) in enumerate(zip(xs, ys), 1)
                    for r in ROWS if math.hypot(r[8] - x, r[9] - y) <= tol]
        if "ST_DWithin" in sql:
            x, y, tol = params
            return [r for r in ROWS if math.hypot(r[8] - x, r[9] - y) <= tol]
        if "LIKE" in sql:                     # search(): substring on names
            q = str(params[0]).strip("%").upper()
            return [r for r in ROWS if q in r[2].upper().replace("/", "")
                    or q in r[2].upper()]
        want = {q.strip().upper() for q in params[0]}
        out = []
        for r in ROWS:
            keys = sorted(_forms(r) & want)
            if keys:
                out.append(tuple(r) + tuple((keys + [None] * 6)[:6]))
        return out


def L(s, e, d):
    return SimpleNamespace(start_point=s, end_point=e, total_distance=d, is_used=True,
                           filename=f"{s}-{e}", setups=[])


class Asker:
    """Scripted Point Verification dialog."""

    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer

    def __call__(self, open_pids, scored, notes=None, statuses=None, allow_defer=False,
                 round_no=0, search_fn=None, assigned=None):
        self.calls.append({"pids": list(open_pids), "statuses": dict(statuses or {}),
                           "scored": scored, "search_fn": search_fn})
        if self.answer is None:
            return None
        return self.answer(open_pids, scored, statuses, search_fn)


class TestResolver(unittest.TestCase):
    def test_auto_match_and_not_in_db(self):
        db = FakeDB()
        lines = [L("250A", "9001X", 1900.0), L("9001X", "2751B", 800.0),
                 L("2751B", "PKT1", 400.0)]

        def answer(pids, scored, statuses, search_fn):
            # only PKT1 (not in the DB) is open; the search works from the dialog
            self.assertEqual(pids, ["PKT1"])
            self.assertEqual(statuses["PKT1"], "not_in_db")
            hits = search_fn("PKT1", "9001")
            self.assertTrue(hits and hits[0].name == "9001/X")
            return {"records": {"PKT1": None}, "modes": {"PKT1": "estimate"}, "manual": {}}

        ask = Asker(answer)
        out = PointResolver(db).resolve(lines, ask)
        self.assertEqual(len(ask.calls), 1)
        self.assertEqual(out.estimated, {"PKT1": "estimate"})
        self.assertGreaterEqual(out.auto_matched, 3)
        # 250A resolved to the Ginosar point (near the measured network), not Gilboa
        self.assertEqual(db.resolve_benchmark("250A").kfar_aher_name, "Ginosar")
        # Hebrew letter: field '2751B' matched DB '2751/ב'; field names unchanged
        self.assertEqual(db.resolve_benchmark("2751B").name, "2751/ב")
        self.assertEqual(lines[1].end_point, "2751B")
        coords = resolve_coordinates(lines, db)
        self.assertFalse(coords["250A"].estimated)
        self.assertTrue(coords["PKT1"].estimated)
        self.assertLessEqual(db.queries, 4)          # batched, not per point

    def test_cancel(self):
        lines = [L("PKT1", "PKT2", 100.0)]
        before = [(l.start_point, l.end_point) for l in lines]
        self.assertIsNone(PointResolver(FakeDB()).resolve(lines, Asker(None)))
        self.assertEqual([(l.start_point, l.end_point) for l in lines], before)

    def test_manual_name_renames_and_is_remembered(self):
        db = FakeDB()
        lines = [L("BM9", "9001X", 1950.0)]

        def answer(pids, scored, statuses, search_fn):
            return {"records": {p: None for p in pids},
                    "modes": {p: "use_selected" for p in pids},
                    "manual": {"BM9": "250/A"}}

        r = PointResolver(db)
        out = r.resolve(lines, Asker(answer))
        self.assertEqual(out.remap, {"BM9": "250/A"})
        self.assertEqual(lines[0].start_point, "250/A")
        self.assertEqual(r.point_overrides["BM9"]["mode"], "use_selected")
        # reload: the saved choice is applied without asking
        lines2 = [L("BM9", "9001X", 1950.0)]
        ask = Asker(None)
        r2 = PointResolver(FakeDB(), r.point_overrides, {})
        out2 = r2.resolve(lines2, ask)
        self.assertEqual(lines2[0].start_point, "250/A")
        self.assertEqual(out2.remap, {"BM9": "250/A"})

    def test_exclude_drops_lines(self):
        lines = [L("PKT1", "9001X", 100.0), L("9001X", "250A", 1900.0)]

        def answer(pids, scored, statuses, search_fn):
            return {"records": {p: None for p in pids},
                    "modes": {p: ("exclude" if p == "PKT1" else "estimate") for p in pids},
                    "manual": {}}
        out = PointResolver(FakeDB()).resolve(lines, Asker(answer))
        self.assertIn("PKT1", out.excluded)
        self.assertEqual(len(lines), 1)

    def test_record_round_trip_and_rename(self):
        rec = FakeDB().get_candidates("9001X", include_twins=False)[0]
        self.assertEqual(record_from_dict(record_to_dict(rec)).key(), rec.key())
        st = SimpleNamespace(from_point="A", to_point="B")
        ln = SimpleNamespace(start_point="A", end_point="B", setups=[st])
        self.assertEqual(rename_points_in_lines([ln], {"A": "B", "B": "C"}), 1)
        self.assertEqual((ln.start_point, ln.end_point, st.from_point, st.to_point),
                         ("B", "C", "B", "C"))


class TestConfigAndCredentials(unittest.TestCase):
    def test_table_name_guard(self):
        db = BenchmarkDBManager()
        db.configure("h", 5432, "d", "u", "public.cp_table", "")
        self.assertTrue(db.is_configured())
        for bad in ("cp_table; DROP TABLE x", "a.b.c", "x--", "t where 1=1"):
            with self.assertRaises(ValueError):
                db.configure("h", 5432, "d", "u", bad, "")

    def test_password_order(self):
        p = {"host": "h", "port": 5432, "dbname": "d", "user": "u"}
        old = os.environ.pop(credentials.ENV_VAR, None)
        try:
            credentials.forget_password(p)
            credentials.set_password(p, "session-pw", remember=False)
            self.assertEqual(credentials.get_password(p), "session-pw")
            os.environ[credentials.ENV_VAR] = "env-pw"
            self.assertEqual(credentials.get_password(p), "env-pw")
        finally:
            os.environ.pop(credentials.ENV_VAR, None)
            if old is not None:
                os.environ[credentials.ENV_VAR] = old
            credentials.forget_password(p)
        self.assertTrue(credentials.is_auth_error(
            'FATAL:  password authentication failed for user "u"'))
        self.assertFalse(credentials.is_auth_error("could not connect to server"))


class TestITM(unittest.TestCase):
    # reference values: PROJ (pyproj), "Israel 1993 to WGS 84 (2)"
    REF = [((182709.630, 611990.037), (34.817237599829596, 31.599643286999356)),
           ((252502.894, 753232.284), (35.55754886315815, 32.87341954424534)),
           ((219529.584, 626907.390), (35.20521378192299, 31.734763036508706)),
           ((170000.0, 400000.0), (34.69347053875334, 29.687095158296895))]

    def test_builtin_matches_proj(self):
        saved = (itm._transformer, itm._tried_pyproj)
        itm._transformer, itm._tried_pyproj = None, True      # force the fallback
        try:
            for (e, n), (lon, lat) in self.REF:
                lo, la = itm.itm_to_wgs84(e, n)
                self.assertAlmostEqual(lo, lon, places=7)
                self.assertAlmostEqual(la, lat, places=7)
        finally:
            itm._transformer, itm._tried_pyproj = saved


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
        from geodetic_tool.config.models import Benchmark, MeasurementSummary
        from geodetic_tool.engine.least_squares import LeastSquaresAdjuster
        from geodetic_tool.exporters import FA0Exporter, FA1Exporter
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


class TestLegacyLayout(unittest.TestCase):
    """FA0 / FA1 are written in the legacy program's exact layout."""

    def _net(self):
        from geodetic_tool.config.models import Benchmark, MeasurementSummary
        rows = [("64PU", "875U", -43.1156, 663.0, 24, 0.87, "0320"),
                ("875U", "117", 10.6667, 993.0, 19, 1.66, "0620"),
                ("117", "2751ב", 5.0, 400.0, 10, -3.98, "0620"),
                ("2751ב", "64PU", 27.4505, 500.0, 12, 0.5, "0520")]
        obs = [MeasurementSummary(from_point=f, to_point=t, height_diff=dh, distance=d,
                                  num_setups=n, bf_diff=bf, year_month=ym, source_file="973.raw")
               for f, t, dh, d, n, bf, ym in rows]
        return obs, [Benchmark(point_id="64PU", height=75.795)]

    def test_fa0_rows_and_idempotent(self):
        from geodetic_tool.exporters import FA0Exporter, legacy_point_id
        self.assertEqual([legacy_point_id(p) for p in ("64PU", "117", "2751ב", "PKT1")],
                         ["  64PU", " 117", "2751ב", "PKT1"])
        obs, bms = self._net()
        d = tempfile.mkdtemp()
        a = os.path.join(d, "a.FA0")
        FA0Exporter().export(a, bms, obs, "NET", approx_heights={"875U": 32.67})
        raw = open(a, "rb").read()
        self.assertIn(b"\r\n", raw)
        rows = raw.decode("cp1255").split("\r\n")
        self.assertEqual(rows[0], "   4   1" + " " * 22 + "NET".ljust(49) + "9")
        self.assertEqual(rows[1], "      64PU        75.795")
        self.assertEqual(rows[2], "     117            .")            # unknown
        self.assertEqual(rows[4], "     875U         32.67")          # decimal-aligned
        self.assertEqual(rows[5], "  64PU    875U      -43.11560   663.  24   0.87  0320"
                                  "           973.raw       ")
        self.assertTrue(rows[-2].endswith("973.raw        9 "))
        # read back and write again: identical file
        p = create_parser(a)
        lines = p.parse_many(a)
        from geodetic_tool.engine.least_squares import lines_to_observations
        from geodetic_tool.config.models import Benchmark
        b = os.path.join(d, "b.FA0")
        FA0Exporter().export(b, [Benchmark(point_id=k, height=v) for k, v in p.benchmarks.items()],
                             lines_to_observations(lines), "NET", approx_heights=p.approx_heights)
        self.assertEqual(open(b, "rb").read(), raw)

    def test_fa1_two_iterations_alwd_3mm(self):
        from geodetic_tool.engine.least_squares import LeastSquaresAdjuster
        from geodetic_tool.exporters import FA1Exporter
        obs, bms = self._net()
        res = LeastSquaresAdjuster().adjust(obs, {"64PU": 75.795})
        path = os.path.join(tempfile.mkdtemp(), "n.FA1")
        FA1Exporter().export(path, bms, obs, res, "NET", leveling_class="H3",
                             approx_heights={"875U": 32.67})
        txt = open(path, "rb").read().decode("cp1255")
        self.assertEqual(txt.count("ITERATION  NO. 1"), 1)
        self.assertEqual(txt.count("ITERATION  NO. 2"), 1)
        self.assertEqual(txt.count("                                                           DATA"), 3)
        # ALWD = 3 mm * sqrt(0.663 km) whatever the class
        self.assertIn("    0.002443", txt)
        # last observation row of each table carries KOD 9
        self.assertEqual(txt.count("    9    0- 0      0"), 2)
        p = create_parser(path)
        self.assertEqual(len(p.parse_many(path)), 4)
        self.assertEqual(p.benchmarks, {"64PU": 75.795})


class TestDuplicateGuard(unittest.TestCase):
    def test_same_mark_two_ids(self):
        from geodetic_tool.db.resolution import duplicate_assignments
        db = FakeDB()
        a = db.get_candidates("9001X", include_twins=False)[0]
        near = record_from_dict(dict(record_to_dict(a), name="X9", mispar_nekuda=9,
                                     objectid=99, x=a.x + 0.4, y=a.y))
        far = db.get_candidates("2751B", include_twins=False)[0]
        self.assertEqual([(p, o) for p, o, _ in duplicate_assignments({"117": near}, {"9001X": a})],
                         [("117", "9001X")])
        self.assertEqual(duplicate_assignments({"117": far}, {"9001X": a}), [])
