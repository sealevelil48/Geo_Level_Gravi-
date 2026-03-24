"""
geo_level_reporter.py
PDF report generator for LSA results using Qt's built-in print support.
Zero external dependencies — QPrinter is bundled with QGIS/OSGeo4W.
"""
from datetime import datetime

from qgis.PyQt.QtPrintSupport import QPrinter
from qgis.PyQt.QtGui import (
    QTextDocument, QTextCursor, QTextCharFormat, QTextBlockFormat,
    QTextTableFormat, QFont, QColor, QPageSize
)
from qgis.PyQt.QtCore import Qt, QSizeF


# ── Hebrew detection ──────────────────────────────────────────────────────────
def _has_hebrew(text: str) -> bool:
    return any('\u05d0' <= ch <= '\u05ea' for ch in text)


def _detect_bidi(result, fixed_points: dict) -> bool:
    """Return True if any point ID or project field contains Hebrew."""
    all_ids = list(result.adjusted_heights.keys()) + list(fixed_points.keys())
    return any(_has_hebrew(pid) for pid in all_ids)


# ── Format helpers ────────────────────────────────────────────────────────────
def _char_fmt(size: float = 10, bold: bool = False,
              color: QColor = None) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setFontPointSize(size)
    if bold:
        fmt.setFontWeight(QFont.Bold)
    if color:
        fmt.setForeground(color)
    return fmt


def _block_fmt(align=Qt.AlignLeft, top_margin: float = 6,
               bottom_margin: float = 2) -> QTextBlockFormat:
    fmt = QTextBlockFormat()
    fmt.setAlignment(align)
    fmt.setTopMargin(top_margin)
    fmt.setBottomMargin(bottom_margin)
    return fmt


class GeoLevelReporter:
    """
    Generates a formal PDF survey report from an AdjustmentResult.

    Usage:
        reporter = GeoLevelReporter(result, fixed_points, project_info)
        reporter.generate_pdf("/path/to/report.pdf")
    """

    COMPANY_PLACEHOLDER = "[Company Name / שם החברה]"
    COMPLIANCE_TEXT = "Compliant with Survey of Israel — Directive \u05d32 (2021)"

    def __init__(self, result, fixed_points: dict, project_info: dict = None):
        """
        Args:
            result:       AdjustmentResult from LeastSquaresAdjuster
            fixed_points: {point_id: original_height} used in the adjustment
            project_info: optional dict with keys: company, surveyor, project_name, class
        """
        self.result = result
        self.fixed_points = fixed_points
        self.info = project_info or {}
        self._bidi = _detect_bidi(result, fixed_points)

    # ── Public API ────────────────────────────────────────────────────────────

    def generate_pdf(self, output_path: str) -> str:
        """Render the report to a PDF file. Returns output_path."""
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setPageSize(QPrinter.A4)
        printer.setOutputFileName(output_path)
        printer.setPageMargins(20, 20, 20, 20, QPrinter.Millimeter)

        doc = QTextDocument()
        doc.setPageSize(QSizeF(printer.pageRect().size()))

        # BiDi: if Hebrew detected, set document default direction RTL
        if self._bidi:
            from qgis.PyQt.QtGui import QTextOption
            opt = QTextOption()
            opt.setTextDirection(Qt.RightToLeft)
            doc.setDefaultTextOption(opt)

        cursor = QTextCursor(doc)
        self._write_header(cursor)
        self._write_stats(cursor)
        self._write_heights_table(cursor)
        self._write_residuals_table(cursor)
        self._write_stamp(cursor)

        doc.print_(printer)
        return output_path

    # ── Section writers ───────────────────────────────────────────────────────

    def _write_header(self, cursor: QTextCursor):
        # Title
        cursor.setBlockFormat(_block_fmt(Qt.AlignCenter, top_margin=0))
        cursor.insertText(
            "Geodetic Leveling Report — LSA Results\n"
            "\u05d3\u05d5\u05d7 \u05e4\u05d9\u05dc\u05d5\u05e1 \u05d2\u05d9\u05d0\u05d5\u05d3\u05d8\u05d9 — \u05ea\u05d5\u05e6\u05d0\u05d5\u05ea \u05ea\u05d0\u05d5\u05dd \u05e8\u05e9\u05ea",
            _char_fmt(size=16, bold=True)
        )
        cursor.insertBlock()

        # Company / surveyor meta
        cursor.setBlockFormat(_block_fmt(Qt.AlignCenter))
        company = self.info.get("company", self.COMPANY_PLACEHOLDER)
        surveyor = self.info.get("surveyor", "")
        project = self.info.get("project_name", "")
        cls = self.info.get("class", "")

        meta_lines = [
            f"Company / חברה: {company}",
        ]
        if surveyor:
            meta_lines.append(f"Surveyor / מודד: {surveyor}")
        if project:
            meta_lines.append(f"Project / פרויקט: {project}")
        if cls:
            meta_lines.append(f"Precision Class / דרגת דיוק: {cls}")
        meta_lines += [
            f"Date / תאריך: {datetime.now().strftime('%Y-%m-%d  %H:%M')}",
            self.COMPLIANCE_TEXT,
        ]
        cursor.insertText("\n".join(meta_lines), _char_fmt(size=9))
        cursor.insertBlock()

        # Horizontal rule via a thin table
        self._insert_rule(cursor)

    def _write_stats(self, cursor: QTextCursor):
        r = self.result
        dof = max(0, len(r.residuals) - (
            len(r.adjusted_heights) - len(self.fixed_points)
        ))
        ref_var = r.mse_unit_weight ** 2

        cursor.setBlockFormat(_block_fmt(top_margin=10))
        cursor.insertText(
            "Network Statistics / \u05e1\u05d8\u05d8\u05d9\u05e1\u05d8\u05d9\u05e7\u05ea \u05e8\u05e9\u05ea",
            _char_fmt(size=12, bold=True)
        )
        cursor.insertBlock()

        stats = [
            (f"Reference Variance (\u03c3\u2080\u00b2)", f"{ref_var:.6f} m\u00b2"),
            (f"Unit Weight Std Dev (\u03c3\u2080)", f"{r.mse_unit_weight * 1000:.3f} mm"),
            ("Degrees of Freedom", str(dof)),
            ("Iterations", str(r.iteration)),
            ("K Coefficient", f"{r.k_coefficient:.4f}"),
            ("Total Network Distance", f"{r.total_distance_km:.3f} km"),
            ("Fixed Points", str(len(self.fixed_points))),
            ("Adjusted Points", str(len(r.adjusted_heights) - len(self.fixed_points))),
        ]

        tbl_fmt = self._table_fmt(cols=2)
        tbl = cursor.insertTable(len(stats), 2, tbl_fmt)
        lbl_fmt = _char_fmt(size=9, bold=True)
        val_fmt = _char_fmt(size=9)
        for row, (label, value) in enumerate(stats):
            tbl.cellAt(row, 0).firstCursorPosition().insertText(label, lbl_fmt)
            tbl.cellAt(row, 1).firstCursorPosition().insertText(value, val_fmt)

        cursor.movePosition(QTextCursor.End)
        cursor.insertBlock()

    def _write_heights_table(self, cursor: QTextCursor):
        r = self.result
        all_pts = sorted(r.adjusted_heights.keys())
        fixed_first = [p for p in all_pts if p in self.fixed_points] + \
                      [p for p in all_pts if p not in self.fixed_points]

        cursor.setBlockFormat(_block_fmt(top_margin=10))
        cursor.insertText(
            "Adjusted Heights / \u05d2\u05d1\u05d4\u05d9\u05dd \u05de\u05ea\u05d5\u05d0\u05de\u05d9\u05dd",
            _char_fmt(size=12, bold=True)
        )
        cursor.insertBlock()

        headers = ["Point ID", "Adj. Height (m)", "Correction (mm)", "Std Dev (mm)", "Type"]
        tbl_fmt = self._table_fmt(cols=len(headers))
        tbl = cursor.insertTable(len(fixed_first) + 1, len(headers), tbl_fmt)

        hdr_fmt = _char_fmt(size=9, bold=True, color=QColor(255, 255, 255))
        hdr_bg = QColor(44, 62, 80)  # dark navy

        for col, h in enumerate(headers):
            cell_cur = tbl.cellAt(0, col).firstCursorPosition()
            blk = QTextBlockFormat()
            blk.setBackground(hdr_bg)
            cell_cur.setBlockFormat(blk)
            cell_cur.insertText(h, hdr_fmt)

        row_fmt_fixed = QTextBlockFormat()
        row_fmt_fixed.setBackground(QColor(200, 220, 255))  # light blue for fixed
        val_fmt = _char_fmt(size=9)

        for row, pid in enumerate(fixed_first, start=1):
            adj_h = r.adjusted_heights[pid]
            sigma_mm = r.mse_heights.get(pid, 0.0) * 1000
            is_fixed = pid in self.fixed_points

            if is_fixed:
                corr_mm = (adj_h - self.fixed_points[pid]) * 1000
                corr_str = f"{corr_mm:+.2f}"
                type_str = "Fixed / קבוע"
            else:
                corr_str = "—"
                type_str = "Adjusted / מתואם"

            row_data = [str(pid), f"{adj_h:.5f}", corr_str, f"{sigma_mm:.3f}", type_str]
            for col, val in enumerate(row_data):
                cell_cur = tbl.cellAt(row, col).firstCursorPosition()
                if is_fixed:
                    cell_cur.setBlockFormat(row_fmt_fixed)
                cell_cur.insertText(val, val_fmt)

        cursor.movePosition(QTextCursor.End)
        cursor.insertBlock()

    def _write_residuals_table(self, cursor: QTextCursor):
        r = self.result
        if not r.residuals:
            return

        cursor.setBlockFormat(_block_fmt(top_margin=10))
        cursor.insertText(
            "Observation Residuals / \u05e9\u05d0\u05e8\u05d9\u05d5\u05ea \u05ea\u05e6\u05e4\u05d9\u05d5\u05ea",
            _char_fmt(size=12, bold=True)
        )
        cursor.insertBlock()

        sorted_resid = sorted(r.residuals.items())
        tbl_fmt = self._table_fmt(cols=2)
        tbl = cursor.insertTable(len(sorted_resid) + 1, 2, tbl_fmt)

        hdr_fmt = _char_fmt(size=9, bold=True, color=QColor(255, 255, 255))
        hdr_bg = QColor(44, 62, 80)
        for col, h in enumerate(["Observation", "Residual (mm)"]):
            cell_cur = tbl.cellAt(0, col).firstCursorPosition()
            blk = QTextBlockFormat()
            blk.setBackground(hdr_bg)
            cell_cur.setBlockFormat(blk)
            cell_cur.insertText(h, hdr_fmt)

        warn_bg = QTextBlockFormat()
        warn_bg.setBackground(QColor(255, 200, 100))
        val_fmt = _char_fmt(size=9)

        for row, (obs_id, v_mm) in enumerate(sorted_resid, start=1):
            tbl.cellAt(row, 0).firstCursorPosition().insertText(obs_id, val_fmt)
            cell_cur = tbl.cellAt(row, 1).firstCursorPosition()
            if abs(v_mm) > 5.0:
                cell_cur.setBlockFormat(warn_bg)
            cell_cur.insertText(f"{v_mm:+.3f}", val_fmt)

        cursor.movePosition(QTextCursor.End)
        cursor.insertBlock()

    def _write_stamp(self, cursor: QTextCursor):
        cursor.setBlockFormat(_block_fmt(top_margin=20))
        self._insert_rule(cursor)

        cursor.setBlockFormat(_block_fmt(Qt.AlignCenter, top_margin=8))
        stamp_text = (
            f"Digital Stamp: [GeoLevel-LSA-Verified-{datetime.now().year}]\n"
            "Authorized Signature / \u05d7\u05ea\u05d9\u05de\u05d4 \u05de\u05d0\u05d5\u05e9\u05e8\u05ea\n\n"
            "_" * 35 + "\n"
            f"{self.info.get('surveyor', 'Licensed Surveyor / מודד מוסמך')}\n"
        )
        cursor.insertText(stamp_text, _char_fmt(size=9))

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _table_fmt(cols: int) -> QTextTableFormat:
        fmt = QTextTableFormat()
        fmt.setBorder(0.5)
        fmt.setBorderStyle(QTextTableFormat.BorderStyle_Solid)
        fmt.setCellPadding(4)
        fmt.setCellSpacing(0)
        fmt.setWidth(100)  # percentage — full width
        return fmt

    @staticmethod
    def _insert_rule(cursor: QTextCursor):
        """Insert a full-width horizontal rule via a 1-row borderless table."""
        fmt = QTextTableFormat()
        fmt.setBorder(0)
        fmt.setCellPadding(0)
        fmt.setCellSpacing(0)
        fmt.setWidth(100)
        tbl = cursor.insertTable(1, 1, fmt)
        blk = QTextBlockFormat()
        blk.setBackground(QColor(44, 62, 80))
        blk.setTopMargin(1)
        blk.setBottomMargin(1)
        tbl.cellAt(0, 0).firstCursorPosition().setBlockFormat(blk)
        cursor.movePosition(QTextCursor.End)
