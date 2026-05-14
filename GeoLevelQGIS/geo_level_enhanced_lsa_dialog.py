"""
geo_level_enhanced_lsa_dialog.py
Enhanced Network Adjustment dialog — PyQt5 port of EnhancedNetworkAdjustmentDialog.

Left panel  : Method radio buttons, Fixed Points table, Advanced Options
Right panel : QTabWidget — Summary | Adjusted Heights | Residuals | Matrix Diagnostics
"""

from qgis.PyQt.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QGroupBox, QRadioButton,
    QTableWidget, QTableWidgetItem, QPushButton, QLabel, QTextEdit,
    QTabWidget, QWidget, QDoubleSpinBox, QSpinBox, QCheckBox,
    QSplitter, QHeaderView, QAbstractItemView, QMessageBox, QSizePolicy
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont


class GeoLevelEnhancedLSADialog(QDialog):
    """
    Enhanced Network Adjustment dialog.

    Parameters
    ----------
    lines       : list of LevelingLine
    parent      : QWidget
    """

    def __init__(self, lines, parent=None):
        super().__init__(parent)
        self.lines = lines
        self.result = None
        self.setWindowTitle("Network Adjustment (Enhanced LSA) / כיוון רשת מתקדם")
        self.resize(1000, 650)
        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QHBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter)

        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([320, 680])

    # ── Left panel ──────────────────────────────────────────────────── #

    def _build_left_panel(self):
        panel = QWidget()
        vbox = QVBoxLayout(panel)

        # Method
        grp_method = QGroupBox("Adjustment Method / שיטת כיוון")
        vbox_m = QVBoxLayout(grp_method)
        self.rb_parametric = QRadioButton("Parametric (Observations)  V = Ax − L")
        self.rb_conditional = QRadioButton("Conditional (Constraints)  Bv + W = 0")
        self.rb_parametric.setChecked(True)
        vbox_m.addWidget(self.rb_parametric)
        vbox_m.addWidget(self.rb_conditional)
        vbox.addWidget(grp_method)

        # Fixed points
        grp_fp = QGroupBox("Fixed Points / נקודות קבועות")
        vbox_fp = QVBoxLayout(grp_fp)

        self.fp_table = QTableWidget(0, 2)
        self.fp_table.setHorizontalHeaderLabels(["Point ID", "Height (m)"])
        self.fp_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.fp_table.setMinimumHeight(160)
        vbox_fp.addWidget(self.fp_table)

        btn_row = QHBoxLayout()
        btn_add_fp = QPushButton("+ Add Row")
        btn_add_fp.clicked.connect(self._add_fp_row)
        btn_del_fp = QPushButton("− Remove")
        btn_del_fp.clicked.connect(self._del_fp_row)
        btn_auto = QPushButton("Auto-Select")
        btn_auto.setToolTip("Auto-detect benchmark endpoints from loaded lines")
        btn_auto.clicked.connect(self._auto_select_fixed)
        btn_row.addWidget(btn_add_fp)
        btn_row.addWidget(btn_del_fp)
        btn_row.addWidget(btn_auto)
        vbox_fp.addLayout(btn_row)
        vbox.addWidget(grp_fp)

        # Advanced options
        grp_adv = QGroupBox("Advanced Options / אפשרויות מתקדמות")
        form = QVBoxLayout(grp_adv)

        tol_row = QHBoxLayout()
        tol_row.addWidget(QLabel("Convergence Tolerance (m):"))
        self.spin_tol = QDoubleSpinBox()
        self.spin_tol.setDecimals(8)
        self.spin_tol.setRange(1e-10, 1.0)
        self.spin_tol.setValue(1e-6)
        self.spin_tol.setSingleStep(1e-7)
        tol_row.addWidget(self.spin_tol)
        form.addLayout(tol_row)

        iter_row = QHBoxLayout()
        iter_row.addWidget(QLabel("Max Iterations:"))
        self.spin_iter = QSpinBox()
        self.spin_iter.setRange(1, 100)
        self.spin_iter.setValue(10)
        iter_row.addWidget(self.spin_iter)
        form.addLayout(iter_row)

        self.chk_stability = QCheckBox("Matrix Stability Check")
        self.chk_stability.setChecked(True)
        form.addWidget(self.chk_stability)

        self.chk_dist_weights = QCheckBox("Distance-Based Weights  (P = 1/km)")
        self.chk_dist_weights.setChecked(True)
        form.addWidget(self.chk_dist_weights)

        vbox.addWidget(grp_adv)

        # Run button
        btn_run = QPushButton("▶  Run Adjustment / הפעל כיוון")
        btn_run.setFixedHeight(38)
        btn_run.setStyleSheet("font-weight: bold; background: #1565c0; color: white;")
        btn_run.clicked.connect(self._run_adjustment)
        vbox.addWidget(btn_run)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.reject)
        vbox.addWidget(btn_close)

        vbox.addStretch()
        return panel

    # ── Right panel ─────────────────────────────────────────────────── #

    def _build_right_panel(self):
        panel = QWidget()
        vbox = QVBoxLayout(panel)

        self.tabs = QTabWidget()
        vbox.addWidget(self.tabs)

        self.tabs.addTab(self._build_summary_tab(),     "Summary / סיכום")
        self.tabs.addTab(self._build_heights_tab(),     "Adjusted Heights / גבהים")
        self.tabs.addTab(self._build_residuals_tab(),   "Residuals / שאריות")
        self.tabs.addTab(self._build_matrix_tab(),      "Matrix Diagnostics")

        return panel

    def _build_summary_tab(self):
        w = QWidget()
        vbox = QVBoxLayout(w)
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setFont(QFont("Courier New", 9))
        self.summary_text.setPlaceholderText("Run adjustment to see results…")
        vbox.addWidget(self.summary_text)
        return w

    def _build_heights_tab(self):
        w = QWidget()
        vbox = QVBoxLayout(w)
        self.heights_table = QTableWidget(0, 4)
        self.heights_table.setHorizontalHeaderLabels(
            ["Point ID", "Height (m)", "Correction (mm)", "Std Dev (mm)"]
        )
        self.heights_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.heights_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.heights_table.setAlternatingRowColors(True)
        vbox.addWidget(self.heights_table)
        return w

    def _build_residuals_tab(self):
        w = QWidget()
        vbox = QVBoxLayout(w)
        self.residuals_table = QTableWidget(0, 2)
        self.residuals_table.setHorizontalHeaderLabels(
            ["Observation (FROM–TO)", "Residual (mm)"]
        )
        self.residuals_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.residuals_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.residuals_table.setAlternatingRowColors(True)
        vbox.addWidget(self.residuals_table)
        return w

    def _build_matrix_tab(self):
        w = QWidget()
        vbox = QVBoxLayout(w)
        self.matrix_text = QTextEdit()
        self.matrix_text.setReadOnly(True)
        self.matrix_text.setFont(QFont("Courier New", 8))
        self.matrix_text.setPlaceholderText("Matrix diagnostics appear here after adjustment…")
        vbox.addWidget(self.matrix_text)
        return w

    # ------------------------------------------------------------------ #
    # Slots
    # ------------------------------------------------------------------ #

    def _add_fp_row(self):
        row = self.fp_table.rowCount()
        self.fp_table.insertRow(row)
        self.fp_table.setItem(row, 0, QTableWidgetItem(""))
        self.fp_table.setItem(row, 1, QTableWidgetItem("0.000"))

    def _del_fp_row(self):
        row = self.fp_table.currentRow()
        if row >= 0:
            self.fp_table.removeRow(row)

    def _auto_select_fixed(self):
        """Populate fixed-points table with all unique benchmark endpoints."""
        endpoints = set()
        for line in self.lines:
            if line.start_point:
                endpoints.add(line.start_point)
            if line.end_point:
                endpoints.add(line.end_point)
        # Keep only points that appear as endpoints (not mid-network)
        mid_points = set()
        for line in self.lines:
            for s in line.setups:
                if s.from_point and s.from_point not in (line.start_point, line.end_point):
                    mid_points.add(s.from_point)
                if s.to_point and s.to_point not in (line.start_point, line.end_point):
                    mid_points.add(s.to_point)
        candidates = sorted(endpoints - mid_points)
        self.fp_table.setRowCount(0)
        for pid in candidates:
            row = self.fp_table.rowCount()
            self.fp_table.insertRow(row)
            self.fp_table.setItem(row, 0, QTableWidgetItem(pid))
            self.fp_table.setItem(row, 1, QTableWidgetItem("0.000"))

    def _collect_fixed_points(self):
        fixed = {}
        for row in range(self.fp_table.rowCount()):
            pid_item = self.fp_table.item(row, 0)
            h_item   = self.fp_table.item(row, 1)
            if pid_item and h_item:
                pid = pid_item.text().strip()
                try:
                    h = float(h_item.text().strip())
                    if pid:
                        fixed[pid] = h
                except ValueError:
                    pass
        return fixed

    def _run_adjustment(self):
        fixed_points = self._collect_fixed_points()
        if not fixed_points:
            QMessageBox.warning(self, "Fixed Points",
                                "Please enter at least one fixed point with a known height.")
            return
        if not self.lines:
            QMessageBox.warning(self, "No Lines", "No leveling lines loaded.")
            return

        try:
            if self.rb_parametric.isChecked():
                self._run_parametric(fixed_points)
            else:
                self._run_conditional(fixed_points)
        except Exception as exc:
            import traceback
            QMessageBox.critical(self, "Adjustment Error",
                                 f"{exc}\n\n{traceback.format_exc()}")

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
        """
        True Conditional (Bv+W) adjustment.
        n_conditions = n_loops + (n_BM - 1).
        B matrix uses +1/-1 signs based on line direction vs loop traversal.
        """
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
                "No conditions could be formed.\n"
                "Need loops or at least 2 fixed benchmarks.\n"
                "Falling back to Parametric method."
            )
            self._run_parametric(fixed_points)
            return

        if expected_conditions >= len(active_lines):
            QMessageBox.information(
                self, "Conditional Adjustment",
                "Too many conditions for the number of observations.\n"
                "Falling back to Parametric method."
            )
            self._run_parametric(fixed_points)
            return

        adjuster = ConditionalAdjuster(check_stability=self.chk_stability.isChecked())
        result = adjuster.adjust_loops(active_lines, loop_indices, fixed_points)
        self.result = result
        self._populate_results(result, fixed_points, method="Conditional (Bv + W)")




    # ------------------------------------------------------------------ #
    # Result display
    # ------------------------------------------------------------------ #

    def _populate_results(self, result, fixed_points, method):
        dof = max(0, len(result.residuals) - (
            len(result.adjusted_heights) - len(fixed_points)
        ))
        sigma_sq = result.mse_unit_weight ** 2

        # ── Summary tab ──
        summary = (
            f"{'─'*50}\n"
            f"Method          : {method}\n"
            f"{'─'*50}\n"
            f"σ₀²             : {sigma_sq:.6f}\n"
            f"σ₀  (mm)        : {result.mse_unit_weight * 1000:.4f}\n"
            f"Degrees of Freedom : {dof}\n"
            f"Iterations      : {result.iteration}\n"
            f"K Coefficient   : {result.k_coefficient:.4f}\n"
            f"Total Distance  : {result.total_distance_km:.3f} km\n"
            f"Fixed Points    : {len(fixed_points)}\n"
            f"Adjusted Points : {len(result.adjusted_heights) - len(fixed_points)}\n"
            f"{'─'*50}\n"
        )
        self.summary_text.setPlainText(summary)

        # ── Heights tab ──
        self.heights_table.setRowCount(0)
        for pid, h in sorted(result.adjusted_heights.items()):
            is_fixed = pid in fixed_points
            sigma_mm = result.mse_heights.get(pid, 0.0) * 1000
            corr_mm  = "—" if is_fixed else f"{(h - fixed_points.get(pid, h)) * 1000:.2f}"
            row = self.heights_table.rowCount()
            self.heights_table.insertRow(row)
            for col, val in enumerate([pid, f"{h:.5f}", corr_mm, f"{sigma_mm:.3f}"]):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                if is_fixed:
                    item.setBackground(QColor(173, 216, 230))
                self.heights_table.setItem(row, col, item)

        # ── Residuals tab ──
        self.residuals_table.setRowCount(0)
        for key, res_mm in sorted(result.residuals.items()):
            row = self.residuals_table.rowCount()
            self.residuals_table.insertRow(row)
            for col, val in enumerate([key, f"{res_mm:.3f}"]):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                if abs(res_mm) > 5.0:
                    item.setBackground(QColor(255, 200, 100))
                self.residuals_table.setItem(row, col, item)

        # ── Matrix diagnostics tab ──
        diag = (
            f"Normal Matrix Diagnostics\n{'─'*40}\n"
            f"Observations (n)  : {len(result.residuals)}\n"
            f"Unknowns (u)      : {len(result.adjusted_heights) - len(fixed_points)}\n"
            f"Redundancy (r)    : {dof}\n"
            f"VᵀPV              : {sigma_sq * dof:.6f}\n"
            f"σ₀²               : {sigma_sq:.8f}\n"
            f"σ₀                : {result.mse_unit_weight:.8f} m\n"
            f"K                 : {result.k_coefficient:.6f} mm/√km\n"
        )
        self.matrix_text.setPlainText(diag)

        self.tabs.setCurrentIndex(0)
