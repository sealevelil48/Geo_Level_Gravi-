"""
Topological position estimator for points missing from the benchmark DB
(PKT1, ASH, ...).  Drawn on the map as yellow diamonds.

Each leveling line is an edge weighted by its measured length.  For every
unknown point a Dijkstra search from every located point ("anchor") gives
the measured path length to it, also through chains of other unknown points
(Anchor -> PKT1 -> PKT2 -> Anchor).

  * reachable from 2+ anchors : placed on the chord between the two nearest
    anchors at the ratio of the measured path lengths
    r = d1 / (d1 + d2)
  * reachable from 1 anchor   : placed at the measured path length from it
    on a 45 deg diagonal (fanned out if several points share the anchor)
  * not connected to any anchor: not estimated (logged)

No radius limit applies - works for nationwide lines.
"""
from __future__ import annotations

import heapq
import logging
import math
from collections import defaultdict
from typing import Dict, Iterable, List, Tuple

logger = logging.getLogger(__name__)

XY = Tuple[float, float]


def _edge(line):
    if isinstance(line, dict):
        s, e, d = line.get("start_point"), line.get("end_point"), line.get("total_distance")
    else:
        s = getattr(line, "start_point", None)
        e = getattr(line, "end_point", None)
        d = getattr(line, "total_distance", None)
    s = str(s or "").strip()
    e = str(e or "").strip()
    try:
        d = float(d) if d is not None else None
    except (TypeError, ValueError):
        d = None
    if d is None or not math.isfinite(d) or d <= 0:
        d = 100.0
    return s, e, d


def _graph(lines: Iterable) -> Dict[str, Dict[str, float]]:
    g: Dict[str, Dict[str, float]] = defaultdict(dict)
    for ln in lines:
        s, e, d = _edge(ln)
        if not s or not e or s == e:
            continue
        # repeated runs of the same section: keep the shortest length
        g[s][e] = min(d, g[s].get(e, math.inf))
        g[e][s] = min(d, g[e].get(s, math.inf))
    return g


def _dijkstra(g, source: str, stop_at: set) -> Dict[str, float]:
    """Shortest path lengths from *source*; paths do not pass THROUGH other
    anchors (*stop_at*), so each unknown is related to its nearest anchors."""
    dist = {source: 0.0}
    heap = [(0.0, source)]
    while heap:
        d0, n = heapq.heappop(heap)
        if d0 > dist.get(n, math.inf):
            continue
        if n != source and n in stop_at:
            continue
        for nb, w in g.get(n, {}).items():
            nd = d0 + w
            if nd < dist.get(nb, math.inf):
                dist[nb] = nd
                heapq.heappush(heap, (nd, nb))
    return dist


def estimate_positions(lines: Iterable, known: Dict[str, XY]) -> Dict[str, XY]:
    """Return {point_id: (E, N)} for the points of *lines* not in *known*."""
    lines = list(lines)
    g = _graph(lines)
    anchors = {p for p in known if p in g}
    unknown = [p for p in g if p not in known]
    if not unknown:
        return {}
    reach: Dict[str, List[Tuple[float, str]]] = defaultdict(list)
    for a in anchors:
        for p, d in _dijkstra(g, a, anchors).items():
            if p in known:
                continue
            reach[p].append((d, a))

    out: Dict[str, XY] = {}
    fan: Dict[str, int] = defaultdict(int)
    for p in sorted(unknown):
        cands = sorted(reach.get(p, []))
        # two nearest anchors at distinct positions
        pair = []
        for d, a in cands:
            if not pair or math.dist(known[a], known[pair[0][1]]) > 1e-6:
                pair.append((d, a))
            if len(pair) == 2:
                break
        if len(pair) == 2:
            (d1, a1), (d2, a2) = pair
            r = d1 / (d1 + d2) if (d1 + d2) > 0 else 0.5
            (x1, y1), (x2, y2) = known[a1], known[a2]
            out[p] = (x1 + r * (x2 - x1), y1 + r * (y2 - y1))
            logger.info("topo_estimator: %s between %s and %s (r=%.3f, %.0f/%.0f m)",
                        p, a1, a2, r, d1, d2)
        elif pair:
            d, a = pair[0]
            k = fan[a]
            fan[a] += 1
            ang = math.radians(45 + 30 * k)
            x, y = known[a]
            out[p] = (x + d * math.cos(ang), y + d * math.sin(ang))
            logger.info("topo_estimator: %s from single anchor %s at %.0f m", p, a, d)
        else:
            logger.warning("topo_estimator: %s is not connected to any located "
                           "point - position not estimated", p)
    return out
