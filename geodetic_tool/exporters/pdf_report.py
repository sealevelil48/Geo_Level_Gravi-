"""
PDF report of a network / line adjustment for the standalone application.

Uses matplotlib (DejaVu Sans Mono, also shows Hebrew point names) when it
is installed; otherwise a small built-in PDF writer (Courier, Latin text).
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

LINES_PER_PAGE = 62


def report_lines(result, fixed_points: Dict[str, float], project_name: str = "",
                 leveling_class: str = "H3") -> List[str]:
    """The report as plain text lines (also used for the .txt export)."""
    fixed_points = fixed_points or {}
    out = [
        "Geo Level Gravi - Leveling Adjustment Report",
        f"Project: {project_name}    Class: {leveling_class}    "
        f"Date: {datetime.now():%Y-%m-%d %H:%M}",
        "Directive G2 (2021) - least squares adjustment",
        "=" * 78,
        f"Method              : {'Parametric  V = Ax - L' if result.method == 'parametric' else 'Conditional  Bv + W = 0'}",
        f"Weights             : {getattr(result, 'weight_mode', 'distance')}",
        f"Observations        : {len(result.observations)}",
        f"Fixed points        : {len(fixed_points)}",
        f"Degrees of freedom  : {result.degrees_of_freedom}",
        f"Sigma0 (mm/sqrt km) : {result.mse_unit_weight * 1000:.3f}",
        f"Total length (km)   : {result.total_distance_km:.3f}",
        "",
    ]
    if result.benchmark_misclosures:
        out.append("Misclosures between fixed points (k*sqrt(L))")
        for b in result.benchmark_misclosures:
            out.append(f"  {b['from']:>10} -> {b['to']:<10} f = {b['misclosure_mm']:8.2f} mm"
                       f"  allowed {b['allowed_mm']:6.2f} mm  {'OK' if b['ok'] else 'EXCEEDED'}")
        out.append("")
    out.append("Adjusted heights")
    out.append(f"  {'Point':<12}{'Adjusted (m)':>14}{'Approx (m)':>14}{'Corr (mm)':>11}"
               f"{'Std (mm)':>10}{'95% (mm)':>10}")
    out.append("  " + "-" * 71)
    for pid in sorted(result.adjusted_heights):
        h = result.adjusted_heights[pid]
        if pid in fixed_points:
            out.append(f"  {pid:<12}{h:14.5f}{'fixed':>14}")
            continue
        sd = result.mse_heights.get(pid, 0.0) * 1000
        out.append(f"  {pid:<12}{h:14.5f}{result.approximate_heights.get(pid, h):14.5f}"
                   f"{result.corrections.get(pid, 0.0) * 1000:11.2f}{sd:10.2f}{1.96 * sd:10.2f}")
    out.append("")
    out.append("Observations")
    out.append(f"  {'From':<10}{'To':<10}{'Measured dH':>13}{'Adjusted dH':>13}"
               f"{'v (mm)':>9}{'L (m)':>10}{'Runs':>6}")
    out.append("  " + "-" * 71)
    for o in result.observations:
        out.append(f"  {o['from']:<10}{o['to']:<10}{o['measured_dh']:13.5f}{o['adjusted_dh']:13.5f}"
                   f"{o['residual_mm']:9.2f}{o['distance']:10.1f}{o.get('n_runs', 1):6d}")
    out += ["", "Surveyor: ____________________    Signature: ____________________"]
    return out


def write_adjustment_pdf(path: str, result, fixed_points: Dict[str, float],
                         project_name: str = "", leveling_class: str = "H3") -> str:
    text = report_lines(result, fixed_points, project_name, leveling_class)
    try:
        return _write_matplotlib(path, text)
    except ImportError:
        return _write_plain_pdf(path, text)


def _write_matplotlib(path: str, text: List[str]) -> str:
    import matplotlib
    matplotlib.use("Agg", force=False)
    from matplotlib.backends.backend_pdf import PdfPages
    import matplotlib.pyplot as plt
    with PdfPages(path) as pdf:
        for start in range(0, max(len(text), 1), LINES_PER_PAGE):
            fig = plt.figure(figsize=(8.27, 11.69))     # A4
            fig.text(0.06, 0.96, "\n".join(text[start:start + LINES_PER_PAGE]),
                     family="DejaVu Sans Mono", fontsize=7.5, va="top")
            pdf.savefig(fig)
            plt.close(fig)
    return path


def _pdf_escape(s: str) -> str:
    s = s.encode("latin-1", "replace").decode("latin-1")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _write_plain_pdf(path: str, text: List[str]) -> str:
    """Minimal multi-page PDF (Courier 8 pt, A4)."""
    pages = [text[i:i + LINES_PER_PAGE] for i in range(0, max(len(text), 1), LINES_PER_PAGE)]
    objs: List[Optional[bytes]] = [None, None]          # 1: catalog, 2: pages
    font_id = 3
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    page_ids = []
    for lines in pages:
        body = ["BT /F1 8 Tf 40 800 Td 11 TL"]
        body += [f"({_pdf_escape(ln)}) Tj T*" for ln in lines]
        body.append("ET")
        stream = "\n".join(body).encode("latin-1")
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        content_id = len(objs)
        objs.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                     f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                     f"/Contents {content_id} 0 R >>").encode())
        page_ids.append(len(objs))
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = ("<< /Type /Pages /Kids [" + " ".join(f"{i} 0 R" for i in page_ids)
               + f"] /Count {len(page_ids)} >>").encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, ob in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + ob + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    with open(path, "wb") as f:
        f.write(out)
    return path
