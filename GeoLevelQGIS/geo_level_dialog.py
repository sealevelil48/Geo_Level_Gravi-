"""
GeoLevelDialog - QGIS-native PyQt5 UI for Geo Level Gravi plugin.

Provides:
  - Multi-file selector (.DAT / .RAW / .GSI)
  - Class selector (H1-H6)
  - Output directory selector
  - Run Adjustment & Map button
"""

import os
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QPushButton, QListWidget, QComboBox,
    QLineEdit, QFileDialog, QAbstractItemView,
    QDialogButtonBox, QSizePolicy
)
from qgis.PyQt.QtCore import Qt


SUPPORTED_FILTER = "Measurement Files (*.DAT *.dat *.RAW *.raw *.GSI *.gsi)"

CLASS_DESCRIPTIONS = {
    "H1": "H1 — ±3 mm√km  |  BFFB  |  sight ≤ 30 m",
    "H2": "H2 — ±5 mm√km  |  BFFB  |  sight ≤ 40 m",
    "H3": "H3 — ±10 mm√km |  BFFB  |  sight ≤ 50 m  (default)",
    "H4": "H4 — ±20 mm√km |  BF    |  sight ≤ 80 m",
    "H5": "H5 — ±30 mm√km |  BF    |  sight ≤ 100 m",
    "H6": "H6 — ±60 mm√km |  BF    |  sight ≤ 100 m",
}


class GeoLevelDialog(QDialog):
    """Main plugin dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Geo Level Gravi — פילוס גיאודטי")
        self.setMinimumWidth(560)
        self.setMinimumHeight(480)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        root.addWidget(self._file_group())
        root.addWidget(self._class_group())
        root.addWidget(self._output_group())
        root.addWidget(self._run_button())

    def _file_group(self) -> QGroupBox:
        grp = QGroupBox("Input Files / קבצי קלט")
        layout = QVBoxLayout(grp)

        # File list
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.file_list.setMinimumHeight(140)
        layout.addWidget(self.file_list)

        # Buttons row
        btn_row = QHBoxLayout()

        btn_add = QPushButton("➕ Add Files…")
        btn_add.clicked.connect(self._add_files)
        btn_row.addWidget(btn_add)

        btn_remove = QPushButton("🗑 Remove Selected")
        btn_remove.clicked.connect(self._remove_selected)
        btn_row.addWidget(btn_remove)

        btn_clear = QPushButton("✖ Clear All")
        btn_clear.clicked.connect(self.file_list.clear)
        btn_row.addWidget(btn_clear)

        layout.addLayout(btn_row)
        return grp

    def _class_group(self) -> QGroupBox:
        grp = QGroupBox("Precision Class / דרגת דיוק")
        layout = QHBoxLayout(grp)

        layout.addWidget(QLabel("Active Class:"))

        self.class_combo = QComboBox()
        self.class_combo.addItems(["H1", "H2", "H3", "H4", "H5", "H6"])
        self.class_combo.setCurrentText("H3")          # default per regulations
        self.class_combo.setFixedWidth(70)
        self.class_combo.currentTextChanged.connect(self._update_class_label)
        layout.addWidget(self.class_combo)

        self.class_label = QLabel(CLASS_DESCRIPTIONS["H3"])
        self.class_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(self.class_label)

        return grp

    def _output_group(self) -> QGroupBox:
        grp = QGroupBox("Output Directory / תיקיית פלט")
        layout = QHBoxLayout(grp)

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Select output folder…")
        layout.addWidget(self.output_edit)

        btn_browse = QPushButton("Browse…")
        btn_browse.setFixedWidth(90)
        btn_browse.clicked.connect(self._browse_output)
        layout.addWidget(btn_browse)

        return grp

    def _run_button(self) -> QPushButton:
        self.run_btn = QPushButton("▶  Run Adjustment & Map")
        self.run_btn.setFixedHeight(42)
        self.run_btn.setStyleSheet(
            "QPushButton { font-size: 13px; font-weight: bold; background-color: #2e7d32; color: white; border-radius: 4px; }"
            "QPushButton:hover { background-color: #388e3c; }"
            "QPushButton:pressed { background-color: #1b5e20; }"
            "QPushButton:disabled { background-color: #9e9e9e; }"
        )
        self.run_btn.clicked.connect(self._on_run)
        return self.run_btn

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select Measurement Files", "", SUPPORTED_FILTER
        )
        existing = {self.file_list.item(i).text() for i in range(self.file_list.count())}
        for p in paths:
            if p not in existing:
                self.file_list.addItem(p)

    def _remove_selected(self):
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))

    def _browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if folder:
            self.output_edit.setText(folder)

    def _update_class_label(self, cls: str):
        self.class_label.setText(CLASS_DESCRIPTIONS.get(cls, ""))

    def _on_run(self):
        """Emit data upward; actual processing wired in geo_level_plugin.py."""
        self.accept()

    # ------------------------------------------------------------------
    # Public accessors (used by the plugin controller)
    # ------------------------------------------------------------------

    def get_file_paths(self) -> list:
        return [self.file_list.item(i).text() for i in range(self.file_list.count())]

    def get_selected_class(self) -> str:
        return self.class_combo.currentText()

    def get_output_dir(self) -> str:
        return self.output_edit.text().strip()
