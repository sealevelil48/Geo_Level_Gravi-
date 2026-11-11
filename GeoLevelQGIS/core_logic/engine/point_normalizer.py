"""
ASCII-to-Hebrew transliteration for Israeli survey point IDs.

Field instruments write ASCII only (e.g. '2/BN', '3349MPI').  The national
Bangal PostGIS registry may store the canonical name in Hebrew script.  This
module generates a bounded set of search candidates so the DB manager can try
Hebrew forms when the primary 6-variant SQL match returns nothing.

Design notes
------------
- MULTI_CHAR_MAP is applied first and only on the ASCII-stripped form.
- SINGLE_CHAR_MAP is applied on the ASCII-stripped form only — never on already-
  transliterated strings — to avoid combinatorial explosion.
- Total candidate count is capped at 32; anything beyond that signals an
  unusually ambiguous ID and is silently truncated.
"""
from __future__ import annotations

import re
from typing import List, Set


class PointNormalizer:
    # Survey-specific multi-character sequences come first.
    # Using tuples of (ascii_seq, [hebrew_options]).
    MULTI_CHAR_MAP: List[tuple] = [
        ("MPI",  ['מפ"י', "מפי"]),
        ("PKT",  ['פק"ט', "פקט"]),
        ("TSH",  ["תש"]),
        ("BN",   ["בן", "בנ"]),
        ("SH",   ["ש"]),
        ("CH",   ["ח", "כ"]),
        ("TZ",   ["צ"]),
    ]

    # Single-character fallback — only for letters with an unambiguous primary
    # survey usage in Israeli geodesy.
    SINGLE_CHAR_MAP: dict = {
        "G": ["ג"],
        "C": ["ג", "כ", "ק"],
        "B": ["ב"],
        "H": ["ה", "ח"],
        "K": ["כ", "ק"],
        "M": ["מ"],
        "N": ["נ"],
        "P": ["פ"],
        "R": ["ר"],
        "T": ["ט", "ת"],
        "Y": ["י"],
    }

    _MAX_CANDIDATES = 32

    @staticmethod
    def strip_separators(pid: str) -> str:
        """Remove separators and uppercase: '2/BN-3' → '2BN3'."""
        return re.sub(r"[/\-\s_.]", "", pid).upper()

    @classmethod
    def generate_search_candidates(cls, raw_pid: str) -> List[str]:
        """Return an ordered, deduplicated list of DB name candidates.

        The raw form and stripped ASCII form are always first so the existing
        6-variant SQL matching in BenchmarkDBManager can still find direct hits
        before any Hebrew expansion is attempted.
        """
        raw = raw_pid.strip()
        stripped = cls.strip_separators(raw)

        seen: Set[str] = set()
        result: List[str] = []

        def _add(s: str) -> None:
            if s and s not in seen:
                seen.add(s)
                result.append(s)

        _add(raw)
        _add(stripped)

        if len(result) >= cls._MAX_CANDIDATES:
            return result[: cls._MAX_CANDIDATES]

        # --- Multi-char substitutions on the stripped ASCII form ---
        multi_candidates: Set[str] = {stripped}
        for ascii_seq, heb_options in cls.MULTI_CHAR_MAP:
            if ascii_seq not in stripped:
                continue
            new_batch: Set[str] = set()
            for candidate in multi_candidates:
                for heb in heb_options:
                    new_batch.add(candidate.replace(ascii_seq, heb, 1))
            multi_candidates.update(new_batch)
            if len(multi_candidates) > cls._MAX_CANDIDATES:
                break

        for c in sorted(multi_candidates):
            _add(c)

        if len(result) >= cls._MAX_CANDIDATES:
            return result[: cls._MAX_CANDIDATES]

        # --- Single-char substitutions on the original stripped ASCII form only ---
        for ascii_char, heb_options in cls.SINGLE_CHAR_MAP.items():
            if ascii_char not in stripped:
                continue
            for heb in heb_options:
                _add(stripped.replace(ascii_char, heb, 1))
                if len(result) >= cls._MAX_CANDIDATES:
                    return result

        return result
