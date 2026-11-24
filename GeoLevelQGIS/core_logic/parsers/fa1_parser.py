"""
FA1 reader (adjustment output / report file).

Reads the observation table of an FA1 report (as written by
core_logic.exporters.FA1Exporter and the legacy adjustment program):

         FROM   TO        DH        DISTANCE   DH-(H2-H1)  ...   NO.SH
      5709MPI 5710MPI    -1.23456     812.     0.00012 ...        8

and the known heights from the first DATA section ("idx  point  height").
The title line "npoints  nfixed  name" tells how many of the listed points
(the first ones) are FIXED; other non-zero heights there are approximate
heights (``approx_heights``).  Without that number, fixed points come from
the adjusted-heights table (rows without APPROX/DIF/M.S.E.).  The legacy
program prints the observation table once per iteration: only the first
table is read.

Every observation row becomes its own LevelingLine (see summary_common).
"""
import logging
from pathlib import Path
from typing import Dict, List

from core_logic.config.models import LevelingLine
from core_logic.parsers.base_parser import BaseParser
from core_logic.parsers.summary_common import is_number, summary_line, to_float

logger = logging.getLogger(__name__)


class FA1Parser(BaseParser):
    """Reader for .FA1 files - one LevelingLine per observation row."""

    def detect_format(self, filepath: str) -> bool:
        return filepath.lower().endswith(".fa1")

    def parse(self, filepath: str) -> LevelingLine:
        return self.parse_many(filepath)[0]

    def parse_many(self, filepath: str) -> List[LevelingLine]:
        logger.info("Parsing .FA1 file: %s", filepath)
        rows = self.read_file(filepath)
        if not any(r.strip() for r in rows):
            raise ValueError("Empty or unreadable file")
        self.benchmarks: Dict[str, float] = {}
        self.approx_heights: Dict[str, float] = {}
        data_heights: Dict[str, float] = {}
        data_order: List[str] = []
        n_fixed = None
        obs_tables = 0
        data_blocks = 0
        adjusted_fixed: Dict[str, float] = {}
        stem = Path(filepath).stem
        lines: List[LevelingLine] = []
        section = "data"
        for no, raw in enumerate(rows, start=1):
            text = raw.strip()
            if not text:
                continue
            up = text.upper()
            if "FROM" in up and "TO" in up and "DH" in up:
                obs_tables += 1
                section = "obs" if obs_tables == 1 else "skip"
                continue
            if "ITERATION" in up:
                section = "adjusted"
                continue
            if up == "DATA":
                # the title page repeats "DATA"; a DATA list after the point
                # list (adjusted heights after each iteration) is skipped
                data_blocks += 1
                section = "skip" if data_order else "data"
                continue
            if text.startswith(",") or text.startswith("=") or "SDIST" in up:
                if section == "obs":
                    section = "stats"
                continue
            toks = text.split()
            if section == "data":
                # title "npoints  nfixed  name": how many listed points are fixed
                if (n_fixed is None and not data_order and len(toks) >= 3
                        and toks[0].isdigit() and toks[1].isdigit() and not is_number(toks[2])):
                    n_fixed = int(toks[1])
                    self.title = " ".join(toks[2:]).strip()
                    continue
                # "idx  point  height"
                if len(toks) == 3 and toks[0].isdigit() and is_number(toks[2]):
                    data_order.append(toks[1])
                    h = to_float(toks[2])
                    if abs(h) > 0.0:
                        data_heights[toks[1]] = h
            elif section == "obs":
                if set(text) <= set("-= "):
                    continue
                if len(toks) >= 4 and is_number(toks[2]) and is_number(toks[3]):
                    n = int(toks[-1]) if toks[-1].isdigit() else 0
                    lines.append(summary_line(
                        stem, len(lines) + 1, toks[0], toks[1], to_float(toks[2]),
                        to_float(toks[3]), n,
                        source_path=str(Path(filepath).resolve())))
                else:
                    logger.warning(".FA1 line %d not understood, skipped: %r", no, text)
            elif section == "adjusted":
                # fixed point rows: "idx point height" (no approx/dif/mse)
                if len(toks) == 3 and toks[0].isdigit() and is_number(toks[2]):
                    adjusted_fixed[toks[1]] = to_float(toks[2])
        if (n_fixed is not None and data_order
                and all(p in data_heights for p in data_order[:n_fixed])):
            # legacy layout: the first n_fixed listed points are the fixed ones
            fixed_ids = data_order[:n_fixed]
            self.benchmarks = {p: data_heights[p] for p in fixed_ids if p in data_heights}
            self.approx_heights = {p: h for p, h in data_heights.items()
                                   if p not in self.benchmarks}
        else:
            self.benchmarks = data_heights or adjusted_fixed
        if not lines:
            raise ValueError("No observation lines found")
        logger.info("Parsed %d line(s) and %d known height(s) from .FA1 %s",
                    len(lines), len(self.benchmarks), Path(filepath).name)
        return lines


def parse_fa1_file(filepath: str) -> List[LevelingLine]:
    return FA1Parser().parse_many(filepath)
