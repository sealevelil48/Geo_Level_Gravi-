"""patch_plugin_routing.py — routes analysis output to dock Analysis tab."""
import ast, sys

path = r"GeoLevelQGIS\geo_level_plugin.py"
src = open(path, encoding="utf-8").read()

# ── 1. _detect_loops: remove QMessageBox, route to analysis tab ───────
OLD1 = (
    '            if self.dock:\n'
    '                self.dock.show_adjustment_result(msg)\n'
    '            QMessageBox.information(self.iface.mainWindow(), "Find Loops", msg)\n'
    '        except Exception as exc:\n'
    '            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",\n'
    '                                     level=Qgis.Critical)\n'
    '            QMessageBox.critical(self.iface.mainWindow(), "Find Loops -- Error", str(exc))'
)
NEW1 = (
    '            if self.dock:\n'
    '                self.dock.show_analysis_result(msg)\n'
    '                self.dock.log("Find Loops completed.")\n'
    '            else:\n'
    '                QMessageBox.information(self.iface.mainWindow(), "Find Loops", msg)\n'
    '        except Exception as exc:\n'
    '            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",\n'
    '                                     level=Qgis.Critical)\n'
    '            if self.dock:\n'
    '                self.dock.log("Find Loops ERROR: " + str(exc))\n'
    '            QMessageBox.critical(self.iface.mainWindow(), "Find Loops -- Error", str(exc))'
)
assert OLD1 in src, "LOOPS routing block not found"
src = src.replace(OLD1, NEW1, 1)
print("1. _detect_loops routing: OK")

# ── 2. _detect_double_runs: remove QMessageBox, route to analysis tab ─
OLD2 = (
    '            if self.dock:\n'
    '                self.dock.show_adjustment_result(msg)\n'
    '            QMessageBox.information(self.iface.mainWindow(), "Detect Double-Runs", msg)\n'
    '        except Exception as exc:\n'
    '            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",\n'
    '                                     level=Qgis.Critical)\n'
    '            QMessageBox.critical(self.iface.mainWindow(), "Double-Runs -- Error", str(exc))'
)
NEW2 = (
    '            if self.dock:\n'
    '                self.dock.show_analysis_result(msg)\n'
    '                self.dock.log("Detect Double-Runs completed.")\n'
    '            else:\n'
    '                QMessageBox.information(self.iface.mainWindow(), "Detect Double-Runs", msg)\n'
    '        except Exception as exc:\n'
    '            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",\n'
    '                                     level=Qgis.Critical)\n'
    '            if self.dock:\n'
    '                self.dock.log("Double-Runs ERROR: " + str(exc))\n'
    '            QMessageBox.critical(self.iface.mainWindow(), "Double-Runs -- Error", str(exc))'
)
assert OLD2 in src, "DOUBLE-RUNS routing block not found"
src = src.replace(OLD2, NEW2, 1)
print("2. _detect_double_runs routing: OK")

# ── 3. _run_batch_validation: route summary to analysis tab ───────────
OLD3 = (
    '            self.iface.messageBar().pushMessage(\n'
    '                "Validation",\n'
    '                f"{summary[\'valid\']}/{summary[\'total\']} valid  |  "\n'
    '                f"{summary[\'endpoint_issues\']} endpoint  |  "\n'
    '                f"{summary[\'naming_issues\']} naming  |  "\n'
    '                f"{summary[\'tolerance_issues\']} tolerance",\n'
    '                level=Qgis.Info, duration=8,\n'
    '            )\n'
    '        except Exception as exc:\n'
    '            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",\n'
    '                                     level=Qgis.Critical)\n'
    '            QMessageBox.critical(self.iface.mainWindow(), "Validate All \u2014 Error", str(exc))'
)
NEW3 = (
    '            val_msg = (\n'
    '                "Validation complete: "\n'
    '                + str(summary["valid"]) + "/" + str(summary["total"]) + " valid  |  "\n'
    '                + str(summary["endpoint_issues"]) + " endpoint  |  "\n'
    '                + str(summary["naming_issues"]) + " naming  |  "\n'
    '                + str(summary["tolerance_issues"]) + " tolerance"\n'
    '            )\n'
    '            self.iface.messageBar().pushMessage(\n'
    '                "Validation", val_msg, level=Qgis.Info, duration=8\n'
    '            )\n'
    '            if self.dock:\n'
    '                self.dock.show_analysis_result(val_msg)\n'
    '                self.dock.log("Validate All completed.")\n'
    '        except Exception as exc:\n'
    '            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",\n'
    '                                     level=Qgis.Critical)\n'
    '            if self.dock:\n'
    '                self.dock.log("Validate All ERROR: " + str(exc))\n'
    '            QMessageBox.critical(self.iface.mainWindow(), "Validate All -- Error", str(exc))'
)
assert OLD3 in src, "VALIDATION routing block not found"
src = src.replace(OLD3, NEW3, 1)
print("3. _run_batch_validation routing: OK")

# ── 4. Also route LSA summary to analysis tab ─────────────────────────
OLD4 = (
    '            QgsMessageLog.logMessage(summary, "GeoLevelPlugin", level=Qgis.Info)\n'
    '            if self.dock:\n'
    '                self.dock.show_adjustment_result(summary)'
)
NEW4 = (
    '            QgsMessageLog.logMessage(summary, "GeoLevelPlugin", level=Qgis.Info)\n'
    '            if self.dock:\n'
    '                self.dock.show_analysis_result(summary)\n'
    '                self.dock.log("LSA completed.")'
)
assert OLD4 in src, "LSA summary routing block not found"
src = src.replace(OLD4, NEW4, 1)
print("4. LSA summary routing: OK")

open(path, "w", encoding="utf-8").write(src)
print("File written.")

try:
    ast.parse(src)
    print("SYNTAX OK")
except SyntaxError as e:
    print("SYNTAX ERROR line " + str(e.lineno) + ": " + str(e.msg))
    sys.exit(1)
