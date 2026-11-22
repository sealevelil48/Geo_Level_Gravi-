"""
FA0 reader (adjustment input file).

Layout (as written by core_logic.exporters.FA0Exporter and the legacy
adjustment program):

      5   2            project.rez                             9     <- header
        5709MPI       34.180                                         <- known height
        3349MPI            .                                         <- unknown point
    5709MPI 5710MPI     -1.23456    812.   8   0.15  1125   ASK00.DAT   <- one line
     9                                                               <- terminator

Every observation row becomes its own LevelingLine (see summary_common),
and the known heights are kept in ``parser.benchmarks``.
"""
import logging
from pathlib import Path
from typing import Dict, List

from core_logic.config.models import LevelingLine
from core_logic.parsers.base_parser import BaseParser
from core_logic.parsers.summary_common import is_number, summary_line, to_float

logger = logging.getLogger(__name__)


class FA0Parser(BaseParser):
    """Reader for .FA0 files - one LevelingLine per observation row."""

    def detect_format(self, filepath: str) -> bool:
        return filepath.lower().endswith(".fa0")

    def parse(self, filepath: str) -> LevelingLine:
        """Single-line API (first line only). Use parse_many() for all lines."""
        return self.parse_many(filepath)[0]

    def parse_many(self, filepath: str) -> List[LevelingLine]:
        logger.info("Parsing .FA0 file: %s", filepath)
        rows = self.read_file(filepath)
        if not any(r.strip() for r in rows):
            raise ValueError("Empty or unreadable file")
        self.benchmarks: Dict[str, float] = {}
        self.unknown_points: List[str] = []
        stem = Path(filepath).stem
        lines: List[LevelingLine] = []
        body = rows[1:] if rows and self._is_header(rows[0]) else rows
        for no, raw in enumerate(body, start=2):
            text = raw.rstrip("\r\n")
            toks = text.split()
            if not toks:
                continue
            if toks == ["9"]:
                break
            if len(toks) == 2 and (toks[1] == "." or is_number(toks[1])):
                if toks[1] == ".":
                    self.unknown_points.append(toks[0])
                else:
                    self.benchmarks[toks[0]] = to_float(toks[1])
                continue
            obs = self._parse_observation(text)
            if obs is None:
                logger.warning("FA0 line %d not understood, skipped: %r", no, text)
                self.warnings.append(f"Line {no} skipped: {text.strip()[:60]}")
                continue
            f, t, dh, dist, n, src = obs
            lines.append(summary_line(stem, len(lines) + 1, f, t, dh, dist, n, src,
                                      source_path=str(Path(filepath).resolve())))
        if not lines:
            raise ValueError("No observation lines found")
        logger.info("Parsed %d line(s) and %d known height(s) from .FA0 %s",
                    len(lines), len(self.benchmarks), Path(filepath).name)
        logger.debug("FA0 known heights: %s", self.benchmarks)
        return lines

    @staticmethod
    def _is_header(row: str) -> bool:
        toks = row.split()
        return (len(toks) >= 3 and toks[0].isdigit() and toks[1].isdigit()
                and toks[-1].endswith("9"))

    @staticmethod
    def _parse_observation(text: str):
        """Fixed width 8+8 for the point names, then free-format numbers."""
        candidates = []
        if len(text) > 16:
            candidates.append((text[0:8].strip(), text[8:16].strip(), text[16:].split()))
        toks = text.split()
        if len(toks) >= 4:
            candidates.append((toks[0], toks[1], toks[2:]))
        for f, t, rest in candidates:
            if not f or not t or " " in f or " " in t:
                continue
            if len(rest) < 2 or not (is_number(rest[0]) and is_number(rest[1])):
                continue
            dh, dist = to_float(rest[0]), to_float(rest[1])
            n = 0
            if len(rest) > 2 and rest[2].isdigit():
                n = int(rest[2])
            src = ""
            tail = [x for x in rest[3:] if not is_number(x)]
            if tail:
                src = tail[-1]
            return f, t, dh, dist, n, src
        return None


def parse_fa0_file(filepath: str) -> List[LevelingLine]:
    return FA0Parser().parse_many(filepath)
