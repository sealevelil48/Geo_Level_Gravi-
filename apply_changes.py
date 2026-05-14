"""apply_changes.py — applies all feature-parity changes to geo_level_plugin.py"""
import ast, sys

path = r"GeoLevelQGIS\geo_level_plugin.py"
src = open(path, encoding="utf-8").read()

# ── 1. Split Analysis menu ────────────────────────────────────────────
OLD_ANAL = (
    '        anal_menu.addAction("Validate All Lines").triggered.connect(self._run_batch_validation)\n'
    '        anal_menu.addAction("Detect Loops / Double-Runs").triggered.connect(self._detect_loops)\n'
    '        anal_menu.addSeparator()\n'
    '        anal_menu.addAction("Line Adjustment...").triggered.connect(self._adjust_current_line)\n'
    '        anal_menu.addAction("Network Adjustment (LSA)...").triggered.connect(self._run_lsa)\n'
    '        anal_menu.addAction("Network Adjustment (Enhanced)...").triggered.connect(self._run_enhanced_lsa)\n'
    '        anal_menu.addSeparator()\n'
    '        anal_menu.addAction("Merge Line Segments...").triggered.connect(self._open_merge_dialog)'
)
NEW_ANAL = (
    '        anal_menu.addAction("Validate All Lines").triggered.connect(self._run_batch_validation)\n'
    '        anal_menu.addAction("Detect Double-Runs").triggered.connect(self._detect_double_runs)\n'
    '        anal_menu.addAction("Find Loops").triggered.connect(self._detect_loops)\n'
    '        anal_menu.addSeparator()\n'
    '        anal_menu.addAction("Line Adjustment...").triggered.connect(self._adjust_current_line)\n'
    '        anal_menu.addAction("Network Adjustment (LSA)...").triggered.connect(self._run_lsa)\n'
    '        anal_menu.addAction("Network Adjustment (Enhanced)...").triggered.connect(self._run_enhanced_lsa)\n'
    '        anal_menu.addSeparator()\n'
    '        anal_menu.addAction("Merge Line Segments...").triggered.connect(self._open_merge_dialog)'
)
assert OLD_ANAL in src, "ANAL block not found"
src = src.replace(OLD_ANAL, NEW_ANAL, 1)
print("1. Analysis menu split: OK")

# ── 2. Add Point Exclusion to Settings menu ───────────────────────────
OLD_SETT = (
    '        sett_menu.addAction("Class Parameters\u2026").triggered.connect(self._show_class_settings)\n'
    '        sett_menu.addAction("Encoding\u2026").triggered.connect(self._show_encoding_settings)'
)
NEW_SETT = (
    '        sett_menu.addAction("Class Parameters\u2026").triggered.connect(self._show_class_settings)\n'
    '        sett_menu.addAction("Encoding\u2026").triggered.connect(self._show_encoding_settings)\n'
    '        sett_menu.addAction("Point Exclusion...").triggered.connect(self._show_point_exclusion)'
)
assert OLD_SETT in src, "SETT block not found"
src = src.replace(OLD_SETT, NEW_SETT, 1)
print("2. Settings menu updated: OK")

# ── 3. Fix _adjust_single_line to open dialog ─────────────────────────
OLD_ADJ = '''\
    def _adjust_single_line(self, idx: int):
        if not (0 <= idx < len(self._lines)):
            return
        line = self._lines[idx]
        try:
            from core_logic.engine.line_adjustment import LineAdjuster
            adjusted = LineAdjuster().adjust(line)
            text = (
                f"Line: {line.start_point} \u2192 {line.end_point}\\n"
                f"Total \u0394H (before): {line.total_height_diff:.5f} m\\n"
                f"Total \u0394H (after) : {adjusted.total_height_diff:.5f} m\\n"
                f"Misclosure       : "
                f"{(line.total_height_diff - adjusted.total_height_diff)*1000:.3f} mm\\n"
            )
            if self.dock:
                self.dock.show_adjustment_result(text)
        except Exception as exc:
            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",
                                     level=Qgis.Critical)
            QMessageBox.critical(self.iface.mainWindow(), "Adjust Line \u2014 Error", str(exc))'''
NEW_ADJ = '''\
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
            QMessageBox.critical(self.iface.mainWindow(), "Adjust Line -- Error", str(exc))'''
assert OLD_ADJ in src, "ADJ block not found"
src = src.replace(OLD_ADJ, NEW_ADJ, 1)
print("3. _adjust_single_line fixed: OK")

# ── 4. Replace _detect_loops with safe version + add _detect_double_runs
OLD_LOOPS = '''\
    def _detect_loops(self):
        if not self._lines:
            QMessageBox.information(self.iface.mainWindow(), "Detect Loops",
                                    "No lines loaded.")
            return
        try:
            from core_logic.engine.loop_detector import LoopAnalyzer
            analyzer = LoopAnalyzer(self._lines)
            loops = analyzer.find_loops()
            if not loops:
                msg = "No closed loops detected in the current network."
            else:
                parts = [f"Found {len(loops)} loop(s):\\n"]
                for i, loop in enumerate(loops, 1):
                    ok, mis_mm, tol_mm = loop.check_tolerance()
                    parts.append(
                        f"  {i}. {' \u2192 '.join(loop.points)}\\n"
                        f"     Misclosure: {mis_mm:.2f} mm  |  "
                        f"Tolerance: \u00b1{tol_mm:.2f} mm  {'\u2705' if ok else '\u274c'}"
                    )
                msg = "\\n".join(parts)
            QMessageBox.information(self.iface.mainWindow(), "Detect Loops", msg)
        except Exception as exc:
            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",
                                     level=Qgis.Critical)
            QMessageBox.critical(self.iface.mainWindow(), "Detect Loops \u2014 Error", str(exc))'''
NEW_LOOPS = '''\
    def _detect_loops(self):
        if not self._lines:
            QMessageBox.information(self.iface.mainWindow(), "Find Loops",
                                    "No lines loaded.")
            return
        try:
            from core_logic.engine.loop_detector import LoopAnalyzer
            analyzer = LoopAnalyzer(self._lines)
            loops = analyzer.find_loops()
            if not loops:
                msg = "No closed loops detected in the current network."
            else:
                parts = ["Found " + str(len(loops)) + " loop(s):"]
                for i, loop in enumerate(loops, 1):
                    ok, mis_mm, tol_mm = loop.check_tolerance()
                    path_str = " -> ".join(loop.points)
                    mark = "OK" if ok else "FAIL"
                    parts.append(
                        "  " + str(i) + ". " + path_str + "\\n"
                        + "     Misclosure: " + "{:.2f}".format(mis_mm) + " mm  |  "
                        + "Tolerance: +/-" + "{:.2f}".format(tol_mm) + " mm  [" + mark + "]"
                    )
                msg = "\\n".join(parts)
            if self.dock:
                self.dock.show_adjustment_result(msg)
            QMessageBox.information(self.iface.mainWindow(), "Find Loops", msg)
        except Exception as exc:
            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",
                                     level=Qgis.Critical)
            QMessageBox.critical(self.iface.mainWindow(), "Find Loops -- Error", str(exc))

    def _detect_double_runs(self):
        if not self._lines:
            QMessageBox.information(self.iface.mainWindow(), "Detect Double-Runs",
                                    "No lines loaded.")
            return
        try:
            from core_logic.engine.loop_detector import detect_double_runs, LoopAnalyzer
            pairs = detect_double_runs(self._lines)
            if not pairs:
                msg = "No double-run pairs detected."
            else:
                cls = self.dock.get_selected_class() if self.dock else self._last_class
                analyzer = LoopAnalyzer(self._lines)
                parts = ["Found " + str(len(pairs)) + " double-run pair(s):"]
                for fwd, ret in pairs:
                    res = analyzer.analyze_double_run(fwd, ret, int(cls[1]))
                    mark = "OK" if res["within_tolerance"] else "FAIL"
                    parts.append(
                        "  " + fwd.start_point + " <-> " + fwd.end_point + "\\n"
                        + "    Misclosure: " + "{:.3f}".format(res["misclosure_mm"]) + " mm"
                        + "  Tolerance: +/-" + "{:.2f}".format(res["tolerance_mm"]) + " mm"
                        + "  [" + mark + "]  Class: " + res["class_name"]
                    )
                msg = "\\n".join(parts)
            if self.dock:
                self.dock.show_adjustment_result(msg)
            QMessageBox.information(self.iface.mainWindow(), "Detect Double-Runs", msg)
        except Exception as exc:
            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",
                                     level=Qgis.Critical)
            QMessageBox.critical(self.iface.mainWindow(), "Double-Runs -- Error", str(exc))'''
assert OLD_LOOPS in src, "LOOPS block not found"
src = src.replace(OLD_LOOPS, NEW_LOOPS, 1)
print("4. _detect_loops fixed + _detect_double_runs added: OK")

# ── 5. Add new project/settings methods before Settings section ───────
OLD_SETTINGS_HDR = '''\
    # ------------------------------------------------------------------
    # Settings menu actions
    # ------------------------------------------------------------------'''
NEW_SETTINGS_HDR = '''\
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
            from core_logic.gis.geojson_export import export_network_to_geojson
            files = export_network_to_geojson(
                self._lines, output_dir, project_name="geo_level_export"
            )
            msg = "Exported:\\n  GeoJSON: " + files["lines_geojson"]
            try:
                from core_logic.exporters import export_fteg
                fteg_path = os.path.join(output_dir, "export.fteg")
                export_fteg(self._lines, fteg_path)
                msg += "\\n  FTEG: " + fteg_path
            except Exception:
                pass
            QMessageBox.information(self.iface.mainWindow(), "Export Results", msg)
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
    # ------------------------------------------------------------------'''
assert OLD_SETTINGS_HDR in src, "SETTINGS HDR not found"
src = src.replace(OLD_SETTINGS_HDR, NEW_SETTINGS_HDR, 1)
print("5. New project/settings methods added: OK")

# ── Write and verify ──────────────────────────────────────────────────
open(path, "w", encoding="utf-8").write(src)
print("File written.")
try:
    ast.parse(src)
    print("SYNTAX OK")
except SyntaxError as e:
    print("SYNTAX ERROR line " + str(e.lineno) + ": " + str(e.msg))
    print("  " + str(e.text))
    sys.exit(1)
