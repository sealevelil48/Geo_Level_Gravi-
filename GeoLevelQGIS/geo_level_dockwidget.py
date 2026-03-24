"""
geo_level_dockwidget.py
Professional QGIS Dock Widget for Geo Level Gravi plugin.

Layout
------
Left  : QListWidget — loaded leveling lines (files)
Right : QTabWidget
          Tab 0 "Line Details"  — QTableWidget with setups (BS, FS, Distance)
          Tab 1 "Validation"    — H1-H6 status + error list
          Tab 2 "Adjustment"    — Adjust This Line / LSA buttons
"""

import os
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QTabWidget, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QTextEdit, QSplitter, QHeaderView,
    QAbstractItemView, QGroupBox, QComboBox, QSizePolicy
)
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor


CLASS_DESCRIPTIONS = {
    "H1": "H1 — ±3 mm√km  |  BFFB  |  sight ≤ 30 m",
    "H2": "H2 — ±5 mm√km  |  BFFB  |  sight ≤ 40 m",
    "H3": "H3 — ±10 mm√km |  BFFB  |  sight ≤ 50 m  (default)",
    "H4": "H4 — ±20 mm√km |  BF    |  sight ≤ 80 m",
    "H5": "H5 — ±30 mm√km |  BF    |  sight ≤ 100 m",
    "H6": "H6 — ±60 mm√km |  BF    |  sight ≤ 100 m",
}

STATUS_COLORS = {
    "valid":               "#2e7d32",
    "invalid_endpoint":    "#c62828",
    "naming_error":        "#e65100",
    "exceeded_tolerance":  "#c62828",
    "incomplete":          "#f57f17",
}


class GeoLevelDockWidget(QDockWidget):
    """Main dock widget — replaces the popup dialog."""

    # Emitted when user selects a line in the list; carries the line index
    line_selected = pyqtSignal(int)
    # Emitted when user requests single-line adjustment
    adjust_line_requested = pyqtSignal(int)
    # Emitted when user requests full LSA
    lsa_requested = pyqtSignal()
    # Emitted to tell the plugin to run the full parse→validate→export pipeline
    run_requested = pyqtSignal(list, str, str)  # file_paths, class, output_dir
    # Emitted when user adds files via the + Add Files button
    files_added = pyqtSignal(list, str, str)    # file_paths, class, output_dir

    def __init__(self, parent=None):
        super().__init__("Geo Level Gravi — פילוס גיאודטי", parent)
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.setMinimumWidth(700)

        self._lines = []          # list of LevelingLine objects
        self._val_results = []    # list of (LevelingLine, ValidationResult)
        self._current_idx = -1

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QWidget()
        self.setWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)

        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([220, 480])

    def _build_left_panel(self) -> QWidget:
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

        # Add Files button
        from qgis.PyQt.QtWidgets import QFileDialog as _QFD
        self._qfd = _QFD
        btn_add = QPushButton("+ Add Files")
        btn_add.clicked.connect(self._on_add_files)
        vbox.addWidget(btn_add)

        # Class selector
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

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(0, 0, 0, 0)

        self.tabs = QTabWidget()
        vbox.addWidget(self.tabs)

        self.tabs.addTab(self._build_details_tab(),    "Line Details")
        self.tabs.addTab(self._build_validation_tab(), "Validation")
        self.tabs.addTab(self._build_adjustment_tab(), "Adjustment")

        return panel

    def _build_details_tab(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)

        self.line_info_lbl = QLabel("No line selected")
        self.line_info_lbl.setStyleSheet("font-weight: bold; padding: 2px;")
        vbox.addWidget(self.line_info_lbl)

        self.setup_table = QTableWidget(0, 5)
        self.setup_table.setHorizontalHeaderLabels(
            ["#", "From", "To", "Backsight (m)", "Foresight (m)", ]
        )
        # Extend to 6 columns — add Distance
        self.setup_table.setColumnCount(6)
        self.setup_table.setHorizontalHeaderLabels(
            ["#", "From", "To", "Backsight (m)", "Foresight (m)", "Distance (m)"]
        )
        self.setup_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.setup_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setup_table.setAlternatingRowColors(True)
        vbox.addWidget(self.setup_table)

        return w

    def _build_validation_tab(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)

        self.val_status_lbl = QLabel("—")
        self.val_status_lbl.setStyleSheet("font-size: 14px; font-weight: bold; padding: 4px;")
        vbox.addWidget(self.val_status_lbl)

        self.val_class_lbl = QLabel("")
        vbox.addWidget(self.val_class_lbl)

        self.val_errors = QTextEdit()
        self.val_errors.setReadOnly(True)
        self.val_errors.setPlaceholderText("Validation errors and warnings appear here…")
        vbox.addWidget(self.val_errors)

        return w

    def _build_adjustment_tab(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setAlignment(Qt.AlignTop)

        desc = QLabel(
            "Adjust This Line — applies proportional misclosure distribution "
            "to the currently selected line.\n\n"
            "LSA — runs a full Least Squares Adjustment across all loaded lines."
        )
        desc.setWordWrap(True)
        vbox.addWidget(desc)

        self.btn_adjust_line = QPushButton("⚙  Adjust This Line")
        self.btn_adjust_line.setFixedHeight(36)
        self.btn_adjust_line.setEnabled(False)
        self.btn_adjust_line.clicked.connect(self._on_adjust_line)
        vbox.addWidget(self.btn_adjust_line)

        self.btn_lsa = QPushButton("∑  LSA — Full Network Adjustment")
        self.btn_lsa.setFixedHeight(36)
        self.btn_lsa.setEnabled(False)
        self.btn_lsa.clicked.connect(self.lsa_requested.emit)
        vbox.addWidget(self.btn_lsa)

        self.adj_result_lbl = QTextEdit()
        self.adj_result_lbl.setReadOnly(True)
        self.adj_result_lbl.setPlaceholderText("Adjustment results appear here…")
        vbox.addWidget(self.adj_result_lbl)

        return w

    # ------------------------------------------------------------------
    # Public API — called by the plugin controller
    # ------------------------------------------------------------------

    def populate_line_details(self, line):
        """Public method — fills the Line Details table for a given LevelingLine."""
        self.line_info_lbl.setText(
            f"Line: {line.start_point} ➔ {line.end_point}  |  "
            f"Dist: {line.total_distance:.2f} m  |  "
            f"ΔH: {line.total_height_diff:.4f} m"
        )
        self._populate_setup_table(line)

    def update_validation_tab(self, line):
        """Public method — refreshes the Validation tab for a given LevelingLine."""
        # Find the matching ValidationResult by object identity or index
        for i, ln in enumerate(self._lines):
            if ln is line and i < len(self._val_results):
                _, vr = self._val_results[i]
                self._populate_validation(vr, line)
                return
        # Fallback: show line status only
        status_val = line.status.value if hasattr(line.status, 'value') else str(line.status)
        color = STATUS_COLORS.get(status_val, "#555555")
        self.val_status_lbl.setText(f"Status: {status_val}")
        self.val_status_lbl.setStyleSheet(
            f"font-size: 14px; font-weight: bold; color: {color}; padding: 4px;"
        )

    def load_lines(self, lines, val_results):
        """Populate the list with parsed LevelingLine objects."""
        self._lines = lines
        self._val_results = val_results  # list of (line, ValidationResult)

        self.line_list.clear()
        for i, line in enumerate(lines):
            label = f"{line.start_point} → {line.end_point}  [{line.filename}]"
            self.line_list.addItem(label)
            # Colour the item by validation status
            if i < len(val_results):
                _, vr = val_results[i]
                color = "#2e7d32" if vr.is_valid else "#c62828"
                self.line_list.item(i).setForeground(QColor(color))

        if lines:
            self.btn_lsa.setEnabled(True)
            self.line_list.setCurrentRow(0)

    def show_adjustment_result(self, text: str):
        """Display adjustment result text in the Adjustment tab."""
        self.adj_result_lbl.setPlainText(text)
        self.tabs.setCurrentIndex(2)

    def get_selected_class(self) -> str:
        return self.class_combo.currentText()

    def get_current_index(self) -> int:
        """Return the currently selected row index, or -1."""
        return self.line_list.currentRow()

    def select_line_by_filename(self, filename: str):
        """Select the list row whose line.filename matches; used by map-to-dock sync."""
        for i, line in enumerate(self._lines):
            if os.path.basename(line.filename) == os.path.basename(filename) \
                    or line.filename == filename:
                # Block the signal to avoid re-triggering zoom
                self.line_list.blockSignals(True)
                self.line_list.setCurrentRow(i)
                self.line_list.blockSignals(False)
                self._current_idx = i
                self.populate_line_details(line)
                if i < len(self._val_results):
                    _, vr = self._val_results[i]
                    self._populate_validation(vr, line)
                self.tabs.setCurrentIndex(0)  # switch to Line Details
                return

    def update_file_list(self, files: list):
        """Populate list from bare file paths (basename shown, full path stored)."""
        self.line_list.clear()
        self._lines = []
        self._val_results = []
        for f in files:
            self.line_list.addItem(os.path.basename(f))

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_line_selected(self, idx: int):
        if idx < 0 or idx >= len(self._lines):
            return
        self._current_idx = idx
        line = self._lines[idx]

        # --- Details tab ---
        self.populate_line_details(line)

        # --- Validation tab ---
        if idx < len(self._val_results):
            _, vr = self._val_results[idx]
            self._populate_validation(vr, line)

        self.btn_adjust_line.setEnabled(True)
        self.line_selected.emit(idx)

    def _populate_setup_table(self, line):
        setups = line.setups
        self.setup_table.setRowCount(len(setups))
        for row, s in enumerate(setups):
            dist = ((s.distance_back or 0) + (s.distance_fore or 0)) / 2
            for col, val in enumerate([
                str(s.setup_number),
                s.from_point,
                s.to_point,
                f"{s.backsight_reading:.5f}" if s.backsight_reading is not None else "—",
                f"{s.foresight_reading:.5f}" if s.foresight_reading is not None else "—",
                f"{dist:.2f}",
            ]):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                self.setup_table.setItem(row, col, item)

    def _populate_validation(self, vr, line=None):
        cls_name = self.class_combo.currentText()
        if vr.is_valid:
            self.val_status_lbl.setText(f"✅  VALID — {cls_name}")
            self.val_status_lbl.setStyleSheet(
                "font-size: 14px; font-weight: bold; color: #2e7d32; padding: 4px;"
            )
        else:
            # Show the specific line.status reason alongside INVALID
            status_reason = ""
            if line is not None:
                status_val = line.status.value if hasattr(line.status, 'value') else str(line.status)
                status_reason = f" [{status_val}]"
            self.val_status_lbl.setText(f"❌  INVALID — {cls_name}{status_reason}")
            self.val_status_lbl.setStyleSheet(
                "font-size: 14px; font-weight: bold; color: #c62828; padding: 4px;"
            )

        self.val_class_lbl.setText(CLASS_DESCRIPTIONS.get(cls_name, ""))

        lines_out = []
        for err in vr.errors:
            lines_out.append(f"❌ {err}")
        for warn in vr.warnings:
            lines_out.append(f"⚠  {warn}")
        self.val_errors.setPlainText("\n".join(lines_out) if lines_out else "No issues found.")

    def _on_add_files(self):
        """Open file dialog and emit files_added so the plugin processes them."""
        files, _ = self._qfd.getOpenFileNames(
            self, "Select Measurement Files", "",
            "Geodetic Files (*.DAT *.dat *.RAW *.raw *.GSI *.gsi);;All Files (*)"
        )
        if files:
            cls = self.class_combo.currentText()
            # Output dir: ask only if we have no prior context
            output_dir = self._qfd.getExistingDirectory(
                self, "Select Output Directory"
            )
            if output_dir:
                self.files_added.emit(files, cls, output_dir)

    def _on_adjust_line(self):
        if self._current_idx >= 0:
            self.adjust_line_requested.emit(self._current_idx)
