"""
core_logic/project_naming.py
Automatic project names: ``<Description>_DD_MM_YYYY_H_MM``.

The description comes from the first loaded file: the header title of an
.FA0 / .FA1 file (e.g. ``K6177-64PU-65PU``), the project name of a .rez file,
otherwise the file name (line name of a .DAT / .RAW / .GSI file).  The
timestamp is the moment the project is started, e.g.
``K6177-64PU-65PU_24_11_2026_9_25`` (hour without a leading zero, minutes
with two digits).  The result is a valid Windows file name.
"""

import datetime as _dt
import os
import re

# Characters Windows does not allow in file names, plus control characters
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL",
             *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_DESCRIPTION = 80
DEFAULT_DESCRIPTION = "GeoLevel"


def sanitize_name(text: str, max_len: int = MAX_DESCRIPTION) -> str:
    """Make *text* usable as (part of) a Windows file name: invalid characters
    and whitespace runs become '_', repeated '_' collapse, trailing dots /
    spaces go, reserved device names get a '_' suffix.  Hebrew and other
    Unicode letters are kept.  '' when nothing usable is left."""
    s = _INVALID.sub("_", str(text or ""))
    s = re.sub(r"\s+", "_", s.strip())
    s = re.sub(r"_+", "_", s).strip("_. ")
    s = s[:max_len].rstrip("_. ")
    if s.upper().split(".")[0] in _RESERVED:
        s += "_"
    return s


def timestamp_suffix(when=None) -> str:
    """``DD_MM_YYYY_H_MM`` (24 h clock, hour without leading zero)."""
    d = when or _dt.datetime.now()
    return f"{d:%d}_{d:%m}_{d:%Y}_{d.hour}_{d.minute:02d}"


def make_project_name(description: str, when=None) -> str:
    """``<sanitized description>_DD_MM_YYYY_H_MM``."""
    desc = sanitize_name(description) or DEFAULT_DESCRIPTION
    return f"{desc}_{timestamp_suffix(when)}"


def describe_files(file_paths, file_titles=None) -> str:
    """Description of a load: the header title of the first file that has
    one (FA0/FA1/REZ), else the first file's name without extension."""
    titles = file_titles or {}
    paths = list(file_paths or [])
    for fp in paths:
        t = titles.get(os.path.abspath(fp)) or titles.get(fp)
        if t and sanitize_name(t):
            return t
    for fp in paths:
        stem = os.path.splitext(os.path.basename(fp))[0]
        if sanitize_name(stem):
            return stem
    return DEFAULT_DESCRIPTION
