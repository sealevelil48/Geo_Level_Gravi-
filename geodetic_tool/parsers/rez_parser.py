"""
REZ reader (line summary file written by geodetic_tool.exporters.REZExporter).

    # REZ Summary File - project
    # Generated: ...
    From        To             Height Diff    Distance  Setups      Status  Source File
    ------------------------------------------------------------------------------
    5709MPI     5710MPI           -1.23456      812.40       8          OK  ASK00

Every data row becomes its own LevelingLine (see summary_common).  Rows may
also be '|' separated.  Known heights are not part of a .rez file.
"""
import logging
import re
from pathlib import Path
from typing import Dict, List

from geodetic_tool.config.models import LevelingLine
from geodetic_tool.parsers.base_parser import BaseParser
from geodetic_tool.parsers.summary_common import (
    is_number, status_from_text, summary_line, to_float)

logger = logging.getLogger(__name__)


class RezParser(BaseParser):
    """Reader for .rez files - one LevelingLine per row."""

    def detect_format(self, filepath: str) -> bool:
        return filepath.lower().endswith(".rez")

    def parse(self, filepath: str) -> LevelingLine:
        """Single-line API (first line only). Use parse_many() for all lines."""
        return self.parse_many(filepath)[0]

    def parse_many(self, filepath: str) -> List[LevelingLine]:
        logger.info("Parsing .rez file: %s", filepath)
        rows = self.read_file(filepath)
        if not any(r.strip() for r in rows):
            raise ValueError("Empty or unreadable file")
        self.benchmarks: Dict[str, float] = {}
        stem = Path(filepath).stem
        lines: List[LevelingLine] = []
        for no, raw in enumerate(rows, start=1):
            text = raw.strip()
            if text.startswith("#") and not self.title:
                m = re.match(r"^#\s*REZ Summary File\s*-\s*(.+)$", text, re.I)
                if m:
                    self.title = m.group(1).strip()
            if not text or text.startswith("#") or set(text) <= set("-=| "):
                continue
            toks = ([t.strip() for t in text.split("|") if t.strip()]
                    if "|" in text else text.split())
            if len(toks) < 4 or not (is_number(toks[2]) and is_number(toks[3])):
                if not (toks and toks[0].lower() == "from"):
                    logger.warning(".rez line %d not understood, skipped: %r", no, text)
                    self.warnings.append(f"Line {no} skipped: {text[:60]}")
                continue
            f, t = toks[0], toks[1]
            dh, dist = to_float(toks[2]), to_float(toks[3])
            rest = toks[4:]
            # older exports glued setups and a long status ("4exceeded_tolerance")
            if rest and not rest[0].isdigit():
                m = re.match(r"^(\d+)(\D.*)$", rest[0])
                if m:
                    rest = [m.group(1), m.group(2)] + rest[1:]
            n = int(rest[0]) if rest and rest[0].isdigit() else 0
            bf, ym = None, ""
            if len(rest) > 1 and is_number(rest[1]):
                # legacy layout: setups  bf_diff  MMYY  raw-file
                bf = to_float(rest[1])
                ym = rest[2] if len(rest) > 2 and rest[2].isdigit() else ""
                src = rest[3] if len(rest) > 3 else (rest[2] if len(rest) > 2 and not ym else "")
                status = None
            else:
                # this tool's export: setups  status  file
                status = status_from_text(rest[1]) if len(rest) > 1 else None
                src = rest[2] if len(rest) > 2 else ""
            lines.append(summary_line(stem, len(lines) + 1, f, t, dh, dist, n, src,
                                      status=status, bf_diff=bf, year_month=ym,
                                      source_path=str(Path(filepath).resolve())))
            logger.debug(".rez row %d: %s -> %s dH=%.5f L=%.1f setups=%d",
                         no, f, t, dh, dist, n)
        if not lines:
            raise ValueError("No observation lines found")
        logger.info("Parsed %d line(s) from .rez %s", len(lines), Path(filepath).name)
        return lines


def parse_rez_file(filepath: str) -> List[LevelingLine]:
    return RezParser().parse_many(filepath)
