"""replace_conditional.py — replaces _run_conditional with real Bv+W implementation."""
import ast, sys

path = r"GeoLevelQGIS\geo_level_enhanced_lsa_dialog.py"
lines = open(path, encoding="utf-8").readlines()

# Lines 318-357 (0-indexed 317-356) are the old _run_conditional
# Replace them with the real implementation
NEW_METHOD = '''\
    def _run_conditional(self, fixed_points):
        """
        True Conditional (Bv+W) adjustment using ConditionalAdjuster.adjust_loops().
        Detects independent basis loops, builds condition matrix, solves Bv+w=0.
        Falls back to parametric if no loops exist.
        """
        from core_logic.engine.least_squares import ConditionalAdjuster
        from core_logic.engine.loop_detector import LoopAnalyzer

        active_lines = [ln for ln in self.lines if getattr(ln, 'is_used', True)]
        if not active_lines:
            QMessageBox.warning(self, "No Lines",
                                "No active lines to adjust (all lines are excluded).")
            return

        analyzer = LoopAnalyzer(active_lines)
        loops = analyzer.find_minimum_loops()

        if not loops:
            QMessageBox.information(
                self, "Conditional Adjustment",
                "No closed loops detected in this network.\\n"
                "The Conditional (Bv+W) method requires a loop network.\\n\\n"
                "Falling back to Parametric method."
            )
            self._run_parametric(fixed_points)
            return

        line_id_map = {id(ln): i for i, ln in enumerate(active_lines)}
        loop_indices = []
        for loop in loops:
            indices = [line_id_map[id(ln)] for ln in loop.lines
                       if id(ln) in line_id_map]
            if len(indices) >= 2:
                loop_indices.append(indices)

        if not loop_indices or len(loop_indices) >= len(active_lines):
            QMessageBox.information(
                self, "Conditional Adjustment",
                "Could not build valid loop conditions.\\n"
                "Falling back to Parametric method."
            )
            self._run_parametric(fixed_points)
            return

        adjuster = ConditionalAdjuster(
            check_stability=self.chk_stability.isChecked()
        )
        result = adjuster.adjust_loops(active_lines, loop_indices, fixed_points)
        self.result = result
        self._populate_results(result, fixed_points, method="Conditional (Bv + W)")

'''

# Replace lines 317..356 (0-indexed), i.e. lines 318-357 in 1-indexed
new_lines = lines[:317] + [NEW_METHOD] + lines[358:]

open(path, "w", encoding="utf-8").writelines(new_lines)
print("Written.")

src = open(path, encoding="utf-8").read()
try:
    ast.parse(src)
    print("SYNTAX OK")
except SyntaxError as e:
    print("SYNTAX ERROR line %d: %s" % (e.lineno, e.msg))
    sys.exit(1)
