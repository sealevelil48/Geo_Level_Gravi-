"""
fix_plugin.py  —  replaces the entire _detect_loops method with a clean version.
Run from project root: python fix_plugin.py
"""
import ast, sys

path = r"GeoLevelQGIS\geo_level_plugin.py"
raw = open(path, "rb").read()

# Find start and end of _detect_loops method
START = b"    def _detect_loops(self):"
END   = b"    def _run_lsa(self):"

s = raw.find(START)
e = raw.find(END)

if s == -1 or e == -1:
    print("ERROR: could not locate _detect_loops boundaries")
    print("START found:", s, "  END found:", e)
    sys.exit(1)

print(f"Replacing bytes {s}..{e}")

CLEAN_METHOD = b"""    def _detect_loops(self):
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
                parts = ["Found " + str(len(loops)) + " loop(s):\\n"]
                for i, loop in enumerate(loops, 1):
                    ok, mis_mm, tol_mm = loop.check_tolerance()
                    path_str = " -> ".join(loop.points)
                    mark = chr(0x2705) if ok else chr(0x274c)
                    parts.append(
                        "  " + str(i) + ". " + path_str + "\\n"
                        "     Misclosure: " + f"{mis_mm:.2f}" + " mm  |  "
                        "Tolerance: +/-" + f"{tol_mm:.2f}" + " mm  " + mark
                    )
                msg = "\\n".join(parts)
            if self.dock:
                self.dock.show_adjustment_result(msg)
            QMessageBox.information(self.iface.mainWindow(), "Detect Loops", msg)
        except Exception as exc:
            QgsMessageLog.logMessage(traceback.format_exc(), "GeoLevelPlugin",
                                     level=Qgis.Critical)
            QMessageBox.critical(self.iface.mainWindow(), "Detect Loops -- Error", str(exc))

"""

raw = raw[:s] + CLEAN_METHOD + raw[e:]
open(path, "wb").write(raw)
print("Method replaced and file written.")

# Syntax check
src = open(path, encoding="utf-8").read()
try:
    ast.parse(src)
    print("SYNTAX OK -- ready to deploy.")
except SyntaxError as e:
    print(f"SYNTAX ERROR at line {e.lineno}: {e.msg}")
    print(f"  {e.text}")
    sys.exit(1)
