"""
geo_level_point_exclusion_dialog.py
Point exclusion dialog — lets user mark specific points as excluded from adjustment.
"""
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QMessageBox, QAbstractItemView
)
from qgis.PyQt.QtCore import Qt


class GeoLevelPointExclusionDialog(QDialog):
    """
    Shows all unique points across loaded lines.
    User can select points to exclude; excluded points are stored on the lines.

    After exec_(), check self.updated_lines for the modified list.
    """

    def __init__(self, lines, parent=None):
        super().__init__(parent)
        self.lines = list(lines)
        self.updated_lines = list(lines)
        self.setWindowTitle("Point Exclusion / הוצאת נקודות")
        self.resize(420, 480)
        self._build_ui()
        self._populate()

    def _build_ui(self):
        vbox = QVBoxLayout(self)
        vbox.addWidget(QLabel(
            "Select points to EXCLUDE from network adjustment.\n"
            "Excluded points will be skipped during LSA."
        ))

        self.point_list = QListWidget()
        self.point_list.setSelectionMode(QAbstractItemView.MultiSelection)
        vbox.addWidget(self.point_list)

        btn_row = QHBoxLayout()
        btn_apply = QPushButton("Apply Exclusion")
        btn_apply.setStyleSheet("font-weight: bold; background: #c62828; color: white;")
        btn_apply.clicked.connect(self._apply)
        btn_clear = QPushButton("Clear All Exclusions")
        btn_clear.clicked.connect(self._clear)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.reject)
        btn_row.addWidget(btn_apply)
        btn_row.addWidget(btn_clear)
        btn_row.addWidget(btn_close)
        vbox.addLayout(btn_row)

    def _populate(self):
        points = set()
        for line in self.lines:
            if line.start_point:
                points.add(line.start_point)
            if line.end_point:
                points.add(line.end_point)
        self.point_list.clear()
        for pt in sorted(points):
            item = QListWidgetItem(pt)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.point_list.addItem(item)

    def _apply(self):
        excluded = set()
        for i in range(self.point_list.count()):
            item = self.point_list.item(i)
            if item.checkState() == Qt.Checked:
                excluded.add(item.text())
        if not excluded:
            QMessageBox.information(self, "Point Exclusion",
                                    "No points selected for exclusion.")
            return
        # Mark setups that touch excluded points as not used
        import copy
        updated = []
        for line in self.lines:
            ln = copy.deepcopy(line)
            if ln.start_point in excluded or ln.end_point in excluded:
                ln.is_used = False
            updated.append(ln)
        self.updated_lines = updated
        QMessageBox.information(
            self, "Point Exclusion",
            "Excluded " + str(len(excluded)) + " point(s):\n" + ", ".join(sorted(excluded))
            + "\n\nLines touching these points have been marked as excluded."
        )
        self.accept()

    def _clear(self):
        for i in range(self.point_list.count()):
            self.point_list.item(i).setCheckState(Qt.Unchecked)
        self.updated_lines = list(self.lines)
        QMessageBox.information(self, "Point Exclusion", "All exclusions cleared.")
