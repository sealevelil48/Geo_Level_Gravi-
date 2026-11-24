"""Double-run check against Directive G2 (2021), Appendix C.

The worked example of Appendix C (line A-1-2-3-4-B, class H1): each section
is measured forward and return; delta = dH_fwd + dH_ret, the allowed value is
k*sqrt(L) with L the ONE-direction length in km, and the mean dH is
(dH_fwd - dH_ret)/2 with the sign of the forward direction.

The example's delta and mean columns are reproduced exactly.  (Its "allowed"
column prints 2*sqrt(L) - e.g. +-1.9 for 0.907 km - although its own header
and Appendix B 4.1 give 3 mm*sqrt(L) for H1; the code follows the formula.)
"""
import math

import pytest

from core_logic.config.models import LevelingLine
from core_logic.engine.loop_detector import LoopAnalyzer, detect_double_runs
from core_logic.engine.network_prep import (
    achieved_class, analyze_double_runs, merge_observations, tolerance_mm)

# (from, to, L km, dH forward, dH return, delta mm, mean dH) - Appendix C
APPENDIX_C = [
    ("A", "1", 0.907, 1.9929, -1.9927, 0.2, 1.9928),
    ("1", "2", 0.822, 1.7192, -1.7201, -0.9, 1.71965),
    ("2", "3", 1.304, 2.0401, -2.0398, 0.3, 2.03995),
    ("3", "4", 1.162, -3.6511, 3.6495, -1.6, -3.6503),
    ("4", "B", 1.271, 8.6981, -8.6985, -0.4, 8.6983),
]


def _line(f, t, dh, dist_m, name, cls=None, used=True):
    ln = LevelingLine(filename=name, start_point=f, end_point=t)
    ln.total_height_diff = dh
    ln.total_distance = dist_m
    ln.leveling_class = cls
    ln.is_used = used
    return ln


def _appendix_lines():
    out = []
    for f, t, L, fwd, ret, *_ in APPENDIX_C:
        out.append(_line(f, t, fwd, L * 1000, f"{f}{t}_fwd"))
        out.append(_line(t, f, ret, L * 1000, f"{f}{t}_ret"))
    return out


def test_appendix_c_example_matches():
    res = analyze_double_runs(_appendix_lines(), "H1")
    assert len(res) == 5
    for r, (f, t, L, fwd, ret, delta, mean) in zip(res, APPENDIX_C):
        assert (r.from_point, r.to_point) == (f, t)
        assert r.n_runs == 2 and not r.same_direction
        assert r.distance == pytest.approx(L * 1000)          # one direction
        assert round(r.discrepancy_mm, 1) == pytest.approx(delta)
        assert r.discrepancy_mm == pytest.approx((fwd + ret) * 1000)
        assert r.mean_dh == pytest.approx((fwd - ret) / 2)
        assert r.mean_dh == pytest.approx(mean, abs=1e-4)
        assert r.allowed_mm == pytest.approx(3.0 * math.sqrt(L))  # H1: 3*sqrt(L)
        assert r.ok and r.achieved == 1
    # whole line: sum of the means = 10.8004 (Appendix C), f_H vs known = 6.4 mm
    total = sum(r.mean_dh for r in res)
    assert total == pytest.approx(10.8004, abs=1e-4)
    assert (total - (513.114 - 502.320)) * 1000 == pytest.approx(6.4, abs=0.05)


def test_same_numbers_as_lsa_step1():
    lines = _appendix_lines()
    _, report = merge_observations(lines, "H1")
    res = analyze_double_runs(lines, "H1")
    for r, m in zip(res, report):
        assert r.mean_dh == pytest.approx(m.mean_dh)
        assert r.discrepancy_mm == pytest.approx(m.discrepancy_mm)
        assert r.allowed_mm == pytest.approx(m.allowed_mm)
        assert r.ok == m.within_tolerance


def test_tolerance_uses_one_direction_length_and_line_class():
    # 4 km one way, 4 mm misclosure.  H1 3*sqrt(4)=6 -> pass.  Had the two
    # runs' total length (8 km) been used it would be 8.5 mm - also pass, so
    # check with 7 mm: one-direction 6.0 -> FAIL (total would wrongly pass).
    fwd = _line("P1", "P2", 1.000, 4000, "f")
    ret = _line("P2", "P1", -1.000 + 0.007, 4000, "r")
    r = analyze_double_runs([fwd, ret], "H1")[0]
    assert r.allowed_mm == pytest.approx(6.0)
    assert r.discrepancy_mm == pytest.approx(7.0)
    assert not r.ok and r.achieved == 2           # H2: 5*sqrt(4) = 10 mm
    # the line's own class wins over the project class
    fwd.leveling_class = "H3"
    r = analyze_double_runs([fwd, ret], "H1")[0]
    assert r.leveling_class == "H3" and r.ok


def test_exceeds_all_classes_and_unused_lines():
    assert achieved_class(1000.0, 1000.0) is None          # > 60 mm (H6)
    assert achieved_class(2.9, 1000.0) == 1
    a = _line("X", "Y", 0.5, 1000, "a")
    b = _line("Y", "X", -0.4, 1000, "b")
    c = _line("Y", "X", -0.5, 1000, "c", used=False)       # excluded line
    res = analyze_double_runs([a, b, c], "H6")
    assert len(res) == 1 and res[0].n_runs == 2
    assert not res[0].ok and res[0].achieved is None


def test_repeat_same_direction_and_three_runs():
    a = _line("M", "N", 2.0000, 500, "a")
    b = _line("M", "N", 2.0010, 500, "b")
    r = analyze_double_runs([a, b], "H3")[0]
    assert r.same_direction
    assert r.discrepancy_mm == pytest.approx(-1.0)
    assert r.mean_dh == pytest.approx(2.0005)
    c = _line("N", "M", -2.0030, 500, "c")
    r3 = analyze_double_runs([a, b, c], "H3")[0]
    assert r3.n_runs == 3
    assert r3.discrepancy_mm == pytest.approx(3.0)           # max - min
    assert r3.mean_dh == pytest.approx((2.0 + 2.001 + 2.003) / 3)


def test_loop_detector_api_consistent():
    lines = _appendix_lines()
    pairs = detect_double_runs(lines)
    assert len(pairs) == 5
    an = LoopAnalyzer(lines)
    for (fwd, ret), (f, t, L, dfw, drt, delta, mean) in zip(pairs, APPENDIX_C):
        res = an.analyze_double_run(fwd, ret, "H1")
        assert res["valid"] and res["within_tolerance"]
        assert round(res["misclosure_mm"], 1) == pytest.approx(delta)
        assert res["mean_dh"] == pytest.approx((dfw - drt) / 2)
        assert res["tolerance_mm"] == pytest.approx(tolerance_mm(L * 1000, 1))
        assert res["distance_one_direction"] == pytest.approx(L * 1000)
    bad = an.analyze_double_run(_line("A", "1", 1, 1, "x"), _line("1", "Z", 1, 1, "y"))
    assert not bad["valid"]
    huge = an.analyze_double_run(_line("A", "B", 1.0, 1000, "x"),
                                 _line("B", "A", -0.5, 1000, "y"), 3)
    assert huge["achieved_class"] is None and huge["class_name"] == "Exceeds H6"


def test_tolerance_follows_live_class_registry():
    from core_logic.config.israel_survey_regulations import CLASS_REGISTRY
    p = CLASS_REGISTRY[3]
    old = p.tolerance_coefficient
    try:
        p.tolerance_coefficient = 12.0          # edited in Class Parameters
        assert tolerance_mm(1000, "H3") == pytest.approx(12.0)
    finally:
        p.tolerance_coefficient = old
    assert tolerance_mm(1000, "H3") == pytest.approx(10.0)
