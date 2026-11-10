"""
geo_level_plugin.py
Main QGIS plugin class for Geo Level Gravi.
"""

import os
import sys
import json
import traceback
from datetime import datetime
from pathlib import Path

from qgis.PyQt.QtWidgets import (
    QAction, QMessageBox, QProgressDialog,
    QInputDialog, QMenu, QFileDialog, QTableWidgetItem,
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QPushButton,
    QLabel, QTableWidget, QHeaderView, QSplitter, QWidget
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


def _apply_line_renderer(layer):
    """Apply a live QgsRuleBasedRenderer to a line layer.

    Rule order (first match wins — order is critical):
      1. valid_by_manager → amber  #FFC107, 1.4 mm   (must come before valid)
      2. invalid / error  → red    #F44336, 1.2 mm
      3. ELSE (valid)     → green  #4CAF50, 1.2 mm

    'valid_by_manager' contains the substring 'valid', so manager must be
    checked first to prevent it being coloured green by rule 3.
    ILIKE is used for case-insensitive substring matching so both
    'VALID_BY_MANAGER' (uppercase from builder) and any legacy variants match.
    """
    try:
        from qgis.core import (
            QgsRuleBasedRenderer, QgsSymbol, QgsLineSymbol,
        )
        from qgis.PyQt.QtGui import QColor

        def _line_sym(hex_color, width_mm, offset_mm=0.0):
            sym = QgsLineSymbol.createSimple({
                "color": hex_color,
                "width": str(width_mm),
                "capstyle": "round",
                "offset": str(offset_mm),
                "offset_unit": "MM",
            })
            return sym

        root = QgsRuleBasedRenderer.Rule(None)

        # Rule 0 — Excluded (invisible) — MUST be first so excluded lines
        # are not rendered rather than falling through to green.
        sym_excl = QgsLineSymbol.createSimple({
            "line_style": "no",
            "color": "0,0,0,0",
            "width": "0",
        })
        r_excl = QgsRuleBasedRenderer.Rule(sym_excl)
        r_excl.setFilterExpression("\"status\" ILIKE '%exclud%'")
        r_excl.setLabel("Excluded")

        # Rule 1 — Manager override (amber) — before valid to avoid substring match
        r_mgr = QgsRuleBasedRenderer.Rule(_line_sym("#FFC107", 1.4, 0.6))
        r_mgr.setFilterExpression(
            "\"status\" ILIKE '%manager%' OR \"status\" ILIKE '%mgr%'"
        )
        r_mgr.setLabel("Valid by Manager")

        # Rule 2 — Invalid (red)
        r_invalid = QgsRuleBasedRenderer.Rule(_line_sym("#F44336", 1.2, 0.6))
        r_invalid.setFilterExpression(
            "\"status\" ILIKE '%invalid%' OR \"status\" ILIKE '%fail%' "
            "OR \"status\" ILIKE '%error%' OR \"status\" ILIKE '%exceed%' "
            "OR \"status\" ILIKE '%incomplete%' OR \"status\" ILIKE '%naming%'"
        )
        r_invalid.setLabel("Invalid")

        # Rule 3 — Valid (green) — catch-all ELSE
        r_valid = QgsRuleBasedRenderer.Rule(_line_sym("#4CAF50", 1.2, 0.6))
        r_valid.setFilterExpression("ELSE")
        r_valid.setLabel("Valid")

        root.appendChild(r_excl)
        root.appendChild(r_mgr)
        root.appendChild(r_invalid)
        root.appendChild(r_valid)

        renderer = QgsRuleBasedRenderer(root)
        layer.setRenderer(renderer)
        layer.triggerRepaint()
    except Exception as _e:
        QgsMessageLog.logMessage(
            f"_apply_line_renderer: {_e}", "GeoLevelPlugin", level=Qgis.Warning
        )


def _apply_point_labels(layer) -> None:
    """Enable QgsPalLayerSettings text labeling on a point layer.

    Reads the label text from 'point_id' (the field written by GeoJSONExporter).
    9 pt bold black with a 1 px white halo so names are legible on any basemap.
    Safe no-op when PyQGIS labeling API is unavailable.
    """
    try:
        from qgis.core import (
            QgsPalLayerSettings, QgsTextFormat,
            QgsVectorLayerSimpleLabeling, QgsUnitTypes,
        )
        from qgis.PyQt.QtGui import QColor, QFont

        field_names = [f.name() for f in layer.fields()]
        label_field = next(
            (fn for fn in ("point_id", "name", "id") if fn in field_names), None
        )
        if label_field is None:
            return

        fmt = QgsTextFormat()
        font = QFont("Arial", 9)
        font.setBold(True)
        fmt.setFont(font)
        fmt.setSize(9)
        fmt.setColor(QColor("black"))

        # QgsTextFormat.buffer() returns the owned QgsTextBufferSettings object.
        # Mutating it in-place is the correct QGIS 3 pattern — no setBuffer() needed.
        buf = fmt.buffer()
        buf.setEnabled(True)
        buf.setSize(1.0)
        buf.setSizeUnit(QgsUnitTypes.RenderMillimeters)
        buf.setColor(QColor("white"))

        pal = QgsPalLayerSettings()
        pal.fieldName = label_field
        pal.setFormat(fmt)
        pal.placement = QgsPalLayerSettings.OrderedPositionsAroundPoint
        pal.displayAll = True   # disable collision avoidance — always draw all labels
        try:
            # AllowOverlapIfRequired added in QGIS 3.26; guard for older versions.
            pal.setOverlapHandling(QgsPalLayerSettings.AllowOverlapIfRequired)
        except AttributeError:
            pass

        layer.setLabeling(QgsVectorLayerSimpleLabeling(pal))
        layer.setLabelsEnabled(True)
        layer.triggerRepaint()
    except Exception as _e:
        QgsMessageLog.logMessage(
            f"_apply_point_labels: {_e}", "GeoLevelPlugin", level=Qgis.Warning
        )


def _apply_point_renderer(layer):
    """Apply a live QgsRuleBasedRenderer to a point layer.

    Rules (first match wins):
      status='PKT' / is_benchmark=0 / name contains PKT/ASH
                        → yellow diamond, 4.5 mm
      ELSE (official benchmarks)
                        → blue triangle,  3.0 mm
    """
    try:
        from qgis.core import (
            QgsRuleBasedRenderer, QgsMarkerSymbol,
        )
        from qgis.PyQt.QtGui import QColor

        def _marker(hex_color, size_mm, shape):
            sym = QgsMarkerSymbol.createSimple({
                "color": hex_color,
                "size":  str(size_mm),
                "name":  shape,
                "outline_color": "#444444",
                "outline_width": "0.3",
            })
            return sym

        root = QgsRuleBasedRenderer.Rule(None)

        r_pkt = QgsRuleBasedRenderer.Rule(_marker("#FFEB3B", 4.5, "diamond"))
        r_pkt.setFilterExpression(
            "\"status\" = 'PKT' OR "
            "\"is_benchmark\" = 0 OR \"is_benchmark\" = '0' OR "
            "\"point_id\" LIKE '%PKT%' OR \"point_id\" LIKE '%ASH%'"
        )
        r_pkt.setLabel("Estimated field point")

        r_bm = QgsRuleBasedRenderer.Rule(_marker("#2196F3", 3.0, "triangle"))
        r_bm.setFilterExpression("ELSE")
        r_bm.setLabel("Benchmark / Turning Point")

        root.appendChild(r_pkt)
        root.appendChild(r_bm)

        renderer = QgsRuleBasedRenderer(root)
        layer.setRenderer(renderer)
        layer.triggerRepaint()
    except Exception as _e:
        QgsMessageLog.logMessage(
            f"_apply_point_renderer: {_e}", "GeoLevelPlugin", level=Qgis.Warning
        )


class GeoLevelLSAInputDialog(QDialog):
    """
    Fixed-points input dialog for the standard LSA Network Adjustment.

    Mirrors the Enhanced LSA dialog's fixed-points table so that:
    - Users can add/remove rows with a table instead of a single text box.
    - Auto-Select populates leaf-node endpoints (degree == 1 in network).
    - Rows with a blank height are SKIPPED (treated as unknowns), preventing
      the architecture-inversion bug where 0.000 or accidental entries lock
      extra nodes as fixed constraints.
    """

    def __init__(self, lines, parent=None):
        super().__init__(parent)
        self._lines = lines
        self.fixed_points = {}
        self.setWindowTitle("LSA — Fixed Points / נקודות קבועות")
        self.setMinimumSize(480, 340)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        grp = QGroupBox(
            "Enter known benchmark heights.\n"
            "Rows with a blank height are treated as unknown (adjusted) points."
        )
        vbox = QVBoxLayout(grp)

        self.fp_table = QTableWidget(0, 2)
        self.fp_table.setHorizontalHeaderLabels(["Point ID", "Known Height (m)"])
        self.fp_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.fp_table.setMinimumHeight(180)
        self.fp_table.setToolTip(
            "Enter Point ID and its KNOWN height.\n"
            "Leave height blank to treat the point as unknown."
        )
        vbox.addWidget(self.fp_table)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("+ Add Row")
        btn_add.clicked.connect(self._add_row)
        btn_del = QPushButton("− Remove")
        btn_del.clicked.connect(self._del_row)
        btn_auto = QPushButton("Auto-Select")
        btn_auto.setToolTip("Populate with network leaf-node endpoints")
        btn_auto.clicked.connect(self._auto_select)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_del)
        btn_row.addWidget(btn_auto)
        vbox.addLayout(btn_row)
        layout.addWidget(grp)

        ok_row = QHBoxLayout()
        btn_ok = QPushButton("Run Adjustment")
        btn_ok.setStyleSheet(
            "font-weight: bold; background: #1565c0; color: white; padding: 5px 14px;"
        )
        btn_ok.clicked.connect(self._on_ok)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        ok_row.addStretch()
        ok_row.addWidget(btn_ok)
        ok_row.addWidget(btn_cancel)
        layout.addLayout(ok_row)

    def _add_row(self):
        row = self.fp_table.rowCount()
        self.fp_table.insertRow(row)
        self.fp_table.setItem(row, 0, QTableWidgetItem(""))
        self.fp_table.setItem(row, 1, QTableWidgetItem(""))

    def _del_row(self):
        row = self.fp_table.currentRow()
        if row >= 0:
            self.fp_table.removeRow(row)

    def _auto_select(self):
        """Populate table with leaf-node endpoints (degree == 1 in network)."""
        from collections import Counter
        degree = Counter()
        for ln in self._lines:
            if ln.start_point:
                degree[ln.start_point] += 1
            if ln.end_point:
                degree[ln.end_point] += 1
        candidates = sorted(pid for pid, cnt in degree.items() if cnt == 1)
        if not candidates:
            candidates = sorted(degree.keys())
        # Pre-load DB manager once so each row doesn't re-instantiate
        try:
            from geolevel_db_manager import get_db_manager
            _db_mgr = get_db_manager()
        except Exception:
            _db_mgr = None

        self.fp_table.setRowCount(0)
        for pid in candidates:
            r = self.fp_table.rowCount()
            self.fp_table.insertRow(r)
            self.fp_table.setItem(r, 0, QTableWidgetItem(pid))

            height_text = ""
            if _db_mgr is not None and _db_mgr.is_configured():
                try:
                    rec = _db_mgr.resolve_benchmark(pid)
                    if rec is not None and rec.gova_ort is not None:
                        height_text = f"{rec.gova_ort:.4f}"
                except Exception:
                    pass
            self.fp_table.setItem(r, 1, QTableWidgetItem(height_text))

    def _on_ok(self):
        fixed = {}
        skipped = []
        for row in range(self.fp_table.rowCount()):
            pid_item = self.fp_table.item(row, 0)
            h_item   = self.fp_table.item(row, 1)
            if not pid_item:
                continue
            pid = pid_item.text().strip()
            if not pid:
                continue
            h_text = h_item.text().strip() if h_item else ""
            if not h_text:
                skipped.append(pid)
                continue
            try:
                fixed[pid] = float(h_text)
            except ValueError:
                skipped.append(pid)

        if skipped:
            QMessageBox.information(
                self, "Fixed Points — Incomplete Rows",
                "The following points have no height and will be treated as unknowns "
                "(adjusted):\n\n" + "\n".join(skipped) + "\n\n"
                "Enter a known height or remove these rows before running."
            )

        if not fixed:
            QMessageBox.warning(
                self, "No Fixed Points",
                "Please enter at least one point with a known height."
            )
            return

        self.fixed_points = fixed
        self.accept()


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
        self._pending_pid_remap = {}         # Point ID remaps from resolution dialog
        self._pending_marker_pids = {}       # Marker PIDs (estimate/pkt) from resolution
        self._pending_excluded = set()       # Field PIDs excluded in the resolution dialog
        self._pending_unuse = set()          # PIDs excluded via Point Settings (.glp)
        # Point Settings: engineer's DB choices keyed by ORIGINAL field point ID
        # (persisted in .glp so a reload re-applies them without asking again)
        self._point_overrides = {}
        # current (possibly renamed) point name -> original field point ID
        self._point_origin = {}
        # GeoPackage holding the current project's layers + line/setup tables
        self._gpkg_path = ""

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
        proj_menu.addAction("New Project / פרויקט חדש").triggered.connect(self._new_project)
        proj_menu.addSeparator()
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
        sett_menu.addAction("Database Connection…").triggered.connect(self._show_db_settings)

        # ── Help sub-menu ─────────────────────────────────────────────
        help_menu = self._menu.addMenu("Help / עזרה")
        help_menu.addAction("About Geo Level Gravi").triggered.connect(self._show_about)

        menubar.insertMenu(menubar.actions()[-1], self._menu)

        # Map-to-dock sync: watch for new layers being added
        QgsProject.instance().layerWasAdded.connect(self._prepare_layer_signals)

        # Create dock (hidden until first use)
        self._create_dock()

        # Pre-load DB manager — auto-loads saved connection params from settings.json
        try:
            from geolevel_db_manager import get_db_manager
            get_db_manager()
        except Exception:
            pass

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
        self.dock.override_changed.connect(self._on_override_changed)
        self.dock.edit_points_requested.connect(self._edit_points)

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
        """When a feature is selected on the map, highlight it in the dock and populate details."""
        selected_ids = layer.selectedFeatureIds()
        if not selected_ids or not self.dock:
            return
        feat = layer.getFeature(selected_ids[0])
        filename = feat["filename"] if feat.fields().indexOf("filename") >= 0 else None
        if filename:
            self.dock.select_line_by_filename(filename)
            # Trigger the same logic as if the user clicked the list manually
            idx = self.dock.get_current_index()
            if idx >= 0 and idx < len(self._lines):
                self._on_line_selected(idx)

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

        # Deduplicate: skip files already loaded.  LevelingLine.filename is the
        # file stem (it is the map-sync key), source_path the absolute path.
        existing = {ln.filename for ln in self._lines}
        existing.update(os.path.normcase(os.path.abspath(ln.source_path))
                        for ln in self._lines if getattr(ln, "source_path", None))
        new_paths = [fp for fp in file_paths
                     if os.path.normcase(os.path.abspath(fp)) not in existing
                     and Path(fp).stem not in existing]
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
        n_prev = len(self._lines)
        self._lines.extend(new_lines)

        # Pre-calculation point verification: catch ASCII/Hebrew mismatches,
        # spatial twins and out-of-radius picks before any layer or LSA runs.
        # (The dialog may shrink new_lines in place, so trim by n_prev.)
        self._pending_excluded = set()
        if not self._check_point_resolution(new_lines):
            self._lines = self._lines[:n_prev]
            return

        # Re-run the full geodetic pipeline now that db_mgr._cache contains the
        # verified/resolved coordinates for every point.  The first run above
        # had an empty cache, so its GeoJSON used estimated/missing positions.
        # This second run picks up the confirmed DB coordinates and regenerates
        # both lines_geojson and points_geojson with the correct geometry.
        self._lines = self._lines[:n_prev]   # remove the pre-resolution lines
        # Pass verified name overrides so process_geodetic_data applies them
        # before validation and GeoJSON export — fixes INVALID_ENDPOINT errors
        # and missing symbols caused by ASCII names reaching the exporter.
        pid_remap = getattr(self, "_pending_pid_remap", {})
        try:
            result = process_geodetic_data(
                file_paths=new_paths,
                leveling_class=leveling_class,
                output_dir=output_dir,
                name_overrides=pid_remap or None,
            )
        except Exception as exc:
            QgsMessageLog.logMessage(
                "Re-run after point resolution failed: " + traceback.format_exc(),
                "GeoLevelPlugin", level=Qgis.Warning,
            )
            # Fall back to the pre-resolution result rather than leaving the
            # user with nothing.
        finally:
            self._pending_pid_remap = {}   # consume — one-shot per load

        new_lines = result["lines"]
        # Keep setup endpoints consistent with the renamed line endpoints
        self._rename_points_in_lines(new_lines, pid_remap, setups_only=True)
        # Re-apply exclusions chosen in the dialog (the re-run re-parsed them)
        excluded = getattr(self, "_pending_excluded", set())
        if excluded:
            new_lines = [ln for ln in new_lines
                         if ln.start_point not in excluded and ln.end_point not in excluded]
            self._pending_excluded = set()
        # Points excluded later in Point Settings (saved in .glp): keep the
        # lines but mark them unused, exactly as the edit screen did.
        unuse = getattr(self, "_pending_unuse", set())
        if unuse:
            for ln in new_lines:
                if ln.start_point in unuse or ln.end_point in unuse:
                    ln.is_used = False
            self._pending_unuse = set()
        self._lines.extend(new_lines)

        # Seed the DB spatial centroid from the 'נקודות בקרה' control-points layer.
        # This must run before any LSA/loops/double-runs dialog can fire, so the
        # K-Means centroid is anchored to on-screen verified geometry, not the DB.
        try:
            from geolevel_db_manager import get_db_manager
            all_pts = list(
                {ln.start_point for ln in self._lines if ln.start_point}
                | {ln.end_point   for ln in self._lines if ln.end_point}
            )
            get_db_manager().seed_from_qgis_layer(all_pts)
        except Exception:
            pass

        # Re-validate the full combined set
        try:
            from core_logic.validators import BatchValidator
            bv = BatchValidator(leveling_class=int(leveling_class[1]))
            self._val_results = bv.validate_batch(self._lines)
        except Exception:
            self._val_results = result.get("val_results", [])

        self._load_line_layer(result["lines_geojson"], result["line_style"], new_lines)
        self._load_point_layer(result.get("points_geojson", ""), result.get("point_style", ""))
        # Push validated statuses from memory into the layer attribute table so
        # the rule-based renderer has fresh, accurate values on first paint.
        self._sync_map_statuses()
        # Persist layers + line data to the project GeoPackage and swap the
        # volatile memory/GeoJSON layers for the GPKG-backed ones.
        self._persist_to_gpkg()
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
        _apply_line_renderer(layer)   # live renderer overrides any QML
        QgsProject.instance().addMapLayer(layer)
        self._layer = layer

    def _load_line_layer(self, geojson_path: str, qml_path: str,
                         lines) -> None:
        """
        Add a line layer to the QGIS project.

        Preferred path: QGISLineLayerBuilder — resolves each benchmark
        via BenchmarkDBManager (ST_X/ST_Y geometry, Spatial Median + k=1
        centroid) so coordinates are guaranteed EPSG:2039 ITM 2005 metres.

        Fallback path: GeoJSON OGR layer (schematic coordinates) used when:
          - DB is not configured
          - Builder raises an exception
          - Builder returns None or a layer with 0 features
        """
        layer = None

        try:
            from geolevel_db_manager import get_db_manager
            from qgis_line_layer_builder import QGISLineLayerBuilder

            db_mgr = get_db_manager()
            if db_mgr.is_configured():
                # Convert LevelingLine objects to the List[dict] the builder expects
                rows = [
                    {
                        "start_point":      ln.filename and ln.start_point or ln.start_point,
                        "end_point":        ln.end_point,
                        "filename":         ln.filename,
                        "total_distance":   getattr(ln, "total_distance", None),
                        "total_height_diff": getattr(ln, "total_height_diff", None),
                        "status":           ln.status.value
                                            if hasattr(ln.status, "value")
                                            else str(ln.status),
                    }
                    for ln in lines
                    if ln.start_point and ln.end_point
                ]

                builder = QGISLineLayerBuilder(layer_name="Geodetic Results")
                built = builder.process_line_features(rows, crs="EPSG:2039")

                if built is not None and built.isValid() and built.featureCount() > 0:
                    QgsMessageLog.logMessage(
                        f"QGISLineLayerBuilder: added {built.featureCount()} "
                        f"feature(s) (DB-resolved EPSG:2039 coordinates)",
                        "GeoLevelPlugin", level=Qgis.Info,
                    )
                    layer = built
                else:
                    QgsMessageLog.logMessage(
                        "QGISLineLayerBuilder returned empty layer — "
                        "falling back to GeoJSON renderer",
                        "GeoLevelPlugin", level=Qgis.Warning,
                    )
            else:
                QgsMessageLog.logMessage(
                    "DB not configured — using GeoJSON renderer for line layer",
                    "GeoLevelPlugin", level=Qgis.Info,
                )

        except Exception as exc:
            QgsMessageLog.logMessage(
                f"QGISLineLayerBuilder failed ({exc}) — falling back to GeoJSON",
                "GeoLevelPlugin", level=Qgis.Warning,
            )

        # Fallback: load the GeoJSON written by the exporter.
        # Skip gracefully if the export was blocked by a PermissionError
        # (geojson_path will be "" in that case).
        if layer is None:
            if geojson_path:
                self._load_layer(geojson_path, qml_path)
            else:
                QgsMessageLog.logMessage(
                    "GeoJSON path is empty (export was blocked) and DB layer "
                    "could not be built — no line layer added to project.",
                    "GeoLevelPlugin", level=Qgis.Warning,
                )
            return

        _apply_line_renderer(layer)
        QgsProject.instance().addMapLayer(layer)
        self._layer = layer
        self._prepare_layer_signals(layer)

    def _load_point_layer(self, points_geojson_path: str, qml_path: str) -> None:
        """Load the points GeoJSON as a separate map layer with live symbology."""
        if not points_geojson_path or not os.path.exists(points_geojson_path):
            return

        layer = QgsVectorLayer(points_geojson_path, "Geodetic Points", "ogr")
        if not layer.isValid():
            QgsMessageLog.logMessage(
                f"Failed to load points layer: {points_geojson_path}",
                "GeoLevelPlugin", level=Qgis.Warning,
            )
            return

        _apply_point_renderer(layer)
        QgsProject.instance().addMapLayer(layer)
        # Labels must be applied after the layer is registered in the project
        # so the QGIS label engine can resolve the layer reference correctly.
        _apply_point_labels(layer)
        self.iface.mapCanvas().refresh()
        QgsMessageLog.logMessage(
            f"Points layer loaded: {layer.featureCount()} feature(s)",
            "GeoLevelPlugin", level=Qgis.Info,
        )

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

    def _on_override_changed(self, filename: str, new_status: str):
        """Update the 'status' attribute on the matching line feature in the
        active QGIS layer and immediately repaint the canvas so colour changes
        (valid=green / invalid=red / VALID_BY_MANAGER=amber / excluded=invisible)
        are reflected without requiring a full pipeline re-run."""
        # Prefer cached reference; fall back to project name lookup so this
        # works even when self._layer is stale or None.
        layer = self._layer if (self._layer and self._layer.isValid()) else None
        if layer is None:
            try:
                from qgis.core import QgsProject
                hits = QgsProject.instance().mapLayersByName("Geodetic Results")
                if hits:
                    layer = hits[-1]
            except Exception:
                pass
        if layer is None or not layer.isValid():
            return

        status_idx   = layer.fields().indexOf("status")
        filename_idx = layer.fields().indexOf("filename")
        if status_idx < 0 or filename_idx < 0:
            return

        # Always write uppercase so the ILIKE rules in _apply_line_renderer match.
        # The dock emits "excluded" (lowercase) for excluded lines — normalise here.
        status_value = new_status.upper()

        layer.startEditing()
        changed = 0
        for feat in layer.getFeatures():
            if str(feat[filename_idx]) == filename:
                layer.changeAttributeValue(feat.id(), status_idx, status_value)
                changed += 1
        layer.commitChanges()

        if changed:
            _apply_line_renderer(layer)
            self.iface.mapCanvas().refresh()
            QgsMessageLog.logMessage(
                f"Override repaint: '{filename}' → status='{status_value}' "
                f"({changed} feature(s) updated)",
                "GeoLevelGravi", Qgis.Info,
            )

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
        # Resolve a valid output_dir to store in the project file so the
        # load path never has to prompt the user for a directory.
        output_dir = self._last_output_dir
        if not output_dir or not os.path.isdir(output_dir):
            output_dir = self._default_output_dir()

        # Make sure the GeoPackage holds the current state (statuses,
        # overrides, Point Settings choices) before the .glp points to it.
        gpkg = self._refresh_gpkg_tables()
        gpkg_ref = ""
        if gpkg:
            try:
                gpkg_ref = os.path.relpath(gpkg, os.path.dirname(os.path.abspath(path)))
            except ValueError:            # different drive on Windows
                gpkg_ref = os.path.abspath(gpkg)

        data = {
            "version": 2,
            "class": self._last_class,
            "output_dir": output_dir,
            # GeoPackage with layers + line/setup tables: loading uses it
            # directly, without re-parsing files or querying the DB.
            "gpkg": gpkg_ref,
            # Real measurement-file paths (fallback when the GPKG is missing)
            "files": [self._line_src_path(ln) for ln in self._lines],
            # Point Settings choices keyed by original field point ID
            "point_overrides": self._point_overrides,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        msg = f"Project saved: {path}"
        if not gpkg:
            msg += "  (GeoPackage could not be written - reload will re-parse files)"
        self.iface.messageBar().pushMessage("Geo Level Gravi", msg,
                                            level=Qgis.Success if gpkg else Qgis.Warning,
                                            duration=5)

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
        glp_dir    = os.path.dirname(os.path.abspath(path))
        # Paths inside the .glp are untrusted: never write to / read from a
        # network (UNC) location named by the project file.
        from geo_level_gpkg import is_network_path
        if not isinstance(output_dir, str) or is_network_path(output_dir):
            output_dir = ""
        if not isinstance(files, list):
            files = []

        overrides = data.get("point_overrides", {})
        overrides = dict(overrides) if isinstance(overrides, dict) else {}

        # -- Fast path: everything is in the project GeoPackage ----------
        gpkg = self._resolve_gpkg_ref(data.get("gpkg"), glp_dir, output_dir)
        if gpkg and self._load_project_from_gpkg(gpkg, cls, output_dir, overrides):
            return

        # -- Legacy path: re-parse the measurement files ------------------
        # Resolve each file: absolute path, then the .glp folder and the
        # output folder (by basename, or by stem for old .glp files that
        # stored the extension-less line name).
        resolved_files, missing = [], []
        for fp in files:
            hit = self._resolve_measurement_file(fp, [glp_dir, output_dir])
            if hit:
                resolved_files.append(hit)
            else:
                missing.append(fp)

        if missing:
            QMessageBox.warning(self.iface.mainWindow(), "Load Project",
                                "The following DAT files could not be found and will be skipped:\n"
                                + "\n".join(missing))

        if not resolved_files:
            QMessageBox.warning(self.iface.mainWindow(), "Load Project",
                                "No loadable files found in this project.")
            return

        # If the saved output directory is gone, derive one from the first DAT
        # file so _process_files never stalls on a directory picker dialog.
        if not output_dir or not os.path.isdir(output_dir):
            output_dir = os.path.dirname(resolved_files[0])

        self._last_class = cls
        self._last_output_dir = output_dir
        # Clear existing lines so deduplication does not silently block re-load
        self._lines = []
        self._val_results = []
        self._point_origin = {}
        self._gpkg_path = ""
        self._point_overrides = overrides
        try:
            from geolevel_db_manager import get_db_manager
            get_db_manager().clear_session_cache()
        except Exception:
            pass
        self._remove_result_layers()
        self._process_files(resolved_files, cls, output_dir)

    # ------------------------------------------------------------------
    # GeoPackage persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _line_src_path(ln) -> str:
        src = getattr(ln, "source_path", None)
        return os.path.abspath(src) if src else ln.filename

    def _default_output_dir(self) -> str:
        for ln in self._lines:
            src = getattr(ln, "source_path", None)
            if src:
                return os.path.dirname(src)
        return ""

    @staticmethod
    def _resolve_measurement_file(fp: str, search_dirs) -> str:
        """Return an existing path for *fp* or "" (absolute -> basename -> stem.*).

        Network (UNC) paths from the project file are never touched.
        """
        from geo_level_gpkg import is_network_path
        if not isinstance(fp, str):
            return ""
        search_dirs = [d for d in search_dirs
                       if isinstance(d, str) and d and not is_network_path(d)]
        if fp and not is_network_path(fp) and os.path.isfile(fp):
            return fp
        base = os.path.basename(fp or "")
        stem = os.path.splitext(base)[0]
        exts = (".dat", ".raw", ".gsi")
        for d in search_dirs:
            if not d or not os.path.isdir(d):
                continue
            cand = os.path.join(d, base)
            if base and os.path.isfile(cand):
                return cand
            try:
                for name in os.listdir(d):
                    n_stem, n_ext = os.path.splitext(name)
                    if n_stem == stem and n_ext.lower() in exts:
                        return os.path.join(d, name)
            except OSError:
                pass
        return ""

    @staticmethod
    def _resolve_gpkg_ref(ref, glp_dir: str, output_dir: str) -> str:
        """Resolve the .glp 'gpkg' entry to a local .gpkg file, or "".

        Paths from the project file are untrusted: network (UNC) paths are
        refused before any filesystem access (NTLM credential leak), and only
        files with a .gpkg extension are accepted.
        """
        from geo_level_gpkg import is_network_path
        if not ref or not isinstance(ref, str) or is_network_path(ref):
            return ""
        cands = [ref if os.path.isabs(ref) else os.path.join(glp_dir, ref),
                 os.path.join(glp_dir, os.path.basename(ref))]
        if output_dir and isinstance(output_dir, str) and not is_network_path(output_dir):
            cands.append(os.path.join(output_dir, os.path.basename(ref)))
        for c in cands:
            c = os.path.normpath(c)
            if is_network_path(c) or os.path.splitext(c)[1].lower() != ".gpkg":
                continue
            if os.path.isfile(c):
                real = os.path.realpath(c)
                if not is_network_path(real):
                    return real
        return ""

    def _gpkg_meta(self) -> dict:
        cls = self.dock.get_selected_class() if self.dock else self._last_class
        return {
            "class": cls or self._last_class or "H3",
            "output_dir": self._last_output_dir,
            "point_overrides": self._point_overrides,
            "point_origin": self._point_origin,
            "saved_utc": datetime.utcnow().isoformat(timespec="seconds"),
        }

    @staticmethod
    def _result_layers(name: str) -> list:
        return list(QgsProject.instance().mapLayersByName(name))

    def _layers_use_gpkg(self, gpkg: str) -> bool:
        """True when every 'Geodetic Results' layer is read from *gpkg*."""
        if not gpkg:
            return False
        target = os.path.normcase(os.path.abspath(gpkg))
        lyrs = self._result_layers("Geodetic Results")
        if not lyrs:
            return False
        for lyr in lyrs:
            src = lyr.source().split("|")[0]
            if os.path.normcase(os.path.abspath(src)) != target:
                return False
        return True

    def _persist_to_gpkg(self) -> str:
        """Write the current map layers + lines into the project GeoPackage and
        replace the map layers with GPKG-backed ones.  Returns the GPKG path
        ("" on failure - the map then keeps equivalent in-memory layers)."""
        if not self._lines:
            return ""
        output_dir = self._last_output_dir or self._default_output_dir()
        if not output_dir:
            return ""
        try:
            import geo_level_gpkg as gp
        except Exception:
            QgsMessageLog.logMessage("geo_level_gpkg import failed: " + traceback.format_exc(),
                                     "GeoLevelPlugin", level=Qgis.Warning)
            return ""

        # Snapshot every result layer (appending files adds one layer per
        # batch) into single memory layers, lines in self._lines order so
        # _zoom_to_line_feature(idx) matches the dock list.
        order = {ln.filename: i for i, ln in enumerate(self._lines)}
        try:
            line_src = self._result_layers("Geodetic Results")
            if self._layer is not None and self._layer in line_src:
                line_src.remove(self._layer)
                line_src.insert(0, self._layer)
            mem_lines = gp.merge_layers_to_memory(
                line_src, "line",
                order_key=lambda a: order.get(str(a.get("filename")), len(order)),
                dedupe_field="filename")
            mem_pts = gp.merge_layers_to_memory(
                self._result_layers("Geodetic Points"), "point", dedupe_field="point_id")
        except Exception:
            QgsMessageLog.logMessage("GeoPackage snapshot failed: " + traceback.format_exc(),
                                     "GeoLevelPlugin", level=Qgis.Warning)
            return ""

        # Release every layer reading the old GPKG/GeoJSON before writing.
        self._remove_result_layers()
        default = os.path.join(output_dir, gp.DEFAULT_GPKG_NAME)
        stem, ext = os.path.splitext(default)
        candidates = [self._gpkg_path or default]
        candidates += [f"{stem}_{n}{ext}" for n in range(1, 50)]
        written = ""
        for cand in candidates:
            try:
                written = gp.write_project_gpkg(cand, mem_lines, mem_pts,
                                                self._lines, self._gpkg_meta())
                break
            except PermissionError:
                continue          # still locked by Windows - try the next name
            except Exception:
                QgsMessageLog.logMessage("GeoPackage write failed: " + traceback.format_exc(),
                                         "GeoLevelPlugin", level=Qgis.Warning)
                break

        if not written:
            # Put the snapshot back so the map never ends up empty.
            self._add_result_layers(mem_lines, mem_pts)
            return ""

        self._gpkg_path = written
        if not self._add_gpkg_layers(written):
            self._add_result_layers(mem_lines, mem_pts)
        QgsMessageLog.logMessage(f"Project GeoPackage written: {written}",
                                 "GeoLevelPlugin", level=Qgis.Info)
        return written

    def _refresh_gpkg_tables(self) -> str:
        """Bring the GeoPackage up to date before saving the .glp.

        When the map already reads from the project GPKG, only the line /
        setup / meta tables are rewritten in place (layer attribute edits are
        committed to the GPKG directly).  Otherwise the whole file is written.
        """
        gpkg = self._gpkg_path
        if gpkg and os.path.isfile(gpkg) and self._layers_use_gpkg(gpkg):
            try:
                import geo_level_gpkg as gp
                gp.write_attribute_tables(gpkg, self._lines, self._gpkg_meta())
                return gpkg
            except Exception:
                QgsMessageLog.logMessage("GeoPackage table update failed, rewriting file: "
                                         + traceback.format_exc(),
                                         "GeoLevelPlugin", level=Qgis.Warning)
        canvas = self.iface.mapCanvas()
        extent = canvas.extent()
        written = self._persist_to_gpkg()
        canvas.setExtent(extent)
        canvas.refresh()
        return written

    def _add_result_layers(self, line_layer, point_layer) -> None:
        """Add line/point layers to the project with the plugin symbology."""
        proj = QgsProject.instance()
        if line_layer is not None and line_layer.isValid():
            line_layer.setName("Geodetic Results")
            _apply_line_renderer(line_layer)
            proj.addMapLayer(line_layer)      # layerWasAdded -> selection sync
            self._layer = line_layer
        if point_layer is not None and point_layer.isValid():
            point_layer.setName("Geodetic Points")
            _apply_point_renderer(point_layer)
            proj.addMapLayer(point_layer)
            _apply_point_labels(point_layer)
        self.iface.mapCanvas().refresh()

    def _add_gpkg_layers(self, gpkg: str) -> bool:
        import geo_level_gpkg as gp
        lines = gp.gpkg_layer(gpkg, gp.LINES_LAYER, "Geodetic Results")
        pts = gp.gpkg_layer(gpkg, gp.POINTS_LAYER, "Geodetic Points")
        if lines is None and pts is None:
            return False
        self._add_result_layers(lines, pts)
        return True

    def _revalidate_all(self, cls: str) -> None:
        """Re-derive line statuses in memory (no file parsing)."""
        try:
            from core_logic.validators import BatchValidator
            from core_logic.config.models import LineStatus
            keep = (LineStatus.EXCLUDED_FROM_MERGE, LineStatus.MERGED)
            for ln in self._lines:
                if ln.status not in keep:
                    ln.status = LineStatus.VALID
            bv = BatchValidator(leveling_class=int((cls or "H3")[1]))
            self._val_results = bv.validate_batch(self._lines)
            for ln in self._lines:
                if getattr(ln, "manager_override", False):
                    ln.status = LineStatus.VALID_BY_MANAGER
        except Exception:
            QgsMessageLog.logMessage("Re-validation failed: " + traceback.format_exc(),
                                     "GeoLevelPlugin", level=Qgis.Warning)

    def _replay_point_overrides(self) -> None:
        """Put the saved Point Settings choices back into the DB session cache
        (in memory only - no DB query) so later edits rebuild consistently."""
        try:
            from geolevel_db_manager import get_db_manager
            db_mgr = get_db_manager()
            db_mgr.clear_session_cache()
            for pid, ov in (self._point_overrides or {}).items():
                ov = ov or {}
                if ov.get("mode") in ("estimate", "pkt_average"):
                    db_mgr.accept_record(pid, None)
                elif ov.get("record"):
                    rec = self._record_from_dict(ov.get("record"))
                    name = (rec.name or "").strip() or pid
                    db_mgr.accept_record(pid, rec)
                    db_mgr.accept_record(name, rec)
                    self._point_origin.setdefault(name, pid)
        except Exception:
            QgsMessageLog.logMessage("Replaying point overrides failed: " + traceback.format_exc(),
                                     "GeoLevelPlugin", level=Qgis.Warning)

    def _load_project_from_gpkg(self, gpkg: str, cls: str, output_dir: str,
                                overrides: dict) -> bool:
        """Restore a project from its GeoPackage: no file parsing, no DB query,
        no point-resolution dialog.  Returns False to fall back to the files."""
        try:
            import geo_level_gpkg as gp
            if not gp.has_project_tables(gpkg):
                return False
            lines, meta = gp.read_attribute_tables(gpkg)
        except Exception:
            QgsMessageLog.logMessage("Reading project GeoPackage failed: " + traceback.format_exc(),
                                     "GeoLevelPlugin", level=Qgis.Warning)
            return False
        if not lines:
            return False

        if not output_dir or not os.path.isdir(output_dir):
            output_dir = os.path.dirname(gpkg)
        self._last_class = cls or meta.get("class") or "H3"
        self._last_output_dir = output_dir
        self._lines = lines
        self._val_results = []
        saved = meta.get("point_overrides") if isinstance(meta.get("point_overrides"), dict) else {}
        self._point_overrides = overrides or dict(saved)
        origin = meta.get("point_origin")
        self._point_origin = dict(origin) if isinstance(origin, dict) else {}
        self._replay_point_overrides()

        self._remove_result_layers()
        self._gpkg_path = gpkg
        if not self._add_gpkg_layers(gpkg):
            QgsMessageLog.logMessage(f"No map layers found in {gpkg}", "GeoLevelPlugin",
                                     level=Qgis.Warning)

        # Anchor the DB centroid on the on-screen control points layer
        # (reads a QGIS layer only) for later LSA / loops dialogs.
        try:
            from geolevel_db_manager import get_db_manager
            get_db_manager().seed_from_qgis_layer(self._unique_points(self._lines))
        except Exception:
            pass

        if self.dock and hasattr(self.dock, "class_combo"):
            self.dock.class_combo.setCurrentText(self._last_class)
        self._revalidate_all(self._last_class)
        self._sync_map_statuses()
        self._show_dock()
        if self.dock:
            self.dock.load_lines(self._lines, self._val_results)
        if self._layer is not None and self._layer.isValid():
            ext = self._layer.extent()
            if not ext.isEmpty():
                ext.scale(1.1)
                self.iface.mapCanvas().setExtent(ext)
        self.iface.mapCanvas().refresh()
        self.iface.messageBar().pushMessage(
            "Geo Level Gravi",
            f"Project loaded from GeoPackage - {len(self._lines)} line(s) "
            f"({os.path.basename(gpkg)})",
            level=Qgis.Success, duration=5)
        return True

    def _new_project(self):
        """Clear all loaded data and start a fresh session."""
        reply = QMessageBox.question(
            self.iface.mainWindow(),
            "New Project / פרויקט חדש",
            "Are you sure you want to start a new project?\n"
            "All unsaved data and loaded files will be cleared.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        # Reset backend state
        self._lines = []
        self._val_results = []
        self._last_output_dir = ""
        self._last_class = "H3"

        # Clear DB session cache so proximity centroid resets for the new project
        try:
            from geolevel_db_manager import get_db_manager
            get_db_manager().clear_session_cache()
        except Exception:
            pass

        self._point_overrides = {}
        self._point_origin = {}
        self._gpkg_path = ""

        # Remove the QGIS map layers (lines + points)
        self._remove_result_layers()

        # Wipe the dock UI
        if self.dock:
            self.dock.clear_all()

        self.iface.messageBar().pushMessage(
            "Geo Level Gravi", "New project started.",
            level=Qgis.Success, duration=4
        )

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
            # Refresh dock list colours and sync map layer statuses
            self.dock.load_lines(self._lines, self._val_results)
            self._sync_map_statuses()
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
                try:
                    from geolevel_db_manager import get_db_manager
                    _db_mgr = get_db_manager()
                except Exception:
                    _db_mgr = None

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

                    # Column 6: DB Check — corrected misclosure against known heights
                    db_check_text = "—"
                    if _db_mgr is not None and _db_mgr.is_configured() and loop.points:
                        start_pt = loop.points[0]
                        end_pt   = loop.points[-1]
                        if start_pt == end_pt:
                            db_check_text = "—"  # closed loop, no endpoint correction needed
                        else:
                            try:
                                rec_s = _db_mgr.resolve_benchmark(start_pt)
                                rec_e = _db_mgr.resolve_benchmark(end_pt)
                                if (rec_s and rec_e
                                        and rec_s.gova_ort is not None
                                        and rec_e.gova_ort is not None):
                                    theoretical_dh = rec_e.gova_ort - rec_s.gova_ort
                                    corrected_mm = (loop.misclosure - theoretical_dh) * 1000
                                    db_check_text = "{:+.3f}".format(corrected_mm)
                                else:
                                    db_check_text = "No DB height"
                            except Exception:
                                db_check_text = "DB error"
                    db_item = QTableWidgetItem(db_check_text)
                    db_item.setTextAlignment(Qt.AlignCenter)
                    tbl.setItem(row, 6, db_item)

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
                try:
                    from geolevel_db_manager import get_db_manager
                    _db_mgr = get_db_manager()
                except Exception:
                    _db_mgr = None

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

                    # Reason column (col 7) — descriptive failure text
                    if passed:
                        reason_text = "Within H{} tolerance".format(int(cls[1]))
                    else:
                        achieved = res.get("achieved_class", 6)
                        reason_text = (
                            "Misclosure {:.3f} mm exceeds H{} tolerance {:.2f} mm "
                            "(achieved class: H{})".format(
                                abs(mis_mm), int(cls[1]), tol_mm, achieved
                            )
                        )
                    reason_item = QTableWidgetItem(reason_text)
                    reason_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                    if not passed:
                        reason_item.setForeground(QColor("#c62828"))
                    tbl.setItem(row, 7, reason_item)

                    # Column 8: DB Check — deviation of mean_dh from known height difference
                    db_check_text = "—"
                    if _db_mgr is not None and _db_mgr.is_configured():
                        try:
                            rec_s = _db_mgr.resolve_benchmark(fwd.start_point)
                            rec_e = _db_mgr.resolve_benchmark(fwd.end_point)
                            if (rec_s and rec_e
                                    and rec_s.gova_ort is not None
                                    and rec_e.gova_ort is not None):
                                expected_dh = rec_e.gova_ort - rec_s.gova_ort
                                error_mm = (res["mean_dh"] - expected_dh) * 1000
                                db_check_text = "{:+.3f}".format(error_mm)
                            else:
                                db_check_text = "No DB height"
                        except Exception:
                            db_check_text = "DB error"
                    db_item = QTableWidgetItem(db_check_text)
                    db_item.setTextAlignment(Qt.AlignCenter)
                    tbl.setItem(row, 8, db_item)

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

    def _sync_map_statuses(self) -> None:
        """Push current in-memory line statuses into the active QGIS line layer.

        Matches strictly by 'filename' attribute for guaranteed 1-to-1 accuracy.
        The filename is the natural unique key written by both the GeoJSON exporter
        and QGISLineLayerBuilder, so it is present on every layer feature.
        Runs after every load and re-validation so the renderer always reflects
        the dock's validation table.
        """
        # Prefer the cached reference; fall back to QgsProject name lookup so
        # the method works even if self._layer was not set yet (e.g. when the
        # builder path sets the layer but a code path doesn't update the ref).
        layer = self._layer if (self._layer and self._layer.isValid()) else None
        if layer is None:
            try:
                from qgis.core import QgsProject
                hits = QgsProject.instance().mapLayersByName("Geodetic Results")
                if hits:
                    layer = hits[-1]   # last added = most recent load
            except Exception:
                pass
        if layer is None or not layer.isValid():
            return

        fields       = layer.fields()
        status_idx   = fields.indexOf("status")
        filename_idx = fields.indexOf("filename")

        if status_idx < 0 or filename_idx < 0:
            return

        # filename → uppercase status string for the ILIKE renderer rules.
        # Excluded lines (is_used=False) write "EXCLUDED" regardless of the
        # LineStatus enum value, which still holds the original validation result.
        def _status_str(ln) -> str:
            if not getattr(ln, "is_used", True):
                return "EXCLUDED"
            return (
                ln.status.value.upper()
                if hasattr(ln.status, "value")
                else str(ln.status).upper()
            )

        filename_map: dict = {
            ln.filename: _status_str(ln)
            for ln in self._lines
            if ln.filename
        }
        if not filename_map:
            return

        layer.startEditing()
        for feat in layer.getFeatures():
            fname = str(feat[filename_idx])
            status_str = filename_map.get(fname)
            if status_str is not None:
                layer.changeAttributeValue(feat.id(), status_idx, status_str)
        layer.commitChanges()

        # Rebuild the renderer so ILIKE rules re-evaluate against fresh values
        _apply_line_renderer(layer)
        self.iface.mapCanvas().refresh()

    # ------------------------------------------------------------------
    # Point resolution (load-time) and Point Settings (edit-time)
    # ------------------------------------------------------------------

    @staticmethod
    def _unique_points(lines) -> list:
        return sorted(
            {ln.start_point for ln in lines if ln.start_point}
            | {ln.end_point for ln in lines if ln.end_point}
        )

    @staticmethod
    def _record_to_dict(rec) -> dict:
        """Serialise the persistent (non-scoring) fields of a BenchmarkRecord."""
        keys = ("ot_nekuda", "mispar_nekuda", "name", "gova_ort", "shem_darga_gova",
                "taarih_gova_ort", "ot_nekuda_kfula", "mispar_nekuda_kfula",
                "x", "y", "kfar_aher_name", "objectid", "hearot")
        return {k: getattr(rec, k, None) for k in keys}

    @staticmethod
    def _record_from_dict(d: dict):
        from geolevel_db_manager import BenchmarkRecord
        import dataclasses
        allowed = {f.name for f in dataclasses.fields(BenchmarkRecord)}
        kw = {k: v for k, v in (d or {}).items() if k in allowed}
        for req in ("ot_nekuda", "mispar_nekuda", "name", "gova_ort", "shem_darga_gova",
                    "taarih_gova_ort", "ot_nekuda_kfula", "mispar_nekuda_kfula", "x", "y"):
            kw.setdefault(req, None)
        kw["ot_nekuda"] = kw["ot_nekuda"] or ""
        kw["mispar_nekuda"] = kw["mispar_nekuda"] or 0
        kw["name"] = kw["name"] or ""
        return BenchmarkRecord(**kw)

    @staticmethod
    def _rename_points_in_lines(lines, remap: dict, setups_only: bool = False) -> int:
        """Rename points per *remap* {old: new} in ONE pass (chained remaps
        such as A→B, B→C are not applied twice).  Renames line endpoints
        (unless *setups_only*) and setup from/to points.
        Returns the number of lines whose endpoints changed."""
        remap = {o: n for o, n in (remap or {}).items() if o and n and o != n}
        if not remap:
            return 0
        touched = 0
        for ln in lines:
            if not setups_only:
                s0, e0 = ln.start_point, ln.end_point
                ln.start_point = remap.get(s0, s0)
                ln.end_point = remap.get(e0, e0)
                touched += int((ln.start_point, ln.end_point) != (s0, e0))
            for st in getattr(ln, "setups", []) or []:
                fp = getattr(st, "from_point", None)
                tp = getattr(st, "to_point", None)
                if fp in remap:
                    st.from_point = remap[fp]
                if tp in remap:
                    st.to_point = remap[tp]
        return touched

    def _resolve_manual_name(self, db_mgr, pid: str, manual: str, lines):
        """Look a manually typed DB name up; return a full record or None."""
        try:
            recs = db_mgr.get_candidates(manual, include_twins=False)
        except Exception:
            recs = []
        if not recs:
            return None
        recs = db_mgr.score_candidates(pid, recs, lines)
        if len(recs) == 1:
            return recs[0]
        return db_mgr.pick_unambiguous(recs) or recs[0]

    def _apply_resolution(self, db_mgr, result: dict, lines, edit_mode: bool) -> dict:
        """Apply the output of GeoLevelPointResolutionDialog.get_resolved().

        Pins every chosen record in the DB manager cache, renames point IDs
        to the verified DB names, persists the choice in _point_overrides and
        handles exclude / estimate modes.

        Load mode: *lines* are the freshly parsed lines; renames are returned
        as ``remap`` so the post-resolution re-run applies them too.
        Edit mode: *lines* is self._lines and is renamed in place; excluded
        points switch their lines to is_used=False (reversible).

        Returns {"remap": {old: new}, "excluded": set, "markers": {pid: (rec, mode)}}.
        """
        records = result["records"]
        modes = result["modes"]
        manual = result.get("manual", {})

        remap: dict = {}
        excluded: set = set()
        markers: dict = {}

        for pid, rec in records.items():
            mode = modes.get(pid, "use_selected")
            origin = self._point_origin.get(pid, pid)

            if mode == "exclude":
                excluded.add(pid)
                if edit_mode:
                    self._point_overrides[origin] = {"mode": "exclude"}
                continue

            if mode in ("estimate", "pkt_average"):
                markers[pid] = (rec, mode)
                # Pin as "not in DB" so resolve_benchmark() never auto-picks;
                # coordinates are then estimated topologically from neighbours.
                db_mgr.accept_record(pid, None)
                self._point_overrides[origin] = {"mode": mode}
                continue

            if pid in manual:
                full = self._resolve_manual_name(db_mgr, pid, manual[pid], lines)
                if full is not None:
                    rec = full
                else:
                    # Unknown name: rename only, let the normal lookup run.
                    new_name = manual[pid].strip()
                    db_mgr.forget(new_name)
                    if new_name != pid:
                        remap[pid] = new_name
                        db_mgr.forget(pid)
                    self._point_overrides[origin] = {"mode": "rename", "name": new_name}
                    self._point_origin[new_name] = origin
                    continue

            if rec is None:
                continue

            verified_name = (rec.name or "").strip() or pid
            db_mgr.accept_record(verified_name, rec)
            if not edit_mode or verified_name == pid:
                db_mgr.accept_record(pid, rec)
            if verified_name != pid:
                remap[pid] = verified_name
            self._point_overrides[origin] = {"mode": "use_selected",
                                             "record": self._record_to_dict(rec)}
            self._point_origin[verified_name] = origin

        if edit_mode:
            n = self._rename_points_in_lines(lines, remap)
            if self.dock:
                for old, new in remap.items():
                    self.dock.log(f"Point Settings: '{old}' → '{new}'")
                if remap:
                    self.dock.log(f"Point Settings: {n} line(s) renamed")
            for pid in excluded:
                for ln in lines:
                    if pid in (ln.start_point, ln.end_point):
                        ln.is_used = False
        else:
            if excluded:
                lines[:] = [ln for ln in lines
                            if ln.start_point not in excluded and ln.end_point not in excluded]
            self._rename_points_in_lines(lines, remap)

        return {"remap": remap, "excluded": excluded, "markers": markers}

    def _score_batch(self, db_mgr, batch: dict, lines) -> dict:
        return {pid: db_mgr.score_candidates(pid, recs, lines, batch)
                for pid, recs in batch.items()}

    def _check_point_resolution(self, new_lines: list) -> bool:
        """Verify ambiguous or unresolved field point IDs before loading.

        Pipeline:
          1. Saved choices (from Point Settings / .glp) are applied first.
          2. Batch-fetch candidates for the rest, including < 1 m spatial twins.
          3. Network matching (BenchmarkDBManager.resolve_network): anchors are
             points with ONE DB location; from them, duplicated names are
             matched one by one by comparing DB distances with the distances
             measured in the files, until nothing more can be matched.
          4. Points still open (no anchor at all, ambiguous, not in DB,
             conflicting, twins) → dialog.  The engineer may confirm only some
             points and "Decide later" for the rest; matching then re-runs from
             those choices (repeated rounds) until every point is decided.

        Returns True when loading should proceed, False when the user cancelled.
        DB not configured → returns True immediately.
        """
        try:
            from geolevel_db_manager import get_db_manager
            db_mgr = get_db_manager()
            if not db_mgr.is_configured():
                return True

            unique_pids = self._unique_points(new_lines)
            pid_remap: dict = {}
            self._pending_unuse = set()

            # 1. Re-apply saved overrides
            pending = []
            for pid in unique_pids:
                ov = self._point_overrides.get(pid)
                if not ov:
                    pending.append(pid)
                    continue
                if ov.get("mode") in ("estimate", "pkt_average"):
                    db_mgr.accept_record(pid, None)
                    continue
                if ov.get("mode") == "exclude":
                    self._pending_unuse.add(pid)
                    continue
                if ov.get("mode") == "rename":
                    new_name = (ov.get("name") or "").strip()
                    if new_name and new_name != pid:
                        pid_remap[pid] = new_name
                        self._point_origin[new_name] = pid
                    continue
                rec = self._record_from_dict(ov.get("record"))
                name = (rec.name or "").strip() or pid
                db_mgr.accept_record(pid, rec)
                db_mgr.accept_record(name, rec)
                self._point_origin[name] = pid
                if name != pid:
                    pid_remap[pid] = name
            if pid_remap:
                self._rename_points_in_lines(new_lines, pid_remap)

            if not pending:
                self._pending_pid_remap = pid_remap
                return True

            # The first parse pass already ran resolve_benchmark() and may have
            # auto-picked by centroid (e.g. the wrong 250A).  Those picks are
            # unconfirmed — drop them so they cannot bias anchor or topology.
            for pid in pending:
                db_mgr.forget(pid)

            # 2. Candidates (incl. < 1 m twins)
            batch = db_mgr.get_candidates_batch(pending)
            for pid in pending:
                self._point_origin.setdefault(pid, pid)

            # 3-4. Network matching + dialog rounds
            fixed: dict = {}          # pid -> BenchmarkRecord | None (estimate)
            excluded_now: set = set()
            merged = {"records": {}, "modes": {}, "manual": {}}
            allow_defer = True
            round_no = 0
            auto_total = 0
            from geo_level_point_resolution_dialog import GeoLevelPointResolutionDialog

            while True:
                round_no += 1
                net = db_mgr.resolve_network(pending, new_lines, batch,
                                             fixed=fixed, exclude=excluded_now)
                for pid in pending:
                    if pid in fixed or pid in excluded_now:
                        continue
                    if net.status.get(pid) in ("anchor", "matched"):
                        fixed[pid] = net.chosen[pid]
                        db_mgr.accept_record(pid, net.chosen[pid])
                        auto_total += 1

                open_pids = [p for p in pending
                             if p not in fixed and p not in excluded_now]
                QgsMessageLog.logMessage(
                    f"Point matching round {round_no}: {auto_total} matched automatically "
                    f"by DB/field distances, {len(open_pids)} open: {open_pids or 'none'} "
                    f"(network iterations: {net.iterations})",
                    "GeoLevel", level=Qgis.Info,
                )
                if not open_pids:
                    break

                # Rank candidates against the located points for display
                if net.known:
                    db_mgr.establish_anchor({}, extra_coords=list(net.known.values()),
                                            include_resolved=False)
                else:
                    db_mgr.establish_anchor(batch)
                scored = {p: db_mgr.score_candidates(p, batch.get(p, []), new_lines,
                                                     batch, known=net.known)
                          for p in open_pids}
                notes = {p: net.notes.get(p, "") for p in open_pids}
                if not any(net.status.get(p) in ("anchor", "matched", "fixed")
                           for p in pending) and round_no == 1:
                    for p in open_pids:
                        notes[p] = ("No anchor found (every point is duplicated) - "
                                    "choose one or more points you are sure of. "
                                    + notes[p]).strip()

                dlg = GeoLevelPointResolutionDialog(
                    open_pids, scored, parent=self.iface.mainWindow(), mode="load",
                    notes=notes, statuses={p: net.status.get(p, "") for p in open_pids},
                    allow_defer=allow_defer, round_no=round_no,
                )
                if not dlg.exec_():
                    return False
                res = dlg.get_resolved()

                progress = False
                for pid in open_pids:
                    mode = res["modes"].get(pid, "use_selected")
                    rec = res["records"].get(pid)
                    if mode == "defer":
                        continue
                    progress = True
                    merged["modes"][pid] = mode
                    merged["records"][pid] = rec
                    if mode == "exclude":
                        excluded_now.add(pid)
                        continue
                    if mode in ("estimate", "pkt_average"):
                        fixed[pid] = None
                        continue
                    if pid in res.get("manual", {}):
                        full = self._resolve_manual_name(db_mgr, pid, res["manual"][pid],
                                                         new_lines)
                        if full is not None:
                            merged["records"][pid] = full
                            fixed[pid] = full
                        else:
                            merged["manual"][pid] = res["manual"][pid]
                            fixed[pid] = None
                        continue
                    if rec is None:
                        # "(no DB match)" kept -> estimate from neighbours
                        merged["modes"][pid] = "estimate"
                        fixed[pid] = None
                        continue
                    fixed[pid] = rec
                    db_mgr.accept_record(pid, rec)

                # Everything deferred and nothing new -> next round must decide
                allow_defer = progress
                if round_no > len(pending) + 2:
                    QgsMessageLog.logMessage("Point matching: round limit reached",
                                             "GeoLevel", level=Qgis.Warning)
                    break

            if merged["modes"]:
                applied = self._apply_resolution(db_mgr, merged, new_lines, edit_mode=False)
                pid_remap.update(applied["remap"])
                self._pending_excluded = set(applied["excluded"])
                self._pending_marker_pids = applied["markers"]

            self._pending_pid_remap = pid_remap
            return True

        except Exception:
            QgsMessageLog.logMessage(
                "Point resolution check failed: " + traceback.format_exc(),
                "GeoLevel", level=Qgis.Warning,
            )
            return True  # non-fatal — fall through to normal load

    def _edit_points(self, pids=None):
        """Point Settings screen — re-select DB benchmarks without reloading files."""
        if not self._lines:
            QMessageBox.information(self.iface.mainWindow(), "Point Settings",
                                    "No lines loaded.")
            return
        try:
            from geolevel_db_manager import get_db_manager
            db_mgr = get_db_manager()
        except Exception as exc:
            QMessageBox.critical(self.iface.mainWindow(), "Point Settings", str(exc))
            return
        if not db_mgr.is_configured():
            QMessageBox.warning(
                self.iface.mainWindow(), "Point Settings",
                "The benchmark database is not configured.\n"
                "Open Settings ▸ Database Connection first.",
            )
            return

        all_pids = self._unique_points(self._lines)
        targets = [p for p in (pids or []) if p in all_pids] or all_pids

        try:
            self.iface.mainWindow().setCursor(Qt.WaitCursor)
            batch = db_mgr.get_candidates_batch(targets)
            # Anchor on the points NOT being edited (their pick is trusted);
            # fall back to the whole session pool when editing everything.
            others = []
            for p in all_pids:
                if p in targets:
                    continue
                rec = db_mgr._cache.get(p.strip().upper())
                if rec is not None and rec.x is not None and rec.y is not None:
                    others.append((rec.x, rec.y))
            if others:
                db_mgr.establish_anchor(batch, extra_coords=others, include_resolved=False)
            else:
                db_mgr.establish_anchor(batch)
            scored = self._score_batch(db_mgr, batch, self._lines)
        finally:
            self.iface.mainWindow().unsetCursor()

        current = {p: db_mgr._cache.get(p.strip().upper()) for p in targets}
        current_modes = {}
        for p in targets:
            ov = self._point_overrides.get(self._point_origin.get(p, p)) or {}
            m = ov.get("mode")
            current_modes[p] = m if m in ("estimate", "pkt_average", "exclude") else "use_selected"

        def _search(pid, query):
            recs = db_mgr.get_candidates(query)
            return db_mgr.score_candidates(pid, recs, self._lines)

        from geo_level_point_resolution_dialog import GeoLevelPointResolutionDialog
        dlg = GeoLevelPointResolutionDialog(
            targets, scored, parent=self.iface.mainWindow(), mode="edit",
            current_selection=current, search_fn=_search,
            current_modes=current_modes,
        )
        if not dlg.exec_():
            return

        result = dlg.get_resolved()
        # Only apply rows the engineer actually touched AND that differ from
        # the current state (untouched rows are never re-pinned).
        changed = {}
        manual = result.get("manual", {})
        for pid in targets:
            if pid not in result.get("dirty", set()):
                continue
            mode = result["modes"].get(pid)
            rec = result["records"].get(pid)
            cur = current.get(pid)
            if pid not in manual:
                if mode == "use_selected":
                    if rec is None:
                        continue
                    if (current_modes.get(pid) == "use_selected"
                            and GeoLevelPointResolutionDialog._same(rec, cur)):
                        continue
                elif mode == current_modes.get(pid):
                    continue
            changed[pid] = pid
        if not changed:
            self.iface.messageBar().pushMessage("Point Settings", "No changes.",
                                                level=Qgis.Info, duration=3)
            return

        sub = {
            "records": {p: result["records"][p] for p in changed},
            "modes": {p: result["modes"][p] for p in changed},
            "manual": {p: v for p, v in result.get("manual", {}).items() if p in changed},
        }
        self._apply_resolution(db_mgr, sub, self._lines, edit_mode=True)
        self._rebuild_map_layers()
        self.iface.messageBar().pushMessage(
            "Point Settings",
            f"Updated {len(changed)} point(s): {', '.join(changed)} — map refreshed.",
            level=Qgis.Success, duration=6,
        )

    def _remove_result_layers(self) -> None:
        """Remove every 'Geodetic Results' / 'Geodetic Points' layer."""
        proj = QgsProject.instance()
        for name in ("Geodetic Results", "Geodetic Points"):
            for lyr in proj.mapLayersByName(name):
                proj.removeMapLayer(lyr.id())
        self._layer = None

    def _rebuild_map_layers(self) -> None:
        """Rebuild line + point layers from in-memory self._lines.

        No measurement file is re-parsed: direction toggles, use flags and
        manager overrides on the LevelingLine objects are preserved.
        """
        output_dir = self._last_output_dir
        if not output_dir or not os.path.isdir(output_dir):
            output_dir = self._default_output_dir()

        try:
            from core_logic.validators import BatchValidator
            cls = self.dock.get_selected_class() if self.dock else self._last_class
            cls = cls or self._last_class or "H3"
            bv = BatchValidator(leveling_class=int(cls[1]))
            # The validator only ever downgrades status; lines were not
            # re-parsed, so reset before re-deriving it from renamed points.
            from core_logic.config.models import LineStatus
            keep = (LineStatus.EXCLUDED_FROM_MERGE, LineStatus.MERGED)
            for ln in self._lines:
                if ln.status not in keep:
                    ln.status = LineStatus.VALID
            self._val_results = bv.validate_batch(self._lines)
            for ln in self._lines:
                if getattr(ln, "manager_override", False):
                    ln.status = LineStatus.VALID_BY_MANAGER
        except Exception:
            QgsMessageLog.logMessage("Re-validation failed: " + traceback.format_exc(),
                                     "GeoLevelPlugin", level=Qgis.Warning)

        canvas = self.iface.mapCanvas()
        extent = canvas.extent()

        # Release the old layers (and their GeoJSON file handles) first, then
        # export under a fresh name so Windows file locks cannot block it.
        self._remove_result_layers()
        self._rebuild_counter = getattr(self, "_rebuild_counter", 0) + 1
        files = {"lines_geojson": "", "points_geojson": "", "line_style": "", "point_style": ""}
        try:
            from core_logic.process import export_layers
            files = export_layers(self._lines, output_dir,
                                  project_name=f"geo_level_result_edit{self._rebuild_counter}")
        except Exception:
            QgsMessageLog.logMessage("Layer re-export failed: " + traceback.format_exc(),
                                     "GeoLevelPlugin", level=Qgis.Warning)

        self._load_line_layer(files.get("lines_geojson", ""), files.get("line_style", ""),
                              self._lines)
        self._load_point_layer(files.get("points_geojson", ""), files.get("point_style", ""))
        self._sync_map_statuses()
        self._persist_to_gpkg()
        if self._layer is not None and self._layer.isValid():
            self._layer.triggerRepaint()
        self.iface.mapCanvas().refresh()
        if self.dock:
            cur = self.dock.get_current_index()
            self.dock.load_lines(self._lines, self._val_results)
            if 0 <= cur < len(self._lines):
                self.dock.line_list.setCurrentRow(cur)
        # load_lines/setCurrentRow zoom to a line — restore the user's view
        canvas.setExtent(extent)
        canvas.refresh()

    def _run_lsa(self):
        if not self._lines:
            QMessageBox.information(self.iface.mainWindow(), "LSA Adjustment",
                                    "No lines loaded.")
            return
        try:
            # ── Step 1: collect eligible lines (VALID or manager-overridden) ──
            from core_logic.config.models import LineStatus
            eligible_lines = [
                ln for ln in self._lines
                if getattr(ln, "is_used", True) and (
                    ln.status == LineStatus.VALID
                    or getattr(ln, "manager_override", False)
                )
            ]
            if not eligible_lines:
                QMessageBox.warning(self.iface.mainWindow(), "LSA Adjustment",
                                    "No valid lines available for adjustment.\n"
                                    "Use 'Force Valid (Manager Override)' on lines you wish to include.")
                return

            # ── Step 2: fixed-points table dialog (replaces bare QInputDialog) ──
            input_dlg = GeoLevelLSAInputDialog(eligible_lines, self.iface.mainWindow())
            if not input_dlg.exec_():
                return
            fixed_points = input_dlg.fixed_points
            if not fixed_points:
                return

            from core_logic.engine.least_squares import LeastSquaresAdjuster
            result = LeastSquaresAdjuster().adjust_from_lines(eligible_lines, fixed_points)

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
            dlg = GeoLevelEnhancedLSADialog(
                self._lines, self.iface.mainWindow(),
                val_results=self._val_results
            )
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

                # Re-validate to prevent deepcopy desync with self._val_results
                try:
                    from core_logic.validators import BatchValidator
                    cls = self.dock.get_selected_class() if self.dock else self._last_class
                    bv = BatchValidator(leveling_class=int(cls[1]))
                    self._val_results = bv.validate_batch(self._lines)
                except Exception:
                    pass

                if self.dock:
                    self.dock.load_lines(self._lines, self._val_results)

                # Push the EXCLUDED statuses to the QGIS map instantly
                self._sync_map_statuses()
        except Exception as exc:
            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",
                                     level=Qgis.Critical)
            QMessageBox.critical(self.iface.mainWindow(), "Point Exclusion -- Error", str(exc))

    # ------------------------------------------------------------------
    # Settings menu actions
    # ------------------------------------------------------------------

    def _show_db_settings(self):
        try:
            from geo_level_db_settings_dialog import GeoLevelDBSettingsDialog
            GeoLevelDBSettingsDialog(self.iface.mainWindow()).exec_()
        except Exception as exc:
            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",
                                     level=Qgis.Critical)
            QMessageBox.critical(self.iface.mainWindow(), "DB Settings — Error", str(exc))

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
