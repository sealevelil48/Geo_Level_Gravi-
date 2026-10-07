"""
deploy.py — GeoLevel Gravi QGIS Plugin Installer
Run this script from the GeoLevelQGIS folder (or anywhere — it auto-detects).

Usage:
    python deploy.py

What it does:
    1. Warns if QGIS is currently running (locked files cause errors)
    2. Removes the ENTIRE old plugin folder — including stale .pyc cache and
       any files that were renamed or deleted in the source (ghost-file safe)
    3. Copies fresh plugin files, skipping .pyc / __pycache__ / .glp files
    4. Prints a reminder to press Ctrl+F5 in QGIS

Works on Windows with any QGIS profile.
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path


def find_qgis_plugins_dir() -> Path:
    """Return the default QGIS3 plugins directory for the current user."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA environment variable not set.")
    path = Path(appdata) / "QGIS" / "QGIS3" / "profiles" / "default" / "python" / "plugins"
    return path


def qgis_is_running() -> bool:
    """Return True if any QGIS process is currently running (Windows only)."""
    try:
        output = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq qgis-bin.exe", "/NH"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return "qgis-bin.exe" in output.lower()
    except Exception:
        return False  # Can't determine — don't block the deploy


def main():
    # ── Source: the GeoLevelQGIS folder containing this script ────────────
    src = Path(__file__).parent.resolve()
    plugin_name = src.name  # "GeoLevelQGIS"

    plugins_dir = find_qgis_plugins_dir()
    dst = plugins_dir / plugin_name

    print("=" * 60)
    print("  GeoLevel Gravi — Plugin Deploy")
    print("=" * 60)
    print(f"  Source : {src}")
    print(f"  Target : {dst}")
    print()

    # ── Step 0: Warn if QGIS is open ──────────────────────────────────────
    if qgis_is_running():
        print("  WARNING: QGIS appears to be running.")
        print("  Close QGIS before deploying to avoid PermissionError on")
        print("  locked .pyc files, then re-run this script.")
        print()
        answer = input("  Continue anyway? [y/N] ").strip().lower()
        if answer != "y":
            print("  Aborted. Close QGIS and try again.")
            sys.exit(0)
        print()

    # ── Step 1: Full wipe of old installation ─────────────────────────────
    # shutil.rmtree removes EVERYTHING — stale .pyc, renamed modules, ghost
    # files, __pycache__ — none can survive to cause namespace collisions.
    if dst.exists():
        print(f"  Removing old installation (including __pycache__ and stale files)...")
        try:
            shutil.rmtree(dst)
        except PermissionError as exc:
            print(f"\n  ERROR: Could not remove '{dst}'.")
            print(f"  Detail: {exc}")
            print(f"  QGIS may still be holding a lock on the folder.")
            print(f"  Close QGIS completely and re-run deploy.py.")
            sys.exit(1)
        print("  Old installation removed.")
    else:
        print("  No existing installation found — fresh install.")
    print()

    # ── Step 2: Copy fresh plugin files ───────────────────────────────────
    # Excluded from the copy:
    #   deploy.py     — this script itself
    #   *.pyc         — compiled bytecode (QGIS regenerates on first load)
    #   __pycache__   — bytecode cache directories
    #   *.glp         — project files (user data, not plugin code)
    print("  Copying plugin files...")
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns("deploy.py", "*.pyc", "__pycache__", "*.glp"),
    )
    print(f"  Copied to {dst}")
    print()

    # ── Done ──────────────────────────────────────────────────────────────
    print("=" * 60)
    print("  Plugin deployed successfully!")
    print()
    print("  Next step:  Open QGIS and press  Ctrl + F5")
    print("              (Plugin Reloader) to activate the update.")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
        sys.exit(1)
    except Exception as exc:
        print(f"\n  ERROR: {exc}")
        sys.exit(1)
