"""
geo_level_plugin.py
Main QGIS plugin class for Geo Level Gravi.

Lifecycle
---------
  classFactory(iface)  ->  GeoLevelPlugin(iface)
  initGui()            ->  adds toolbar icon + menu entry
  run()                ->  opens GeoLevelDialog, bridges to core_logic
  unload()             ->  removes toolbar icon + menu entry
"""

import os
import sys
import traceback
from pathlib import Path

from qgis.PyQt.QtWidgets import QAction, QMessageBox, QProgressDialog
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsMessageLog, Qgis, QgsVectorLayer, QgsProject

# Ensure core_logic is importable
_PLUGIN_DIR = Path(__file__).parent
_CORE_DIR = _PLUGIN_DIR / "core_logic"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))


class GeoLevelPlugin:
    """QGIS Plugin — Geo Level Gravi (פילוס גיאודטי)."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = str(_PLUGIN_DIR)
        self.action = None

    # ------------------------------------------------------------------
    # QGIS lifecycle
    # ------------------------------------------------------------------

    def initGui(self):
        self.action = QAction("Geo Level Gravi", self.iface.mainWindow())
        self.action.setToolTip("Run geodetic leveling adjustment and map results")
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&Geo Level Gravi", self.action)

    def unload(self):
        self.iface.removePluginMenu("&Geo Level Gravi", self.action)
        self.iface.removeToolBarIcon(self.action)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self):
        """Open dialog, collect inputs, call core_logic, load layers."""
        from .geo_level_dialog import GeoLevelDialog

        dlg = GeoLevelDialog(self.iface.mainWindow())
        if not dlg.exec_():
            return                          # user cancelled

        # ---- collect inputs from dialog --------------------------------
        file_paths    = dlg.get_file_paths()
        leveling_class = dlg.get_selected_class()
        output_dir    = dlg.get_output_dir()

        if not file_paths:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "Geo Level Gravi",
                "No input files selected."
            )
            return

        if not output_dir:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "Geo Level Gravi",
                "No output directory selected."
            )
            return

        # ---- progress indicator ----------------------------------------
        progress = QProgressDialog(
            "Processing geodetic data…", "Cancel", 0, 0,
            self.iface.mainWindow()
        )
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.show()

        # ---- call core_logic -------------------------------------------
        try:
            from core_logic.process import process_geodetic_data

            result = process_geodetic_data(
                file_paths=file_paths,
                leveling_class=leveling_class,
                output_dir=output_dir,
            )

        except Exception as exc:
            progress.close()
            err_detail = traceback.format_exc()
            QgsMessageLog.logMessage(
                f"GeoLevelGravi error:\n{err_detail}",
                "GeoLevelPlugin",
                level=Qgis.Critical,
            )
            QMessageBox.critical(
                self.iface.mainWindow(),
                "Geo Level Gravi — Error",
                f"Processing failed:\n\n{exc}\n\nSee QGIS log for details."
            )
            return

        progress.close()

        # ---- load GeoJSON layer into QGIS ------------------------------
        lines_path = result["lines_geojson"]
        qml_path   = result["line_style"]

        layer = QgsVectorLayer(lines_path, "Geodetic Results", "ogr")

        if not layer.isValid():
            QgsMessageLog.logMessage(
                f"Failed to load layer: {lines_path}",
                "GeoLevelPlugin",
                level=Qgis.Warning,
            )
        else:
            # Apply QML style if it exists
            if Path(qml_path).exists():
                layer.loadNamedStyle(qml_path)

            QgsProject.instance().addMapLayer(layer)

        # ---- success message -------------------------------------------
        s = result["summary"]
        msg = (
            f"Processing complete!\n\n"
            f"Files processed : {s['total']}\n"
            f"Valid           : {s['valid']}\n"
            f"Invalid         : {s['invalid']}\n"
            f"Warnings        : {s['warnings']}\n\n"
            f"Output → {output_dir}"
        )

        if s.get("parse_errors"):
            msg += f"\n\nParse errors ({len(s['parse_errors'])}):\n"
            msg += "\n".join(f"  • {e}" for e in s["parse_errors"][:5])

        self.iface.messageBar().pushMessage(
            "Geo Level Gravi",
            f"Done — {s['valid']}/{s['total']} lines valid",
            level=Qgis.Success,
            duration=6,
        )

        QgsMessageLog.logMessage(msg, "GeoLevelPlugin", level=Qgis.Info)

        QMessageBox.information(
            self.iface.mainWindow(),
            "Geo Level Gravi — Complete",
            msg,
        )
