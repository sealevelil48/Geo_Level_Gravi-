"""
Point Verification dialog (Tkinter) - standalone port of the QGIS plugin's
GeoLevelPointResolutionDialog.

One row per open field point ID:
  Field ID | DB candidate (Name | E | N | Height | Class | Locality | Distance)
  | Reason | Search DB | Manual name | Handling option

* Candidate dropdowns show at most 12 rows and scroll.
* Search DB: part of a name or a locality + Enter / button; hits are added
  to the dropdown (same ranking rules as the plugin) and the row switches
  to "Use Selected Match".
* Amber rows hold < 1 m twins with different names: a choice is required.
* "Decide later" lets distance matching continue from the confirmed points.

Result (``self.result`` after Confirm, None after Cancel) has the plugin's
get_resolved() format: {"records", "modes", "manual", "dirty"}.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Dict, List, Optional

from ..db.benchmark_db import BenchmarkRecord, cluster_needs_choice, group_twins
from .window_utils import fit_to_screen

CHOOSE_PLACEHOLDER = "— choose one: twin points at the same location —"
NO_MATCH = "(no DB match — use search / manual override or skip)"
_TWIN_BG = "#fff3cd"
_HANDLING = [("Use Selected Match", "use_selected"),
             ("Exclude from Project", "exclude"),
             ("Point Not Found (Estimate)", "estimate"),
             ("PKT (Average Neighbors)", "pkt_average")]
_DEFER = ("Decide later (match by distance)", "defer")


def row_needs_forced_choice(records: list) -> bool:
    """True when the acceptable candidates include a < 1 m twin cluster
    holding differently named points (e.g. 250/A + 849/L)."""
    ok = [r for r in records
          if r.x is not None and r.y is not None and getattr(r, "topo_ok", None) is not False]
    if not ok:
        return False
    return any(cluster_needs_choice(cl) for cl in group_twins(ok) if len(cl) > 1)


def same_record(a, b) -> bool:
    if a is None or b is None:
        return a is b
    if hasattr(a, "key") and hasattr(b, "key") and a.key() == b.key():
        return True
    if getattr(a, "name", None) != getattr(b, "name", None):
        return False
    if a.x is None or b.x is None:
        return a.x is None and b.x is None
    return abs(a.x - b.x) < 1e-3 and abs(a.y - b.y) < 1e-3


def format_record_label(rec) -> str:
    e_str = f"{rec.x:.3f}" if rec.x is not None else "?"
    n_str = f"{rec.y:.3f}" if rec.y is not None else "?"
    h_str = f"{rec.gova_ort:.3f} m" if rec.gova_ort is not None else "no height"
    parts = [f"{rec.name}", f"E {e_str}  N {n_str}",
             f"H {h_str}  ({rec.shem_darga_gova or '—'})",
             getattr(rec, "kfar_aher_name", None) or "—"]
    d = getattr(rec, "dist_to_anchor_m", None)
    if d is not None:
        parts.append(f"d={d / 1000.0:.1f} km")
    flags = []
    if getattr(rec, "topo_ok", None) is False:
        flags.append("⚠ line-length mismatch")
    twin = getattr(rec, "twin_of", None)
    if twin:
        td = getattr(rec, "twin_dist_m", None)
        flags.append(f"≈ twin of {twin}" + (f" ({td:.2f} m)" if td is not None else ""))
    if flags:
        parts.append("  ".join(flags))
    return "   |   ".join(parts)


def format_details(rec) -> str:
    bits = []
    if getattr(rec, "hearot", None):
        bits.append(f"Remarks: {rec.hearot}")
    if getattr(rec, "taarih_gova_ort", None):
        bits.append(f"Height date: {rec.taarih_gova_ort}")
    if getattr(rec, "ot_nekuda_kfula", None) or getattr(rec, "mispar_nekuda_kfula", None):
        bits.append(f"Alternate name: {rec.mispar_nekuda_kfula or ''}{rec.ot_nekuda_kfula or ''}")
    if getattr(rec, "objectid", None) is not None:
        bits.append(f"objectid: {rec.objectid}")
    return "   ".join(bits)


def _placeholder_record(name: str) -> BenchmarkRecord:
    return BenchmarkRecord(ot_nekuda="", mispar_nekuda=0, name=name, gova_ort=None,
                           shem_darga_gova=None, taarih_gova_ort=None,
                           ot_nekuda_kfula=None, mispar_nekuda_kfula=None, x=None, y=None)


class PointResolutionDialog(tk.Toplevel):
    """Verify / pick DB benchmarks for field point IDs (see module doc)."""

    def __init__(self, parent, target_points: List[str], candidate_map: Dict[str, list],
                 notes: Optional[Dict[str, str]] = None,
                 statuses: Optional[Dict[str, str]] = None,
                 allow_defer: bool = False, round_no: int = 0,
                 search_fn: Optional[Callable[[str, str], list]] = None):
        super().__init__(parent)
        self.result: Optional[dict] = None
        self._pids = list(target_points)
        self._cands = {p: list(candidate_map.get(p, [])) for p in self._pids}
        self._notes = notes or {}
        self._statuses = statuses or {}
        self._allow_defer = allow_defer
        self._search_fn = search_fn
        self._dirty: set = set()
        self._combo: Dict[str, ttk.Combobox] = {}
        self._items: Dict[str, list] = {}         # pid -> records aligned with combo values
        self._forced: Dict[str, bool] = {}
        self._id_label: Dict[str, tk.Label] = {}
        self._search: Dict[str, ttk.Entry] = {}
        self._manual: Dict[str, ttk.Entry] = {}
        self._handling: Dict[str, ttk.Combobox] = {}
        self._handling_keys = list(_HANDLING) + ([_DEFER] if allow_defer else [])

        title = "Point Verification / אימות נקודות"
        if round_no > 1:
            title += f"  —  matching round {round_no}"
        self.title(title)
        fit_to_screen(self, 1250, 620)
        self.transient(parent)
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        try:
            self.grab_set()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ #
    def _build(self):
        info = (
            "Some field point IDs were not found, have several DB matches, or coincide "
            "with another DB point.  Pick the correct benchmark in the dropdown "
            "(coordinates shown).  Not listed?  Type part of the name or a locality in "
            "Search DB and press Enter / 🔍 - the matching DB points are added to the "
            "dropdown.  Amber rows hold twin points (same location, different name) - "
            "choose one explicitly.  If nothing could be anchored, confirm one or more "
            "points you are sure of and set the others to 'Decide later'; matching then "
            "continues from your choice.  Nothing is loaded until you confirm.")
        ttk.Label(self, text=info, wraplength=1150, justify=tk.LEFT).pack(
            fill=tk.X, padx=10, pady=(10, 6))

        outer = ttk.Frame(self)
        outer.pack(fill=tk.BOTH, expand=True, padx=10)
        canvas = tk.Canvas(outer, highlightthickness=0)
        vbar = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        hbar = ttk.Scrollbar(outer, orient=tk.HORIZONTAL, command=canvas.xview)
        canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)
        hbar.pack(side=tk.BOTTOM, fill=tk.X)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        grid = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=grid, anchor="nw")
        grid.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        self._canvas = canvas

        heads = ["Field Point ID",
                 "DB Candidate  (Name | E | N | Height | Class | Locality | Distance)"]
        if self._notes:
            heads.append("Reason")
        if self._search_fn is not None:
            heads.append("Search DB")
        heads += ["Manual Name Override", "Handling Option"]
        for c, h in enumerate(heads):
            ttk.Label(grid, text=h, font=("TkDefaultFont", 9, "bold")).grid(
                row=0, column=c, sticky="w", padx=3, pady=2)

        for r, pid in enumerate(self._pids, start=1):
            c = 0
            lab = tk.Label(grid, text=pid, anchor="w", width=12)
            lab.grid(row=r, column=c, sticky="we", padx=3, pady=2)
            self._id_label[pid] = lab
            c += 1
            combo = ttk.Combobox(grid, state="readonly", width=95, height=12)
            combo.grid(row=r, column=c, sticky="we", padx=3)
            combo.bind("<<ComboboxSelected>>", lambda e, p=pid: self._on_pick(p))
            self._combo[pid] = combo
            self._fill_combo(pid)
            c += 1
            if self._notes:
                fg = "#c62828" if self._statuses.get(pid) in ("not_in_db", "conflict") else None
                ttk.Label(grid, text=self._notes.get(pid, ""), wraplength=260,
                          foreground=fg).grid(row=r, column=c, sticky="w", padx=3)
                c += 1
            if self._search_fn is not None:
                cell = ttk.Frame(grid)
                ent = ttk.Entry(cell, width=16)
                ent.insert(0, pid)
                ent.bind("<Return>", lambda e, p=pid: self._on_search(p))
                ent.pack(side=tk.LEFT)
                ttk.Button(cell, text="🔍", width=3,
                           command=lambda p=pid: self._on_search(p)).pack(side=tk.LEFT)
                cell.grid(row=r, column=c, sticky="w", padx=3)
                self._search[pid] = ent
                c += 1
            man = ttk.Entry(grid, width=18)
            man.bind("<KeyRelease>", lambda e, p=pid: self._dirty.add(p))
            man.grid(row=r, column=c, sticky="w", padx=3)
            self._manual[pid] = man
            c += 1
            hc = ttk.Combobox(grid, state="readonly", width=30, height=12,
                              values=[lbl for lbl, _ in self._handling_keys])
            hc.current(0)
            if self._allow_defer and self._statuses.get(pid) in ("ambiguous", "no_link"):
                hc.current(len(self._handling_keys) - 1)
            if self._statuses.get(pid) == "not_in_db":
                hc.current(self._mode_index("estimate"))
            hc.bind("<<ComboboxSelected>>", lambda e, p=pid: self._dirty.add(p))
            hc.grid(row=r, column=c, sticky="w", padx=3)
            self._handling[pid] = hc

        self._details = ttk.Label(self, text="", foreground="#555")
        self._details.pack(fill=tk.X, padx=10, pady=(4, 0))
        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, padx=10, pady=8)
        ttk.Button(bar, text="Confirm & Load Project",
                   command=self._confirm).pack(side=tk.RIGHT)
        ttk.Button(bar, text="Cancel Loading", command=self._cancel).pack(side=tk.RIGHT, padx=6)

    # ------------------------------------------------------------------ #
    def _mode_index(self, mode: str) -> int:
        for i, (_, key) in enumerate(self._handling_keys):
            if key == mode:
                return i
        return 0

    def mode(self, pid: str) -> str:
        i = self._handling[pid].current()
        return self._handling_keys[max(i, 0)][1]

    def set_mode(self, pid: str, mode: str) -> None:
        self._handling[pid].current(self._mode_index(mode))
        self._dirty.add(pid)

    def selected_record(self, pid: str):
        i = self._combo[pid].current()
        items = self._items.get(pid, [])
        return items[i] if 0 <= i < len(items) else None

    def select_record(self, pid: str, rec) -> bool:
        for i, r in enumerate(self._items.get(pid, [])):
            if r is not None and same_record(r, rec):
                self._combo[pid].current(i)
                self._on_pick(pid)
                return True
        return False

    def _fill_combo(self, pid: str) -> None:
        recs = self._cands.get(pid, [])
        forced = row_needs_forced_choice(recs)
        self._forced[pid] = forced
        labels, items = [], []
        if not recs:
            labels.append(NO_MATCH)
            items.append(None)
        else:
            if forced:
                labels.append(CHOOSE_PLACEHOLDER)
                items.append(None)
            for rec in recs:
                labels.append(format_record_label(rec))
                items.append(rec)
        self._combo[pid]["values"] = labels
        self._items[pid] = items
        self._combo[pid].current(0)
        self._id_label[pid].configure(bg=_TWIN_BG if forced else self.cget("bg"))

    def _on_pick(self, pid: str) -> None:
        self._dirty.add(pid)
        rec = self.selected_record(pid)
        self._details.configure(text=(f"{pid}: " + format_details(rec)) if rec else "")

    def _on_search(self, pid: str) -> None:
        if self._search_fn is None:
            return
        query = self._search[pid].get().strip()
        if not query:
            return
        try:
            hits = self._search_fn(pid, query) or []
        except Exception as exc:
            messagebox.showwarning("Search DB", f"Search failed:\n{exc}", parent=self)
            return
        if not hits:
            messagebox.showinfo("Search DB", f"No DB point found for '{query}'.", parent=self)
            return
        self.set_mode(pid, "use_selected")
        merged = list(hits)
        for r in self._cands.get(pid, []):
            if not any(same_record(r, h) for h in merged):
                merged.append(r)
        self._cands[pid] = merged
        self._fill_combo(pid)
        self.select_record(pid, hits[0])

    def _confirm(self) -> None:
        missing = [pid for pid in self._pids
                   if self.mode(pid) == "use_selected"
                   and not self._manual[pid].get().strip()
                   and self._forced.get(pid) and self.selected_record(pid) is None]
        if missing:
            messagebox.showwarning(
                "Choose twin point",
                "These points coincide with another DB point under a different name "
                "(same location, different height).\nPlease choose one explicitly:\n\n  "
                + "\n  ".join(missing), parent=self)
            return
        self.result = self.get_resolved()
        self._close()

    def _cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        try:
            self._canvas.unbind_all("<MouseWheel>")
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def get_resolved(self) -> dict:
        records, modes, manual = {}, {}, {}
        for pid in self._pids:
            mode = self.mode(pid)
            modes[pid] = mode
            if mode in ("exclude", "defer"):
                records[pid] = None
            elif mode in ("estimate", "pkt_average"):
                records[pid] = _placeholder_record(f"[{mode.upper()}:{pid}]")
            else:
                name = self._manual[pid].get().strip()
                if name:
                    manual[pid] = name
                    records[pid] = _placeholder_record(name)
                else:
                    records[pid] = self.selected_record(pid)
        return {"records": records, "modes": modes, "manual": manual,
                "dirty": set(self._dirty)}


def ask_point_resolution(parent) -> Callable[..., Optional[dict]]:
    """The *ask* callable for PointResolver.resolve(), showing this dialog."""
    def _ask(open_pids, scored, notes=None, statuses=None, allow_defer=False,
             round_no=0, search_fn=None):
        dlg = PointResolutionDialog(parent, open_pids, scored, notes=notes,
                                    statuses=statuses, allow_defer=allow_defer,
                                    round_no=round_no, search_fn=search_fn)
        parent.wait_window(dlg)
        return dlg.result
    return _ask
