"""
FA1 reader (adjustment output / report file).

Reads the observation table of an FA1 report (as written by
geodetic_tool.exporters.FA1Exporter and the legacy adjustment program):

         FROM   TO        DH        DISTANCE   DH-(H2-H1)  ...   NO.SH
      5709MPI 5710MPI    -1.23456     812.     0.00012 ...        8

and the known heights from the DATA section ("idx  point  height", a
non-zero height = known).  If the DATA section has no heights, fixed points
are taken from the adjusted-heights table (rows without APPROX/DIF/M.S.E.).

Every observation row becomes its own LevelingLine (see summary_common).
"""
import logging
from pathlib import Path
from typing import Dict, List

from geodetic_tool.config.models import LevelingLine
from geodetic_tool.parsers.base_parser import BaseParser
from geodetic_tool.parsers.summary_common import is_number, summary_line, to_float

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
        data_heights: Dict[str, float] = {}
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
                section = "obs"
                continue
            if "ITERATION" in up:
                section = "adjusted"
                continue
            if text.startswith(",") or text.startswith("=") or "SDIST" in up:
                if section == "obs":
                    section = "stats"
                continue
            toks = text.split()
            if section == "data":
                # "idx  point  height"
                if len(toks) == 3 and toks[0].isdigit() and is_number(toks[2]):
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
        self.benchmarks = data_heights or adjusted_fixed
        if not lines:
            raise ValueError("No observation lines found")
        logger.info("Parsed %d line(s) and %d known height(s) from .FA1 %s",
                    len(lines), len(self.benchmarks), Path(filepath).name)
        return lines


def parse_fa1_file(filepath: str) -> List[LevelingLine]:
    return FA1Parser().parse_many(filepath)
