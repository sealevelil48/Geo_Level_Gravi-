"""Benchmark database (PostgreSQL / PostGIS control-point table) for the
standalone app - port of the QGIS plugin's geolevel_db_manager."""
from .benchmark_db import BenchmarkDBManager, BenchmarkRecord, get_db_manager  # noqa: F401
