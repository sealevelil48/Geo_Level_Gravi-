"""
Standalone test for BenchmarkDBManager point disambiguation
(twin rule, normal-radius filter, measured-length topology check,
kfula mapping and the _normalize_name crash fix).

Run:  python test_point_resolution.py

A fake DB is emulated by overriding _fetch(); every other code path
(get_candidates_batch, get_spatial_twins, establish_anchor,
score_candidates, pick_unambiguous, _resolve_by_proximity) is real.

Coordinates are ITM 2005 as returned by ST_X/ST_Y(geom_full):
x = Easting, y = Northing.  250/A and 849/L come from the field CSV
(Ginosar, north Kineret, 0.30 m apart).  A fake 250/A in Gilboa sits
~43 km to the south-west.
"""
import math
import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "GeoLevelQGIS"))

from geolevel_db_manager import BenchmarkDBManager, group_twins  # noqa: E402

# (ot, mispar, name, gova, darga, taarih, ot_k, mispar_k, x=E, y=N, kfar, objectid, hearot)
ROWS = [
    ("A", 250, "250/A", -196.04, "1", "2006-03-29", None, None,
     252502.894, 753232.284, "Ginosar", 440212, "= 849L"),
    ("L", 849, "849/L", -194.90, None, None, None, None,
     252502.84, 753232.58, "Ginosar", 440215, "= BM 250A"),
    ("A", 250, "250/A", 95.120, "2", None, None, None,          # fake Gilboa duplicate
     237100.0, 713400.0, "Gilboa", 900001, None),
    ("X", 9001, "9001/X", -150.0, "3", None, None, None,        # unique, 1.9 km from Ginosar
     253800.0, 751800.0, "Ginosar", 900002, None),
    ("K", 5555, "5555/K", 10.0, "3", None, "Q", 777,            # reachable only via kfula
     253000.0, 752000.0, "Ginosar", 900003, None),
]


def _forms(r):
    ot, mispar, name = r[0], r[1], r[2]
    f = {f"{mispar}/{ot}", f"{mispar}{ot}", f"{ot}{mispar}", name}
    if r[7] is not None:
        f |= {f"{r[7]}/{r[6] or ''}", f"{r[7]}{r[6] or ''}"}
    return {s.strip().upper() for s in f}


class FakeDB(BenchmarkDBManager):
    def __init__(self):
        super().__init__()
        self._params = {"table": "cp_table", "authcfg": "x"}
        self.queries = 0

    def _fetch(self, sql, params, what=""):
        self.queries += 1
        if "ST_DWithin" in sql:
            x, y, tol = params
            return [r for r in ROWS if math.hypot(r[8] - x, r[9] - y) <= tol]
        q = params[0].strip().upper()
        return [r for r in ROWS if q in _forms(r)]


def line(s, e, dist):
    return SimpleNamespace(start_point=s, end_point=e, total_distance=dist,
                           filename=f"{s}-{e}.DAT", setups=[])


def check(cond, msg):
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond:
        check.failed += 1


check.failed = 0

# ---------------------------------------------------------------- 1. crash fix
db = FakeDB()
try:
    batch = db.get_candidates_batch(["250A", "849L", "9001X", "777Q"])
    check(True, "get_candidates_batch runs (no _normalize_name AttributeError)")
except AttributeError as exc:
    check(False, f"get_candidates_batch raised {exc}")
    sys.exit(1)

# ---------------------------------------------------------------- 2. kfula mapping
check([r.name for r in batch["777Q"]] == ["5555/K"],
      "kfula-only match (777Q -> 5555/K) is kept, not dropped by name comparison")

# ---------------------------------------------------------------- 3. twins
names_250 = sorted(r.name for r in batch["250A"])
check(names_250 == ["250/A", "250/A", "849/L"],
      f"250A candidates = Kineret 250/A, Gilboa 250/A + twin 849/L  ({names_250})")
twin = [r for r in batch["250A"] if r.name == "849/L"][0]
check(twin.twin_of == "250/A" and abs(twin.twin_dist_m - 0.30) < 0.02,
      f"849/L tagged as twin of 250/A at {twin.twin_dist_m:.3f} m")
check(any(r.twin_of == "849/L" for r in batch["849L"]),
      "849L candidates include 250/A as twin")
clusters = group_twins(batch["250A"])
check(len(clusters) == 2, f"250A candidates form 2 location clusters ({len(clusters)})")

# ---------------------------------------------------------------- 4. anchor + radius (Kineret survey)
lines_k = [line("250A", "9001X", 2000.0), line("9001X", "777Q", 1500.0)]
anchor = db.establish_anchor(batch)
check(anchor is not None and math.hypot(anchor[0] - 253400, anchor[1] - 751900) < 2000,
      f"anchor established near Ginosar from unambiguous points: {anchor}")
scored = db.score_candidates("250A", batch["250A"], lines_k, batch)
gilboa = [r for r in scored if r.kfar_aher_name == "Gilboa"][0]
check(gilboa.in_radius is False, f"Gilboa 250/A out of 15 km radius (d={gilboa.dist_to_anchor_m/1000:.1f} km)")
check(gilboa.topo_ok is False, "Gilboa 250/A fails measured-length check (2 km line, ~40 km away)")
check(scored[-1] is gilboa, "Gilboa 250/A ranked last")
check(db.pick_unambiguous(scored) is None,
      "250A NOT auto-approved: 250/A + 849/L twins force the user to choose")

s9001 = db.score_candidates("9001X", batch["9001X"], lines_k, batch)
check(db.pick_unambiguous(s9001) is not None, "9001X (single in-radius location) auto-approved")

# ---------------------------------------------------------------- 5. reverse: Gilboa survey
db2 = FakeDB()
b2 = db2.get_candidates_batch(["250A"])
db2._resolved_coords["GILBOA1"] = (237500.0, 713000.0)   # unambiguous Gilboa point
db2.establish_anchor(b2)
s2 = db2.score_candidates("250A", b2["250A"], [], b2)
check(s2[0].kfar_aher_name == "Gilboa" and s2[0].in_radius is True,
      "Gilboa survey: Gilboa 250/A ranked first, in radius")
check(all(r.in_radius is False for r in s2 if r.kfar_aher_name == "Ginosar"),
      "Gilboa survey: Kineret 250/A and 849/L out of radius")
check(db2.pick_unambiguous(s2) is not None and db2.pick_unambiguous(s2).kfar_aher_name == "Gilboa",
      "Gilboa survey: Gilboa 250/A auto-approved (Kineret twins are out of radius)")

# ---------------------------------------------------------------- 6. _resolve_by_proximity honours radius
db3 = FakeDB()
db3._anchor = (237500.0, 713000.0)
win = db3._resolve_by_proximity([r for r in b2["250A"] if r.name == "250/A"], "250A")
check(win.kfar_aher_name == "Gilboa", "_resolve_by_proximity picks in-radius candidate")
db3._anchor = (200000.0, 600000.0)   # nothing within 15 km
win = db3._resolve_by_proximity(list(b2["250A"]), "250A")
check(win is not None, "_resolve_by_proximity never returns None when all are out of radius")

# ---------------------------------------------------------------- 7. radius setting
db.set_search_radius_km(50)
s50 = db.score_candidates("250A", batch["250A"], [], batch)
check(all(r.in_radius for r in s50), "radius 50 km -> Gilboa now inside radius")

# ---------------------------------------------------------------- 8. per-pid copies
check(batch["250A"][0] is not batch["849L"][-1], "records are per-pid copies (no annotation leakage)")

# ---------------------------------------------------------------- 9. same-name duplicate rows, different heights
from geolevel_db_manager import BenchmarkRecord  # noqa: E402


def rec(name, x, y, h, oid):
    return BenchmarkRecord("A", 1, name, h, None, None, None, None, x, y, objectid=oid)


dup = [rec("1/A", 100.0, 100.0, 5.000, 1), rec("1/A", 100.4, 100.0, 5.120, 2)]
check(BenchmarkDBManager.pick_unambiguous(dup) is None,
      "same-name rows < 1 m apart with different heights -> user must choose")
same = [rec("1/A", 100.0, 100.0, 5.000, 1), rec("1/A", 100.4, 100.0, 5.000, 2)]
check(BenchmarkDBManager.pick_unambiguous(same) is not None,
      "same-name rows < 1 m apart with identical heights -> auto-approved")
nocoord = [rec("1/A", 100.0, 100.0, 5.0, 1), rec("1/A", None, None, 5.0, 2)]
check(BenchmarkDBManager.pick_unambiguous(nocoord) is None,
      "a candidate without coordinates sends the point to the user")

# ---------------------------------------------------------------- 10. forget() drops layer-seeded alias keys
db4 = FakeDB()
db4._resolved_coords["250/A"] = (237100.0, 713400.0)   # seeded from layer under raw value
db4._cache["250A"] = None
db4.forget("250A")
check("250/A" not in db4._resolved_coords and "250A" not in db4._cache,
      "forget('250A') also removes seeded '250/A' coordinate (no anchor bias)")

# ---------------------------------------------------------------- 11. network matching (boss's drawing)
# Chain 1 -A- 2 -B- 3 -C- 4 -D- 5, lines ~1 km.  Point 1 has a single DB
# location (anchor); 2..4 each have a true location plus a namesake in
# another city ~40 km away; 5 is a PKT (not in DB).
def R(name, x, y, oid, h=0.0):
    return BenchmarkRecord("A", 1, name, h, None, None, None, None, x, y, objectid=oid)


TRUE = {"P1": (200000, 700000), "P2": (200800, 700500), "P3": (201600, 700100),
        "P4": (202400, 700600)}
FAR = (40000, -15000)
net_batch = {
    "P1": [R("1/A", *TRUE["P1"], 1)],
    "P2": [R("2/A", *TRUE["P2"], 2), R("2/A", TRUE["P2"][0] + FAR[0], TRUE["P2"][1] + FAR[1], 12)],
    "P3": [R("3/A", TRUE["P3"][0] + FAR[0], TRUE["P3"][1] + FAR[1], 13), R("3/A", *TRUE["P3"], 3)],
    "P4": [R("4/A", *TRUE["P4"], 4), R("4/A", TRUE["P4"][0] - FAR[0], TRUE["P4"][1], 14)],
    "PKT5": [],
}


def L(s, e, d):
    return line(s, e, d)


chain = [L("P1", "P2", 1000.0), L("P2", "P3", 950.0), L("P3", "P4", 1000.0), L("P4", "PKT5", 600.0)]
pids = list(net_batch)
dbn = FakeDB()
net = dbn.resolve_network(pids, chain, net_batch)
check(net.status["P1"] == "anchor", f"P1 is the anchor ({net.status['P1']})")
check(all(net.status[p] == "matched" for p in ("P2", "P3", "P4")),
      f"P2..P4 matched one by one along the chain ({[net.status[p] for p in ('P2','P3','P4')]})")
check(all(net.chosen[p].objectid == int(p[1]) for p in ("P2", "P3", "P4")),
      "the correct namesake chosen for every duplicated point")
check(net.status["PKT5"] == "not_in_db", "PKT5 flagged as not in DB (user is asked / PKT estimate)")
netr = FakeDB().resolve_network(list(reversed(pids)), chain, net_batch)
check(netr.iterations >= 3 and all(netr.status[p] == "matched" for p in ("P2", "P3", "P4")),
      f"recursive propagation converges regardless of order ({netr.iterations} iterations)")

# Outlier 1: everything duplicated -> no anchor -> all go to the user
nb2 = dict(net_batch)
nb2["P1"] = [R("1/A", *TRUE["P1"], 1), R("1/A", TRUE["P1"][0] + FAR[0], TRUE["P1"][1] + FAR[1], 11)]
net2 = FakeDB().resolve_network(pids, chain, nb2)
check(not any(s in ("anchor", "matched") for s in net2.status.values()),
      "everything duplicated -> no automatic anchor")
check(set(net2.unresolved(pids)) == set(pids), "all points go to the dialog")
# ... user picks P1 -> matching continues from it
net3 = FakeDB().resolve_network(pids, chain, nb2, fixed={"P1": nb2["P1"][0]})
check(all(net3.status[p] == "matched" for p in ("P2", "P3", "P4")),
      "after the user picks one anchor, the rest are matched automatically")

# Conflict: the only DB location of P3 is the far namesake (true point not in DB)
nb4 = dict(net_batch)
nb4["P3"] = [R("3/A", TRUE["P3"][0] + FAR[0], TRUE["P3"][1] + FAR[1], 13)]
net4 = FakeDB().resolve_network(pids, chain, nb4)
check(net4.status["P3"] == "conflict", f"wrong-only namesake flagged as conflict ({net4.status['P3']})")
check(net4.status["P4"] in ("ambiguous", "no_link", "conflict"),
      f"P4 cannot be matched through the bad P3 ({net4.status['P4']})")

# Twins found by distance still need a human choice
nb5 = dict(net_batch)
nb5["P2"] = net_batch["P2"] + [R("9/Z", TRUE["P2"][0] + 0.3, TRUE["P2"][1], 99, h=1.1)]
net5 = FakeDB().resolve_network(pids, chain, nb5)
check(net5.status["P2"] == "identity" and "P2" in net5.known,
      "twin at the matched location -> located, but user chooses the name")
check(net5.status["P3"] == "matched", "matching continues past the twin point")

print()
print("ALL PASSED" if not check.failed else f"{check.failed} FAILED")
sys.exit(1 if check.failed else 0)
