"""
geo_level_plugin.py
Main QGIS plugin class for Geo Level Gravi.
"""

import os
import sys
import json
import traceback
from pathlib import Path

from qgis.PyQt.QtWidgets import (
    QAction, QMessageBox, QProgressDialog,
    QInputDialog, QMenu, QFileDialog, QTableWidgetItem
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsMessageLog, Qgis, QgsVectorLayer, QgsProject
)

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

_PROJECTS_DIR = os.path.join(_PLUGIN_DIR, "projects")


class GeoLevelPlugin:
    """QGIS Plugin — Geo Level Gravi (פילוס גיאודטי)."""

    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self._toolbar_action = None
        self._menu = None
        self._lines = []
        self._val_results = []
        self._layer = None
        self._last_output_dir = ""
        self._last_class = "H3"

    # ------------------------------------------------------------------
    # QGIS lifecycle
    # ------------------------------------------------------------------

    def initGui(self):
        # Toolbar icon
        self._toolbar_action = QAction("Geo Level Gravi", self.iface.mainWindow())
        self._toolbar_action.setToolTip("Open Geo Level Gravi dock panel")
        self._toolbar_action.triggered.connect(self._show_dock)
        self.iface.addToolBarIcon(self._toolbar_action)
        self.iface.addPluginToMenu("&Geo Level Gravi", self._toolbar_action)

        # Top-level menu — inserted before the last menu (Help)
        menubar = self.iface.mainWindow().menuBar()
        self._menu = QMenu("&Geo Leveling", self.iface.mainWindow())

        # ── Project sub-menu ──────────────────────────────────────────
        proj_menu = self._menu.addMenu("Project / פרויקט")
        proj_menu.addAction("Open / Load Files…").triggered.connect(self._open_files_dialog)
        proj_menu.addSeparator()
        proj_menu.addAction("Save Project…").triggered.connect(self._save_project)
        proj_menu.addAction("Load Project…").triggered.connect(self._load_project)
        proj_menu.addSeparator()
        proj_menu.addAction("Export Results (REZ / GeoJSON)…").triggered.connect(self._export_results)
        proj_menu.addSeparator()
        proj_menu.addAction("Project Properties…").triggered.connect(self._show_project_properties)

        # ── Analysis sub-menu ─────────────────────────────────────────
        anal_menu = self._menu.addMenu("Analysis / ניתוח")
        anal_menu.addAction("Validate All Lines").triggered.connect(self._run_batch_validation)
        anal_menu.addAction("Detect Double-Runs").triggered.connect(self._detect_double_runs)
        anal_menu.addAction("Find Loops").triggered.connect(self._detect_loops)
        anal_menu.addSeparator()
        anal_menu.addAction("Line Adjustment...").triggered.connect(self._adjust_current_line)
        anal_menu.addAction("Network Adjustment (LSA)...").triggered.connect(self._run_lsa)
        anal_menu.addAction("Network Adjustment (Enhanced)...").triggered.connect(self._run_enhanced_lsa)
        anal_menu.addSeparator()
        anal_menu.addAction("Merge Line Segments...").triggered.connect(self._open_merge_dialog)

        # ── Settings sub-menu ─────────────────────────────────────────
        sett_menu = self._menu.addMenu("Settings / הגדרות")
        sett_menu.addAction("Class Parameters…").triggered.connect(self._show_class_settings)
        sett_menu.addAction("Encoding…").triggered.connect(self._show_encoding_settings)
        sett_menu.addAction("Point Exclusion...").triggered.connect(self._show_point_exclusion)

        # ── Help sub-menu ─────────────────────────────────────────────
        help_menu = self._menu.addMenu("Help / עזרה")
        help_menu.addAction("About Geo Level Gravi").triggered.connect(self._show_about)

        menubar.insertMenu(menubar.actions()[-1], self._menu)

        # Map-to-dock sync: watch for new layers being added
        QgsProject.instance().layerWasAdded.connect(self._prepare_layer_signals)

        # Create dock (hidden until first use)
        self._create_dock()

    def unload(self):
        self.iface.removePluginMenu("&Geo Level Gravi", self._toolbar_action)
        self.iface.removeToolBarIcon(self._toolbar_action)
        if self._menu:
            self.iface.mainWindow().menuBar().removeAction(self._menu.menuAction())
        try:
            QgsProject.instance().layerWasAdded.disconnect(self._prepare_layer_signals)
        except Exception:
            pass
        if self.dock:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None

    # ------------------------------------------------------------------
    # Dock management
    # ------------------------------------------------------------------

    def _create_dock(self):
        from geo_level_dockwidget import GeoLevelDockWidget
        self.dock = GeoLevelDockWidget(self.iface.mainWindow())
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.hide()

        self.dock.line_selected.connect(self._on_line_selected)
        self.dock.adjust_line_requested.connect(self._adjust_single_line)
        self.dock.lsa_requested.connect(self._run_lsa)
        self.dock.files_added.connect(self._process_files)
        self.dock.double_runs_requested.connect(self._detect_double_runs)
        self.dock.loops_requested.connect(self._detect_loops)
        self.dock.enhanced_lsa_requested.connect(self._run_enhanced_lsa)

    def _show_dock(self):
        if self.dock:
            self.dock.show()
            self.dock.raise_()

    # ------------------------------------------------------------------
    # Map-to-Dock sync (Task 1)
    # ------------------------------------------------------------------

    def _prepare_layer_signals(self, layer):
        """Connect selectionChanged for any 'Geodetic Results' layer."""
        if layer.name() == "Geodetic Results":
            layer.selectionChanged.connect(lambda: self._sync_dock_to_map(layer))

    def _sync_dock_to_map(self, layer):
        """When a feature is selected on the map, highlight it in the dock."""
        selected_ids = layer.selectedFeatureIds()
        if not selected_ids or not self.dock:
            return
        feat = layer.getFeature(selected_ids[0])
        filename = feat["filename"] if feat.fields().indexOf("filename") >= 0 else None
        if filename:
            self.dock.select_line_by_filename(filename)

    # ------------------------------------------------------------------
    # File loading / processing
    # ------------------------------------------------------------------

    def _open_files_dialog(self):
        from geo_level_dialog import GeoLevelDialog
        dlg = GeoLevelDialog(self.iface.mainWindow())
        if not dlg.exec_():
            return
        file_paths     = dlg.get_file_paths()
        leveling_class = dlg.get_selected_class()
        output_dir     = dlg.get_output_dir()
        if not file_paths:
            QMessageBox.warning(self.iface.mainWindow(), "Geo Level Gravi",
                                "No input files selected.")
            return
        if not output_dir:
            QMessageBox.warning(self.iface.mainWindow(), "Geo Level Gravi",
                                "No output directory selected.")
            return
        self._last_class = leveling_class
        self._last_output_dir = output_dir
        self._process_files(file_paths, leveling_class, output_dir)

    def _process_files(self, file_paths, leveling_class=None, output_dir=None):
        """Core parse -> validate -> export pipeline. Appends new files, deduplicates."""
        leveling_class = leveling_class or self._last_class or "H3"
        output_dir     = output_dir or self._last_output_dir

        if not output_dir:
            output_dir = QFileDialog.getExistingDirectory(
                self.iface.mainWindow(), "Select Output Directory"
            )
            if not output_dir:
                return

        # Deduplicate: skip files whose basename is already loaded
        existing = {os.path.basename(ln.filename) for ln in self._lines}
        existing.update({ln.filename for ln in self._lines})
        new_paths = [fp for fp in file_paths if fp not in existing
                     and os.path.basename(fp) not in existing]
        if not new_paths:
            self.iface.messageBar().pushMessage(
                "Geo Level Gravi", "All selected files are already loaded.",
                level=Qgis.Info, duration=4)
            return

        self._last_class      = leveling_class
        self._last_output_dir = output_dir

        progress = QProgressDialog("Processing geodetic data...", "Cancel", 0, 0,
                                   self.iface.mainWindow())
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        try:
            from core_logic.process import process_geodetic_data
            result = process_geodetic_data(
                file_paths=new_paths,
                leveling_class=leveling_class,
                output_dir=output_dir,
            )
        except Exception as exc:
            progress.close()
            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",
                                     level=Qgis.Critical)
            QMessageBox.critical(self.iface.mainWindow(), "Geo Level Gravi -- Error",
                                 "Processing failed:\n\n" + str(exc))
            return

        progress.close()

        # Append new lines to existing list
        new_lines = result["lines"]
        self._lines.extend(new_lines)

        # Re-validate the full combined set
        try:
            from core_logic.validators import BatchValidator
            bv = BatchValidator(leveling_class=int(leveling_class[1]))
            self._val_results = bv.validate_batch(self._lines)
        except Exception:
            self._val_results = result.get("val_results", [])

        self._load_layer(result["lines_geojson"], result["line_style"])
        self._show_dock()
        self.dock.load_lines(self._lines, self._val_results)

        s = result["summary"]
        total_loaded = len(self._lines)
        rez_path = result.get("rez_path")
        rez_info = ("  |  REZ: " + os.path.basename(rez_path)) if rez_path else ""
        self.iface.messageBar().pushMessage(
            "Geo Level Gravi",
            "Added " + str(len(new_lines)) + " line(s) -- total " + str(total_loaded)
            + "  (" + str(s["valid"]) + "/" + str(s["total"]) + " new valid)" + rez_info,
            level=Qgis.Success, duration=6,
        )

    def _load_layer(self, geojson_path: str, qml_path: str):
        layer = QgsVectorLayer(geojson_path, "Geodetic Results", "ogr")
        if not layer.isValid():
            QgsMessageLog.logMessage(f"Failed to load layer: {geojson_path}",
                                     "GeoLevelPlugin", level=Qgis.Warning)
            return
        if os.path.exists(qml_path):
            layer.loadNamedStyle(qml_path)
        QgsProject.instance().addMapLayer(layer)
        self._layer = layer

    # ------------------------------------------------------------------
    # Dock ↔ map sync
    # ------------------------------------------------------------------

    def _on_line_selected(self, idx: int):
        if not (0 <= idx < len(self._lines)):
            return
        selected_line = self._lines[idx]
        self.dock.populate_line_details(selected_line)
        self.dock.update_validation_tab(selected_line)
        self._zoom_to_line_feature(idx)

    def _zoom_to_line_feature(self, idx: int):
        if not self._layer or not self._layer.isValid():
            return
        line_features = [
            f for f in self._layer.getFeatures()
            if f.geometry() and f.geometry().type() == 1
        ]
        if idx >= len(line_features):
            return
        bbox = line_features[idx].geometry().boundingBox()
        bbox.scale(1.2)
        self.iface.mapCanvas().setExtent(bbox)
        self.iface.mapCanvas().refresh()

    # ------------------------------------------------------------------
    # Project menu actions
    # ------------------------------------------------------------------

    def _save_project(self):
        if not self._lines:
            QMessageBox.information(self.iface.mainWindow(), "Save Project",
                                    "No lines loaded to save.")
            return
        os.makedirs(_PROJECTS_DIR, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(
            self.iface.mainWindow(), "Save Project",
            _PROJECTS_DIR, "Geo Level Project (*.glp)"
        )
        if not path:
            return
        data = {
            "class": self._last_class,
            "output_dir": self._last_output_dir,
            "files": [ln.filename for ln in self._lines],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        self.iface.messageBar().pushMessage("Geo Level Gravi",
                                            f"Project saved: {path}",
                                            level=Qgis.Success, duration=4)

    def _load_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self.iface.mainWindow(), "Load Project",
            _PROJECTS_DIR, "Geo Level Project (*.glp)"
        )
        if not path or not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        files      = data.get("files", [])
        cls        = data.get("class", "H3")
        output_dir = data.get("output_dir", "")
        missing = [fp for fp in files if not os.path.exists(fp)]
        if missing:
            QMessageBox.warning(self.iface.mainWindow(), "Load Project",
                                f"Missing files:\n" + "\n".join(missing))
            files = [fp for fp in files if os.path.exists(fp)]
        if files:
            self._last_class = cls
            self._last_output_dir = output_dir
            self._process_files(files, cls, output_dir)

    def _show_project_properties(self):
        points = set()
        for ln in self._lines:
            points.add(ln.start_point)
            points.add(ln.end_point)
        msg = (
            f"Project Properties\n{'─'*40}\n"
            f"Lines loaded   : {len(self._lines)}\n"
            f"Unique points  : {len(points)}\n"
            f"Active class   : {self.dock.get_selected_class() if self.dock else self._last_class}\n"
            f"QGIS layer     : {'Yes' if self._layer and self._layer.isValid() else 'None'}\n"
            f"Output dir     : {self._last_output_dir or '—'}\n"
        )
        QMessageBox.information(self.iface.mainWindow(), "Project Properties", msg)

    # ------------------------------------------------------------------
    # Analysis menu actions
    # ------------------------------------------------------------------

    def _run_batch_validation(self):
        if not self._lines:
            QMessageBox.information(self.iface.mainWindow(), "Validate All",
                                    "No lines loaded.")
            return
        try:
            from core_logic.validators import BatchValidator
            cls = self.dock.get_selected_class() if self.dock else self._last_class
            bv = BatchValidator(leveling_class=int(cls[1]))
            self._val_results = bv.validate_batch(self._lines)
            # Refresh dock list colours
            self.dock.load_lines(self._lines, self._val_results)
            summary = bv.get_summary([vr for _, vr in self._val_results])
            val_msg = (
                "Validation complete: "
                + str(summary["valid"]) + "/" + str(summary["total"]) + " valid  |  "
                + str(summary["endpoint_issues"]) + " endpoint  |  "
                + str(summary["naming_issues"]) + " naming  |  "
                + str(summary["tolerance_issues"]) + " tolerance"
            )
            self.iface.messageBar().pushMessage(
                "Validation", val_msg, level=Qgis.Info, duration=8
            )
            if self.dock:
                self.dock.show_analysis_result(val_msg)
                self.dock.log("Validate All completed.")
        except Exception as exc:
            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",
                                     level=Qgis.Critical)
            if self.dock:
                self.dock.log("Validate All ERROR: " + str(exc))
            QMessageBox.critical(self.iface.mainWindow(), "Validate All -- Error", str(exc))

    def _detect_loops(self):
        if not self._lines:
            QMessageBox.information(self.iface.mainWindow(), "Find Loops",
                                    "No lines loaded.")
            return
        try:
            from core_logic.engine.loop_detector import LoopAnalyzer
            analyzer = LoopAnalyzer(self._lines)
            loops = analyzer.find_loops()

            tbl = self.dock.loop_table
            tbl.setRowCount(0)

            if not loops:
                self.dock.log("Find Loops: no closed loops detected.")
            else:
                for i, loop in enumerate(loops, 1):
                    ok, mis_mm, tol_mm = loop.check_tolerance()
                    row = tbl.rowCount()
                    tbl.insertRow(row)

                    path_str = " -> ".join(loop.points)
                    dist_m   = getattr(loop, "total_distance", 0.0)

                    values = [
                        str(i),
                        path_str,
                        "{:.1f}".format(dist_m),
                        "{:.3f}".format(mis_mm),
                        "{:.2f}".format(tol_mm),
                    ]
                    for col, val in enumerate(values):
                        item = QTableWidgetItem(val)
                        item.setTextAlignment(Qt.AlignCenter)
                        tbl.setItem(row, col, item)

                    status_item = QTableWidgetItem("PASS" if ok else "FAIL")
                    status_item.setTextAlignment(Qt.AlignCenter)
                    status_item.setForeground(
                        QColor("#2e7d32") if ok else QColor("#c62828")
                    )
                    tbl.setItem(row, 5, status_item)

                self.dock.log("Find Loops: " + str(len(loops)) + " loop(s) found.")

            # Switch to Analysis tab -> Loops sub-tab
            self.dock.tabs.setCurrentIndex(2)
            self.dock.analysis_tabs.setCurrentIndex(1)
            self._show_dock()

        except Exception as exc:
            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",
                                     level=Qgis.Critical)
            if self.dock:
                self.dock.log("Find Loops ERROR: " + str(exc))
            QMessageBox.critical(self.iface.mainWindow(), "Find Loops -- Error", str(exc))

    def _detect_double_runs(self):
        if not self._lines:
            QMessageBox.information(self.iface.mainWindow(), "Detect Double-Runs",
                                    "No lines loaded.")
            return
        try:
            from core_logic.engine.loop_detector import detect_double_runs, LoopAnalyzer
            pairs = detect_double_runs(self._lines)

            tbl = self.dock.double_run_table
            tbl.setRowCount(0)

            if not pairs:
                self.dock.log("Detect Double-Runs: no pairs found.")
            else:
                cls = self.dock.get_selected_class() if self.dock else self._last_class
                analyzer = LoopAnalyzer(self._lines)
                for fwd, ret in pairs:
                    res = analyzer.analyze_double_run(fwd, ret, int(cls[1]))
                    row = tbl.rowCount()
                    tbl.insertRow(row)

                    pair_label = fwd.start_point + " <-> " + fwd.end_point
                    mean_dh = (fwd.total_height_diff - ret.total_height_diff) / 2.0
                    mis_mm  = res["misclosure_mm"]
                    tol_mm  = res["tolerance_mm"]
                    passed  = res["within_tolerance"]

                    values = [
                        pair_label,
                        os.path.basename(fwd.filename),
                        os.path.basename(ret.filename),
                        "{:.5f}".format(mean_dh),
                        "{:.3f}".format(mis_mm),
                        "{:.2f}".format(tol_mm),
                    ]
                    for col, val in enumerate(values):
                        item = QTableWidgetItem(val)
                        item.setTextAlignment(Qt.AlignCenter)
                        tbl.setItem(row, col, item)

                    status_item = QTableWidgetItem("PASS" if passed else "FAIL")
                    status_item.setTextAlignment(Qt.AlignCenter)
                    status_item.setForeground(
                        QColor("#2e7d32") if passed else QColor("#c62828")
                    )
                    tbl.setItem(row, 6, status_item)

                self.dock.log("Detect Double-Runs: " + str(len(pairs)) + " pair(s) found.")

            # Switch to Analysis tab -> Double-Runs sub-tab
            self.dock.tabs.setCurrentIndex(2)
            self.dock.analysis_tabs.setCurrentIndex(0)
            self._show_dock()

        except Exception as exc:
            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",
                                     level=Qgis.Critical)
            if self.dock:
                self.dock.log("Double-Runs ERROR: " + str(exc))
            QMessageBox.critical(self.iface.mainWindow(), "Double-Runs -- Error", str(exc))

    def _run_lsa(self):
        if not self._lines:
            QMessageBox.information(self.iface.mainWindow(), "LSA Adjustment",
                                    "No lines loaded.")
            return
        try:
            fixed_input, ok = QInputDialog.getText(
                self.iface.mainWindow(), "LSA — Fixed Points / נקודות קבועות",
                "Enter fixed benchmarks as  POINT_ID=HEIGHT  (comma-separated):\n"
                "Example:  BM1=100.000, BM2=105.500"
            )
            if not ok:
                return
            fixed_points = {}
            for token in fixed_input.split(","):
                token = token.strip()
                if "=" in token:
                    pid, h = token.split("=", 1)
                    try:
                        fixed_points[pid.strip()] = float(h.strip())
                    except ValueError:
                        pass
            if not fixed_points:
                QMessageBox.warning(self.iface.mainWindow(), "LSA Adjustment",
                                    "No valid fixed points entered.")
                return

            from core_logic.engine.least_squares import LeastSquaresAdjuster
            result = LeastSquaresAdjuster().adjust_from_lines(self._lines, fixed_points)

            # ── Show results dialog ───────────────────────────────────
            from geo_level_lsa_dialog import GeoLevelLSADialog
            dlg = GeoLevelLSADialog(result, fixed_points, self.iface.mainWindow())
            dlg.exec_()

            # ── Update map layer with adj_height on point features ────
            self._apply_lsa_to_layer(result)

            # ── Summary in dock ───────────────────────────────────────
            dof = max(0, len(result.residuals) - (
                len(result.adjusted_heights) - len(fixed_points)
            ))
            summary = (
                f"LSA complete — {result.iteration} iteration(s)\n"
                f"σ₀² = {result.mse_unit_weight**2:.6f} m²  |  "
                f"σ₀ = {result.mse_unit_weight*1000:.3f} mm  |  "
                f"DoF = {dof}\n"
                f"K = {result.k_coefficient:.4f}  |  "
                f"Total dist = {result.total_distance_km:.3f} km\n\n"
                "Adjusted Heights:\n"
            )
            for pid, h in sorted(result.adjusted_heights.items()):
                sigma = result.mse_heights.get(pid, 0.0) * 1000
                summary += f"  {pid:20s}  {h:.5f} m  ±{sigma:.3f} mm\n"
            QgsMessageLog.logMessage(summary, "GeoLevelPlugin", level=Qgis.Info)
            if self.dock:
                self.dock.show_analysis_result(summary)
                self.dock.log("LSA completed.")

        except Exception as exc:
            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",
                                     level=Qgis.Critical)
            QMessageBox.critical(self.iface.mainWindow(), "LSA Adjustment — Error", str(exc))

    def _apply_lsa_to_layer(self, result):
        """Add/update adj_height field on point features in the active layer."""
        if not self._layer or not self._layer.isValid():
            return
        from qgis.core import QgsField
        from qgis.PyQt.QtCore import QVariant

        layer = self._layer
        layer.startEditing()

        # Add adj_height field if absent
        if layer.fields().indexOf("adj_height") < 0:
            layer.addAttribute(QgsField("adj_height", QVariant.Double, "double", 12, 5))

        adj_idx = layer.fields().indexOf("adj_height")
        pid_idx = layer.fields().indexOf("point_id")

        if pid_idx >= 0 and adj_idx >= 0:
            for feat in layer.getFeatures():
                if feat.geometry() and feat.geometry().type() == 0:  # Point
                    pid = str(feat[pid_idx])
                    if pid in result.adjusted_heights:
                        layer.changeAttributeValue(
                            feat.id(), adj_idx, result.adjusted_heights[pid]
                        )

        layer.commitChanges()
        self.iface.messageBar().pushMessage(
            "LSA", "adj_height field updated on map layer",
            level=Qgis.Success, duration=4
        )

    def _adjust_current_line(self):
        """Adjust whichever line is currently selected in the dock."""
        if self.dock:
            idx = self.dock.get_current_index()
            if idx >= 0:
                self._adjust_single_line(idx)

    def _adjust_single_line(self, idx: int):
        if not (0 <= idx < len(self._lines)):
            return
        line = self._lines[idx]
        try:
            from geo_level_line_adj_dialog import GeoLevelLineAdjDialog
            dlg = GeoLevelLineAdjDialog(line, self.iface.mainWindow())
            dlg.exec_()
        except Exception as exc:
            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",
                                     level=Qgis.Critical)
            QMessageBox.critical(self.iface.mainWindow(), "Adjust Line -- Error", str(exc))

    def _run_enhanced_lsa(self):
        if not self._lines:
            QMessageBox.information(self.iface.mainWindow(), "Enhanced LSA",
                                    "No lines loaded.")
            return
        try:
            from geo_level_enhanced_lsa_dialog import GeoLevelEnhancedLSADialog
            dlg = GeoLevelEnhancedLSADialog(self._lines, self.iface.mainWindow())
            if dlg.exec_() and dlg.result:
                self._apply_lsa_to_layer(dlg.result)
        except Exception as exc:
            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",
                                     level=Qgis.Critical)
            QMessageBox.critical(self.iface.mainWindow(), "Enhanced LSA -- Error", str(exc))

    def _open_merge_dialog(self):
        if not self._lines:
            QMessageBox.information(self.iface.mainWindow(), "Merge Segments",
                                    "No lines loaded.")
            return
        try:
            from geo_level_merge_dialog import GeoLevelMergeDialog
            dlg = GeoLevelMergeDialog(self._lines, self.iface.mainWindow())
            if dlg.exec_():
                self._lines = dlg.merged_lines
                try:
                    from core_logic.validators import BatchValidator
                    cls = self.dock.get_selected_class() if self.dock else self._last_class
                    bv = BatchValidator(leveling_class=int(cls[1]))
                    self._val_results = bv.validate_batch(self._lines)
                except Exception:
                    pass
                if self.dock:
                    self.dock.load_lines(self._lines, self._val_results)
                self.iface.messageBar().pushMessage(
                    "Merge",
                    "Lines updated -- " + str(len(self._lines)) + " total",
                    level=Qgis.Success, duration=4)
        except Exception as exc:
            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",
                                     level=Qgis.Critical)
            QMessageBox.critical(self.iface.mainWindow(), "Merge -- Error", str(exc))

    def _create_joint_project(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self.iface.mainWindow(), "Select Project Files to Join",
            _PROJECTS_DIR, "Geo Level Project (*.glp)"
        )
        if len(paths) < 2:
            QMessageBox.information(self.iface.mainWindow(), "Create Joint Project",
                                    "Please select at least two .glp project files.")
            return
        combined_files, combined_class, combined_output = [], "H3", self._last_output_dir
        for p in paths:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                combined_files.extend(data.get("files", []))
                combined_class = data.get("class", combined_class)
                combined_output = data.get("output_dir", combined_output) or combined_output
            except Exception:
                pass
        seen, unique_files = set(), []
        for fp in combined_files:
            if fp not in seen:
                seen.add(fp)
                unique_files.append(fp)
        save_path, _ = QFileDialog.getSaveFileName(
            self.iface.mainWindow(), "Save Joint Project",
            _PROJECTS_DIR, "Geo Level Project (*.glp)"
        )
        if not save_path:
            return
        os.makedirs(_PROJECTS_DIR, exist_ok=True)
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump({"class": combined_class, "output_dir": combined_output,
                       "files": unique_files}, f, indent=2)
        self.iface.messageBar().pushMessage(
            "Joint Project",
            "Saved " + str(len(unique_files)) + " files to " + save_path,
            level=Qgis.Success, duration=5)

    def _export_results(self):
        if not self._lines:
            QMessageBox.information(self.iface.mainWindow(), "Export Results",
                                    "No lines loaded.")
            return
        output_dir = QFileDialog.getExistingDirectory(
            self.iface.mainWindow(), "Select Export Directory", self._last_output_dir
        )
        if not output_dir:
            return
        try:
            exported = []

            # REZ summary
            from core_logic.exporters import export_rez
            rez_path = os.path.join(output_dir, "geo_level_export.rez")
            export_rez(rez_path, self._lines, project_name="geo_level_export")
            exported.append("REZ: " + rez_path)

            # GeoJSON
            from core_logic.gis.geojson_export import export_network_to_geojson
            files = export_network_to_geojson(
                self._lines, output_dir, project_name="geo_level_export"
            )
            exported.append("GeoJSON: " + files["lines_geojson"])

            QMessageBox.information(
                self.iface.mainWindow(), "Export Results",
                "Exported:\n  " + "\n  ".join(exported)
            )
        except Exception as exc:
            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",
                                     level=Qgis.Critical)
            QMessageBox.critical(self.iface.mainWindow(), "Export -- Error", str(exc))

    def _show_point_exclusion(self):
        try:
            from geo_level_point_exclusion_dialog import GeoLevelPointExclusionDialog
            dlg = GeoLevelPointExclusionDialog(self._lines, self.iface.mainWindow())
            if dlg.exec_():
                self._lines = dlg.updated_lines
                if self.dock:
                    self.dock.load_lines(self._lines, self._val_results)
        except Exception as exc:
            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",
                                     level=Qgis.Critical)
            QMessageBox.critical(self.iface.mainWindow(), "Point Exclusion -- Error", str(exc))

    # ------------------------------------------------------------------
    # Settings menu actions
    # ------------------------------------------------------------------

    def _show_class_settings(self):
        try:
            from geo_level_settings_dialog import GeoLevelSettingsDialog
            dlg = GeoLevelSettingsDialog(self.iface.mainWindow())
            if dlg.exec_():
                # Re-run validation so dock colours reflect new k values immediately
                if self._lines:
                    try:
                        from core_logic.validators import BatchValidator
                        cls = self.dock.get_selected_class() if self.dock else self._last_class
                        bv = BatchValidator(leveling_class=int(cls[1]))
                        self._val_results = bv.validate_batch(self._lines)
                        self.dock.load_lines(self._lines, self._val_results)
                    except Exception:
                        pass
                self.iface.messageBar().pushMessage(
                    "Settings",
                    "Class parameters updated and saved to settings.json",
                    level=Qgis.Success, duration=5,
                )
        except Exception as exc:
            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",
                                     level=Qgis.Critical)
            QMessageBox.critical(self.iface.mainWindow(), "Class Parameters — Error", str(exc))

    def _show_encoding_settings(self):
        try:
            from core_logic.config.settings import get_settings
            s = get_settings()
            enc = getattr(s, "encoding", "cp1255")
            new_enc, ok = QInputDialog.getText(
                self.iface.mainWindow(), "Encoding",
                "Input file encoding (e.g. cp1255, utf-8, latin-1):",
                text=enc
            )
            if ok and new_enc.strip():
                s.encoding = new_enc.strip()
                self.iface.messageBar().pushMessage(
                    "Settings", f"Encoding set to: {s.encoding}",
                    level=Qgis.Info, duration=4
                )
        except Exception as exc:
            QMessageBox.critical(self.iface.mainWindow(), "Encoding — Error", str(exc))

    # ------------------------------------------------------------------
    # Help
    # ------------------------------------------------------------------

    def _show_about(self):
        QMessageBox.information(
            self.iface.mainWindow(), "About Geo Level Gravi",
            "Geo Level Gravi v1.1\n"
            "Geodetic Leveling Tool — QGIS Plugin\n\n"
            "Compliant with Survey of Israel Directive ג2 (2021)\n"
            "Supports Trimble DAT, Leica RAW/GSI\n\n"
            "GitHub: github.com/sealevelil48/Geo_Level_Gravi-"
        )

    # ------------------------------------------------------------------
    # Legacy entry point
    # ------------------------------------------------------------------

    def run(self):
        self._open_files_dialog()
