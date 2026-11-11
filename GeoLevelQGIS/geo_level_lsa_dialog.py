"""
geo_level_lsa_dialog.py
LSA Network Adjustment Results Dialog.
"""
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QLabel, QPushButton, QGroupBox, QSizePolicy, QFileDialog, QMessageBox
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor


class GeoLevelLSADialog(QDialog):
    """Displays LSA results: adjusted heights, corrections, std-devs, residuals."""

    def __init__(self, result, fixed_points: dict, parent=None):
        """
        Args:
            result: AdjustmentResult from LeastSquaresAdjuster.adjust_from_lines()
            fixed_points: {point_id: original_height} used in the adjustment
        """
        super().__init__(parent)
        self.setWindowTitle("Network Adjustment Results (LSA) / תוצאות תאום רשת")
        self.setMinimumSize(750, 560)
        self.result = result
        self.fixed_points = fixed_points
        self._setup_ui()
        self._populate()

    # ------------------------------------------------------------------
    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # ── Statistics summary ────────────────────────────────────────
        stats_box = QGroupBox("Adjustment Statistics / סטטיסטיקת תאום")
        stats_layout = QHBoxLayout(stats_box)

        dof = max(0, len(self.result.residuals) - (
            len(self.result.adjusted_heights) - len(self.fixed_points)
        ))
        ref_var = self.result.mse_unit_weight ** 2

        stats_layout.addWidget(QLabel(
            f"<b>Iterations:</b> {self.result.iteration}"
        ))
        stats_layout.addWidget(QLabel(
            f"<b>σ₀² (Ref. Variance):</b> {ref_var:.6f} m²"
        ))
        stats_layout.addWidget(QLabel(
            f"<b>σ₀ (Unit Weight):</b> {self.result.mse_unit_weight * 1000:.3f} mm"
        ))
        stats_layout.addWidget(QLabel(
            f"<b>Degrees of Freedom:</b> {dof}"
        ))
        stats_layout.addWidget(QLabel(
            f"<b>K coefficient:</b> {self.result.k_coefficient:.4f}"
        ))
        stats_layout.addWidget(QLabel(
            f"<b>Total dist:</b> {self.result.total_distance_km:.3f} km"
        ))
        layout.addWidget(stats_box)

        # ── Adjusted heights table ────────────────────────────────────
        layout.addWidget(QLabel("<b>Adjusted Heights / גבהים מתואמים:</b>"))

        self.heights_table = QTableWidget()
        self.heights_table.setColumnCount(4)
        self.heights_table.setHorizontalHeaderLabels([
            "Point ID", "Adjusted Height (m)", "Correction (mm)", "Std Dev (mm)"
        ])
        self.heights_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.heights_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.heights_table.setAlternatingRowColors(True)
        layout.addWidget(self.heights_table)

        # ── Residuals table ───────────────────────────────────────────
        layout.addWidget(QLabel("<b>Observation Residuals / שאריות תצפיות:</b>"))

        self.resid_table = QTableWidget()
        self.resid_table.setColumnCount(2)
        self.resid_table.setHorizontalHeaderLabels(["Observation", "Residual (mm)"])
        self.resid_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.resid_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.resid_table.setAlternatingRowColors(True)
        self.resid_table.setMaximumHeight(160)
        layout.addWidget(self.resid_table)

        # ── Bottom buttons ────────────────────────────────────────────
        btn_row = QHBoxLayout()

        self.btn_report = QPushButton("Export PDF Report / הפק דוח PDF")
        self.btn_report.setStyleSheet(
            "background-color: #2c3e50; color: white; font-weight: bold; padding: 6px 14px;"
        )
        self.btn_report.clicked.connect(self.on_export_report)
        btn_row.addWidget(self.btn_report)

        btn_row.addStretch()

        btn_close = QPushButton("Close / סגור")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)

        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    def on_export_report(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save PDF Report / שמור דוח PDF",
            f"LSA_Report_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
            "PDF Files (*.pdf)"
        )
        if not path:
            return
        try:
            from geo_level_reporter import GeoLevelReporter
            reporter = GeoLevelReporter(self.result, self.fixed_points)
            reporter.generate_pdf(path)
            QMessageBox.information(
                self, "Report Saved / דוח נשמר",
                f"PDF report saved successfully:\n{path}"
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", str(exc))

    # ------------------------------------------------------------------
    def _populate(self):
        r = self.result

        # Heights table — fixed points first, then unknowns
        all_pts = sorted(r.adjusted_heights.keys())
        fixed_first = [p for p in all_pts if p in self.fixed_points] + \
                      [p for p in all_pts if p not in self.fixed_points]

        self.heights_table.setRowCount(len(fixed_first))
        for row, pid in enumerate(fixed_first):
            adj_h = r.adjusted_heights[pid]
            sigma_mm = r.mse_heights.get(pid, 0.0) * 1000

            # Correction = adjusted − original (only meaningful for fixed points)
            if pid in self.fixed_points:
                corr_mm = (adj_h - self.fixed_points[pid]) * 1000
                corr_str = f"{corr_mm:+.2f}"
            else:
                corr_str = "—"

            self.heights_table.setItem(row, 0, QTableWidgetItem(str(pid)))
            self.heights_table.setItem(row, 1, QTableWidgetItem(f"{adj_h:.5f}"))
            self.heights_table.setItem(row, 2, QTableWidgetItem(corr_str))
            self.heights_table.setItem(row, 3, QTableWidgetItem(f"{sigma_mm:.3f}"))

            # Colour fixed points light-blue
            if pid in self.fixed_points:
                for col in range(4):
                    item = self.heights_table.item(row, col)
                    if item:
                        item.setBackground(QColor(200, 220, 255))

        # Residuals table
        self.resid_table.setRowCount(len(r.residuals))
        for row, (obs_id, v_mm) in enumerate(sorted(r.residuals.items())):
            self.resid_table.setItem(row, 0, QTableWidgetItem(obs_id))
            item = QTableWidgetItem(f"{v_mm:+.3f}")
            # Highlight large residuals (>5 mm) in orange
            if abs(v_mm) > 5.0:
                item.setBackground(QColor(255, 200, 100))
            self.resid_table.setItem(row, 1, item)
