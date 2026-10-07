"""
geo_level_line_adj_dialog.py
Single-line adjustment dialog for Geo Level Gravi QGIS plugin.
"""
import copy
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QDoubleSpinBox, QComboBox, QPushButton, QTextEdit, QMessageBox
)
from qgis.PyQt.QtGui import QFont


class GeoLevelLineAdjDialog(QDialog):
    def __init__(self, line, parent=None):
        super().__init__(parent)
        self.line = line
        self.adjusted_line = None
        self.setWindowTitle(
            "Line Adjustment -- " + (line.start_point or "?") + " -> " + (line.end_point or "?")
        )
        self.resize(500, 440)
        self._build_ui()

    def _build_ui(self):
        vbox = QVBoxLayout(self)

        info_grp = QGroupBox("Line Information")
        form = QFormLayout(info_grp)
        form.addRow("Start Point:", QLabel(self.line.start_point or "--"))
        form.addRow("End Point:",   QLabel(self.line.end_point   or "--"))
        form.addRow("Distance (m):", QLabel("{:.2f}".format(self.line.total_distance)))
        form.addRow("Measured dH (m):", QLabel("{:.5f}".format(self.line.total_height_diff)))
        form.addRow("Setups:", QLabel(str(len(self.line.setups))))
        vbox.addWidget(info_grp)

        bm_grp = QGroupBox("Known Benchmark Heights")
        bm_form = QFormLayout(bm_grp)
        self.spin_start = QDoubleSpinBox()
        self.spin_start.setDecimals(5)
        self.spin_start.setRange(-9999.0, 9999.0)
        self.spin_start.setValue(0.0)
        bm_form.addRow("Height of " + (self.line.start_point or "start") + " (m):", self.spin_start)
        self.spin_end = QDoubleSpinBox()
        self.spin_end.setDecimals(5)
        self.spin_end.setRange(-9999.0, 9999.0)
        self.spin_end.setValue(0.0)
        bm_form.addRow("Height of " + (self.line.end_point or "end") + " (m):", self.spin_end)
        vbox.addWidget(bm_grp)

        meth_grp = QGroupBox("Distribution Method")
        meth_hbox = QHBoxLayout(meth_grp)
        meth_hbox.addWidget(QLabel("Method:"))
        self.combo_method = QComboBox()
        self.combo_method.addItems(["Proportional to Distance", "Equal Distribution"])
        meth_hbox.addWidget(self.combo_method)
        vbox.addWidget(meth_grp)

        res_grp = QGroupBox("Results")
        res_vbox = QVBoxLayout(res_grp)
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setFont(QFont("Courier New", 9))
        self.result_text.setMinimumHeight(110)
        res_vbox.addWidget(self.result_text)
        vbox.addWidget(res_grp)

        btn_row = QHBoxLayout()
        btn_run = QPushButton("Adjust Line")
        btn_run.setStyleSheet("font-weight: bold; background: #1565c0; color: white;")
        btn_run.clicked.connect(self._run)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.reject)
        btn_row.addWidget(btn_run)
        btn_row.addWidget(btn_close)
        vbox.addLayout(btn_row)

    def _run(self):
        start_h = self.spin_start.value()
        end_h   = self.spin_end.value()
        method  = "proportional" if self.combo_method.currentIndex() == 0 else "equal"
        try:
            from core_logic.engine.height_calculator import (
                calculate_misclosure, distribute_misclosure, apply_corrections
            )
            misclosure  = calculate_misclosure(self.line.total_height_diff, start_h, end_h)
            corrections = distribute_misclosure(self.line, misclosure, method=method)
            adj = copy.deepcopy(self.line)
            apply_corrections(adj, corrections)
            self.adjusted_line = adj
            misc_mm = misclosure * 1000
            residual_mm = (adj.total_height_diff - (end_h - start_h)) * 1000
            text = (
                "Start height  : " + "{:.5f}".format(start_h) + " m\n"
                + "End height    : " + "{:.5f}".format(end_h) + " m\n"
                + "Expected dH   : " + "{:.5f}".format(end_h - start_h) + " m\n"
                + "Measured dH   : " + "{:.5f}".format(self.line.total_height_diff) + " m\n"
                + "Misclosure    : " + "{:.3f}".format(misc_mm) + " mm\n"
                + "Method        : " + method + "\n"
                + "-" * 40 + "\n"
                + "Adjusted dH   : " + "{:.5f}".format(adj.total_height_diff) + " m\n"
                + "Residual after: " + "{:.4f}".format(residual_mm) + " mm\n"
            )
            self.result_text.setPlainText(text)
        except Exception as exc:
            import traceback
            QMessageBox.critical(self, "Error", str(exc) + "\n\n" + traceback.format_exc())
