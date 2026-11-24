"""
Network preparation for leveling adjustment (Directive G2, 2021).

Preliminary steps that run BEFORE any least-squares adjustment:

Step 1 - merge_observations():
    Every height difference enters the adjustment exactly once.  Repeated
    runs of the same section (forward / return, or several runs in the same
    direction) are reduced to the direction of the first (forward) run and
    averaged:   dH_mean = (dH_fwd - dH_ret) / 2   for a forward/return pair.
    The run discrepancy  delta = dH_fwd + dH_ret  is checked against the class
    tolerance  T = k * sqrt(L)  where L is the ONE-direction length in km
    (Directive G2, Appendix B 4.1 and Appendix C).

Step 2 - approximate_heights():
    Approximate heights for every point, propagated along the shortest
    measured path (multi-source Dijkstra on distance) from points with a
    known height.  Any known height works as a seed - even a rough one
    (0.1 m) - because the adjustment itself is linear.

Also provides network topology helpers shared by both adjustment methods:
connected components, datum check, and an independent set of conditions
(fundamental loops of a spanning forest + benchmark-to-benchmark paths).
"""
from __future__ import annotations

import heapq
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from geodetic_tool.config.models import MeasurementSummary


class NetworkDatumError(ValueError):
    """Raised when a group of points has no fixed (known) height."""

    def __init__(self, message: str, components: List[List[str]]):
        super().__init__(message)
        self.components = components


# --------------------------------------------------------------------------- #
# Class tolerances (Directive G2, Appendix B, item 4.1): T = k * sqrt(L_km)
# --------------------------------------------------------------------------- #
CLASS_K_MM = {1: 3.0, 2: 5.0, 3: 10.0, 4: 20.0, 5: 30.0, 6: 60.0}


def class_number(leveling_class) -> int:
    """'H3' / 3 / '3' -> 3 (default 3)."""
    if leveling_class is None:
        return 3
    s = str(leveling_class).strip().upper().lstrip("H")
    try:
        n = int(s)
    except ValueError:
        return 3
    return n if n in CLASS_K_MM else 3


def class_k_mm(leveling_class=3) -> float:
    """k (mm/sqrt(km)) of a class.  Reads the live class registry, so values
    edited in Settings -> Class Parameters apply here exactly as in line
    validation; the Directive defaults (CLASS_K_MM) are the fallback."""
    n = class_number(leveling_class)
    try:
        from geodetic_tool.config.israel_survey_regulations import CLASS_REGISTRY
        k = float(CLASS_REGISTRY[n].tolerance_coefficient)
        if k > 0:
            return k
    except Exception:
        pass
    return CLASS_K_MM[n]


def tolerance_mm(distance_m: float, leveling_class=3) -> float:
    """Allowed misclosure in mm for a one-direction length *distance_m*."""
    return class_k_mm(leveling_class) * math.sqrt(max(distance_m, 0.0) / 1000.0)


def achieved_class(discrepancy_mm: float, distance_m: float) -> Optional[int]:
    """Best (lowest) class whose k*sqrt(L) the discrepancy meets; None when
    it exceeds even H6."""
    for n in sorted(CLASS_K_MM):
        if abs(discrepancy_mm) <= tolerance_mm(distance_m, n) + 1e-9:
            return n
    return None


# --------------------------------------------------------------------------- #
# Step 1: merge repeated runs
# --------------------------------------------------------------------------- #
@dataclass
class MergedRun:
    """Report row for one section after Step 1."""
    from_point: str
    to_point: str
    mean_dh: float              # m, in the direction of the first run
    distance: float             # m, one-direction (mean of the runs)
    n_runs: int
    runs_dh: List[float]        # each run reduced to the forward direction
    sources: List[str]
    discrepancy_mm: Optional[float]   # dH_fwd + dH_ret (None for a single run)
    allowed_mm: float
    within_tolerance: bool


def merge_observations(
    observations: Sequence,
    leveling_class=3,
) -> Tuple[List[MeasurementSummary], List[MergedRun]]:
    """Reduce repeated runs of the same section to one averaged observation.

    *observations* may be MeasurementSummary objects or LevelingLine objects
    (anything with from/start, to/end, height_diff/total_height_diff,
    distance/total_distance).

    Returns (merged_observations, report).  Each merged MeasurementSummary has
    an extra attribute ``n_runs`` used by the adjusters for weighting
    (the mean of n runs has weight n / L).
    """
    groups: Dict[frozenset, List[Tuple[str, str, float, float, int, str]]] = {}
    first_obs: Dict[frozenset, object] = {}
    order: List[frozenset] = []
    for o in observations:
        f, t, dh, d, n, src = _obs_fields(o)
        if not f or not t or f == t:
            continue
        key = frozenset((f, t))
        if key not in groups:
            groups[key] = []
            first_obs[key] = o
            order.append(key)
        groups[key].append((f, t, dh, d, n, src))

    merged: List[MeasurementSummary] = []
    report: List[MergedRun] = []
    for key in order:
        runs = groups[key]
        f0, t0 = runs[0][0], runs[0][1]
        reduced = [dh if f == f0 else -dh for f, t, dh, d, n, s in runs]
        dists = [d for _, _, _, d, _, _ in runs]
        mean_dh = sum(reduced) / len(reduced)
        dist = sum(dists) / len(dists)
        allowed = tolerance_mm(dist, leveling_class)
        if len(reduced) == 1:
            disc = None
            ok = True
        elif len(reduced) == 2:
            # delta = dH_fwd + dH_ret, with dH_ret in its own (reverse) direction
            disc = (reduced[0] - reduced[1]) * 1000.0
            ok = abs(disc) <= allowed
        else:
            disc = (max(reduced) - min(reduced)) * 1000.0
            ok = abs(disc) <= allowed
        sources = [s for *_, s in runs if s]
        if len(runs) == 1:
            # a single run keeps its own columns (FA0 / FA1 export)
            o0 = first_obs[key]
            bf = getattr(o0, "bf_diff", None) or 0.0
            ym = getattr(o0, "year_month", "") or ""
        else:
            bf, ym = disc, ""
        ms = MeasurementSummary(
            from_point=f0, to_point=t0, height_diff=mean_dh, distance=dist,
            num_setups=max(int(r[4] or 0) for r in runs),
            bf_diff=bf, year_month=ym, source_file=" + ".join(sources),
        )
        ms.n_runs = len(reduced)
        merged.append(ms)
        report.append(MergedRun(f0, t0, mean_dh, dist, len(reduced), reduced,
                                sources, disc, allowed, ok))
    return merged, report


@dataclass
class DoubleRunResult:
    """One section measured more than once (Directive G2 App. C check)."""
    from_point: str             # forward direction = first run
    to_point: str
    runs: list                  # the LevelingLine objects, forward first
    leveling_class: str         # class the check was made against ("H3")
    forward_dh: float           # m, first run
    return_dh: Optional[float]  # m, second run in ITS OWN direction (2 runs)
    same_direction: bool        # repeat in the same direction (not fwd/ret)
    mean_dh: float              # m, in the forward direction
    distance: float             # m, one-direction L (mean of the runs)
    discrepancy_mm: float       # delta = dH_fwd + dH_ret (spread for > 2 runs)
    allowed_mm: float           # k * sqrt(L_km)
    ok: bool
    achieved: Optional[int]     # best class met (None = exceeds H6)

    @property
    def n_runs(self) -> int:
        return len(self.runs)


def analyze_double_runs(lines: Sequence, project_class="H3") -> List[DoubleRunResult]:
    """Double-run check of Directive G2 (2021), Appendix C, for every section
    measured more than once (forward / return, or repeats).

    * Pairing: by section (unordered end points), unused lines skipped; the
      first run (input order) is the forward direction.
    * delta = dH_fwd + dH_ret (return in its own direction); with three or
      more runs the spread max - min of the runs reduced to the forward
      direction.
    * |delta| <= k*sqrt(L), L = ONE-direction length in km (mean of the runs),
      k of the line's class (``leveling_class``, else the project class).
    * mean dH = (dH_fwd - dH_ret) / 2, sign of the forward direction.

    The numbers come from :func:`merge_observations` (Step 1 of the
    adjustment), so this table and the LSA use identical values."""
    groups: Dict[frozenset, list] = {}
    order: List[frozenset] = []
    for ln in lines:
        if not getattr(ln, "is_used", True):
            continue
        f, t, *_ = _obs_fields(ln)
        if not f or not t or f == t:
            continue
        key = frozenset((f, t))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(ln)

    out: List[DoubleRunResult] = []
    for key in order:
        runs = groups[key]
        if len(runs) < 2:
            continue
        cls = getattr(runs[0], "leveling_class", None) or project_class or "H3"
        cls_name = f"H{class_number(cls)}"
        _merged, report = merge_observations(runs, cls_name)
        r = report[0]
        f0, t0, dh0, *_ = _obs_fields(runs[0])
        f1, t1, dh1, *_ = _obs_fields(runs[1])
        disc = float(r.discrepancy_mm or 0.0)
        out.append(DoubleRunResult(
            from_point=f0, to_point=t0, runs=list(runs), leveling_class=cls_name,
            forward_dh=dh0, return_dh=dh1 if len(runs) == 2 else None,
            same_direction=(len(runs) == 2 and f1 == f0),
            mean_dh=r.mean_dh, distance=r.distance, discrepancy_mm=disc,
            allowed_mm=r.allowed_mm, ok=bool(r.within_tolerance),
            achieved=achieved_class(disc, r.distance)))
    return out


def _obs_fields(o):
    f = getattr(o, "from_point", None) or getattr(o, "start_point", "")
    t = getattr(o, "to_point", None) or getattr(o, "end_point", "")
    dh = getattr(o, "height_diff", None)
    if dh is None:
        dh = getattr(o, "total_height_diff", 0.0)
    d = getattr(o, "distance", None)
    if d is None:
        d = getattr(o, "total_distance", 0.0)
    n = getattr(o, "num_setups", 0)
    src = getattr(o, "source_file", None) or getattr(o, "filename", "") or ""
    return str(f).strip(), str(t).strip(), float(dh), float(d or 0.0), n, str(src)


# --------------------------------------------------------------------------- #
# Weights
# --------------------------------------------------------------------------- #
WEIGHT_MODES = ("distance", "setups", "equal")


def observation_weight(obs, mode: str = "distance") -> float:
    """Weight p_i of one observation.

    distance : p = n_runs / L_km      (Directive G2 / standard leveling)
    setups   : p = n_runs / n_setups
    equal    : p = n_runs
    An explicit ``weight`` attribute on the observation overrides the mode.
    """
    w = getattr(obs, "weight", None)
    if w is not None and w > 0:
        return float(w)
    n_runs = getattr(obs, "n_runs", 1) or 1
    if mode == "setups":
        ns = getattr(obs, "num_setups", 0) or 0
        return n_runs / ns if ns > 0 else float(n_runs)
    if mode == "equal":
        return float(n_runs)
    d_km = (getattr(obs, "distance", 0.0) or 0.0) / 1000.0
    return n_runs / d_km if d_km > 0 else float(n_runs)


# --------------------------------------------------------------------------- #
# Topology
# --------------------------------------------------------------------------- #
def connected_components(observations: Iterable) -> List[List[str]]:
    """Connected groups of points (sorted, largest first)."""
    parent: Dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for o in observations:
        f, t, *_ = _obs_fields(o)
        if not f or not t:
            continue
        ra, rb = find(f), find(t)
        if ra != rb:
            parent[ra] = rb
    comps: Dict[str, List[str]] = defaultdict(list)
    for p in list(parent):
        comps[find(p)].append(p)
    return sorted((sorted(c) for c in comps.values()), key=lambda c: (-len(c), c))


def check_datum(observations: Sequence, fixed_points: Dict[str, float]) -> None:
    """Raise NetworkDatumError when a connected group has no fixed point."""
    comps = connected_components(observations)
    floating = [c for c in comps if not any(p in fixed_points for p in c)]
    if not floating:
        return
    unused_fixed = sorted(p for p in fixed_points
                          if not any(p in c for c in comps))
    parts = ["The following group(s) of points are not connected to any fixed "
             "point, so their heights cannot be determined:"]
    for i, c in enumerate(floating, 1):
        shown = ", ".join(c[:12]) + (" …" if len(c) > 12 else "")
        parts.append(f"  Group {i} ({len(c)} points): {shown}")
    if unused_fixed:
        parts.append("Fixed point(s) not connected to any active line: "
                     + ", ".join(unused_fixed))
    parts.append("Add a fixed height inside each group, or include the lines "
                 "that connect it to a fixed point (excluded / invalid lines "
                 "are not used).")
    raise NetworkDatumError("\n".join(parts), floating)


def _adjacency(observations: Sequence):
    adj: Dict[str, List[Tuple[str, int, int, float, float]]] = defaultdict(list)
    for i, o in enumerate(observations):
        f, t, dh, d, *_ = _obs_fields(o)
        if not f or not t or f == t:
            continue
        adj[f].append((t, i, +1, dh, d))
        adj[t].append((f, i, -1, -dh, d))
    return adj


# --------------------------------------------------------------------------- #
# Step 2: approximate heights
# --------------------------------------------------------------------------- #
@dataclass
class ApproxHeight:
    point_id: str
    height: float
    seed: str            # point the height was carried from
    seed_kind: str       # 'fixed', 'known', or 'relative'
    path_km: float       # measured length of the path from the seed


def approximate_heights(
    observations: Sequence,
    fixed_points: Dict[str, float],
    known_heights: Optional[Dict[str, float]] = None,
) -> Dict[str, ApproxHeight]:
    """Approximate height of every point (Step 2).

    Each connected group is seeded from its fixed points; a group without
    fixed points is seeded from any *known_heights* entry (DB / file values,
    any accuracy), and failing that from its first point at 0.000 m
    ('relative').  Heights are carried along the shortest measured path.
    """
    known_heights = dict(known_heights or {})
    adj = _adjacency(observations)
    result: Dict[str, ApproxHeight] = {}
    for comp in connected_components(observations):
        seeds = {p: ("fixed", fixed_points[p]) for p in comp if p in fixed_points}
        if not seeds:
            seeds = {p: ("known", known_heights[p]) for p in comp if p in known_heights}
        if not seeds:
            seeds = {comp[0]: ("relative", 0.0)}
        dist: Dict[str, float] = {}
        heap = []
        for p, (kind, h) in seeds.items():
            dist[p] = 0.0
            result[p] = ApproxHeight(p, float(h), p, kind, 0.0)
            heapq.heappush(heap, (0.0, p))
        while heap:
            d0, node = heapq.heappop(heap)
            if d0 > dist.get(node, math.inf):
                continue
            for nb, _i, _s, dh, d in adj.get(node, []):
                nd = d0 + max(d, 0.0)
                if nd < dist.get(nb, math.inf):
                    dist[nb] = nd
                    src = result[node]
                    result[nb] = ApproxHeight(nb, src.height + dh, src.seed,
                                              src.seed_kind, nd / 1000.0)
                    heapq.heappush(heap, (nd, nb))
    return result


# --------------------------------------------------------------------------- #
# Independent conditions (for Bv + w = 0)
# --------------------------------------------------------------------------- #
@dataclass
class Condition:
    kind: str                           # 'loop' or 'benchmark'
    terms: List[Tuple[int, int]]        # (observation index, sign)
    constant: float = 0.0               # known height difference (BM paths)
    label: str = ""
    points: List[str] = field(default_factory=list)


def independent_conditions(
    observations: Sequence,
    fixed_points: Dict[str, float],
) -> List[Condition]:
    """Independent condition equations of a leveling network.

    r = n_loops + sum over groups of (n_fixed_in_group - 1)
    where n_loops = n_obs - n_points + n_groups (Euler).  Loops are the
    fundamental cycles of a breadth-first spanning forest, so they are
    always linearly independent.
    """
    adj = _adjacency(observations)
    parent: Dict[str, Tuple[Optional[str], Optional[int], int]] = {}
    depth: Dict[str, int] = {}
    tree_edges = set()
    for comp in connected_components(observations):
        root = next((p for p in comp if p in fixed_points), comp[0])
        parent[root] = (None, None, 0)
        depth[root] = 0
        queue = [root]
        for node in queue:
            for nb, i, sign, _dh, _d in sorted(adj.get(node, []), key=lambda e: e[1]):
                if nb not in parent:
                    # sign as traversed node -> nb
                    parent[nb] = (node, i, sign)
                    depth[nb] = depth[node] + 1
                    tree_edges.add(i)
                    queue.append(nb)

    def path(a: str, b: str) -> List[Tuple[int, int]]:
        """Signed observation terms for the tree path a -> b."""
        up_a, up_b = [], []
        x, y = a, b
        while depth[x] > depth[y]:
            p, i, s = parent[x]; up_a.append((i, -s)); x = p
        while depth[y] > depth[x]:
            p, i, s = parent[y]; up_b.append((i, s)); y = p
        while x != y:
            p, i, s = parent[x]; up_a.append((i, -s)); x = p
            p, i, s = parent[y]; up_b.append((i, s)); y = p
        return up_a + list(reversed(up_b))

    conds: List[Condition] = []
    for i, o in enumerate(observations):
        f, t, *_ = _obs_fields(o)
        if i in tree_edges or not f or not t or f == t:
            continue
        # loop: f -> t along observation i, then back t -> f along the tree
        terms = [(i, +1)] + path(t, f)
        conds.append(Condition("loop", terms, 0.0, f"Loop via {f}->{t}"))

    for comp in connected_components(observations):
        fixed_here = [p for p in comp if p in fixed_points]
        if len(fixed_here) < 2:
            continue
        base = fixed_here[0]
        for other in fixed_here[1:]:
            conds.append(Condition(
                "benchmark", path(base, other),
                fixed_points[other] - fixed_points[base],
                f"{base} -> {other}", [base, other]))
    return conds


# --------------------------------------------------------------------------- #
# Single line between two known heights (Directive G2, Appendix C)
# --------------------------------------------------------------------------- #
def adjust_single_line(line, h_start: float, h_end: float, leveling_class=3) -> Dict:
    """Misclosure of one line between two known heights and the proportional
    distribution of the correction:  v_i = -f * L_i / sum(L).

    Returns {'misclosure_mm', 'allowed_mm', 'ok', 'length_m', 'rows': [...]}
    where each row holds setup no., length, measured dH, correction,
    corrected dH and the height after that setup.
    """
    setups = [s for s in getattr(line, "setups", []) if getattr(s, "is_used", True)]
    if setups:
        parts = [((s.distance_back or 0.0) + (s.distance_fore or 0.0),
                  s.height_diff or 0.0, s.setup_number) for s in setups]
    else:
        parts = [(line.total_distance, line.total_height_diff, 1)]
    total_len = sum(p[0] for p in parts) or float(line.total_distance or 0.0)
    measured = sum(p[1] for p in parts)
    f = measured - (h_end - h_start)
    allowed = tolerance_mm(total_len, leveling_class)
    rows, h = [], h_start
    n = len(parts)
    for L_i, dh, no in parts:
        v = -f * (L_i / total_len) if total_len > 0 else -f / n
        h += dh + v
        rows.append({"setup": no, "length_m": L_i, "measured_dh": dh,
                     "correction_mm": v * 1000.0, "corrected_dh": dh + v,
                     "height": h})
    return {"misclosure_mm": f * 1000.0, "allowed_mm": allowed,
            "ok": abs(f * 1000.0) <= allowed, "length_m": total_len,
            "measured_dh": measured, "known_dh": h_end - h_start, "rows": rows}
