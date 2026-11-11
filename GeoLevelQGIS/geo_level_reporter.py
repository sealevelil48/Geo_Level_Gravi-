"""
geo_level_reporter.py
Branded PDF report generator for Geo Level Gravi.

Uses only QGIS-bundled libraries (QPrinter, QTextDocument, QImage).
Zero external dependencies — works in any OSGeo4W environment.
"""

import os
from datetime import datetime

from qgis.PyQt.QtPrintSupport import QPrinter
from qgis.PyQt.QtGui import (
    QTextDocument, QTextCursor, QTextTableFormat, QTextLength,
    QTextCharFormat, QTextBlockFormat, QFont, QColor, QImage
)
from qgis.PyQt.QtCore import Qt, QSizeF, QUrl


class GeoLevelReporter:
    """
    Generate a formal A4 PDF survey certificate from LSA results.

    Accepts either an ``AdjustmentResult`` object (normal usage from the LSA
    results dialog) or the legacy ``{stats, points, residuals}`` dict format.
    """

    # ------------------------------------------------------------------ #
    # Colours
    # ------------------------------------------------------------------ #
    _NAVY   = QColor(0,   51, 102)
    _BLUE   = QColor(173, 216, 230)   # light-blue for fixed points
    _ORANGE = QColor(255, 200, 100)   # residuals > 5 mm
    _WHITE  = QColor(255, 255, 255)
    _LGRAY  = QColor(240, 240, 240)

    def __init__(self, result_data, fixed_points=None, project_info=None):
        """
        Parameters
        ----------
        result_data : AdjustmentResult  OR  dict
            Accepts either an ``AdjustmentResult`` object (the normal case when
            called from ``GeoLevelLSADialog.on_export_report``) or the legacy
            dict format ``{stats: {...}, points: {...}, residuals: [...]}``.
            When an ``AdjustmentResult`` is supplied it is converted to the
            internal dict representation automatically.
        fixed_points : dict | list | set | None
            Known benchmarks: either a ``{point_id: height}`` dict (from the
            dialog) or a list/set of IDs.
        project_info : dict, optional
            company, surveyor, project_name, class keys for the header.
        """
        # Normalise fixed_points to a set of IDs regardless of input type.
        if isinstance(fixed_points, dict):
            self.fixed_points = set(fixed_points.keys())
        else:
            self.fixed_points = set(fixed_points or [])

        self.project_info = project_info or {}
        self.logo_path    = os.path.join(os.path.dirname(__file__),
                                         'survey_of_israel_logo.png')

        # Convert AdjustmentResult → internal dict if needed.
        if isinstance(result_data, dict):
            self.results = result_data
        else:
            self.results = self._from_adjustment_result(result_data, fixed_points)

    # ------------------------------------------------------------------ #
    # AdjustmentResult → internal dict converter
    # ------------------------------------------------------------------ #

    def _from_adjustment_result(self, result, fixed_points_input):
        """
        Build the internal ``{stats, points, residuals}`` dict from an
        ``AdjustmentResult`` object.

        Parameters
        ----------
        result            : AdjustmentResult
        fixed_points_input: dict {pid: height} | list | set | None
        """
        # fixed heights lookup (may be dict or collection)
        if isinstance(fixed_points_input, dict):
            fp_heights = fixed_points_input
        else:
            fp_heights = {}

        n_obs      = len(result.residuals)
        n_unknowns = len(result.adjusted_heights) - len(self.fixed_points)
        dof        = max(0, n_obs - n_unknowns)
        sigma_sq   = result.mse_unit_weight ** 2

        stats = {
            "sigma_zero_sq":  sigma_sq,
            "sigma_zero":     result.mse_unit_weight * 1000,   # mm
            "dof":            dof,
            "iterations":     result.iteration,
            "k_coeff":        result.k_coefficient,
            "total_dist_km":  result.total_distance_km,
        }

        points = {}
        for pid, adj_h in result.adjusted_heights.items():
            is_fixed   = pid in self.fixed_points
            sigma_mm   = result.mse_heights.get(pid, 0.0) * 1000
            orig_h     = fp_heights.get(pid, adj_h)
            corr_mm    = (adj_h - orig_h) * 1000 if is_fixed else (
                result.residuals.get(
                    # try to find a residual keyed by this point as destination
                    next((k for k in result.residuals if k.endswith("-" + pid)), ""),
                    0.0
                )
            )
            points[pid] = {
                "height":        adj_h,
                "correction_mm": corr_mm,
                "sigma_mm":      sigma_mm,
                "is_fixed":      is_fixed,
            }

        residuals = [
            {"key": key, "residual_mm": v_mm}
            for key, v_mm in sorted(result.residuals.items())
        ]

        return {"stats": stats, "points": points, "residuals": residuals}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def generate_pdf(self, output_path: str) -> str:
        """Render the report to *output_path* and return the path."""
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setPageSize(QPrinter.A4)
        printer.setOutputFileName(output_path)

        doc  = QTextDocument()
        doc.setDefaultFont(QFont("Arial", 10))
        doc.setDocumentMargin(20)

        # RTL default so Hebrew labels render correctly
        opt = doc.defaultTextOption()
        opt.setTextDirection(Qt.RightToLeft)
        doc.setDefaultTextOption(opt)

        cursor = QTextCursor(doc)

        self._insert_header(cursor, printer)
        self._insert_stats(cursor)
        self._insert_heights_table(cursor)
        self._insert_residuals_table(cursor)
        self._insert_stamp(cursor)

        doc.print_(printer)
        return output_path

    # ------------------------------------------------------------------ #
    # Section builders
    # ------------------------------------------------------------------ #

    def _insert_header(self, cursor: QTextCursor, printer: QPrinter):
        """Logo + bilingual title + meta block + navy rule."""

        # --- Logo ---
        img = QImage(self.logo_path)
        if not img.isNull():
            scaled = img.scaledToHeight(80, Qt.SmoothTransformation)
            doc    = cursor.document()
            doc.addResource(QTextDocument.ImageResource,
                            QUrl("logo://survey"),
                            scaled)
            img_fmt = QTextCharFormat()
            cursor.insertImage(scaled)
            cursor.insertText("\n")

        # --- Title ---
        fmt_title = QTextCharFormat()
        fmt_title.setFontWeight(QFont.Bold)
        fmt_title.setFontPointSize(16)
        fmt_title.setForeground(self._NAVY)
        cursor.insertText("Geodetic Leveling Report — LSA Results\n", fmt_title)

        fmt_heb = QTextCharFormat()
        fmt_heb.setFontPointSize(13)
        fmt_heb.setForeground(self._NAVY)
        cursor.insertText("דוח פילוס גיאודטי — תוצאות כיוון ריבועים פחותים\n\n", fmt_heb)

        # --- Meta ---
        fmt_meta = QTextCharFormat()
        fmt_meta.setFontPointSize(9)
        pi = self.project_info
        now = datetime.now().strftime("%Y-%m-%d  %H:%M")
        lines = [
            f"Company / חברה    : {pi.get('company',  '___________________')}",
            f"Surveyor / מודד   : {pi.get('surveyor', '___________________')}",
            f"Project / פרויקט  : {pi.get('project_name', '___________________')}",
            f"Class / דרגה      : {pi.get('class', 'H3')}",
            f"Date / תאריך      : {now}",
            f"Compliance        : Israeli Survey Regulations — Directive \u05d22 (2021)",
        ]
        for ln in lines:
            cursor.insertText(ln + "\n", fmt_meta)

        # --- Navy rule ---
        fmt_rule = QTextCharFormat()
        fmt_rule.setForeground(self._NAVY)
        fmt_rule.setFontPointSize(6)
        cursor.insertText("\n" + "─" * 90 + "\n\n", fmt_rule)

    def _insert_stats(self, cursor: QTextCursor):
        """2-column statistics table."""
        fmt_h = self._heading_fmt()
        cursor.insertText("Network Statistics / סטטיסטיקת רשת\n", fmt_h)

        stats = self.results.get("stats", {})
        rows = [
            ("σ₀²  (Reference Variance)",        f"{stats.get('sigma_zero_sq', 0):.6f}"),
            ("σ₀   (Unit Weight Std Dev mm)",     f"{stats.get('sigma_zero',    0):.4f}"),
            ("Degrees of Freedom",                str(stats.get("dof",          0))),
            ("Iterations",                        str(stats.get("iterations",   0))),
            ("K Coefficient",                     f"{stats.get('k_coeff',       0):.4f}"),
            ("Total Distance (km)",               f"{stats.get('total_dist_km', 0):.3f}"),
            ("Fixed Points",                      str(len(self.fixed_points))),
            ("Adjusted Points",                   str(len(self.results.get("points", {}))
                                                       - len(self.fixed_points))),
        ]

        tbl_fmt = self._table_fmt(2)
        tbl = cursor.insertTable(len(rows), 2, tbl_fmt)
        for r, (label, value) in enumerate(rows):
            tbl.cellAt(r, 0).firstCursorPosition().insertText(label)
            tbl.cellAt(r, 1).firstCursorPosition().insertText(value)

        cursor.movePosition(QTextCursor.End)
        cursor.insertText("\n\n")

    def _insert_heights_table(self, cursor: QTextCursor):
        """5-column adjusted heights table with colour coding."""
        fmt_h = self._heading_fmt()
        cursor.insertText("Adjusted Heights / גבהים מתואמים\n", fmt_h)

        points = self.results.get("points", {})
        if not points:
            cursor.insertText("No adjusted heights available.\n\n")
            return

        headers = ["Point ID", "Height (m)", "Correction (mm)", "Std Dev (mm)", "Type"]
        tbl_fmt = self._table_fmt(len(headers))
        tbl_fmt.setHeaderRowCount(1)
        tbl = cursor.insertTable(len(points) + 1, len(headers), tbl_fmt)

        # Header row — navy background
        for c, h in enumerate(headers):
            cell   = tbl.cellAt(0, c)
            cfmt   = cell.format()
            cfmt.setBackground(self._NAVY)
            cell.setFormat(cfmt)
            hfmt = QTextCharFormat()
            hfmt.setForeground(self._WHITE)
            hfmt.setFontWeight(QFont.Bold)
            cell.firstCursorPosition().insertText(h, hfmt)

        # Data rows
        for row, (pid, data) in enumerate(points.items(), start=1):
            is_fixed = pid in self.fixed_points
            bg = self._BLUE if is_fixed else self._WHITE

            values = [
                str(pid),
                f"{data.get('height', 0):.4f}",
                f"{data.get('correction_mm', 0):.1f}" if is_fixed else "—",
                f"{data.get('sigma_mm', 0):.2f}",
                "Fixed" if is_fixed else "Adjusted",
            ]
            for c, val in enumerate(values):
                cell = tbl.cellAt(row, c)
                cfmt = cell.format()
                cfmt.setBackground(bg)
                cell.setFormat(cfmt)
                cell.firstCursorPosition().insertText(val)

        cursor.movePosition(QTextCursor.End)
        cursor.insertText("\n\n")

    def _insert_residuals_table(self, cursor: QTextCursor):
        """2-column residuals table; rows > 5 mm highlighted orange."""
        fmt_h = self._heading_fmt()
        cursor.insertText("Observation Residuals / שאריות תצפיות\n", fmt_h)

        residuals = self.results.get("residuals", [])
        if not residuals:
            cursor.insertText("No residuals available.\n\n")
            return

        headers = ["Observation (FROM–TO)", "Residual (mm)"]
        tbl_fmt = self._table_fmt(2)
        tbl_fmt.setHeaderRowCount(1)
        tbl = cursor.insertTable(len(residuals) + 1, 2, tbl_fmt)

        for c, h in enumerate(headers):
            cell = tbl.cellAt(0, c)
            cfmt = cell.format()
            cfmt.setBackground(self._NAVY)
            cell.setFormat(cfmt)
            hfmt = QTextCharFormat()
            hfmt.setForeground(self._WHITE)
            hfmt.setFontWeight(QFont.Bold)
            cell.firstCursorPosition().insertText(h, hfmt)

        for row, item in enumerate(residuals, start=1):
            res_mm = item.get("residual_mm", 0)
            bg     = self._ORANGE if abs(res_mm) > 5 else self._WHITE
            for c, val in enumerate([item.get("key", ""), f"{res_mm:.2f}"]):
                cell = tbl.cellAt(row, c)
                cfmt = cell.format()
                cfmt.setBackground(bg)
                cell.setFormat(cfmt)
                cell.firstCursorPosition().insertText(val)

        cursor.movePosition(QTextCursor.End)
        cursor.insertText("\n\n")

    def _insert_stamp(self, cursor: QTextCursor):
        """Digital stamp + authorised signature line."""
        year = datetime.now().year
        fmt  = QTextCharFormat()
        fmt.setFontPointSize(9)
        fmt.setForeground(self._NAVY)

        cursor.insertText("─" * 90 + "\n", fmt)
        cursor.insertText(f"Digital Stamp: [GeoLevel-LSA-Verified-{year}]\n\n", fmt)

        fmt_sig = QTextCharFormat()
        fmt_sig.setFontPointSize(10)
        cursor.insertText("_" * 35 + "\n", fmt_sig)
        cursor.insertText("Authorized Signature / חתימה מאושרת\n", fmt_sig)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _heading_fmt() -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Bold)
        fmt.setFontPointSize(12)
        fmt.setForeground(QColor(0, 51, 102))
        return fmt

    @staticmethod
    def _table_fmt(cols: int) -> QTextTableFormat:
        fmt = QTextTableFormat()
        fmt.setBorderStyle(QTextTableFormat.BorderStyle_Solid)
        fmt.setBorder(0.5)
        fmt.setCellPadding(4)
        fmt.setCellSpacing(0)
        fmt.setWidth(QTextLength(QTextLength.PercentageLength, 100))
        return fmt
