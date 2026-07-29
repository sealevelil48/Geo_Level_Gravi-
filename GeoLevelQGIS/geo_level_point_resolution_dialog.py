"""
Pre-calculation point verification dialog.

Shows field point IDs that are either unresolved (0 DB hits) or ambiguous
(2+ spatial duplicates) BEFORE the QGIS layer is built or any LSA runs.
The engineer selects the correct database benchmark — with easting, northing,
height and accuracy class displayed — or types a known name manually.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class GeoLevelPointResolutionDialog(QDialog):
    """Verify ambiguous or unresolved field point IDs before project loading.

    Args:
        target_points: Ordered list of field IDs that need user confirmation.
        candidate_map: Maps each field ID to a list of BenchmarkRecord objects
            fetched from the DB.  An empty list means no DB hit was found.
        parent: Parent QWidget.
    """

    def __init__(
        self,
        target_points: List[str],
        candidate_map: Dict[str, list],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Point Verification / אימות נקודות")
        self.setMinimumSize(860, 420)
        self._target_points = target_points
        self._candidate_map = candidate_map
        self._combos: Dict[str, QComboBox] = {}
        self._edits: Dict[str, QLineEdit] = {}
        self._setup_ui()
        self._populate()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        info = QLabel(
            "<b>Some field point IDs were not found or have multiple spatial matches in"
            " the Bangal database.</b><br>"
            "Select the correct benchmark from the dropdown (coordinates shown) or type"
            " the exact database name in the override column.<br>"
            "<i>No layers will be built and no calculations will run until you confirm"
            " these points.</i>"
        )
        info.setWordWrap(True)
        root.addWidget(info)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels([
            "Field Point ID",
            "DB Candidate  (Name | Easting | Northing | Height | Class)",
            "Manual Name Override",
        ])
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        root.addWidget(self._table)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._btn_cancel = QPushButton("Cancel Loading")
        self._btn_cancel.clicked.connect(self.reject)

        self._btn_confirm = QPushButton("Confirm && Load Project")
        self._btn_confirm.setStyleSheet(
            "font-weight: bold; background: #1565c0; color: white; padding: 6px 18px;"
        )
        self._btn_confirm.clicked.connect(self.accept)

        btn_row.addWidget(self._btn_cancel)
        btn_row.addWidget(self._btn_confirm)
        root.addLayout(btn_row)

    def _populate(self) -> None:
        self._table.setRowCount(len(self._target_points))
        for row, pid in enumerate(self._target_points):
            # Column 0 — original field ID (read-only)
            id_item = QTableWidgetItem(pid)
            id_item.setFlags(Qt.ItemIsEnabled)
            self._table.setItem(row, 0, id_item)

            # Column 1 — dropdown of DB candidates
            combo = QComboBox()
            records = self._candidate_map.get(pid, [])
            if not records:
                combo.addItem("(no DB match — use manual override or skip)", None)
            else:
                for rec in records:
                    label = self._format_record_label(rec)
                    combo.addItem(label, rec)
            self._table.setCellWidget(row, 1, combo)
            self._combos[pid] = combo

            # Column 2 — free-text override
            edit = QLineEdit()
            edit.setPlaceholderText("Optional: type exact DB name…")
            self._table.setCellWidget(row, 2, edit)
            self._edits[pid] = edit

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_record_label(rec) -> str:
        """Build the human-readable dropdown label from a BenchmarkRecord."""
        e_str = f"{rec.x:.0f}" if rec.x is not None else "?"
        n_str = f"{rec.y:.0f}" if rec.y is not None else "?"
        h_str = f"{rec.gova_ort:.3f} m" if rec.gova_ort is not None else "no height"
        cls_str = rec.shem_darga_gova or "—"
        city_str = getattr(rec, "kfar_aher_name", None) or "—"
        return (
            f"{rec.name}   |   E {e_str}  N {n_str}  |  H {h_str}"
            f"  ({cls_str})  |  {city_str}"
        )

    # ------------------------------------------------------------------
    # Public result accessor
    # ------------------------------------------------------------------

    def get_resolved(self) -> Dict[str, object]:
        """Return mapping of field PID → BenchmarkRecord (or synthetic record).

        Must be called only after the dialog has been accepted (exec_() returned
        QDialog.Accepted).  For manual-override entries a minimal synthetic
        BenchmarkRecord is created so callers can always access `.name` and
        `.gova_ort` without branching.
        """
        from geolevel_db_manager import BenchmarkRecord  # local import — avoids circular dep

        resolved: Dict[str, object] = {}
        for pid in self._target_points:
            manual = self._edits[pid].text().strip()
            if manual:
                resolved[pid] = BenchmarkRecord(
                    ot_nekuda="",
                    mispar_nekuda=0,
                    name=manual,
                    gova_ort=None,
                    shem_darga_gova=None,
                    taarih_gova_ort=None,
                    ot_nekuda_kfula=None,
                    mispar_nekuda_kfula=None,
                    x=None,
                    y=None,
                )
            else:
                combo = self._combos[pid]
                rec = combo.currentData()
                resolved[pid] = rec  # may be None if "(no DB match)" was selected
        return resolved
