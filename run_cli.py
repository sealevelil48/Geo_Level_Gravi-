#!/usr/bin/env python3
"""
Launch the Geodetic Tool CLI
Usage:
    python run_cli.py parse *.DAT
    python run_cli.py validate *.DAT
    python run_cli.py geojson *.DAT -o ./output
"""
import sys
import os

# Add the package to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from geodetic_tool.cli.main import main

if __name__ == "__main__":
    main()
