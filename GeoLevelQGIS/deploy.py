"""
deploy.py — GeoLevel Gravi QGIS Plugin Installer
Run this script from the GeoLevelQGIS folder (or anywhere — it auto-detects).

Usage:
    python deploy.py

What it does:
    1. Removes the old plugin folder completely (including stale .pyc cache)
    2. Copies the fresh plugin files to the QGIS plugins directory
    3. Prints a reminder to press Ctrl+F5 in QGIS

Works on Windows with any QGIS profile.
"""

import os
import sys
import shutil
from pathlib import Path


def find_qgis_plugins_dir():
    """Return the default QGIS3 plugins directory for the current user."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA environment variable not set.")
    path = Path(appdata) / "QGIS" / "QGIS3" / "profiles" / "default" / "python" / "plugins"
    return path


def main():
    # Source: the GeoLevelQGIS folder that contains this script
    src = Path(__file__).parent.resolve()
    plugin_name = src.name  # "GeoLevelQGIS"

    plugins_dir = find_qgis_plugins_dir()
    dst = plugins_dir / plugin_name

    print(f"Source : {src}")
    print(f"Target : {dst}")
    print()

    # Step 1: Remove old installation completely (clears stale .pyc files)
    if dst.exists():
        print(f"Removing old installation at {dst} ...")
        shutil.rmtree(dst)
        print("  Done.")
    else:
        print("No existing installation found — fresh install.")

    # Step 2: Copy fresh
    print(f"Copying plugin files ...")
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("deploy.py", "*.pyc", "__pycache__", "*.glp"))
    print(f"  Copied to {dst}")
    print()
    print("=" * 60)
    print("  Plugin deployed successfully!")
    print("  Now open QGIS and press Ctrl+F5 to reload the plugin.")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nERROR: {exc}")
        sys.exit(1)
