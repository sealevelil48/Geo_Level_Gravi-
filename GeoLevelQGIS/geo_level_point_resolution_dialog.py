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
        self.setMinimumSize(1000, 480)
        self._target_points = target_points
        self._candidate_map = candidate_map
        self._combos: Dict[str, QComboBox] = {}          # field ID → DB candidate dropdown
        self._edits: Dict[str, QLineEdit] = {}            # field ID → manual name override
        self._handling: Dict[str, QComboBox] = {}         # field ID → handling mode dropdown
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

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels([
            "Field Point ID",
            "DB Candidate  (Name | Easting | Northing | Height | Class)",
            "Manual Name Override",
            "Handling Option",
        ])
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
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

            # Column 3 — handling option
            handling_combo = QComboBox()
            handling_combo.addItem("Use Selected Match", "use_selected")
            handling_combo.addItem("Exclude from Project", "exclude")
            handling_combo.addItem("Point Not Found (Estimate)", "estimate")
            handling_combo.addItem("PKT (Average Neighbors)", "pkt_average")
            self._table.setCellWidget(row, 3, handling_combo)
            self._handling[pid] = handling_combo

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
        """Return mapping of field PID → handling mode and resolution data.

        Must be called only after the dialog has been accepted (exec_() returned
        QDialog.Accepted).

        Returns a dict with two keys:
          'records': Dict[pid → BenchmarkRecord | None]
          'modes':   Dict[pid → handling mode string]

        Handling modes:
          'use_selected' — use the DB candidate or manual override
          'exclude' — skip this point from the project
          'estimate' — point not found; ask engine to estimate from neighbors
          'pkt_average' — PKT point; use average of all neighbor positions
        """
        from geolevel_db_manager import BenchmarkRecord  # local import — avoids circular dep

        records: Dict[str, object] = {}
        modes: Dict[str, str] = {}

        for pid in self._target_points:
            mode = self._handling[pid].currentData()
            modes[pid] = mode

            if mode == "exclude":
                records[pid] = None
                continue

            if mode in ("estimate", "pkt_average"):
                # For these modes, create a synthetic marker record so the engine
                # can recognize them and apply topological estimation/averaging logic.
                synthetic = BenchmarkRecord(
                    ot_nekuda="",
                    mispar_nekuda=0,
                    name=f"[{mode.upper()}:{pid}]",
                    gova_ort=None,
                    shem_darga_gova=None,
                    taarih_gova_ort=None,
                    ot_nekuda_kfula=None,
                    mispar_nekuda_kfula=None,
                    x=None,
                    y=None,
                )
                records[pid] = synthetic
                continue

            # mode == "use_selected" (default)
            manual = self._edits[pid].text().strip()
            if manual:
                records[pid] = BenchmarkRecord(
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
                records[pid] = rec  # may be None if "(no DB match)" was selected

        return {"records": records, "modes": modes}
