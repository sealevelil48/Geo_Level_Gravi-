"""
geo_level_enhanced_lsa_dialog.py
The single adjustment window of the plugin ("Network Adjustment (LSA)").

Left panel
    Method (Parametric V = Ax - L / Conditional Bv + W = 0)
    Lines in the adjustment (tick / untick; opened from "Adjust This Line"
    only that line is ticked)
    Fixed points (Auto-Select, Fetch from DB, heights from .FA0/.FA1 files)
    Options: Step 1 averaging, weights, stability check
    Buttons: Prepare (Steps 1-2) | Run | Apply to Map | Export FA0 / FA1 / PDF

Right panel tabs
    Summary | Step 1: Observations | Step 2: Approximate Heights |
    Adjusted Heights | Residuals | Line Adjustment | Matrix Diagnostics
"""

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QRadioButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
    QTextEdit, QVBoxLayout, QWidget,
)

_FIXED_BG = QColor(173, 216, 230)
_WARN_BG = QColor(255, 200, 100)
_BAD_BG = QColor(255, 170, 170)

_WEIGHTS = [("Distance  p = n/L(km)", "distance"),
            ("Setups  p = n/setups", "setups"),
            ("Equal  p = n", "equal")]


def _item(text, align=Qt.AlignCenter, bg=None, editable=False):
    it = QTableWidgetItem(str(text))
    it.setTextAlignment(align)
    if not editable:
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if bg is not None:
        it.setBackground(bg)
    return it


class GeoLevelEnhancedLSADialog(QDialog):
    """
    Parameters
    ----------
    lines          : list of LevelingLine (all loaded lines)
    val_results    : list of (LevelingLine, ValidationResult)
    leveling_class : 'H1'..'H6' - tolerances k*sqrt(L)
    known_heights  : {point: height} from .FA0/.FA1 files (used for Auto-Select
                     and as Step 2 seeds)
    selected_lines : lines to tick initially (single-line mode); None = all
                     eligible lines
    project_name   : name written into exported FA0 / FA1 files
    output_dir     : default folder for exports
    """

    def __init__(self, lines, parent=None, val_results=None, leveling_class="H3",
                 known_heights=None, selected_lines=None, project_name="geo_level",
                 output_dir="", approx_heights=None):
        super().__init__(parent)
        self.lines = list(lines or [])
        self._val_results = val_results or []
        self.leveling_class = leveling_class or "H3"
        self.known_heights = dict(known_heights or {})   # FA0/FA1 fixed points (header order)
        self.approx_heights = dict(approx_heights or {})  # other FA0/FA1 heights (approximate)
        self._run_obs = None                              # observations of the last run
        self.run_lines = []                               # lines of the last run (map: blue)
        self.project_name = project_name or "geo_level"
        self.output_dir = output_dir or ""
        self._selected_ids = ({id(l) for l in selected_lines}
                              if selected_lines else None)
        self.result = None
        self.fixed_points = {}
        self._observations = []
        self._merge_report = []
        self.setWindowTitle("Network Adjustment (LSA) / כיוון רשת")
        self.resize(1250, 760)
        self._build_ui()
        self._fill_lines_table()
        if self._selected_ids:
            self._auto_select_fixed(quiet=True)

    # ================================================================== #
    # UI
    # ================================================================== #
    def _build_ui(self):
        root = QHBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([430, 820])

    def _build_left_panel(self):
        panel = QWidget()
        vbox = QVBoxLayout(panel)

        grp_m = QGroupBox("Adjustment Method / שיטת כיוון")
        vm = QVBoxLayout(grp_m)
        self.rb_parametric = QRadioButton("Parametric (Observations)  V = Ax − L")
        self.rb_conditional = QRadioButton("Conditional (Constraints)  Bv + W = 0")
        self.rb_parametric.setChecked(True)
        vm.addWidget(self.rb_parametric)
        vm.addWidget(self.rb_conditional)
        vbox.addWidget(grp_m)

        grp_l = QGroupBox("Lines in adjustment / קווים בכיוון")
        vl = QVBoxLayout(grp_l)
        self.lines_table = QTableWidget(0, 5)
        self.lines_table.setHorizontalHeaderLabels(["Use", "Line", "From → To", "dH (m)", "Status"])
        self.lines_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.lines_table.horizontalHeader().setStretchLastSection(True)
        self.lines_table.setMinimumHeight(150)
        self.lines_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        vl.addWidget(self.lines_table)
        hb = QHBoxLayout()
        b_all = QPushButton("All eligible")
        b_all.clicked.connect(lambda: self._check_all(True))
        b_none = QPushButton("None")
        b_none.clicked.connect(lambda: self._check_all(False))
        self.lbl_lines = QLabel("")
        hb.addWidget(b_all)
        hb.addWidget(b_none)
        hb.addWidget(self.lbl_lines, 1)
        vl.addLayout(hb)
        vbox.addWidget(grp_l, 2)

        grp_fp = QGroupBox("Fixed Points / נקודות קבועות")
        vfp = QVBoxLayout(grp_fp)
        self.fp_table = QTableWidget(0, 2)
        self.fp_table.setHorizontalHeaderLabels(["Point ID", "Known Height (m)"])
        self.fp_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.fp_table.setMinimumHeight(120)
        self.fp_table.setToolTip("Only rows with a height value are held fixed.")
        vfp.addWidget(self.fp_table)
        g = QGridLayout()
        for i, (txt, slot, tip) in enumerate([
            ("+ Add Row", self._add_fp_row, ""),
            ("− Remove", self._del_fp_row, ""),
            ("Auto-Select", self._auto_select_fixed,
             "Points of the ticked lines that have a known height\n"
             "(from .FA0/.FA1 files or the national DB)"),
            ("Fetch from DB", self._fetch_from_db,
             "Fill missing heights from the national benchmark database"),
        ]):
            b = QPushButton(txt)
            b.clicked.connect(lambda _=False, s=slot: s())
            if tip:
                b.setToolTip(tip)
            g.addWidget(b, 0, i)
        vfp.addLayout(g)
        vbox.addWidget(grp_fp, 1)

        grp_o = QGroupBox("Options / אפשרויות")
        vo = QVBoxLayout(grp_o)
        self.chk_merge = QCheckBox("Step 1: average repeated runs (forward / return)")
        self.chk_merge.setChecked(True)
        self.chk_merge.setToolTip("Each height difference enters the adjustment once:\n"
                                  "dH = (dH_fwd − dH_ret)/2, checked against k·√L "
                                  "(L = one-direction length).")
        vo.addWidget(self.chk_merge)
        hw = QHBoxLayout()
        hw.addWidget(QLabel("Weights:"))
        self.cmb_weights = QComboBox()
        for label, _key in _WEIGHTS:
            self.cmb_weights.addItem(label)
        hw.addWidget(self.cmb_weights, 1)
        hw.addWidget(QLabel("Class:"))
        self.cmb_class = QComboBox()
        self.cmb_class.addItems(["H1", "H2", "H3", "H4", "H5", "H6"])
        self.cmb_class.setCurrentText(self.leveling_class)
        hw.addWidget(self.cmb_class)
        vo.addLayout(hw)
        self.chk_stability = QCheckBox("Matrix stability check")
        self.chk_stability.setChecked(True)
        vo.addWidget(self.chk_stability)
        vbox.addWidget(grp_o)

        btn_prep = QPushButton("Prepare (Steps 1–2) / הכנה")
        btn_prep.clicked.connect(self._prepare)
        vbox.addWidget(btn_prep)
        btn_run = QPushButton("▶  Run Adjustment / הפעל כיוון")
        btn_run.setFixedHeight(36)
        btn_run.setStyleSheet("font-weight: bold; background: #1565c0; color: white;")
        btn_run.clicked.connect(self._run_adjustment)
        vbox.addWidget(btn_run)

        row = QHBoxLayout()
        self.btn_apply = QPushButton("Apply to Map")
        self.btn_apply.setToolTip("Write adjusted heights to the map (adj_height) "
                                  "and keep this result in the project")
        self.btn_apply.clicked.connect(self._apply)
        self.btn_fa0 = QPushButton("Export FA0…")
        self.btn_fa0.clicked.connect(self._export_fa0)
        self.btn_fa1 = QPushButton("Export FA1…")
        self.btn_fa1.clicked.connect(self._export_fa1)
        self.btn_pdf = QPushButton("PDF Report…")
        self.btn_pdf.clicked.connect(self._export_pdf)
        for b in (self.btn_apply, self.btn_fa0, self.btn_fa1, self.btn_pdf):
            row.addWidget(b)
        vbox.addLayout(row)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.reject)
        vbox.addWidget(btn_close)
        self._set_result_buttons(False)
        return panel

    def _build_right_panel(self):
        panel = QWidget()
        vbox = QVBoxLayout(panel)
        self.tabs = QTabWidget()
        vbox.addWidget(self.tabs)

        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setFont(QFont("Courier New", 9))
        self.summary_text.setPlaceholderText("Prepare or run the adjustment to see results…")
        self.tabs.addTab(self.summary_text, "Summary / סיכום")

        self.obs_table = self._table(["From", "To", "Mean dH (m)", "L (m)", "Runs",
                                      "Δ runs (mm)", "Allowed (mm)", "Check", "Sources"])
        self.tabs.addTab(self.obs_table, "Step 1: Observations")

        self.approx_table = self._table(["Point ID", "Approx. Height (m)", "Carried from",
                                         "Seed type", "Path (km)"])
        self.tabs.addTab(self.approx_table, "Step 2: Approximate Heights")

        self.heights_table = self._table(["Point ID", "Adjusted (m)", "Approx. (m)",
                                          "Correction (mm)", "Std Dev (mm)",
                                          "95% (mm)"])
        self.tabs.addTab(self.heights_table, "Adjusted Heights / גבהים")

        self.residuals_table = self._table(["From", "To", "Measured dH (m)",
                                            "Adjusted dH (m)", "Residual (mm)",
                                            "L (m)", "Weight"])
        self.tabs.addTab(self.residuals_table, "Residuals / שאריות")

        self.lineadj_text = QTextEdit()
        self.lineadj_text.setReadOnly(True)
        self.lineadj_text.setFont(QFont("Courier New", 9))
        self.lineadj_text.setPlaceholderText(
            "Single line between two known heights: tick one line, give both "
            "end heights and Run.")
        self.tabs.addTab(self.lineadj_text, "Line Adjustment / תיאום קו")

        self.matrix_text = QTextEdit()
        self.matrix_text.setReadOnly(True)
        self.matrix_text.setFont(QFont("Courier New", 8))
        self.tabs.addTab(self.matrix_text, "Matrix Diagnostics")
        return panel

    @staticmethod
    def _table(headers):
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setAlternatingRowColors(True)
        return t

    def _set_result_buttons(self, enabled):
        for b in (self.btn_apply, self.btn_fa1, self.btn_pdf):
            b.setEnabled(enabled)

    # ================================================================== #
    # Lines
    # ================================================================== #
    def _validation_for(self, line):
        for ln, vr in self._val_results:
            if ln is line:
                return vr
        return None

    def _is_eligible(self, line) -> bool:
        """Used (not excluded) and either valid or forced valid by the manager.
        A line with no validation result is not eligible (fail-closed)."""
        if not getattr(line, "is_used", True):
            return False
        if getattr(line, "manager_override", False):
            return True
        if not self._val_results:
            return True
        vr = self._validation_for(line)
        return bool(vr and vr.is_valid)

    def _fill_lines_table(self):
        t = self.lines_table
        t.setRowCount(0)
        self._row_lines = []
        n_el = 0
        for line in self.lines:
            elig = self._is_eligible(line)
            n_el += elig
            r = t.rowCount()
            t.insertRow(r)
            chk = QTableWidgetItem()
            flags = Qt.ItemIsUserCheckable | Qt.ItemIsSelectable
            if elig:
                flags |= Qt.ItemIsEnabled
            chk.setFlags(flags)
            on = elig and (self._selected_ids is None or id(line) in self._selected_ids)
            chk.setCheckState(Qt.Checked if on else Qt.Unchecked)
            t.setItem(r, 0, chk)
            status = getattr(line.status, "value", str(line.status))
            if not getattr(line, "is_used", True):
                status = "excluded"
            bg = None if elig else QColor(235, 235, 235)
            t.setItem(r, 1, _item(line.filename, Qt.AlignLeft | Qt.AlignVCenter, bg))
            t.setItem(r, 2, _item(f"{line.start_point} → {line.end_point}", bg=bg))
            t.setItem(r, 3, _item(f"{line.total_height_diff:.5f}", bg=bg))
            t.setItem(r, 4, _item(status, bg=bg))
            self._row_lines.append(line)
        self.lbl_lines.setText(f"{n_el} eligible of {len(self.lines)} "
                               "(invalid / excluded lines are greyed)")

    def _check_all(self, on):
        for r in range(self.lines_table.rowCount()):
            it = self.lines_table.item(r, 0)
            if it.flags() & Qt.ItemIsEnabled:
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _active_lines(self):
        out = []
        for r, line in enumerate(self._row_lines):
            it = self.lines_table.item(r, 0)
            if it.checkState() == Qt.Checked and self._is_eligible(line):
                out.append(line)
        return out

    # ================================================================== #
    # Fixed points
    # ================================================================== #
    def _add_fp_row(self, pid="", height=""):
        r = self.fp_table.rowCount()
        self.fp_table.insertRow(r)
        self.fp_table.setItem(r, 0, QTableWidgetItem(pid))
        self.fp_table.setItem(r, 1, QTableWidgetItem(height))

    def _del_fp_row(self):
        r = self.fp_table.currentRow()
        if r >= 0:
            self.fp_table.removeRow(r)

    def _db(self):
        try:
            from geolevel_db_manager import get_db_manager
            mgr = get_db_manager()
            return mgr if mgr.is_configured() else None
        except Exception:
            return None

    def _db_height(self, mgr, pid):
        if mgr is None:
            return None
        try:
            rec = mgr.resolve_benchmark(pid)
            return None if rec is None or rec.gova_ort is None else float(rec.gova_ort)
        except Exception:
            return None

    def _auto_select_fixed(self, quiet=False):
        """Fixed points for the ticked lines.

        When the loaded FA0 / FA1 files define fixed points - the FIRST N
        points listed, N being the second number of the header
        ('36   2   K6177-64PU-65PU' -> 2) - exactly those are selected, in the
        file's order; other listed heights are approximate and never fixed.
        Only when no loaded file defines fixed points for these lines are DB
        heights offered.  For a single ticked line the two end points are
        always listed (single-line adjustment)."""
        lines = self._active_lines()
        on_lines = []
        for ln in lines:
            for p in (ln.start_point, ln.end_point):
                if p and p not in on_lines:
                    on_lines.append(p)
        file_fixed = [p for p in self.known_heights if p in on_lines]
        rows = []
        if file_fixed:
            rows = [(p, f"{self.known_heights[p]:.4f}") for p in file_fixed]
        else:
            mgr = self._db()
            if mgr is not None:
                try:
                    mgr.prefetch(sorted(on_lines))          # one batched lookup
                except Exception:
                    pass
            for p in sorted(on_lines):
                h = self._db_height(mgr, p)
                if h is not None:
                    rows.append((p, f"{h:.4f}"))
        if len(lines) == 1 and not rows:
            rows = [(lines[0].start_point, ""), (lines[0].end_point, "")]
        elif len(lines) == 1:
            have = {p for p, _ in rows}
            rows += [(p, "") for p in (lines[0].start_point, lines[0].end_point)
                     if p not in have]
        self.fp_table.setRowCount(0)
        for p, h in rows:
            self._add_fp_row(p, h)
        if not rows and not quiet:
            QMessageBox.information(
                self, "Auto-Select",
                "No point of the ticked lines is a fixed point of the loaded FA0 / FA1 "
                "files or has a height in the database.\nEnter the fixed heights manually.")

    def _fetch_from_db(self):
        mgr = self._db()
        if mgr is None:
            QMessageBox.information(self, "DB Not Configured",
                                    "Set up the database connection first via\n"
                                    "Settings → Database Connection…")
            return
        filled, missing = 0, []
        for r in range(self.fp_table.rowCount()):
            pid_it, h_it = self.fp_table.item(r, 0), self.fp_table.item(r, 1)
            pid = pid_it.text().strip() if pid_it else ""
            if not pid or (h_it and h_it.text().strip()):
                continue
            h = self._db_height(mgr, pid)
            if h is None:
                missing.append(pid)
            else:
                self.fp_table.setItem(r, 1, QTableWidgetItem(f"{h:.4f}"))
                filled += 1
        msg = f"Filled {filled} height(s) from DB."
        if missing:
            msg += "\nNo height in DB for: " + ", ".join(missing)
        QMessageBox.information(self, "DB Fetch Complete", msg)

    def _collect_fixed_points(self, warn=True):
        fixed, skipped = {}, []
        for r in range(self.fp_table.rowCount()):
            pid_it, h_it = self.fp_table.item(r, 0), self.fp_table.item(r, 1)
            pid = pid_it.text().strip() if pid_it else ""
            if not pid:
                continue
            txt = h_it.text().strip() if h_it else ""
            try:
                fixed[pid] = float(txt)
            except ValueError:
                skipped.append(pid)
        if skipped and warn:
            QMessageBox.information(
                self, "Fixed Points",
                "These rows have no valid height and are treated as unknown:\n"
                + ", ".join(skipped))
        return fixed

    # ================================================================== #
    # Steps 1-2
    # ================================================================== #
    def _weight_mode(self):
        return _WEIGHTS[self.cmb_weights.currentIndex()][1]

    def _build_observations(self, lines):
        from core_logic.engine.least_squares import lines_to_observations
        from core_logic.engine.network_prep import (
            MergedRun, merge_observations, tolerance_mm)
        cls = self.cmb_class.currentText()
        if self.chk_merge.isChecked():
            return merge_observations(lines, cls)
        obs = lines_to_observations(lines)
        report = [MergedRun(o.from_point, o.to_point, o.height_diff, o.distance, 1,
                            [o.height_diff], [o.source_file], None,
                            tolerance_mm(o.distance, cls), True) for o in obs]
        return obs, report

    def _prepare(self, quiet=False):
        """Run Steps 1 and 2 and show them. Returns (obs, fixed) or None."""
        from core_logic.engine.network_prep import approximate_heights
        lines = self._active_lines()
        if not lines:
            if not quiet:
                QMessageBox.warning(self, "No Lines", "Tick at least one eligible line.")
            return None
        fixed = self._collect_fixed_points(warn=not quiet)
        obs, report = self._build_observations(lines)
        self._observations, self._merge_report = obs, report
        self._fill_obs_table(report)
        known = dict(self.known_heights)
        approx = approximate_heights(obs, fixed, known)
        self._fill_approx_table(approx, fixed)
        n_bad = sum(not r.within_tolerance for r in report)
        n_rep = sum(r.n_runs > 1 for r in report)
        txt = (f"Step 1: {len(lines)} line(s) → {len(obs)} observation(s); "
               f"{n_rep} averaged from repeated runs; {n_bad} exceed k·√L.\n"
               f"Step 2: approximate heights for {len(approx)} point(s).\n")
        if any(a.seed_kind == "relative" for a in approx.values()):
            txt += ("⚠ Some groups have no known height: approximate heights there "
                    "are relative (0.000 at the first point).\n")
        if not quiet:
            self.summary_text.setPlainText(txt)
            self.tabs.setCurrentWidget(self.obs_table)
        return obs, fixed

    def _fill_obs_table(self, report):
        t = self.obs_table
        t.setRowCount(0)
        for r in report:
            row = t.rowCount()
            t.insertRow(row)
            bg = None if r.within_tolerance else _BAD_BG
            disc = "—" if r.discrepancy_mm is None else f"{r.discrepancy_mm:.2f}"
            check = "—" if r.discrepancy_mm is None else ("✓" if r.within_tolerance else "✗")
            vals = [r.from_point, r.to_point, f"{r.mean_dh:.5f}", f"{r.distance:.1f}",
                    r.n_runs, disc, f"{r.allowed_mm:.2f}", check, " + ".join(r.sources)]
            for c, v in enumerate(vals):
                t.setItem(row, c, _item(v, bg=bg))

    def _fill_approx_table(self, approx, fixed):
        t = self.approx_table
        t.setRowCount(0)
        for pid in sorted(approx):
            a = approx[pid]
            row = t.rowCount()
            t.insertRow(row)
            bg = _FIXED_BG if pid in fixed else (_WARN_BG if a.seed_kind == "relative" else None)
            for c, v in enumerate([pid, f"{a.height:.4f}", a.seed, a.seed_kind,
                                   f"{a.path_km:.3f}"]):
                t.setItem(row, c, _item(v, bg=bg))

    # ================================================================== #
    # Run
    # ================================================================== #
    def _run_adjustment(self):
        prep = self._prepare(quiet=True)
        if prep is None:
            QMessageBox.warning(self, "No Lines", "Tick at least one eligible line.")
            return
        obs, _f = prep
        fixed = self._collect_fixed_points(warn=True)
        if not fixed:
            QMessageBox.warning(self, "Fixed Points",
                                "Enter at least one fixed point with a known height.")
            return
        from core_logic.engine.network_prep import NetworkDatumError, _obs_fields
        lines = self._active_lines()

        # Single line with both ends known -> Directive G2 line adjustment
        pts = {p for o in obs for p in _obs_fields(o)[:2]}
        if pts and pts <= set(fixed):
            self._run_line_adjustment(lines, fixed)
            return

        cls = self.cmb_class.currentText()
        try:
            if self.rb_parametric.isChecked():
                from core_logic.engine.least_squares import LeastSquaresAdjuster
                adj = LeastSquaresAdjuster(check_stability=self.chk_stability.isChecked(),
                                           weight_mode=self._weight_mode(),
                                           leveling_class=cls)
            else:
                from core_logic.engine.least_squares import ConditionalAdjuster
                adj = ConditionalAdjuster(check_stability=self.chk_stability.isChecked(),
                                          weight_mode=self._weight_mode(),
                                          leveling_class=cls)
            result = adj.adjust(obs, fixed, known_heights=self.known_heights)
            self._run_obs = list(obs)
            self.run_lines = list(self._active_lines())
        except NetworkDatumError as exc:
            self.result = None
            self._set_result_buttons(False)
            self.summary_text.setPlainText("Adjustment not run.\n\n" + str(exc))
            self.tabs.setCurrentWidget(self.summary_text)
            QMessageBox.warning(self, "Network not connected to a fixed point", str(exc))
            return
        except Exception as exc:
            import traceback
            self.result = None
            self._set_result_buttons(False)
            QMessageBox.critical(self, "Adjustment Error",
                                 f"{exc}\n\n{traceback.format_exc()}")
            return
        self.result = result
        self.fixed_points = fixed
        self._populate_results(result, fixed)
        self._set_result_buttons(True)

    def _run_line_adjustment(self, lines, fixed):
        from core_logic.engine.network_prep import adjust_single_line
        cls = self.cmb_class.currentText()
        out = []
        for ln in lines:
            if ln.start_point not in fixed or ln.end_point not in fixed:
                continue
            r = adjust_single_line(ln, fixed[ln.start_point], fixed[ln.end_point], cls)
            out.append(f"Line {ln.filename}:  {ln.start_point} → {ln.end_point}")
            out.append(f"  Measured dH   : {r['measured_dh']:12.5f} m")
            out.append(f"  Known dH      : {r['known_dh']:12.5f} m")
            out.append(f"  Misclosure f  : {r['misclosure_mm']:9.2f} mm   "
                       f"allowed ±{r['allowed_mm']:.2f} mm ({cls}, L={r['length_m']/1000:.3f} km)  "
                       + ("✓ OK" if r["ok"] else "✗ EXCEEDED"))
            out.append("  v_i = −f · L_i / ΣL")
            out.append(f"  {'Setup':>5} {'L (m)':>9} {'dH (m)':>11} {'v (mm)':>8} "
                       f"{'dH corr (m)':>12} {'H (m)':>11}")
            for row in r["rows"]:
                out.append(f"  {row['setup']:>5} {row['length_m']:9.2f} "
                           f"{row['measured_dh']:11.5f} {row['correction_mm']:8.2f} "
                           f"{row['corrected_dh']:12.5f} {row['height']:11.4f}")
            out.append("")
        self.result = None
        self._set_result_buttons(False)
        self.lineadj_text.setPlainText("\n".join(out) or
                                       "Both end points of each ticked line must be fixed.")
        self.summary_text.setPlainText(
            "All points of the ticked line(s) are fixed: single-line adjustment "
            "(Directive G2, Appendix C) - see the Line Adjustment tab.")
        self.tabs.setCurrentWidget(self.lineadj_text)

    # ================================================================== #
    # Results
    # ================================================================== #
    def _populate_results(self, res, fixed):
        cls = self.cmb_class.currentText()
        n = len(res.observations)
        u = n - res.degrees_of_freedom if res.method == "parametric" else \
            len([p for p in res.adjusted_heights if p not in fixed])
        lines = [
            "─" * 60,
            f"Method              : {'Parametric  V = Ax − L' if res.method == 'parametric' else 'Conditional  Bv + W = 0'}",
            f"Weights             : {res.weight_mode}",
            "─" * 60,
            f"Observations (n)    : {n}",
            f"Unknown heights (u) : {u}",
            f"Fixed points        : {len(fixed)}",
            f"Degrees of freedom  : {res.degrees_of_freedom}",
            f"σ₀ (mm/√km)         : {res.mse_unit_weight * 1000:.3f}",
            f"Total length        : {res.total_distance_km:.3f} km",
            f"K coefficient       : {res.k_coefficient:.3f}",
            "Solution            : direct (linear model, no iterations)",
            "─" * 60,
        ]
        if res.benchmark_misclosures:
            lines.append(f"Misclosures between fixed points ({cls}, k·√L):")
            for b in res.benchmark_misclosures:
                lines.append(f"  {b['from']:>10} → {b['to']:<10} f = {b['misclosure_mm']:8.2f} mm"
                             f"   allowed ±{b['allowed_mm']:.2f} mm  "
                             + ("✓" if b["ok"] else "✗ EXCEEDED"))
        bad = [r for r in self._merge_report if not r.within_tolerance]
        if bad:
            lines.append(f"⚠ {len(bad)} repeated-run discrepancy(ies) exceed k·√L "
                         "(see Step 1 tab).")
        self.summary_text.setPlainText("\n".join(lines))

        t = self.heights_table
        t.setRowCount(0)
        for pid in sorted(res.adjusted_heights):
            h = res.adjusted_heights[pid]
            is_fixed = pid in fixed
            sd = res.mse_heights.get(pid, 0.0) * 1000
            approx = res.approximate_heights.get(pid, h)
            corr = "—" if is_fixed else f"{res.corrections.get(pid, 0.0) * 1000:.2f}"
            vals = [pid, f"{h:.5f}", f"{approx:.5f}", corr,
                    "—" if is_fixed else f"{sd:.2f}",
                    "—" if is_fixed else f"{1.96 * sd:.2f}"]
            row = t.rowCount()
            t.insertRow(row)
            for c, v in enumerate(vals):
                t.setItem(row, c, _item(v, bg=_FIXED_BG if is_fixed else None))

        t = self.residuals_table
        t.setRowCount(0)
        for o in res.observations:
            row = t.rowCount()
            t.insertRow(row)
            v = o["residual_mm"]
            allowed = _allowed(o["distance"], cls)
            bg = _WARN_BG if abs(v) > allowed else None
            vals = [o["from"], o["to"], f"{o['measured_dh']:.5f}", f"{o['adjusted_dh']:.5f}",
                    f"{v:.2f}", f"{o['distance']:.1f}", f"{o['weight']:.3f}"]
            for c, val in enumerate(vals):
                t.setItem(row, c, _item(val, bg=bg))

        self.matrix_text.setPlainText(
            f"Normal matrix diagnostics\n{'─' * 40}\n"
            f"n = {n}   u = {u}   r = n − u = {res.degrees_of_freedom}\n"
            f"VᵀPV = {res.mse_unit_weight ** 2 * res.degrees_of_freedom:.8f}\n"
            f"σ₀²  = {res.mse_unit_weight ** 2:.10f}\n"
            f"σ₀   = {res.mse_unit_weight * 1000:.4f} mm/√km\n"
            f"Std dev of heights: σ₀·√Qxx ; 95%: 1.96·σ\n")
        self.tabs.setCurrentWidget(self.summary_text)

    # ================================================================== #
    # Apply / export
    # ================================================================== #
    def _apply(self):
        if self.result is not None:
            self.accept()

    def _ask_path(self, title, default_name, filt):
        start = os.path.join(self.output_dir, default_name) if self.output_dir else default_name
        path, _ = QFileDialog.getSaveFileName(self, title, start, filt)
        return path

    def _export_fa0(self):
        prep = self._prepare(quiet=True)
        if prep is None:
            QMessageBox.warning(self, "Export FA0", "Tick at least one eligible line.")
            return
        obs, _ = prep
        fixed = self._collect_fixed_points(warn=False)
        path = self._ask_path("Export FA0", f"{self.project_name}.FA0", "FA0 (*.FA0 *.fa0)")
        if not path:
            return
        try:
            from core_logic.config.models import Benchmark
            from core_logic.exporters import FA0Exporter
            FA0Exporter().export(path, [Benchmark(point_id=k, height=v) for k, v in fixed.items()],
                                 obs, self.project_name, approx_heights=self.approx_heights)
            QMessageBox.information(self, "Export FA0", f"Written:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export FA0", str(exc))

    def _export_fa1(self):
        if self.result is None:
            return
        path = self._ask_path("Export FA1", f"{self.project_name}.FA1", "FA1 (*.FA1 *.fa1)")
        if not path:
            return
        try:
            from core_logic.config.models import Benchmark, MeasurementSummary
            from core_logic.exporters import FA1Exporter
            obs = self._run_obs or [
                MeasurementSummary(from_point=o["from"], to_point=o["to"],
                                   height_diff=o["measured_dh"], distance=o["distance"],
                                   num_setups=o.get("num_setups", 0) or 0, bf_diff=0.0,
                                   year_month="", source_file=o.get("source", ""))
                for o in self.result.observations]
            FA1Exporter().export(path, [Benchmark(point_id=k, height=v)
                                        for k, v in self.fixed_points.items()],
                                 obs, self.result, self.project_name,
                                 leveling_class=self.cmb_class.currentText(),
                                 approx_heights=self.approx_heights)
            QMessageBox.information(self, "Export FA1", f"Written:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export FA1", str(exc))

    def _export_pdf(self):
        if self.result is None:
            return
        path = self._ask_path("PDF Report", f"{self.project_name}_LSA.pdf", "PDF (*.pdf)")
        if not path:
            return
        try:
            from geo_level_reporter import GeoLevelReporter
            GeoLevelReporter(self.result, self.fixed_points, {
                "project_name": self.project_name,
                "class": self.cmb_class.currentText(),
            }).generate_pdf(path)
            QMessageBox.information(self, "PDF Report", f"Written:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "PDF Report", str(exc))


def _allowed(distance_m, cls):
    from core_logic.engine.network_prep import tolerance_mm
    return tolerance_mm(distance_m, cls)
