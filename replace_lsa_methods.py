"""replace_lsa_methods.py"""
import ast, sys

path = r"GeoLevelQGIS\geo_level_enhanced_lsa_dialog.py"
raw = open(path, "rb").read()
lines = raw.split(b"\n")
print("Total lines:", len(lines))

# 0-indexed: _run_parametric = 271..280, _run_conditional = 282..304
# Keep everything before line 271, insert new methods, keep from line 305 onward

NEW_METHODS = b'''\
    def _run_parametric(self, fixed_points):
        """Parametric (Ax+L): V = Ax - L, normal equations N*X = U."""
        from core_logic.engine.least_squares import LeastSquaresAdjuster
        from core_logic.config.models import MeasurementSummary

        observations = []
        for line in self.lines:
            if not getattr(line, 'is_used', True):
                continue
            observations.append(MeasurementSummary(
                from_point=line.start_point,
                to_point=line.end_point,
                height_diff=line.total_height_diff,
                distance=line.total_distance,
                num_setups=len(line.setups),
                bf_diff=0.0, year_month="", source_file=line.filename,
            ))

        if not observations:
            QMessageBox.warning(self, "No Lines",
                                "No active lines to adjust.")
            return

        adjuster = LeastSquaresAdjuster(
            max_iterations=self.spin_iter.value(),
            tolerance=self.spin_tol.value(),
            check_stability=self.chk_stability.isChecked()
        )
        result = adjuster.adjust(observations, fixed_points)
        self.result = result
        self._populate_results(result, fixed_points, method="Parametric (Ax - L)")

    def _run_conditional(self, fixed_points):
        """Conditional (Bv+W): uses ConditionalAdjuster.adjust_loops() with
        minimum independent loops. Falls back to parametric if no loops exist."""
        from core_logic.engine.least_squares import ConditionalAdjuster
        from core_logic.engine.loop_detector import LoopAnalyzer

        active_lines = [ln for ln in self.lines if getattr(ln, 'is_used', True)]
        if not active_lines:
            QMessageBox.warning(self, "No Lines", "No active lines to adjust.")
            return

        analyzer = LoopAnalyzer(active_lines)
        loops = analyzer.find_minimum_loops()

        if not loops:
            QMessageBox.information(
                self, "Conditional Adjustment",
                "No closed loops detected.\\n"
                "Conditional method requires a loop network.\\n"
                "Falling back to Parametric method."
            )
            self._run_parametric(fixed_points)
            return

        line_id_map = {id(ln): i for i, ln in enumerate(active_lines)}
        loop_indices = []
        for loop in loops:
            idx = [line_id_map[id(ln)] for ln in loop.lines if id(ln) in line_id_map]
            if len(idx) >= 2:
                loop_indices.append(idx)

        if not loop_indices or len(loop_indices) >= len(active_lines):
            QMessageBox.information(
                self, "Conditional Adjustment",
                "Could not build valid loop conditions.\\n"
                "Falling back to Parametric method."
            )
            self._run_parametric(fixed_points)
            return

        adjuster = ConditionalAdjuster(check_stability=self.chk_stability.isChecked())
        result = adjuster.adjust_loops(active_lines, loop_indices, fixed_points)
        self.result = result
        self._populate_results(result, fixed_points, method="Conditional (Bv + W)")

'''

# Replace lines 271..304 (0-indexed) with new methods
new_lines = lines[:271] + NEW_METHODS.split(b"\n") + lines[305:]
open(path, "wb").write(b"\n".join(new_lines))
print("Written.")

src = open(path, encoding="utf-8").read()
try:
    ast.parse(src)
    print("SYNTAX OK")
except SyntaxError as e:
    print("SYNTAX ERROR line %d: %s" % (e.lineno, e.msg))
    print(e.text)
    sys.exit(1)
