"""
Exporters Package

Export modules for generating geodetic output files.
All exports use ANSI encoding (cp1255 for Hebrew).
"""
from typing import List, Dict, Optional, TextIO
from pathlib import Path
from datetime import datetime
import logging

from core_logic.config.models import (
    LevelingLine, MeasurementSummary, Benchmark, AdjustmentResult
)
from core_logic.config.settings import get_settings


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Legacy FA0 / FA1 layout (the Survey of Israel adjustment program)
# --------------------------------------------------------------------------- #
import math as _math
import re as _re

LEGACY_ALWD_K_MM = 3.0   # the legacy FA1 prints ALWD = 3 mm * sqrt(L km)
_LEGACY_ID = _re.compile(r"^(\d{1,4})(\D.*)?$")


def legacy_point_id(pid: str) -> str:
    """Legacy ID layout: the point number right-aligned in 4 characters,
    followed by its letters ('64PU' -> '  64PU', '117' -> ' 117',
    '2751ב' -> '2751ב').  Other IDs are written as they are."""
    pid = (pid or "").strip()
    m = _LEGACY_ID.match(pid)
    if not m:
        return pid
    return f"{m.group(1):>4}{m.group(2) or ''}"


def _legacy_height(h) -> str:
    """12-character height column aligned on the decimal point, trailing
    zeros dropped ('56.470' -> '      56.47'); unknown -> '.' only."""
    if h is None:
        return " " * 8 + "."
    return f"{float(h):12.3f}".rstrip("0")


def _nz(v: float, decimals: int) -> float:
    """No '-0.00000': values that round to zero print as 0 (legacy layout)."""
    return 0.0 if abs(v) < 0.5 * 10 ** -decimals else v


def _point_order(fixed_ids, observations, extra=()):
    """Fixed points first (in the given order), then all other points sorted."""
    pts = set(extra)
    for o in observations:
        pts.add(o.from_point)
        pts.add(o.to_point)
    fixed_ids = [p for p in fixed_ids]
    return fixed_ids + sorted(pts - set(fixed_ids))


def _used(observations, only_used=True):
    if not only_used:
        return list(observations)
    return [o for o in observations if not hasattr(o, "is_used") or o.is_used]


class FA0Exporter:
    """
    Export FA0 files (adjustment input) in the legacy layout:

        npoints nfixed  name                                    9
        <fixed points with heights>        <- the first nfixed rows
        <other points: approximate height or '.'>
        <one row per observation: from to dh dist. n bf date source>
        ... last observation row ends with ' 9'
    """

    def __init__(self):
        self.settings = get_settings()
        self.encoding = self.settings.encoding.output_encoding

    def export(
        self,
        filepath: str,
        benchmarks: List[Benchmark],
        observations: List[MeasurementSummary],
        project_name: str = "project.rez",
        only_used: bool = True,
        approx_heights: Optional[Dict[str, float]] = None,
    ):
        """*benchmarks* are the fixed points (written first, in this order);
        *approx_heights* optional approximate heights of the other points."""
        observations = _used(observations, only_used)
        fixed = {bm.point_id: bm.height for bm in benchmarks}
        approx = dict(approx_heights or {})
        order = _point_order(list(fixed), observations)
        out = [f"{len(order):4d}{len(fixed):4d}{' ' * 22}{project_name:<49}9"]
        for pid in order:
            h = fixed[pid] if pid in fixed else approx.get(pid)
            out.append(f"    {legacy_point_id(pid):<8}{_legacy_height(h)}".rstrip())
        for i, obs in enumerate(observations):
            bf = getattr(obs, "bf_diff", None) or 0.0
            ym = (getattr(obs, "year_month", "") or "")[:4]
            src = getattr(obs, "source_file", "") or ""
            row = (f"{legacy_point_id(obs.from_point):<9}{legacy_point_id(obs.to_point):<9}"
                   f"{obs.height_diff:11.5f}{obs.distance:6.0f}."
                   f"{int(getattr(obs, 'num_setups', 0) or 0):4d}{bf:7.2f}"
                   f"  {ym:<4}{' ' * 11}{src:<14}")
            if i == len(observations) - 1:
                row += " 9 "
            out.append(row)
        with open(filepath, "w", encoding=self.encoding, newline="\r\n") as f:
            f.write("\n".join(out) + "\n")


class FA1Exporter:
    """
    Export FA1 files (adjustment report) in the legacy layout, two
    iterations as the legacy program prints them:

      DATA (input heights) -> misclosures DH-(H2-H1) against those heights
      -> ITERATION NO. 1 (ADJUSTED / APPROX / DIF / M.S.E.)
      -> DATA (adjusted heights) -> misclosures against the adjusted heights
      -> ITERATION NO. 2 (APPROX = adjusted, DIF = 0).

    ALWD = 3 mm * sqrt(L km); DELTA = |DH-(H2-H1)| - ALWD, '**' when > 0.
    """

    def __init__(self):
        self.settings = get_settings()
        self.encoding = self.settings.encoding.output_encoding

    def export(
        self,
        filepath: str,
        benchmarks: List[Benchmark],
        observations: List[MeasurementSummary],
        result: AdjustmentResult,
        project_name: str = "project.rez",
        leveling_class=3,
        approx_heights: Optional[Dict[str, float]] = None,
        only_used: bool = True,
    ):
        """*benchmarks*: fixed points (first, in this order).  *approx_heights*:
        input approximate heights of other points (unknown -> 0.000, as in
        the legacy DATA list).  *leveling_class* is kept for API compatibility;
        the legacy ALWD is always 3 mm * sqrt(L)."""
        observations = _used(observations, only_used)
        fixed = {bm.point_id: float(bm.height) for bm in benchmarks}
        approx = {p: float(h) for p, h in (approx_heights or {}).items() if h is not None}
        adj = dict(result.adjusted_heights or {})
        order = _point_order(list(fixed), observations, extra=adj.keys())
        h_in = {p: fixed.get(p, approx.get(p, 0.0)) for p in order}
        h_adj = {p: float(adj.get(p, h_in[p])) for p in order}
        s0 = float(getattr(result, "mse_unit_weight", 0.0) or 0.0)
        mse = dict(getattr(result, "mse_heights", None) or {})

        out: List[str] = [" ", "", ""]
        out += self._data(order, h_in, len(fixed), project_name, title=True)
        out += self._table(observations, h_in)
        out += self._iteration(1, order, fixed, h_adj, h_in, mse, s0, project_name)
        out += ["", "", ""]
        out += self._data(order, h_adj, len(fixed), project_name, title=False)
        out += self._table(observations, h_adj)
        out += self._iteration(2, order, fixed, h_adj, h_adj, mse, s0, project_name)
        with open(filepath, "w", encoding=self.encoding, newline="\r\n") as f:
            f.write("\n".join(out) + "\n")

    @staticmethod
    def _data(order, heights, n_fixed, name, title):
        out = []
        if title:
            out += [" " * 59 + "DATA", " " * 58 + "******", "",
                    f"{' ' * 45}{len(order):2d}{n_fixed:4d}{' ' * 22}{name:<35}9",
                    "", "", "", "", ""]
        out += [" " * 59 + "DATA", " " * 58 + "******"]
        for i, p in enumerate(order, 1):
            out.append(f"{' ' * 44}{i:3d}  {legacy_point_id(p):<8}{heights[p]:12.3f}")
        out += ["0", "", "1", "", ""]
        return out

    @staticmethod
    def _table(observations, heights):
        out = ["         FROM   TO        DH        DISTANCE   DH-(H2-H1)     ALWD      DELTA"
               "    G/\u05d1   KOD  YEARS  DURATION   NO.SH",
               "        " + "-" * 107, ""]
        sdist = sdif = 0.0
        last = len(observations) - 1
        for i, o in enumerate(observations):
            misc = o.height_diff - (heights.get(o.to_point, 0.0) - heights.get(o.from_point, 0.0))
            alwd = LEGACY_ALWD_K_MM * _math.sqrt(max(o.distance, 0.0) / 1000.0) / 1000.0
            delta = abs(misc) - alwd
            flag = "**" if delta > 0 else "  "
            kod = 9 if i == last else 0
            out.append(f"{' ' * 6}{legacy_point_id(o.from_point):<9}{legacy_point_id(o.to_point):<9}"
                       f"{o.height_diff:11.5f}{o.distance:8.0f}.{_nz(misc, 5):12.5f}{alwd:12.6f}"
                       f"{_nz(delta, 6):12.6f}  {flag}{kod:5d}    0- 0      0" + " " * 12)
            out.append("")
            sdist += o.distance / 1000.0
            sdif += abs(misc) * 1000.0
        k = sdif / _math.sqrt(sdist) if sdist > 0 else 0.0
        out += ["", "", "," + " " * 17 + "=" * 75,
                f"{' ' * 19}SDIST(KM)={sdist:9.3f}   SDIF(MM)={sdif:12.1f}   COEFF_CLASS K={k:11.1f}",
                ""]
        return out

    @staticmethod
    def _iteration(no, order, fixed, h_adj, h_prev, mse, s0, name):
        out = [f"1{' ' * 46}ITERATION  NO. {no}", "", f"{' ' * 49}{name:<30}", "",
               f"{' ' * 37}M.S.E. OF UNIT WEIGHT = {s0:9.6f}", "",
               f"{' ' * 34}NO.      ADJUSTED     APPROX          DIF         M.S.E.", ""]
        for i, p in enumerate(order, 1):
            row = f"{' ' * 28}{i:3d} {legacy_point_id(p):<8}{h_adj[p]:12.5f}"
            if p not in fixed:
                row += (f"{h_prev[p]:12.5f}{_nz(h_adj[p] - h_prev[p], 5):13.5f}"
                        f"{float(mse.get(p, 0.0)):13.6f}")
            out.append(row)
        return out


class FTEGExporter:
    """
    Export FTEG format files.
    
    FTEG Format (simplified measurement data):
        from_pt  to_pt  dh  dist  setups  bf_diff  date  source  terminator
    """
    
    def __init__(self):
        self.settings = get_settings()
        self.encoding = self.settings.encoding.output_encoding
    
    def export(
        self,
        filepath: str,
        observations: List[MeasurementSummary]
    ):
        """
        Export to FTEG format.
        
        Args:
            filepath: Output file path
            observations: List of measurement summaries
        """
        with open(filepath, 'w', encoding=self.encoding) as f:
            for i, obs in enumerate(observations):
                # Last observation gets terminator 9, others get 0
                terminator = "9" if i == len(observations) - 1 else "0"
                
                line = (
                    f"{obs.from_point:8}{obs.to_point:8}"
                    f"{obs.height_diff:12.5f}"
                    f"{obs.distance:7.0f}."
                    f"{obs.num_setups:4}"
                    f"{obs.bf_diff:7.2f}"
                    f"   {obs.year_month:3}"
                    f"           {obs.source_file} {terminator}\n"
                )
                f.write(line)


class REZExporter:
    """
    Export REZ format files (summary/results).
    """

    def __init__(self):
        self.settings = get_settings()
        self.encoding = self.settings.encoding.output_encoding

    def export(
        self,
        filepath: str,
        lines: List[LevelingLine],
        project_name: str = "project",
        only_used: bool = True,
        include_source_files: bool = True
    ):
        """
        Export summary REZ file.

        Args:
            filepath: Output file path
            lines: List of LevelingLine objects
            project_name: Project identifier
            only_used: If True, only export lines marked as is_used=True
            include_source_files: If True, add Source Files column (NEW in v1.1)
        """
        # Filter lines if requested
        if only_used:
            lines = [line for line in lines if line.is_used]

        with open(filepath, 'w', encoding=self.encoding) as f:
            f.write(f"# REZ Summary File - {project_name}\n")
            f.write(f"# Generated: {datetime.now().isoformat()}\n")
            f.write("#" + "=" * 78 + "\n\n")

            # Build header with optional Source Files column
            if include_source_files:
                f.write(f"{'From':<12}{'To':<12}{'Height Diff':>14}{'Distance':>12}{'Setups':>8} {'Status':>12}  {'Source File':<30}\n")
                f.write("-" * 110 + "\n")
            else:
                f.write(f"{'From':<12}{'To':<12}{'Height Diff':>14}{'Distance':>12}{'Setups':>8} {'Status':>12}\n")
                f.write("-" * 70 + "\n")

            for line in lines:
                status_str = "OK" if line.status.value == "valid" else line.status.value

                # Base columns
                output = (
                    f"{line.start_point:<12}{line.end_point:<12}"
                    f"{line.total_height_diff:>14.5f}{line.total_distance:>12.2f}"
                    f"{line.num_setups:>8} {status_str:>12}"
                )

                # Add source file if requested
                if include_source_files:
                    from pathlib import Path
                    source_file = Path(line.filename).name if line.filename else "Unknown"
                    output += f"  {source_file:<30}"

                f.write(output + "\n")


# Convenience functions
def export_fa0(
    filepath: str,
    benchmarks: List[Benchmark],
    observations: List[MeasurementSummary],
    project_name: str = "project.rez"
):
    """Export to FA0 format."""
    exporter = FA0Exporter()
    exporter.export(filepath, benchmarks, observations, project_name)


def export_fa1(
    filepath: str,
    benchmarks: List[Benchmark],
    observations: List[MeasurementSummary],
    result: AdjustmentResult,
    project_name: str = "project.rez",
    leveling_class=3,
):
    """Export to FA1 format."""
    exporter = FA1Exporter()
    exporter.export(filepath, benchmarks, observations, result, project_name,
                    leveling_class)


def export_fteg(filepath: str, observations: List[MeasurementSummary]):
    """Export to FTEG format."""
    exporter = FTEGExporter()
    exporter.export(filepath, observations)


def export_rez(
    filepath: str,
    lines: List[LevelingLine],
    project_name: str = "project"
):
    """Export to REZ format."""
    exporter = REZExporter()
    exporter.export(filepath, lines, project_name)
