"""
Shared helpers for summary-file readers (.FA0 / .FA1 / .rez).

A summary file lists one row per leveling line (section) - from point, to
point, measured height difference, length, number of setups.  Each row is
loaded as its OWN LevelingLine so the engineer can review, toggle direction,
include / exclude and override it exactly like a line read from a .DAT file.

Summary files carry no individual staff readings, so every line gets
``num_setups`` evenly split placeholder setups: their sum reproduces the
line's height difference and length, the setup count is kept, and the line
is flagged ``is_summary`` so setup-level field checks are skipped.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Optional

from geodetic_tool.config.models import LevelingLine, LineStatus, StationSetup

logger = logging.getLogger(__name__)

_NUM = re.compile(r"^[+-]?\d+(\.\d*)?$|^[+-]?\.\d+$")


def is_number(tok: str) -> bool:
    return bool(_NUM.match(tok.strip()))


def to_float(tok: str) -> float:
    tok = tok.strip()
    if tok.endswith("."):
        tok = tok + "0"
    return float(tok)


def summary_line(
    file_stem: str,
    row_no: int,
    from_point: str,
    to_point: str,
    height_diff: float,
    distance: float,
    num_setups: int = 0,
    source: str = "",
    status: Optional[LineStatus] = None,
    source_path: Optional[str] = None,
) -> LevelingLine:
    """Build one editable LevelingLine from one summary-file row."""
    n = int(num_setups) if num_setups and int(num_setups) > 0 else 1
    dh_i = height_diff / n
    half = (distance / n) / 2.0
    setups = []
    for k in range(1, n + 1):
        setups.append(StationSetup(
            setup_number=k, from_point=from_point if k == 1 else "",
            to_point=to_point if k == n else "",
            backsight_reading=1.5 + dh_i / 2.0, foresight_reading=1.5 - dh_i / 2.0,
            distance_back=half, distance_fore=half, height_diff=dh_i,
        ))
    src = Path(source).stem if source else ""
    name = f"{file_stem}#{row_no:02d}" + (f"_{src}" if src else "")
    line = LevelingLine(
        filename=name, start_point=from_point, end_point=to_point, setups=setups,
        method="BF", total_distance=float(distance), total_height_diff=float(height_diff),
        status=status or LineStatus.VALID, source_path=source_path,
    )
    line.is_summary = True
    return line


def status_from_text(text: str) -> LineStatus:
    t = (text or "").strip().lower()
    if t in ("", "ok", "valid"):
        return LineStatus.VALID
    try:
        return LineStatus(t)
    except ValueError:
        return LineStatus.VALID
