"""replace_run_conditional.py"""
import ast, sys

path = r"GeoLevelQGIS\geo_level_enhanced_lsa_dialog.py"
raw = open(path, "rb").read()
lines = raw.split(b"\n")
print("Total lines:", len(lines))

NEW = b"""\
    def _run_conditional(self, fixed_points):
        \"\"\"
        True Conditional (Bv+W) adjustment.
        n_conditions = n_loops + (n_BM - 1).
        B matrix uses +1/-1 signs based on line direction vs loop traversal.
        \"\"\"
        from core_logic.engine.least_squares import ConditionalAdjuster
        from core_logic.engine.loop_detector import LoopAnalyzer

        active_lines = [ln for ln in self.lines if getattr(ln, 'is_used', True)]
        if not active_lines:
            QMessageBox.warning(self, "No Lines", "No active lines to adjust.")
            return

        analyzer = LoopAnalyzer(active_lines)
        loops = analyzer.find_basis_loops()

        # Build signed loop indices: list of (line_index, sign) tuples
        line_id_map = {id(ln): i for i, ln in enumerate(active_lines)}
        loop_indices = []
        for loop in loops:
            signed = []
            prev_pt = loop.points[0] if loop.points else None
            for ln in loop.lines:
                if id(ln) not in line_id_map:
                    continue
                idx = line_id_map[id(ln)]
                if prev_pt is not None and ln.start_point == prev_pt:
                    sign = 1
                    prev_pt = ln.end_point
                else:
                    sign = -1
                    prev_pt = ln.start_point
                signed.append((idx, sign))
            if len(signed) >= 2:
                loop_indices.append(signed)

        n_bm = len(fixed_points)
        expected_conditions = len(loop_indices) + max(0, n_bm - 1)

        if expected_conditions == 0:
            QMessageBox.information(
                self, "Conditional Adjustment",
                "No conditions could be formed.\\n"
                "Need loops or at least 2 fixed benchmarks.\\n"
                "Falling back to Parametric method."
            )
            self._run_parametric(fixed_points)
            return

        if expected_conditions >= len(active_lines):
            QMessageBox.information(
                self, "Conditional Adjustment",
                "Too many conditions for the number of observations.\\n"
                "Falling back to Parametric method."
            )
            self._run_parametric(fixed_points)
            return

        adjuster = ConditionalAdjuster(check_stability=self.chk_stability.isChecked())
        result = adjuster.adjust_loops(active_lines, loop_indices, fixed_points)
        self.result = result
        self._populate_results(result, fixed_points, method="Conditional (Bv + W)")

"""

# Replace lines 303..347 (0-indexed), i.e. 1-indexed 304..348
new_lines = lines[:303] + NEW.split(b"\n") + lines[348:]
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
