"""
The single adjustment window of the standalone application
("Network Adjustment (LSA)") - Tkinter port of the QGIS plugin window.

Left : method (Parametric V = Ax - L / Conditional Bv + W = 0), lines in the
       adjustment (double-click to tick/untick), fixed points (Auto-Select from
       .FA0/.FA1 heights), options (Step 1 averaging, weights, class)
       Buttons: Prepare (Steps 1-2) | Run | Export FA0 / FA1 / PDF | Close
Right: Summary | Step 1: Observations | Step 2: Approximate Heights |
       Adjusted Heights | Residuals | Line Adjustment
Opened from "Adjust This Line" the same window starts with only that line
ticked; a line whose two ends are fixed gets the Directive G2 line adjustment.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from ..config.models import Benchmark, MeasurementSummary
from .window_utils import fit_to_screen
from ..engine.least_squares import (
    ConditionalAdjuster, LeastSquaresAdjuster, lines_to_observations)
from ..engine.network_prep import (
    MergedRun, NetworkDatumError, _obs_fields, adjust_single_line,
    approximate_heights, merge_observations, tolerance_mm)

WEIGHTS = [("Distance  p = n/L(km)", "distance"),
           ("Setups  p = n/setups", "setups"),
           ("Equal  p = n", "equal")]
CHECK, NOCHECK = "☑", "☐"


class AdjustmentWindow(tk.Toplevel):
    def __init__(self, parent, lines, leveling_class: str = "H3",
                 known_heights: Optional[Dict[str, float]] = None,
                 selected_lines=None, project_name: str = "geo_level",
                 output_dir: str = "", db_heights=None,
                 approx_heights: Optional[Dict[str, float]] = None):
        super().__init__(parent)
        self.title("Network Adjustment (LSA) / תיאום רשת")
        fit_to_screen(self, 1280, 780)
        self.lines = list(lines or [])
        self.known_heights = dict(known_heights or {})
        # db_heights(pids) -> {pid: height}: one batched benchmark-DB lookup
        self.db_heights = db_heights
        # approximate heights listed in the loaded FA0 / FA1 (not fixed)
        self.approx_heights = dict(approx_heights or {})
        self._run_obs = None             # observations of the last run (FA1)
        self.project_name = project_name or "geo_level"
        self.output_dir = output_dir or ""
        self.result = None
        self.fixed_points: Dict[str, float] = {}
        self._report: List[MergedRun] = []
        sel = {id(l) for l in selected_lines} if selected_lines else None
        self._checked = {id(l): (self._eligible(l) and (sel is None or id(l) in sel))
                         for l in self.lines}

        self.method_var = tk.StringVar(value="parametric")
        self.merge_var = tk.BooleanVar(value=True)
        self.weight_var = tk.StringVar(value=WEIGHTS[0][0])
        self.class_var = tk.StringVar(value=leveling_class or "H3")
        self._build()
        self._fill_lines()
        if sel:
            self.auto_select()

    # ------------------------------------------------------------------ UI
    def _build(self):
        pw = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        pw.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(pw, padding=6)
        right = ttk.Frame(pw, padding=6)
        pw.add(left, weight=1)
        pw.add(right, weight=2)

        m = ttk.LabelFrame(left, text="Adjustment Method / שיטת תיאום")
        m.pack(fill=tk.X)
        ttk.Radiobutton(m, text="Parametric (Observations)  V = Ax − L",
                        variable=self.method_var, value="parametric").pack(anchor=tk.W)
        ttk.Radiobutton(m, text="Conditional (Constraints)  Bv + W = 0",
                        variable=self.method_var, value="conditional").pack(anchor=tk.W)

        lf = ttk.LabelFrame(left, text="Lines in adjustment (double-click to tick)")
        lf.pack(fill=tk.BOTH, expand=True, pady=4)
        self.lines_tree = self._tree(lf, ("use", "line", "route", "dh", "status"),
                                     ("Use", "Line", "From → To", "dH (m)", "Status"),
                                     (40, 140, 160, 90, 110), height=8)
        self.lines_tree.bind("<Double-1>", self._toggle_line)
        row = ttk.Frame(lf)
        row.pack(fill=tk.X)
        ttk.Button(row, text="All eligible", command=lambda: self._check_all(True)).pack(side=tk.LEFT)
        ttk.Button(row, text="None", command=lambda: self._check_all(False)).pack(side=tk.LEFT)
        self.lines_label = ttk.Label(row, text="")
        self.lines_label.pack(side=tk.LEFT, padx=6)

        ff = ttk.LabelFrame(left, text="Fixed Points / נקודות קבועות")
        ff.pack(fill=tk.BOTH, expand=True, pady=4)
        self.fp_tree = self._tree(ff, ("pid", "h"), ("Point ID", "Known Height (m)"),
                                  (150, 150), height=5)
        er = ttk.Frame(ff)
        er.pack(fill=tk.X)
        self.pid_entry = ttk.Entry(er, width=12)
        self.h_entry = ttk.Entry(er, width=12)
        self.pid_entry.pack(side=tk.LEFT)
        self.h_entry.pack(side=tk.LEFT, padx=2)
        ttk.Button(er, text="+ Add", command=self._add_fp_from_entries).pack(side=tk.LEFT)
        ttk.Button(er, text="− Remove", command=self._remove_fp).pack(side=tk.LEFT)
        ttk.Button(er, text="Auto-Select", command=self.auto_select).pack(side=tk.LEFT)
        if self.db_heights is not None:
            ttk.Button(er, text="Fetch from DB", command=self.fetch_from_db).pack(side=tk.LEFT)

        of = ttk.LabelFrame(left, text="Options / אפשרויות")
        of.pack(fill=tk.X, pady=4)
        ttk.Checkbutton(of, text="Step 1: average repeated runs (forward / return)",
                        variable=self.merge_var).pack(anchor=tk.W)
        orow = ttk.Frame(of)
        orow.pack(fill=tk.X)
        ttk.Label(orow, text="Weights:").pack(side=tk.LEFT)
        ttk.Combobox(orow, height=12, textvariable=self.weight_var, state="readonly", width=22,
                     values=[w[0] for w in WEIGHTS]).pack(side=tk.LEFT)
        ttk.Label(orow, text="Class:").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Combobox(orow, height=12, textvariable=self.class_var, state="readonly", width=5,
                     values=["H1", "H2", "H3", "H4", "H5", "H6"]).pack(side=tk.LEFT)

        ttk.Button(left, text="Prepare (Steps 1–2) / הכנה", command=self.prepare).pack(fill=tk.X)
        ttk.Button(left, text="▶  Run Adjustment / הרץ תיאום", command=self.run).pack(fill=tk.X, pady=2)
        br = ttk.Frame(left)
        br.pack(fill=tk.X)
        self.btn_fa0 = ttk.Button(br, text="Export FA0…", command=self.export_fa0)
        self.btn_fa1 = ttk.Button(br, text="Export FA1…", command=self.export_fa1)
        self.btn_pdf = ttk.Button(br, text="PDF Report…", command=self.export_pdf)
        for b in (self.btn_fa0, self.btn_fa1, self.btn_pdf):
            b.pack(side=tk.LEFT, expand=True, fill=tk.X)
        ttk.Button(left, text="Close", command=self.destroy).pack(fill=tk.X, pady=2)
        self._set_result_buttons(False)

        self.nb = ttk.Notebook(right)
        self.nb.pack(fill=tk.BOTH, expand=True)
        self.summary = self._text_tab("Summary / סיכום")
        self.obs_tree = self._tree_tab(
            "Step 1: Observations",
            ("f", "t", "dh", "L", "runs", "delta", "allowed", "ok", "src"),
            ("From", "To", "Mean dH (m)", "L (m)", "Runs", "Δ runs (mm)", "Allowed (mm)",
             "Check", "Sources"), (80, 80, 100, 80, 45, 90, 90, 50, 200))
        self.approx_tree = self._tree_tab(
            "Step 2: Approximate Heights", ("pid", "h", "seed", "kind", "km"),
            ("Point ID", "Approx. Height (m)", "Carried from", "Seed type", "Path (km)"),
            (110, 130, 110, 90, 90))
        self.heights_tree = self._tree_tab(
            "Adjusted Heights / גבהים", ("pid", "h", "ap", "corr", "sd", "c95"),
            ("Point ID", "Adjusted (m)", "Approx. (m)", "Correction (mm)", "Std Dev (mm)",
             "95% (mm)"), (110, 110, 110, 110, 100, 90))
        self.res_tree = self._tree_tab(
            "Residuals / שאריות", ("f", "t", "mdh", "adh", "v", "L", "w"),
            ("From", "To", "Measured dH", "Adjusted dH", "Residual (mm)", "L (m)", "Weight"),
            (80, 80, 110, 110, 100, 80, 80))
        self.lineadj = self._text_tab("Line Adjustment / תיאום קו")
        for t in (self.lines_tree, self.obs_tree, self.approx_tree, self.res_tree):
            t.tag_configure("bad", background="#ffaaaa")
            t.tag_configure("off", foreground="#999999")
        self.approx_tree.tag_configure("fixed", background="#add8e6")
        self.approx_tree.tag_configure("relative", background="#ffc864")
        self.heights_tree.tag_configure("fixed", background="#add8e6")

    @staticmethod
    def _tree(parent, cols, heads, widths, height=10):
        fr = ttk.Frame(parent)
        fr.pack(fill=tk.BOTH, expand=True)
        t = ttk.Treeview(fr, columns=cols, show="headings", height=height)
        for c, h, w in zip(cols, heads, widths):
            t.heading(c, text=h)
            t.column(c, width=w, anchor=tk.CENTER)
        sb = ttk.Scrollbar(fr, orient=tk.VERTICAL, command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        return t

    def _tree_tab(self, title, cols, heads, widths):
        fr = ttk.Frame(self.nb)
        self.nb.add(fr, text=title)
        return self._tree(fr, cols, heads, widths)

    def _text_tab(self, title):
        fr = ttk.Frame(self.nb)
        self.nb.add(fr, text=title)
        t = tk.Text(fr, font=("Courier New", 9), wrap=tk.NONE)
        t.pack(fill=tk.BOTH, expand=True)
        return t

    @staticmethod
    def _set_text(widget, text):
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, text)

    def _set_result_buttons(self, on):
        for b in (self.btn_fa1, self.btn_pdf):
            b.state(["!disabled"] if on else ["disabled"])

    # --------------------------------------------------------------- lines
    @staticmethod
    def _eligible(line) -> bool:
        if not getattr(line, "is_used", True):
            return False
        if getattr(line, "manager_override", False):
            return True
        status = getattr(getattr(line, "status", None), "value", "valid")
        return status in ("valid", "valid_by_manager", "merged")

    def _fill_lines(self):
        t = self.lines_tree
        t.delete(*t.get_children())
        n_el = 0
        for i, ln in enumerate(self.lines):
            el = self._eligible(ln)
            n_el += el
            status = "excluded" if not getattr(ln, "is_used", True) else \
                getattr(ln.status, "value", str(ln.status))
            t.insert("", tk.END, iid=str(i), tags=() if el else ("off",), values=(
                CHECK if self._checked.get(id(ln)) else NOCHECK, ln.filename,
                f"{ln.start_point} → {ln.end_point}", f"{ln.total_height_diff:.5f}", status))
        self.lines_label.config(text=f"{n_el} eligible of {len(self.lines)}")

    def _toggle_line(self, event=None):
        iid = self.lines_tree.focus()
        if not iid:
            return
        ln = self.lines[int(iid)]
        if not self._eligible(ln):
            return
        self._checked[id(ln)] = not self._checked.get(id(ln))
        self._fill_lines()

    def _check_all(self, on):
        for ln in self.lines:
            self._checked[id(ln)] = on and self._eligible(ln)
        self._fill_lines()

    def active_lines(self):
        return [ln for ln in self.lines if self._checked.get(id(ln)) and self._eligible(ln)]

    # -------------------------------------------------------- fixed points
    def add_fixed(self, pid: str, height: str = ""):
        self.fp_tree.insert("", tk.END, values=(pid, height))

    def _add_fp_from_entries(self):
        pid = self.pid_entry.get().strip()
        if pid:
            self.add_fixed(pid, self.h_entry.get().strip())
            self.pid_entry.delete(0, tk.END)
            self.h_entry.delete(0, tk.END)

    def _remove_fp(self):
        for iid in self.fp_tree.selection():
            self.fp_tree.delete(iid)

    def clear_fixed(self):
        self.fp_tree.delete(*self.fp_tree.get_children())

    def auto_select(self):
        """Fixed points for the ticked lines.

        When the loaded FA0 / FA1 files define fixed points (the first N
        points listed, N from the header), exactly those are selected, in the
        file's order.  Only when no loaded file defines fixed points for these
        lines are DB heights offered instead."""
        lines = self.active_lines()
        on_lines = {p for ln in lines for p in (ln.start_point, ln.end_point) if p}
        file_fixed = [p for p in self.known_heights if p in on_lines]
        if file_fixed:
            rows = [(p, f"{self.known_heights[p]:.4f}") for p in file_fixed]
        else:
            pts = sorted(on_lines)
            heights = {}
            if self.db_heights is not None:
                try:
                    heights.update(self.db_heights(pts) or {})
                except Exception:
                    pass
            rows = [(p, f"{heights[p]:.4f}") for p in pts if p in heights]
        if len(lines) == 1:
            have = {p for p, _ in rows}
            rows += [(p, "") for p in (lines[0].start_point, lines[0].end_point)
                     if p not in have]
        self.clear_fixed()
        for p, h in rows:
            self.add_fixed(p, h)

    def fetch_from_db(self):
        """Fill empty heights in the fixed-point list from the DB."""
        empty = [iid for iid in self.fp_tree.get_children()
                 if not str(self.fp_tree.item(iid, "values")[1]).strip()]
        pids = [str(self.fp_tree.item(iid, "values")[0]).strip() for iid in empty]
        found = self.db_heights(pids) if (self.db_heights and pids) else {}
        for iid, pid in zip(empty, pids):
            if pid in found:
                vals = list(self.fp_tree.item(iid, "values"))
                vals[1] = f"{found[pid]:.4f}"
                self.fp_tree.item(iid, values=vals)
        missing = [p for p in pids if p not in found]
        msg = f"Filled {len(found)} height(s) from the DB."
        if missing:
            msg += "\nNo height in the DB for: " + ", ".join(missing)
        messagebox.showinfo("Fetch from DB", msg, parent=self)

    def collect_fixed(self) -> Dict[str, float]:
        fixed = {}
        for iid in self.fp_tree.get_children():
            pid, h = self.fp_tree.item(iid, "values")[:2]
            try:
                fixed[str(pid).strip()] = float(h)
            except (TypeError, ValueError):
                pass
        return fixed

    # ------------------------------------------------------------ Steps 1-2
    def _weight_mode(self):
        return dict(WEIGHTS).get(self.weight_var.get(), "distance")

    def build_observations(self, lines):
        cls = self.class_var.get()
        if self.merge_var.get():
            return merge_observations(lines, cls)
        obs = lines_to_observations(lines)
        return obs, [MergedRun(o.from_point, o.to_point, o.height_diff, o.distance, 1,
                               [o.height_diff], [o.source_file], None,
                               tolerance_mm(o.distance, cls), True) for o in obs]

    def prepare(self, quiet=False):
        lines = self.active_lines()
        if not lines:
            if not quiet:
                messagebox.showwarning("No Lines", "Tick at least one eligible line.", parent=self)
            return None
        fixed = self.collect_fixed()
        obs, report = self.build_observations(lines)
        self._report = report
        t = self.obs_tree
        t.delete(*t.get_children())
        for r in report:
            t.insert("", tk.END, tags=() if r.within_tolerance else ("bad",), values=(
                r.from_point, r.to_point, f"{r.mean_dh:.5f}", f"{r.distance:.1f}", r.n_runs,
                "—" if r.discrepancy_mm is None else f"{r.discrepancy_mm:.2f}",
                f"{r.allowed_mm:.2f}",
                "—" if r.discrepancy_mm is None else ("✓" if r.within_tolerance else "✗"),
                " + ".join(r.sources)))
        approx = approximate_heights(obs, fixed, self.known_heights)
        t = self.approx_tree
        t.delete(*t.get_children())
        for pid in sorted(approx):
            a = approx[pid]
            tag = "fixed" if pid in fixed else ("relative" if a.seed_kind == "relative" else "")
            t.insert("", tk.END, tags=(tag,) if tag else (), values=(
                pid, f"{a.height:.4f}", a.seed, a.seed_kind, f"{a.path_km:.3f}"))
        if not quiet:
            n_bad = sum(not r.within_tolerance for r in report)
            self._set_text(self.summary,
                           f"Step 1: {len(lines)} line(s) → {len(obs)} observation(s); "
                           f"{sum(r.n_runs > 1 for r in report)} averaged; {n_bad} exceed k·√L.\n"
                           f"Step 2: approximate heights for {len(approx)} point(s).\n")
        return obs, fixed

    # ----------------------------------------------------------------- run
    def run(self):
        prep = self.prepare(quiet=True)
        if prep is None:
            messagebox.showwarning("No Lines", "Tick at least one eligible line.", parent=self)
            return
        obs, fixed = prep
        if not fixed:
            messagebox.showwarning("Fixed Points",
                                   "Enter at least one fixed point with a known height.",
                                   parent=self)
            return
        pts = {p for o in obs for p in _obs_fields(o)[:2]}
        if pts and pts <= set(fixed):
            self._run_line_adjustment(self.active_lines(), fixed)
            return
        cls = self.class_var.get()
        Adj = LeastSquaresAdjuster if self.method_var.get() == "parametric" else ConditionalAdjuster
        try:
            res = Adj(weight_mode=self._weight_mode(), leveling_class=cls).adjust(
                obs, fixed, known_heights=self.known_heights)
            self._run_obs = list(obs)
        except NetworkDatumError as exc:
            self.result = None
            self._set_result_buttons(False)
            self._set_text(self.summary, "Adjustment not run.\n\n" + str(exc))
            self.nb.select(0)
            messagebox.showwarning("Network not connected to a fixed point", str(exc), parent=self)
            return
        except Exception as exc:
            self.result = None
            self._set_result_buttons(False)
            messagebox.showerror("Adjustment Error", str(exc), parent=self)
            return
        self.result, self.fixed_points = res, fixed
        self._show_result(res, fixed)
        self._set_result_buttons(True)

    def _run_line_adjustment(self, lines, fixed):
        cls = self.class_var.get()
        out = []
        for ln in lines:
            if ln.start_point not in fixed or ln.end_point not in fixed:
                continue
            r = adjust_single_line(ln, fixed[ln.start_point], fixed[ln.end_point], cls)
            out += [f"Line {ln.filename}:  {ln.start_point} → {ln.end_point}",
                    f"  Measured dH  : {r['measured_dh']:12.5f} m",
                    f"  Known dH     : {r['known_dh']:12.5f} m",
                    f"  Misclosure f : {r['misclosure_mm']:9.2f} mm   allowed ±{r['allowed_mm']:.2f} mm "
                    f"({cls}, L={r['length_m'] / 1000:.3f} km)  " + ("✓ OK" if r["ok"] else "✗ EXCEEDED"),
                    "  v_i = −f · L_i / ΣL",
                    f"  {'Setup':>5} {'L (m)':>9} {'dH (m)':>11} {'v (mm)':>8} {'dH corr':>11} {'H (m)':>11}"]
            out += [f"  {x['setup']:>5} {x['length_m']:9.2f} {x['measured_dh']:11.5f} "
                    f"{x['correction_mm']:8.2f} {x['corrected_dh']:11.5f} {x['height']:11.4f}"
                    for x in r["rows"]]
            out.append("")
        self.result = None
        self._set_result_buttons(False)
        self._set_text(self.lineadj, "\n".join(out) or "Both ends of each ticked line must be fixed.")
        self._set_text(self.summary, "All points of the ticked line(s) are fixed: single-line "
                                     "adjustment (Directive G2, Appendix C) - see Line Adjustment.")
        self.nb.select(5)

    def _show_result(self, res, fixed):
        from ..exporters.pdf_report import report_lines
        rep = report_lines(res, fixed, self.project_name, self.class_var.get())
        cut = rep.index("Adjusted heights") if "Adjusted heights" in rep else len(rep)
        self._set_text(self.summary, "\n".join(rep[4:cut]))
        t = self.heights_tree
        t.delete(*t.get_children())
        for pid in sorted(res.adjusted_heights):
            h = res.adjusted_heights[pid]
            if pid in fixed:
                t.insert("", tk.END, tags=("fixed",), values=(pid, f"{h:.5f}", "fixed", "—", "—", "—"))
                continue
            sd = res.mse_heights.get(pid, 0.0) * 1000
            t.insert("", tk.END, values=(pid, f"{h:.5f}", f"{res.approximate_heights.get(pid, h):.5f}",
                                         f"{res.corrections.get(pid, 0.0) * 1000:.2f}",
                                         f"{sd:.2f}", f"{1.96 * sd:.2f}"))
        t = self.res_tree
        t.delete(*t.get_children())
        cls = self.class_var.get()
        for o in res.observations:
            bad = abs(o["residual_mm"]) > tolerance_mm(o["distance"], cls)
            t.insert("", tk.END, tags=("bad",) if bad else (), values=(
                o["from"], o["to"], f"{o['measured_dh']:.5f}", f"{o['adjusted_dh']:.5f}",
                f"{o['residual_mm']:.2f}", f"{o['distance']:.1f}", f"{o['weight']:.3f}"))
        self.nb.select(0)

    # --------------------------------------------------------------- export
    def _ask(self, title, name, ext, types):
        return filedialog.asksaveasfilename(parent=self, title=title, defaultextension=ext,
                                            initialdir=self.output_dir or None,
                                            initialfile=name, filetypes=types)

    def export_fa0(self):
        prep = self.prepare(quiet=True)
        if prep is None:
            messagebox.showwarning("Export FA0", "Tick at least one eligible line.", parent=self)
            return
        obs, fixed = prep
        path = self._ask("Export FA0", f"{self.project_name}.FA0", ".FA0", [("FA0", "*.FA0 *.fa0")])
        if not path:
            return
        from ..exporters import FA0Exporter
        FA0Exporter().export(path, [Benchmark(point_id=k, height=v) for k, v in fixed.items()],
                             obs, self.project_name, approx_heights=self.approx_heights)
        messagebox.showinfo("Export FA0", f"Written:\n{path}", parent=self)

    def export_fa1(self):
        if self.result is None:
            return
        path = self._ask("Export FA1", f"{self.project_name}.FA1", ".FA1", [("FA1", "*.FA1 *.fa1")])
        if not path:
            return
        from ..exporters import FA1Exporter
        obs = self._run_obs or [
            MeasurementSummary(from_point=o["from"], to_point=o["to"],
                               height_diff=o["measured_dh"], distance=o["distance"],
                               num_setups=o.get("num_setups", 0) or 0, bf_diff=0.0,
                               year_month="", source_file=o.get("source", ""))
            for o in self.result.observations]
        FA1Exporter().export(path, [Benchmark(point_id=k, height=v)
                                    for k, v in self.fixed_points.items()],
                             obs, self.result, self.project_name,
                             leveling_class=self.class_var.get(),
                             approx_heights=self.approx_heights)
        messagebox.showinfo("Export FA1", f"Written:\n{path}", parent=self)

    def export_pdf(self):
        if self.result is None:
            return
        path = self._ask("PDF Report", f"{self.project_name}_LSA.pdf", ".pdf", [("PDF", "*.pdf")])
        if not path:
            return
        from ..exporters.pdf_report import write_adjustment_pdf
        write_adjustment_pdf(path, self.result, self.fixed_points, self.project_name,
                             self.class_var.get())
        messagebox.showinfo("PDF Report", f"Written:\n{path}", parent=self)
