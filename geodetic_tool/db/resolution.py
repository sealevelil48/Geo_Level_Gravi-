"""
Point verification for the standalone app: matches the field point IDs of
newly loaded lines to benchmark-database records before they enter the
project.  GUI-independent port of the QGIS plugin's
``_check_point_resolution`` / ``_apply_resolution``.

Pipeline
  1. Saved choices (point overrides kept in the project) are applied first.
  2. Candidates for all other IDs in ONE batched lookup, including < 1 m
     spatial twins (``get_candidates_batch``).
  3. Network matching (``resolve_network``): anchors are points with one DB
     location; duplicated names are matched by comparing DB distances with
     the distances measured in the files.
  4. Points still open go to *ask* (the Point Verification dialog).  The user
     may decide only some of them ("Decide later" for the rest); matching
     then re-runs from those choices, round after round.

The dialog is passed in as a callable so the logic runs (and is tested)
without Tk:

    ask(open_pids, scored, notes, statuses, allow_defer, round_no, search_fn)
        -> {"records": {pid: rec}, "modes": {pid: mode}, "manual": {pid: name}}
           or None when the user cancels loading.
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

from .benchmark_db import BenchmarkRecord

logger = logging.getLogger(__name__)

AskFn = Callable[..., Optional[dict]]

RECORD_KEYS = ("ot_nekuda", "mispar_nekuda", "name", "gova_ort", "shem_darga_gova",
               "taarih_gova_ort", "ot_nekuda_kfula", "mispar_nekuda_kfula",
               "x", "y", "kfar_aher_name", "objectid", "hearot")


def record_to_dict(rec) -> dict:
    """Persistent (non-scoring) fields of a BenchmarkRecord, JSON-safe."""
    out = {}
    for k in RECORD_KEYS:
        v = getattr(rec, k, None)
        if v is not None and not isinstance(v, (str, int, float, bool)):
            v = str(v)                       # e.g. a date
        out[k] = v
    return out


def record_from_dict(d: dict) -> BenchmarkRecord:
    allowed = {f.name for f in dataclasses.fields(BenchmarkRecord)}
    kw = {k: v for k, v in (d or {}).items() if k in allowed}
    for req in ("ot_nekuda", "mispar_nekuda", "name", "gova_ort", "shem_darga_gova",
                "taarih_gova_ort", "ot_nekuda_kfula", "mispar_nekuda_kfula", "x", "y"):
        kw.setdefault(req, None)
    kw["ot_nekuda"] = kw["ot_nekuda"] or ""
    kw["mispar_nekuda"] = kw["mispar_nekuda"] or 0
    kw["name"] = kw["name"] or ""
    return BenchmarkRecord(**kw)


def duplicate_assignments(choices: Dict[str, object], assigned: Dict[str, object],
                          tol_m: float = 1.0) -> List[Tuple[str, str, float]]:
    """Field IDs whose chosen DB record is the same benchmark as (same record,
    or within *tol_m* of) the record of ANOTHER field ID.

    *choices*: the dialog's picks {pid: rec}; *assigned*: points already
    matched {pid: rec}.  Returns [(pid, other_pid, distance_m)].  Two field
    IDs on one mark is almost always a wrong pick (e.g. '117' -> '6177/',
    the twin of fixed point 6177)."""
    out = []
    pool = {p: r for p, r in assigned.items() if r is not None}
    pool.update({p: r for p, r in choices.items() if r is not None})
    for pid, rec in choices.items():
        if rec is None or rec.x is None or rec.y is None:
            continue
        for other, orec in pool.items():
            if other == pid or orec is None:
                continue
            same = hasattr(rec, "key") and hasattr(orec, "key") and rec.key() == orec.key()
            if orec.x is None or orec.y is None:
                if same:
                    out.append((pid, other, 0.0))
                continue
            d = ((rec.x - orec.x) ** 2 + (rec.y - orec.y) ** 2) ** 0.5
            if same or d <= tol_m:
                out.append((pid, other, d))
                break
    return out


def unique_points(lines) -> List[str]:
    return sorted({ln.start_point for ln in lines if ln.start_point}
                  | {ln.end_point for ln in lines if ln.end_point})


def rename_points_in_lines(lines, remap: dict, setups_only: bool = False) -> int:
    """Rename points per *remap* {old: new} in one pass (chained renames such as
    A->B, B->C are not applied twice).  Returns the number of lines whose
    endpoints changed."""
    remap = {o: n for o, n in (remap or {}).items() if o and n and o != n}
    if not remap:
        return 0
    touched = 0
    for ln in lines:
        if not setups_only:
            s0, e0 = ln.start_point, ln.end_point
            ln.start_point = remap.get(s0, s0)
            ln.end_point = remap.get(e0, e0)
            touched += int((ln.start_point, ln.end_point) != (s0, e0))
        for st in getattr(ln, "setups", []) or []:
            fp = getattr(st, "from_point", None)
            tp = getattr(st, "to_point", None)
            if fp in remap:
                st.from_point = remap[fp]
            if tp in remap:
                st.to_point = remap[tp]
    return touched


@dataclass
class ResolutionOutcome:
    remap: Dict[str, str] = field(default_factory=dict)       # field ID -> DB name
    excluded: Set[str] = field(default_factory=set)           # lines dropped
    unuse: Set[str] = field(default_factory=set)              # lines kept, unused
    estimated: Dict[str, str] = field(default_factory=dict)   # pid -> estimate mode
    auto_matched: int = 0
    rounds: int = 0


class PointResolver:
    """Holds the project's point choices and runs the verification.

    point_overrides : {raw field ID: {"mode": ..., "record": {...}|"name": ...}}
                      - saved with the project and re-applied on the next load.
    point_origin    : {current name: raw field ID}
    """

    def __init__(self, db, point_overrides: Optional[dict] = None,
                 point_origin: Optional[dict] = None, log: Callable[[str], None] = None):
        self.db = db
        self.point_overrides: dict = dict(point_overrides or {})
        self.point_origin: dict = dict(point_origin or {})
        self.log = log or (lambda msg: logger.info(msg))

    # ------------------------------------------------------------------ #
    def resolve_manual_name(self, pid: str, manual: str, lines):
        """Look a manually typed DB name up; return a full record or None."""
        try:
            recs = self.db.get_candidates(manual, include_twins=False)
        except Exception:
            recs = []
        if not recs:
            return None
        recs = self.db.score_candidates(pid, recs, lines)
        if len(recs) == 1:
            return recs[0]
        return self.db.pick_unambiguous(recs) or recs[0]

    def make_search_fn(self, lines, batch=None, known=None):
        """search_fn(pid, query) for the dialog: DB search, scored like the
        candidates (distance to the located points, line-length check)."""
        def _search(pid, query):
            return self.db.score_candidates(pid, self.db.search(query), lines,
                                            batch or {}, known=known)
        return _search

    # ------------------------------------------------------------------ #
    def resolve(self, lines: list, ask: AskFn) -> Optional[ResolutionOutcome]:
        """Verify the points of *lines* (renamed / filtered IN PLACE).

        Returns the outcome, or None when the user cancelled loading.  With
        no DB configured nothing happens (empty outcome)."""
        out = ResolutionOutcome()
        db = self.db
        if db is None or not db.is_configured():
            return out

        # 1. saved choices
        pending: List[str] = []
        pre_remap: Dict[str, str] = {}
        for pid in unique_points(lines):
            origin = self.point_origin.get(pid, pid)
            ov = self.point_overrides.get(origin) or self.point_overrides.get(pid)
            if not isinstance(ov, dict) or not ov:
                pending.append(pid)
                continue
            mode = ov.get("mode")
            if mode in ("estimate", "pkt_average"):
                db.accept_record(pid, None)
                out.estimated[pid] = mode
                continue
            if mode == "exclude":
                out.unuse.add(pid)
                continue
            if mode == "rename":
                new_name = (ov.get("name") or "").strip()
                if new_name and new_name != pid:
                    pre_remap[pid] = new_name
                    self.point_origin[new_name] = pid
                continue
            rec = record_from_dict(ov.get("record"))
            name = (rec.name or "").strip() or pid
            db.accept_record(pid, rec)
            db.accept_record(name, rec)
            if origin == pid:
                self.point_origin[name] = pid
            else:
                db.accept_record(origin, rec)
            if name != pid:
                pre_remap[pid] = name
        if pre_remap:
            rename_points_in_lines(lines, pre_remap)
            out.remap.update(pre_remap)
        if not pending:
            self._apply_unuse(lines, out.unuse)
            return out

        for pid in pending:
            db.forget(pid)                      # no unconfirmed earlier pick
            self.point_origin.setdefault(pid, pid)

        # 2. candidates, one batch
        batch = db.get_candidates_batch(pending)

        # 3-4. network matching + dialog rounds
        fixed: Dict[str, Optional[BenchmarkRecord]] = {}
        excluded_now: Set[str] = set()
        merged = {"records": {}, "modes": {}, "manual": {}}
        allow_defer = True
        round_no = 0
        while True:
            round_no += 1
            net = db.resolve_network(pending, lines, batch, fixed=fixed, exclude=excluded_now)
            for pid in pending:
                if pid in fixed or pid in excluded_now:
                    continue
                if net.status.get(pid) in ("anchor", "matched"):
                    fixed[pid] = net.chosen[pid]
                    db.accept_record(pid, net.chosen[pid])
                    out.auto_matched += 1
            open_pids = [p for p in pending if p not in fixed and p not in excluded_now]
            self.log(f"Point matching round {round_no}: {out.auto_matched} matched "
                     f"automatically, {len(open_pids)} open")
            if not open_pids:
                break

            if net.known:
                db.establish_anchor({}, extra_coords=list(net.known.values()),
                                    include_resolved=False)
            else:
                db.establish_anchor(batch)
            scored = {p: db.score_candidates(p, batch.get(p, []), lines, batch,
                                             known=net.known) for p in open_pids}
            notes = {p: net.notes.get(p, "") for p in open_pids}
            if round_no == 1 and not any(net.status.get(p) in ("anchor", "matched", "fixed")
                                         for p in pending):
                for p in open_pids:
                    notes[p] = ("No anchor found (every point is duplicated) - choose one "
                                "or more points you are sure of. " + notes[p]).strip()

            res = ask(open_pids, scored, notes=notes,
                      statuses={p: net.status.get(p, "") for p in open_pids},
                      allow_defer=allow_defer, round_no=round_no,
                      search_fn=self.make_search_fn(lines, batch, net.known),
                      assigned={p: r for p, r in fixed.items() if r is not None})
            if res is None:
                return None

            progress = False
            for pid in open_pids:
                mode = res["modes"].get(pid, "use_selected")
                rec = res["records"].get(pid)
                if mode == "defer":
                    continue
                progress = True
                merged["modes"][pid] = mode
                merged["records"][pid] = rec
                if mode == "exclude":
                    excluded_now.add(pid)
                    continue
                if mode in ("estimate", "pkt_average"):
                    fixed[pid] = None
                    continue
                if pid in res.get("manual", {}):
                    full = self.resolve_manual_name(pid, res["manual"][pid], lines)
                    if full is not None:
                        merged["records"][pid] = full
                        fixed[pid] = full
                    else:
                        merged["manual"][pid] = res["manual"][pid]
                        fixed[pid] = None
                    continue
                if rec is None:                   # "(no DB match)" kept
                    merged["modes"][pid] = "estimate"
                    fixed[pid] = None
                    continue
                fixed[pid] = rec
                db.accept_record(pid, rec)
            allow_defer = progress
            if round_no > len(pending) + 2:
                self.log("Point matching: round limit reached")
                break
        out.rounds = round_no

        # Automatic matches stay pinned in the DB cache under the field ID
        # (no rename, no saved override) - same as the plugin; only the
        # engineer's dialog choices are renamed / remembered.
        if merged["modes"]:
            self.apply(merged, lines, out)
        self._apply_unuse(lines, out.unuse)
        return out

    # ------------------------------------------------------------------ #
    def apply(self, result: dict, lines: list, out: ResolutionOutcome) -> None:
        """Pin the chosen records, rename IDs to the verified DB names, store
        the choices as point overrides and drop excluded points' lines."""
        db = self.db
        remap: Dict[str, str] = {}
        excluded: Set[str] = set()
        manual = result.get("manual", {})
        for pid, rec in result["records"].items():
            mode = result["modes"].get(pid, "use_selected")
            origin = self.point_origin.get(pid, pid)
            if mode == "exclude":
                excluded.add(pid)
                continue
            if mode in ("estimate", "pkt_average"):
                db.accept_record(pid, None)
                self.point_overrides[origin] = {"mode": mode}
                out.estimated[pid] = mode
                continue
            if pid in manual:
                full = self.resolve_manual_name(pid, manual[pid], lines)
                if full is not None:
                    rec = full
                else:
                    new_name = manual[pid].strip()
                    db.forget(new_name)
                    if new_name != pid:
                        remap[pid] = new_name
                        db.forget(pid)
                    self.point_overrides[origin] = {"mode": "rename", "name": new_name}
                    self.point_origin[new_name] = origin
                    continue
            if rec is None:
                continue
            verified = (rec.name or "").strip() or pid
            db.accept_record(verified, rec)
            db.accept_record(pid, rec)
            if verified != pid:
                remap[pid] = verified
            self.point_overrides[origin] = {"mode": "use_selected",
                                            "record": record_to_dict(rec)}
            self.point_origin[verified] = origin
        if excluded:
            lines[:] = [ln for ln in lines
                        if ln.start_point not in excluded and ln.end_point not in excluded]
        rename_points_in_lines(lines, remap)
        out.remap.update(remap)
        out.excluded |= excluded

    @staticmethod
    def _apply_unuse(lines, unuse: Set[str]) -> None:
        for ln in lines:
            if ln.start_point in unuse or ln.end_point in unuse:
                ln.is_used = False


# ---------------------------------------------------------------------- #
# Coordinates for the data model / GeoJSON / adjustment
# ---------------------------------------------------------------------- #

@dataclass
class PointCoordinate:
    easting: float
    northing: float
    height: Optional[float]           # DB orthometric height (None = unknown)
    estimated: bool                   # True = topology estimate, not from the DB
    record: Optional[BenchmarkRecord] = None


def resolve_coordinates(lines, db) -> Dict[str, PointCoordinate]:
    """EPSG:2039 coordinates of every point of *lines*: DB records (already
    pinned by the verification, else resolved with the spatial-median /
    k=1 centroid logic) plus topology estimates for points missing from the
    DB.  One batched lookup."""
    from ..gis.topo_estimator import estimate_positions
    pids = unique_points(lines)
    out: Dict[str, PointCoordinate] = {}
    if db is not None and db.is_configured() and pids:
        db.prefetch(pids)
        for pid in pids:
            rec = db.resolve_benchmark(pid)
            if rec is not None and rec.x is not None and rec.y is not None:
                h = float(rec.gova_ort) if rec.gova_ort is not None else None
                out[pid] = PointCoordinate(float(rec.x), float(rec.y), h, False, rec)
    known = {p: (c.easting, c.northing) for p, c in out.items()}
    if known:
        for pid, (e, n) in estimate_positions(lines, known).items():
            out[pid] = PointCoordinate(e, n, None, True, None)
    return out
