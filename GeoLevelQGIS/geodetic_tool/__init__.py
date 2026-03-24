"""
Geodetic Leveling Tool
=====================
A comprehensive Python package for geodetic leveling data processing.

Supports:
- Trimble DAT format
- Leica RAW/GSI format

Features:
- Data parsing and validation
- Height difference calculations
- Line and network adjustments
- Loop detection and analysis
- Export to FA0, FA1, FTEG, REZ formats
- GeoJSON export for QGIS integration
"""

__version__ = "1.0.0"
__author__ = "Geodetic Tools"

from .config.models import LevelingLine, StationSetup, Benchmark
from .config.settings import Settings
