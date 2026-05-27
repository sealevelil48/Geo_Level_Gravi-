"""
geo_level_dockwidget.py
QGIS Dock Widget for Geo Level Gravi — 4-tab layout matching app.py.

Left panel  : file list + Add Files + Toggle Dir/Use + class selector
Right panel : QTabWidget
    Tab 0  Line Details   — setup table for selected line
    Tab 1  Validation     — all-lines table (File/Start/End/Setups/Dist/dH/Status/Details)
                            + Toggle Direction / Toggle Use / Refresh buttons
    Tab 2  Analysis       — read-only QTextEdit for loop/double-run output
    Tab 3  Log            — read-only QTextEdit for general log messages
"""

import os
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QTabWidget, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QTextEdit, QSplitter, QHeaderView,
    QAbstractItemView, QGroupBox, QComboBox, QFileDialog
)
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont


CLASS_DESCRIPTIONS = {
    "H1": "H1 -- +/-3 mm*sqrt(km)  |  BFFB  |  sight <= 30 m",
    "H2": "H2 -- +/-5 mm*sqrt(km)  |  BFFB  |  sight <= 40 m",
    "H3": "H3 -- +/-10 mm*sqrt(km) |  BFFB  |  sight <= 50 m  (default)",
    "H4": "H4 -- +/-20 mm*sqrt(km) |  BF    |  sight <= 80 m",
    "H5": "H5 -- +/-30 mm*sqrt(km) |  BF    |  sight <= 100 m",
    "H6": "H6 -- +/-60 mm*sqrt(km) |  BF    |  sight <= 100 m",
}

VAL_COLS = ["File", "Start", "End", "Setups", "Distance (m)", "dH (m)", "Status", "Details"]


class GeoLevelDockWidget(QDockWidget):
    """Main dock widget."""

    line_selected         = pyqtSignal(int)
    adjust_line_requested = pyqtSignal(int)
    lsa_requested         = pyqtSignal()
    files_added           = pyqtSignal(list, str, str)
    double_runs_requested = pyqtSignal()
    loops_requested       = pyqtSignal()
    enhanced_lsa_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Geo Level Gravi", parent)
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.setMinimumWidth(760)

        self._lines       = []
        self._val_results = []
        self._current_idx = -1

        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QWidget()
        self.setWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([220, 540])

    # ── Left panel ──────────────────────────────────────────────────── #

    def _build_left_panel(self):
        panel = QWidget()
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(0, 0, 0, 0)

        lbl = QLabel("Leveling Lines")
        lbl.setStyleSheet("font-weight: bold;")
        vbox.addWidget(lbl)

        self.line_list = QListWidget()
        self.line_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.line_list.currentRowChanged.connect(self._on_line_selected)
        vbox.addWidget(self.line_list)

        btn_add = QPushButton("+ Add Files")
        btn_add.clicked.connect(self._on_add_files)
        vbox.addWidget(btn_add)

        toggle_row = QHBoxLayout()
        self.btn_toggle_dir = QPushButton("Toggle Dir")
        self.btn_toggle_dir.setToolTip("Reverse direction of selected line")
        self.btn_toggle_dir.setEnabled(False)
        self.btn_toggle_dir.clicked.connect(self._on_toggle_dir)
        toggle_row.addWidget(self.btn_toggle_dir)

        self.btn_toggle_use = QPushButton("Toggle Use")
        self.btn_toggle_use.setToolTip("Include / exclude selected line from adjustment")
        self.btn_toggle_use.setEnabled(False)
        self.btn_toggle_use.clicked.connect(self._on_toggle_use)
        toggle_row.addWidget(self.btn_toggle_use)
        vbox.addLayout(toggle_row)

        grp = QGroupBox("Precision Class")
        hbox = QHBoxLayout(grp)
        self.class_combo = QComboBox()
        self.class_combo.addItems(["H1", "H2", "H3", "H4", "H5", "H6"])
        self.class_combo.setCurrentText("H3")
        self.class_combo.setFixedWidth(60)
        hbox.addWidget(self.class_combo)
        self.class_lbl = QLabel(CLASS_DESCRIPTIONS["H3"])
        self.class_lbl.setWordWrap(True)
        self.class_combo.currentTextChanged.connect(
            lambda c: self.class_lbl.setText(CLASS_DESCRIPTIONS.get(c, ""))
        )
        hbox.addWidget(self.class_lbl)
        vbox.addWidget(grp)

        return panel

    # ── Right panel ─────────────────────────────────────────────────── #

    def _build_right_panel(self):
        panel = QWidget()
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(0, 0, 0, 0)

        self.tabs = QTabWidget()
        vbox.addWidget(self.tabs)

        self.tabs.addTab(self._build_details_tab(),    "Line Details")
        self.tabs.addTab(self._build_validation_tab(), "Validation")
        self.tabs.addTab(self._build_analysis_tab(),   "Analysis")
        self.tabs.addTab(self._build_log_tab(),        "Log")

        return panel

    # ── Tab 0: Line Details ─────────────────────────────────────────── #

    def _build_details_tab(self):
        w = QWidget()
        vbox = QVBoxLayout(w)

        self.line_info_lbl = QLabel("No line selected")
        self.line_info_lbl.setStyleSheet("font-weight: bold; padding: 2px;")
        vbox.addWidget(self.line_info_lbl)

        self.setup_table = QTableWidget(0, 6)
        self.setup_table.setHorizontalHeaderLabels(
            ["#", "From", "To", "Backsight (m)", "Foresight (m)", "Distance (m)"]
        )
        self.setup_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.setup_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setup_table.setAlternatingRowColors(True)
        vbox.addWidget(self.setup_table)

        btn_row = QHBoxLayout()
        self.btn_adjust_line = QPushButton("Adjust This Line")
        self.btn_adjust_line.setEnabled(False)
        self.btn_adjust_line.clicked.connect(self._on_adjust_line)
        btn_row.addWidget(self.btn_adjust_line)

        self.btn_lsa = QPushButton("LSA -- Full Network Adjustment")
        self.btn_lsa.setEnabled(False)
        self.btn_lsa.clicked.connect(self.lsa_requested.emit)
        btn_row.addWidget(self.btn_lsa)
        vbox.addLayout(btn_row)

        return w

    # ── Tab 1: Validation ───────────────────────────────────────────── #

    def _build_validation_tab(self):
        w = QWidget()
        vbox = QVBoxLayout(w)

        # Toolbar buttons
        btn_row = QHBoxLayout()
        btn_tog_dir = QPushButton("Toggle Direction")
        btn_tog_dir.clicked.connect(self._on_toggle_dir)
        btn_row.addWidget(btn_tog_dir)

        btn_tog_use = QPushButton("Toggle Use")
        btn_tog_use.clicked.connect(self._on_toggle_use)
        btn_row.addWidget(btn_tog_use)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._refresh_val_table)
        btn_row.addWidget(btn_refresh)

        btn_export_xl = QPushButton("Export to Excel")
        btn_export_xl.setToolTip("Export validation table to .xlsx")
        btn_export_xl.clicked.connect(self._export_validation_to_excel)
        btn_row.addWidget(btn_export_xl)

        btn_row.addStretch()
        vbox.addLayout(btn_row)

        # All-lines validation table
        self.val_table = QTableWidget(0, len(VAL_COLS))
        self.val_table.setHorizontalHeaderLabels(VAL_COLS)
        self.val_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.val_table.horizontalHeader().setStretchLastSection(True)
        self.val_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.val_table.setAlternatingRowColors(True)
        self.val_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.val_table.itemSelectionChanged.connect(self._on_val_table_selection_changed)
        vbox.addWidget(self.val_table)

        # Per-line detail text
        self.val_detail_text = QTextEdit()
        self.val_detail_text.setReadOnly(True)
        self.val_detail_text.setMaximumHeight(100)
        self.val_detail_text.setFont(QFont("Courier New", 8))
        self.val_detail_text.setPlaceholderText("Select a row to see validation details...")
        vbox.addWidget(self.val_detail_text)

        return w

    # ── Tab 2: Analysis ─────────────────────────────────────────────── #

    def _build_analysis_tab(self):
        w = QWidget()
        vbox = QVBoxLayout(w)

        # Action buttons row
        btn_row = QHBoxLayout()
        btn_dr = QPushButton("Detect Double-Runs")
        btn_dr.clicked.connect(self.double_runs_requested.emit)
        btn_row.addWidget(btn_dr)

        btn_loops = QPushButton("Find Loops")
        btn_loops.clicked.connect(self.loops_requested.emit)
        btn_row.addWidget(btn_loops)

        btn_lsa = QPushButton("Network Adjustment (LSA)")
        btn_lsa.clicked.connect(self.lsa_requested.emit)
        btn_row.addWidget(btn_lsa)

        btn_elsa = QPushButton("Enhanced LSA")
        btn_elsa.clicked.connect(self.enhanced_lsa_requested.emit)
        btn_row.addWidget(btn_elsa)
        vbox.addLayout(btn_row)

        # Results sub-tabs
        self.analysis_tabs = QTabWidget()
        vbox.addWidget(self.analysis_tabs)

        # Sub-tab: Double-Runs
        dr_widget = QWidget()
        dr_vbox = QVBoxLayout(dr_widget)
        self.double_run_table = QTableWidget(0, 8)
        self.double_run_table.setHorizontalHeaderLabels([
            "Pair", "Forward File", "Return File",
            "Mean dH (m)", "Misclosure (mm)", "Tolerance (mm)", "Status", "Reason"
        ])
        self.double_run_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.double_run_table.horizontalHeader().setStretchLastSection(True)
        self.double_run_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.double_run_table.setAlternatingRowColors(True)
        dr_vbox.addWidget(self.double_run_table)
        self.analysis_tabs.addTab(dr_widget, "Double-Runs")

        # Sub-tab: Loops
        loop_widget = QWidget()
        loop_vbox = QVBoxLayout(loop_widget)
        self.loop_table = QTableWidget(0, 6)
        self.loop_table.setHorizontalHeaderLabels([
            "Loop ID", "Path", "Distance (m)",
            "Misclosure (mm)", "Tolerance (mm)", "Status"
        ])
        self.loop_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.loop_table.horizontalHeader().setStretchLastSection(True)
        self.loop_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.loop_table.setAlternatingRowColors(True)
        loop_vbox.addWidget(self.loop_table)
        self.analysis_tabs.addTab(loop_widget, "Loops")

        return w

    # ── Tab 3: Log ──────────────────────────────────────────────────── #

    def _build_log_tab(self):
        w = QWidget()
        vbox = QVBoxLayout(w)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Courier New", 9))
        self.log_text.setPlaceholderText("Processing log messages appear here...")
        vbox.addWidget(self.log_text)

        btn_clear = QPushButton("Clear Log")
        btn_clear.clicked.connect(self.log_text.clear)
        vbox.addWidget(btn_clear)
        return w

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def load_lines(self, lines, val_results):
        """Populate left list and validation table."""
        self._lines       = lines
        self._val_results = val_results

        # Left list
        self.line_list.clear()
        for i, line in enumerate(lines):
            used = getattr(line, "is_used", True)
            excl = "" if used else " [EXCL]"
            label = line.start_point + " -> " + line.end_point + "  [" + line.filename + "]" + excl
            self.line_list.addItem(label)
            if i < len(val_results):
                _, vr = val_results[i]
                color = "#2e7d32" if (vr.is_valid and used) else ("#888888" if not used else "#c62828")
                self.line_list.item(i).setForeground(QColor(color))

        if lines:
            self.btn_lsa.setEnabled(True)
            self.line_list.setCurrentRow(0)

        self._refresh_val_table()

    def populate_line_details(self, line):
        """Fill the Line Details tab for a given LevelingLine."""
        dist_str = "{:.2f}".format(line.total_distance)
        dh_str   = "{:.4f}".format(line.total_height_diff)
        self.line_info_lbl.setText(
            "Line: " + (line.start_point or "?") + " -> " + (line.end_point or "?")
            + "  |  Dist: " + dist_str + " m  |  dH: " + dh_str + " m"
        )
        self._populate_setup_table(line)

    def update_validation_tab(self, line):
        """Sync validation detail text when a line is selected."""
        for i, ln in enumerate(self._lines):
            if ln is line and i < len(self._val_results):
                _, vr = self._val_results[i]
                self._show_val_detail(vr)
                return

    def show_analysis_result(self, text):
        """Append text to the Log tab (analysis text output)."""
        self.log_text.append(text)
        self.tabs.setCurrentIndex(3)  # Log tab

    def show_adjustment_result(self, text):
        """Append text to the Log tab (backward-compat alias)."""
        self.log_text.append(text)
        self.tabs.setCurrentIndex(3)

    def log(self, text):
        """Append a message to the Log tab."""
        self.log_text.append(text)

    def get_selected_class(self):
        return self.class_combo.currentText()

    def get_current_index(self):
        return self.line_list.currentRow()

    def select_line_by_filename(self, filename):
        for i, line in enumerate(self._lines):
            if (os.path.basename(line.filename) == os.path.basename(filename)
                    or line.filename == filename):
                self.line_list.blockSignals(True)
                self.line_list.setCurrentRow(i)
                self.line_list.blockSignals(False)
                self._current_idx = i
                self.populate_line_details(line)
                self.tabs.setCurrentIndex(0)
                return

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    def _refresh_val_table(self):
        """Rebuild the Validation tab table from current lines + results."""
        self.val_table.setRowCount(0)
        for i, line in enumerate(self._lines):
            row = self.val_table.rowCount()
            self.val_table.insertRow(row)

            used   = getattr(line, "is_used", True)
            is_valid = True
            details  = ""
            if i < len(self._val_results):
                _, vr = self._val_results[i]
                is_valid = vr.is_valid
                errs = list(vr.errors) + list(vr.warnings)
                details = errs[0] if errs else ("OK" if is_valid else "See errors")

            status_str = "VALID" if is_valid else "INVALID"
            if not used:
                status_str = "EXCLUDED"

            dist_str = "{:.1f}".format(line.total_distance)
            dh_str   = "{:.4f}".format(line.total_height_diff)

            values = [
                os.path.basename(line.filename),
                line.start_point or "",
                line.end_point   or "",
                str(len(line.setups)),
                dist_str,
                dh_str,
                status_str,
                details,
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                if not used:
                    item.setForeground(QColor("#888888"))
                elif is_valid:
                    if col == 6:
                        item.setForeground(QColor("#2e7d32"))
                else:
                    if col == 6:
                        item.setForeground(QColor("#c62828"))
                self.val_table.setItem(row, col, item)

    def _show_val_detail(self, vr):
        lines_out = []
        for err in vr.errors:
            lines_out.append("ERROR: " + err)
        for warn in vr.warnings:
            lines_out.append("WARN:  " + warn)
        self.val_detail_text.setPlainText(
            "\n".join(lines_out) if lines_out else "No issues found."
        )

    def _populate_setup_table(self, line):
        self.setup_table.setRowCount(len(line.setups))
        for row, s in enumerate(line.setups):
            dist = ((s.distance_back or 0) + (s.distance_fore or 0)) / 2
            bs = "{:.5f}".format(s.backsight_reading) if s.backsight_reading is not None else "--"
            fs = "{:.5f}".format(s.foresight_reading) if s.foresight_reading is not None else "--"
            for col, val in enumerate([
                str(s.setup_number), s.from_point, s.to_point,
                bs, fs, "{:.2f}".format(dist)
            ]):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                self.setup_table.setItem(row, col, item)

    # ------------------------------------------------------------------ #
    # Slots
    # ------------------------------------------------------------------ #

    def _on_line_selected(self, idx):
        if idx < 0 or idx >= len(self._lines):
            return
        self._current_idx = idx
        line = self._lines[idx]
        self.populate_line_details(line)
        if idx < len(self._val_results):
            _, vr = self._val_results[idx]
            self._show_val_detail(vr)
        self.btn_adjust_line.setEnabled(True)
        self.btn_toggle_dir.setEnabled(True)
        self.btn_toggle_use.setEnabled(True)
        # Sync val table selection
        self.val_table.blockSignals(True)
        self.val_table.selectRow(idx)
        self.val_table.blockSignals(False)
        self.line_selected.emit(idx)

    def _on_val_table_selection_changed(self):
        row = self.val_table.currentRow()
        if row < 0 or row >= len(self._lines):
            return
        self.line_list.blockSignals(True)
        self.line_list.setCurrentRow(row)
        self.line_list.blockSignals(False)
        self._current_idx = row
        self.populate_line_details(self._lines[row])
        if row < len(self._val_results):
            _, vr = self._val_results[row]
            self._show_val_detail(vr)

    def _on_add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Measurement Files", "",
            "Geodetic Files (*.DAT *.dat *.RAW *.raw *.GSI *.gsi);;All Files (*)"
        )
        if files:
            cls = self.class_combo.currentText()
            output_dir = QFileDialog.getExistingDirectory(self, "Select Output Directory")
            if output_dir:
                self.files_added.emit(files, cls, output_dir)

    def _on_adjust_line(self):
        if self._current_idx >= 0:
            self.adjust_line_requested.emit(self._current_idx)

    def _on_toggle_dir(self):
        idx = self._current_idx
        if idx < 0 or idx >= len(self._lines):
            return
        line = self._lines[idx]
        try:
            line.toggle_direction()
        except AttributeError:
            line.start_point, line.end_point = line.end_point, line.start_point
            line.total_height_diff = -line.total_height_diff
        used  = getattr(line, "is_used", True)
        excl  = "" if used else " [EXCL]"
        label = line.start_point + " -> " + line.end_point + "  [" + line.filename + "]" + excl
        self.line_list.item(idx).setText(label)
        self.populate_line_details(line)
        self._refresh_val_table()
        self.log("Toggled direction: " + line.filename)

    def _on_toggle_use(self):
        idx = self._current_idx
        if idx < 0 or idx >= len(self._lines):
            return
        line = self._lines[idx]
        line.is_used = not getattr(line, "is_used", True)
        used  = line.is_used
        excl  = "" if used else " [EXCL]"
        label = line.start_point + " -> " + line.end_point + "  [" + line.filename + "]" + excl
        item  = self.line_list.item(idx)
        item.setText(label)
        item.setForeground(QColor("#2e7d32" if used else "#888888"))
        self._refresh_val_table()
        state = "included" if used else "excluded"
        self.log(line.filename + " marked as " + state)

    def _export_validation_to_excel(self):
        """Export the current validation table to a colour-coded .xlsx file."""
        if not self._lines:
            from qgis.PyQt.QtWidgets import QMessageBox
            QMessageBox.information(self, "Export to Excel", "No validation data to export.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Validation Table",
            "validation_results.xlsx",
            "Excel Workbook (*.xlsx)"
        )
        if not path:
            return

        try:
            import openpyxl
            from openpyxl.styles import Font, PatternFill, Alignment
        except ImportError:
            from qgis.PyQt.QtWidgets import QMessageBox
            QMessageBox.critical(
                self, "Export to Excel",
                "The 'openpyxl' library is required for Excel export.\n"
                "Install it with:  pip install openpyxl"
            )
            return

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Validation Results"

        # Header row — dark navy background, white bold text
        hdr_font = Font(bold=True, color="FFFFFF")
        hdr_fill = PatternFill(start_color="2C3E50", end_color="2C3E50", fill_type="solid")
        for col_idx, col_name in enumerate(VAL_COLS, start=1):
            cell = ws.cell(row=1, column=col_idx, value=col_name)
            cell.font = hdr_font
            cell.fill = hdr_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")

        # Data rows — colour by status
        green_fill = PatternFill(start_color="C8E6C9", end_color="C8E6C9", fill_type="solid")
        red_fill   = PatternFill(start_color="FFCDD2", end_color="FFCDD2", fill_type="solid")
        grey_fill  = PatternFill(start_color="E0E0E0", end_color="E0E0E0", fill_type="solid")

        for row_idx in range(self.val_table.rowCount()):
            status_item = self.val_table.item(row_idx, 6)
            status_text = status_item.text() if status_item else ""
            row_fill = (green_fill if status_text == "VALID"
                        else grey_fill if status_text == "EXCLUDED"
                        else red_fill)

            for col_idx in range(self.val_table.columnCount()):
                tbl_item = self.val_table.item(row_idx, col_idx)
                value = tbl_item.text() if tbl_item else ""
                cell = ws.cell(row=row_idx + 2, column=col_idx + 1, value=value)
                cell.fill = row_fill
                cell.alignment = Alignment(horizontal="center", vertical="center",
                                           wrap_text=True)

        # Auto-fit column widths (capped at 60)
        for col_cells in ws.columns:
            max_len = max((len(str(c.value or "")) for c in col_cells), default=10)
            ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 4, 60)

        wb.save(path)
        self.log("Validation table exported to: " + path)
