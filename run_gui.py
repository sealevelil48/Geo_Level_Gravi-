#!/usr/bin/env python3
"""
Launch the Geodetic Tool GUI Application
"""
import sys
import os

# Add the package to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from geodetic_tool.gui.app import main

if __name__ == "__main__":
    main()
