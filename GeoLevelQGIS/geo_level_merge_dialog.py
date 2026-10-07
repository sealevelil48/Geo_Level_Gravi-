"""
geo_level_merge_dialog.py
Merge Line Segments dialog — PyQt5 port of MergeDialog from app.py.
Uses LineCoordinator to detect and apply mergeable segments.
"""

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QTextEdit,
    QMessageBox, QLineEdit, QAbstractItemView
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont


class GeoLevelMergeDialog(QDialog):
    """
    Detect and merge contiguous leveling line segments.

    Parameters
    ----------
    lines   : list of LevelingLine
    parent  : QWidget

    After exec_() check self.merged_lines for the updated list.
    """

    def __init__(self, lines, parent=None):
        super().__init__(parent)
        self.lines = list(lines)
        self.merged_lines = list(lines)   # updated after merge
        self.candidates = []
        self.setWindowTitle("Merge Line Segments / מיזוג קטעי קו")
        self.resize(700, 520)
        self._build_ui()
        self._detect_candidates()

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        vbox = QVBoxLayout(self)

        # Candidates list
        grp_cand = QGroupBox("Mergeable Segments Detected / קטעים הניתנים למיזוג")
        vbox_c = QVBoxLayout(grp_cand)
        self.cand_list = QListWidget()
        self.cand_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.cand_list.currentRowChanged.connect(self._on_candidate_selected)
        vbox_c.addWidget(self.cand_list)
        vbox.addWidget(grp_cand)

        # Detail
        grp_det = QGroupBox("Merge Details / פרטי מיזוג")
        vbox_d = QVBoxLayout(grp_det)
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setFont(QFont("Courier New", 9))
        self.detail_text.setMinimumHeight(130)
        vbox_d.addWidget(self.detail_text)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Merged filename:"))
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("Auto-generated if empty")
        name_row.addWidget(self.edit_name)
        vbox_d.addLayout(name_row)
        vbox.addWidget(grp_det)

        # Buttons
        btn_row = QHBoxLayout()
        self.btn_merge = QPushButton("⚙  Merge Selected")
        self.btn_merge.setEnabled(False)
        self.btn_merge.setStyleSheet("font-weight: bold; background: #1565c0; color: white;")
        self.btn_merge.clicked.connect(self._apply_merge)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_merge)
        btn_row.addWidget(btn_close)
        vbox.addLayout(btn_row)

    # ------------------------------------------------------------------ #
    # Logic
    # ------------------------------------------------------------------ #

    def _detect_candidates(self):
        try:
            from core_logic.engine.line_coordinator import LineCoordinator
            coordinator = LineCoordinator(self.lines)
            self.candidates = coordinator.find_merge_candidates()
            self.cand_list.clear()
            if not self.candidates:
                item = QListWidgetItem("No mergeable segments found.")
                item.setForeground(QColor("#888888"))
                self.cand_list.addItem(item)
                return
            for i, cand in enumerate(self.candidates):
                from core_logic.engine.line_coordinator import LineCoordinator as LC
                summary = LC(self.lines).get_merge_summary(cand)
                label = (
                    f"{summary['start_point']} → {summary['end_point']}  "
                    f"({summary['num_segments']} segments, "
                    f"{summary['total_distance']:.0f} m)"
                )
                self.cand_list.addItem(label)
        except Exception as exc:
            QMessageBox.critical(self, "Detection Error", str(exc))

    def _on_candidate_selected(self, idx):
        if idx < 0 or idx >= len(self.candidates):
            self.btn_merge.setEnabled(False)
            return
        self.btn_merge.setEnabled(True)
        cand = self.candidates[idx]
        try:
            from core_logic.engine.line_coordinator import LineCoordinator
            summary = LineCoordinator(self.lines).get_merge_summary(cand)
            lines_out = [
                f"Merge: {summary['start_point']} → {summary['end_point']}",
                f"Segments : {summary['num_segments']}",
                f"Total dist: {summary['total_distance']:.2f} m",
                f"Total setups: {summary['total_setups']}",
                "─" * 40,
            ]
            for seg in summary["segments"]:
                rev = "  [REVERSED]" if seg["needs_reversal"] else ""
                lines_out.append(
                    f"  {seg['filename']:20s}  {seg['direction']}{rev}  "
                    f"{seg['distance']:.0f} m  ({seg['setups']} setups)"
                )
            if summary["common_nodes"]:
                lines_out.append(f"Common nodes: {', '.join(summary['common_nodes'])}")
            self.detail_text.setPlainText("\n".join(lines_out))
            # Pre-fill name
            self.edit_name.setText(
                f"MERGED_{summary['start_point']}-{summary['end_point']}"
            )
        except Exception as exc:
            self.detail_text.setPlainText(f"Error: {exc}")

    def _apply_merge(self):
        idx = self.cand_list.currentRow()
        if idx < 0 or idx >= len(self.candidates):
            return
        cand = self.candidates[idx]
        custom_name = self.edit_name.text().strip() or None
        try:
            from core_logic.engine.line_coordinator import LineCoordinator
            coordinator = LineCoordinator(self.lines)
            merged = coordinator.apply_merge(cand, self.merged_lines, custom_name)
            QMessageBox.information(
                self, "Merge Complete",
                f"Merged {len(cand.lines)} segments into '{merged.filename}'.\n"
                f"Original segments marked as excluded.\n"
                f"Total distance: {merged.total_distance:.2f} m"
            )
            # Refresh candidates
            self.lines = self.merged_lines
            self._detect_candidates()
        except Exception as exc:
            import traceback
            QMessageBox.critical(self, "Merge Error",
                                 f"{exc}\n\n{traceback.format_exc()}")
