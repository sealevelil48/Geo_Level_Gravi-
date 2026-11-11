"""
geo_level_settings_dialog.py
Class Parameters settings dialog for Geo Level Gravi.

Displays an editable table of H1-H6 regulation parameters.
Reads from / writes to ~/.geodetic_tool/settings.json via SettingsManager.

Formula reminder:  T = k × √L   (T in mm, L in km)
"""

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QHeaderView, QAbstractItemView, QMessageBox
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont

# Column indices
_COL_CLASS   = 0
_COL_K       = 1
_COL_SIGHT   = 2
_COL_METHOD  = 3
_COL_MAXLINE = 4
_COL_IMBAL   = 5

_HEADERS = [
    "Class",
    "k  (mm/√km)\nT = k×√L",
    "Max Sight\nGeom. (m)",
    "Method\nRequired",
    "Max Line\n(km)",
    "Max Dist\nImbal. (m)",
]

_ALL_CLASSES = ["H1", "H2", "H3", "H4", "H5", "H6"]

# Colour rows by class family
_ROW_COLORS = {
    "H1": "#e8f5e9",
    "H2": "#e8f5e9",
    "H3": "#e8f5e9",
    "H4": "#fff8e1",
    "H5": "#fff8e1",
    "H6": "#fce4ec",
}


class GeoLevelSettingsDialog(QDialog):
    """Editable table of H1-H6 class parameters."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Class Parameters / פרמטרי דרגות דיוק")
        self.setMinimumWidth(680)
        self.setMinimumHeight(340)
        self._setup_ui()
        self._load_data()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Description
        desc = QLabel(
            "Edit Survey of Israel Directive ג2 (2021) parameters.\n"
            "Formula:  T = k × √L   where T = tolerance (mm), L = line length (km)."
        )
        desc.setStyleSheet("color: #555; padding: 4px;")
        layout.addWidget(desc)

        # Table
        self.table = QTableWidget(6, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        bold = QFont()
        bold.setBold(True)
        self.table.horizontalHeader().setFont(bold)
        layout.addWidget(self.table)

        # Buttons
        btn_row = QHBoxLayout()
        self.btn_reset = QPushButton("Reset to Defaults")
        self.btn_reset.clicked.connect(self._reset_defaults)
        btn_row.addWidget(self.btn_reset)
        btn_row.addStretch()
        self.btn_cancel = QPushButton("Cancel / ביטול")
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)
        self.btn_save = QPushButton("Save / שמור")
        self.btn_save.setDefault(True)
        self.btn_save.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; "
            "font-weight: bold; padding: 4px 16px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #388e3c; }"
        )
        self.btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self.btn_save)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_data(self):
        """Populate table from live CLASS_REGISTRY (already patched by settings_manager)."""
        from core_logic.config.israel_survey_regulations import CLASS_REGISTRY_BY_NAME
        for row, cls in enumerate(_ALL_CLASSES):
            p = CLASS_REGISTRY_BY_NAME[cls]
            values = [
                cls,
                str(p.tolerance_coefficient),
                str(p.max_sight_distance_geometric_m),
                p.required_method,
                str(p.max_line_length_km) if p.max_line_length_km is not None else "∞",
                str(p.max_cumulative_distance_imbalance_m),
            ]
            bg = QColor(_ROW_COLORS.get(cls, "#ffffff"))
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setBackground(bg)
                item.setTextAlignment(Qt.AlignCenter)
                # Class column — read-only
                if col == _COL_CLASS:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    item.setFont(QFont("", -1, QFont.Bold))
                self.table.setItem(row, col, item)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_save(self):
        """Validate, apply to live registry, persist, accept."""
        try:
            updated = self._parse_table()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Input", str(exc))
            return

        # Apply to live CLASS_REGISTRY so validators pick up changes immediately
        from core_logic.config.israel_survey_regulations import CLASS_REGISTRY_BY_NAME
        for cls, params in updated.items():
            p = CLASS_REGISTRY_BY_NAME[cls]
            p.tolerance_coefficient              = params["tolerance_coefficient"]
            p.max_sight_distance_geometric_m     = params["max_sight_distance_geometric_m"]
            p.required_method                    = params["required_method"]
            p.max_line_length_km                 = params["max_line_length_km"]
            p.max_cumulative_distance_imbalance_m = params["max_cumulative_distance_imbalance_m"]

        # Persist to ~/.geodetic_tool/settings.json
        from core_logic.config.settings_manager import get_settings_manager
        mgr = get_settings_manager()
        for cls, params in updated.items():
            mgr.update_class_parameters(cls, params)

        self.accept()

    def _reset_defaults(self):
        ans = QMessageBox.question(
            self, "Reset to Defaults",
            "Reset all class parameters to Survey of Israel defaults?\n"
            "This will delete your custom settings.json.",
            QMessageBox.Yes | QMessageBox.No
        )
        if ans != QMessageBox.Yes:
            return
        from core_logic.config.settings_manager import get_settings_manager
        get_settings_manager().reset_to_defaults()
        # Reload defaults by re-importing the module
        import importlib
        import core_logic.config.israel_survey_regulations as reg_mod
        importlib.reload(reg_mod)
        self._load_data()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_table(self) -> dict:
        """Read and validate all editable cells. Raises ValueError on bad input."""
        result = {}
        for row in range(6):
            cls = self.table.item(row, _COL_CLASS).text()
            try:
                k = float(self.table.item(row, _COL_K).text())
                if k <= 0:
                    raise ValueError(f"{cls}: k must be > 0")
            except (TypeError, ValueError):
                raise ValueError(f"{cls}: k (mm/√km) must be a positive number")

            try:
                sight = float(self.table.item(row, _COL_SIGHT).text())
                if sight <= 0:
                    raise ValueError(f"{cls}: Max sight must be > 0")
            except (TypeError, ValueError):
                raise ValueError(f"{cls}: Max sight (m) must be a positive number")

            method = self.table.item(row, _COL_METHOD).text().strip().upper()
            if method not in ("BF", "BFFB", "FB"):
                raise ValueError(f"{cls}: Method must be BF, BFFB, or FB")

            max_line_text = self.table.item(row, _COL_MAXLINE).text().strip()
            max_line = None if max_line_text in ("∞", "", "None") else float(max_line_text)

            try:
                imbal = float(self.table.item(row, _COL_IMBAL).text())
            except (TypeError, ValueError):
                raise ValueError(f"{cls}: Max dist imbalance must be a number")

            result[cls] = {
                "tolerance_coefficient":               k,
                "max_sight_distance_geometric_m":      sight,
                "required_method":                     method,
                "max_line_length_km":                  max_line,
                "max_cumulative_distance_imbalance_m": imbal,
            }
        return result

    def get_updated_settings(self) -> dict:
        """Public accessor — returns the parsed table dict (used by tests)."""
        return self._parse_table()
