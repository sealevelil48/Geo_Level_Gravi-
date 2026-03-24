"""
core_logic/process.py
Bridge between the QGIS plugin UI and the geodetic_tool engine.

Public API
----------
process_geodetic_data(file_paths, leveling_class, output_dir)
    -> dict with keys: lines_geojson, line_style, point_style, summary
"""

import os
import sys
import logging
from pathlib import Path
from typing import List, Dict

# Make core_logic importable regardless of QGIS sys.path state
_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

log = logging.getLogger("GeoLevelGravi")


def process_geodetic_data(
    file_paths: List[str],
    leveling_class: str,
    output_dir: str,
) -> Dict:
    """
    Parse, validate and export a list of measurement files.

    Parameters
    ----------
    file_paths    : absolute paths to .DAT / .RAW / .GSI files
    leveling_class: "H1" … "H6"
    output_dir    : folder where GeoJSON + QML files are written

    Returns
    -------
    dict
        lines_geojson : str   – path to the exported lines GeoJSON
        line_style    : str   – path to the lines QML style
        point_style   : str   – path to the points QML style
        summary       : dict  – {total, valid, invalid, warnings}
        lines         : list  – parsed LevelingLine objects
    """
    # ------------------------------------------------------------------ #
    # 1. Parse files
    # ------------------------------------------------------------------ #
    from parsers.base_parser import create_parser
    from validators import BatchValidator
    from gis.geojson_export import export_network_to_geojson
    from config.israel_survey_regulations import get_class_parameters_by_name

    lines = []
    parse_errors = []

    for fp in file_paths:
        try:
            parser = create_parser(fp)
            line = parser.parse(fp)
            lines.append(line)
            log.info("Parsed: %s  (%s → %s)", Path(fp).name,
                     line.start_point, line.end_point)
        except Exception as exc:
            msg = f"{Path(fp).name}: {exc}"
            parse_errors.append(msg)
            log.warning("Parse error – %s", msg)

    if not lines:
        raise RuntimeError(
            "No files could be parsed.\n" + "\n".join(parse_errors)
        )

    # ------------------------------------------------------------------ #
    # 2. Apply selected class to validator
    # ------------------------------------------------------------------ #
    class_num = int(leveling_class[1])          # "H3" -> 3
    validator = BatchValidator(leveling_class=class_num)
    results = validator.validate_batch(lines)

    valid_count   = sum(1 for _, r in results if r.is_valid)
    invalid_count = len(results) - valid_count
    warning_count = sum(len(r.warnings) for _, r in results)

    log.info("Validation: %d valid, %d invalid, %d warnings",
             valid_count, invalid_count, warning_count)

    # ------------------------------------------------------------------ #
    # 3. Export to GeoJSON + QML styles
    # ------------------------------------------------------------------ #
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    output_files = export_network_to_geojson(
        lines,
        output_dir,
        project_name="geo_level_result",
    )

    return {
        "lines_geojson": output_files["lines_geojson"],
        "line_style":    output_files["line_style"],
        "point_style":   output_files["point_style"],
        "summary": {
            "total":    len(lines),
            "valid":    valid_count,
            "invalid":  invalid_count,
            "warnings": warning_count,
            "parse_errors": parse_errors,
        },
        "lines": lines,
    }
