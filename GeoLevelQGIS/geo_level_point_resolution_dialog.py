"""
Point verification / Point Settings dialog.

Two modes:

* ``mode="load"`` — pre-calculation verification.  Shows field point IDs that
  are unresolved (0 DB hits), ambiguous (2+ locations), outside the normal
  working radius, or that coincide with a differently named DB point
  (spatial twins such as 250/A ≡ 849/L, 0.30 m apart).
* ``mode="edit"`` — "Point Settings / הגדרות נקודה".  Opened from the dock at
  any time after loading so the engineer can re-select the DB benchmark for a
  point without reloading the measurement files.  Adds a per-row DB search.

Each candidate label shows name, E/N, height, class, locality, distance to
the field anchor and warnings (⚠ out of radius, ⚠ length mismatch,
≈ twin).  Candidates arrive pre-sorted by BenchmarkDBManager.score_candidates.
"""
from __future__ import annotations

import html
from typing import Callable, Dict, List, Optional

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QBrush
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

_TWIN_BG = QColor("#fff3cd")       # amber — forced choice between twins
_BAD_FG = QColor("#9e9e9e")        # grey text — topology-inconsistent candidate

_CHOOSE_PLACEHOLDER = "— choose one: twin points at the same location —"


def _row_needs_forced_choice(records: list) -> bool:
    """True when the acceptable candidates include a < 1 m twin cluster
    holding differently named points (e.g. 250/A + 849/L)."""
    try:
        from geolevel_db_manager import group_twins, cluster_needs_choice
    except Exception:
        return any(getattr(r, "twin_of", None) for r in records)
    ok = [r for r in records
          if r.x is not None and r.y is not None
          and getattr(r, "topo_ok", None) is not False]
    if not ok:
        return False
    return any(cluster_needs_choice(cl) for cl in group_twins(ok) if len(cl) > 1)


class GeoLevelPointResolutionDialog(QDialog):
    """Verify / re-select DB benchmarks for field point IDs.

    Args:
        target_points: Ordered list of field IDs to show.
        candidate_map: Field ID -> list of BenchmarkRecord (pre-scored/sorted).
        parent: Parent QWidget.
        mode: "load" (pre-calculation) or "edit" (Point Settings).
        current_selection: Field ID -> currently used BenchmarkRecord (edit mode).
        current_modes: Field ID -> current handling mode (edit mode), e.g.
            "estimate" for a point pinned as not-in-DB.
        notes / statuses: Field ID -> reason text / network status keyword
            from BenchmarkDBManager.resolve_network (shown in a Reason column).
        allow_defer: offer "Decide later (match by distance)" so the user can
            confirm only some anchors and let distance matching do the rest.
        round_no: matching round shown in the title (load mode).
        search_fn: Callable(pid, query) -> list of scored BenchmarkRecord used
            by the per-row "Search DB" box (edit mode).
    """

    def __init__(
        self,
        target_points: List[str],
        candidate_map: Dict[str, list],
        parent=None,
        mode: str = "load",
        current_selection: Optional[Dict[str, object]] = None,
        search_fn: Optional[Callable[[str, str], list]] = None,
        current_modes: Optional[Dict[str, str]] = None,
        notes: Optional[Dict[str, str]] = None,
        statuses: Optional[Dict[str, str]] = None,
        allow_defer: bool = False,
        round_no: int = 0,
    ) -> None:
        super().__init__(parent)
        self._mode = mode if mode in ("load", "edit") else "load"
        self._target_points = list(target_points)
        self._candidate_map = {pid: list(candidate_map.get(pid, [])) for pid in target_points}
        self._current = current_selection or {}
        self._current_modes = current_modes or {}
        self._notes = notes or {}
        self._statuses = statuses or {}
        self._allow_defer = allow_defer and mode != "edit"
        self._round_no = round_no
        self._dirty: set = set()                          # rows the user touched
        self._search_fn = search_fn
        self._combos: Dict[str, QComboBox] = {}          # field ID -> DB candidate dropdown
        self._edits: Dict[str, QLineEdit] = {}            # field ID -> manual name override
        self._handling: Dict[str, QComboBox] = {}         # field ID -> handling mode dropdown
        self._searches: Dict[str, QLineEdit] = {}         # field ID -> search box (edit)
        self._forced: Dict[str, bool] = {}                # field ID -> twin choice required

        if self._mode == "edit":
            self.setWindowTitle("Point Settings / הגדרות נקודה")
        else:
            title = "Point Verification / אימות נקודות"
            if self._round_no > 1:
                title += f"  —  matching round {self._round_no}"
            self.setWindowTitle(title)
        self.setMinimumSize(1150, 500)
        self._setup_ui()
        self._populate()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _col(self, name: str) -> int:
        return self._columns.index(name)

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        if self._mode == "edit":
            text = (
                "<b>Re-select the database benchmark used for each point.</b><br>"
                "Candidates are sorted by distance to the field anchor (the area "
                "measured in the loaded files). "
                "<b>≈ twin</b> = a differently named DB point at the same spot "
                "(&lt; 1 m).<br>Use <i>Search DB</i> to look up any other name. "
                "<i>Applying re-builds the map from memory — files are not reloaded.</i>"
            )
        else:
            text = (
                "<b>Some field point IDs were not found, have multiple matches, "
                "or coincide with another DB point.</b><br>"
                "Select the correct benchmark from the dropdown (coordinates shown) or "
                "type the exact database name in the override column.<br>"
                "<span style='background:#fff3cd'>&nbsp;Amber rows&nbsp;</span> hold "
                "twin points (same location, different name / height) — you must "
                "choose one explicitly.<br>"
                "Points are matched automatically by comparing DB distances with the "
                "distances measured in the files. If nothing could be anchored, pick "
                "<b>one or more</b> points you are sure of and set the others to "
                "<i>Decide later</i> — matching then continues from your choice.<br>"
                "<i>No layers will be built and no calculations will run until you "
                "confirm these points.</i>"
            )
        info = QLabel(text)
        info.setWordWrap(True)
        root.addWidget(info)

        self._columns = ["Field Point ID",
                         "DB Candidate  (Name | E | N | Height | Class | Locality | Distance)"]
        if self._notes:
            self._columns.append("Reason")
        if self._mode == "edit":
            self._columns.append("Search DB")
        self._columns += ["Manual Name Override", "Handling Option"]

        self._table = QTableWidget(0, len(self._columns))
        self._table.setHorizontalHeaderLabels(self._columns)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        hdr = self._table.horizontalHeader()
        for c in range(len(self._columns)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.verticalHeader().setVisible(False)
        root.addWidget(self._table)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._btn_cancel = QPushButton("Cancel" if self._mode == "edit" else "Cancel Loading")
        self._btn_cancel.clicked.connect(self.reject)

        self._btn_confirm = QPushButton(
            "Apply && Update Map" if self._mode == "edit" else "Confirm && Load Project"
        )
        self._btn_confirm.setStyleSheet(
            "font-weight: bold; background: #1565c0; color: white; padding: 6px 18px;"
        )
        self._btn_confirm.clicked.connect(self._on_confirm)

        btn_row.addWidget(self._btn_cancel)
        btn_row.addWidget(self._btn_confirm)
        root.addLayout(btn_row)

    def _populate(self) -> None:
        self._table.setRowCount(len(self._target_points))
        for row, pid in enumerate(self._target_points):
            id_item = QTableWidgetItem(pid)
            id_item.setFlags(Qt.ItemIsEnabled)
            self._table.setItem(row, 0, id_item)

            combo = QComboBox()
            self._table.setCellWidget(row, 1, combo)
            self._combos[pid] = combo
            self._fill_combo(pid)
            combo.currentIndexChanged.connect(lambda _i, p=pid: self._dirty.add(p))

            if "Reason" in self._columns:
                note = QTableWidgetItem(self._notes.get(pid, ""))
                note.setFlags(Qt.ItemIsEnabled)
                note.setToolTip(html.escape(self._notes.get(pid, "")))
                if self._statuses.get(pid) in ("not_in_db", "conflict"):
                    note.setForeground(QBrush(QColor("#c62828")))
                self._table.setItem(row, self._col("Reason"), note)

            if self._mode == "edit":
                search = QLineEdit()
                search.setPlaceholderText("name + Enter…")
                search.returnPressed.connect(lambda p=pid: self._on_search(p))
                self._table.setCellWidget(row, self._col("Search DB"), search)
                self._searches[pid] = search

            edit = QLineEdit()
            edit.setPlaceholderText("Optional: type exact DB name…")
            edit.textEdited.connect(lambda _t, p=pid: self._dirty.add(p))
            self._table.setCellWidget(row, self._col("Manual Name Override"), edit)
            self._edits[pid] = edit

            handling_combo = QComboBox()
            handling_combo.addItem("Use Selected Match", "use_selected")
            handling_combo.addItem("Exclude from Project", "exclude")
            handling_combo.addItem("Point Not Found (Estimate)", "estimate")
            handling_combo.addItem("PKT (Average Neighbors)", "pkt_average")
            if self._allow_defer:
                handling_combo.addItem("Decide later (match by distance)", "defer")
                if self._statuses.get(pid) in ("ambiguous", "no_link"):
                    handling_combo.setCurrentIndex(handling_combo.count() - 1)
            if self._statuses.get(pid) == "not_in_db" and not self._current_modes.get(pid):
                handling_combo.setCurrentIndex(handling_combo.findData("estimate"))
            cur_mode = self._current_modes.get(pid)
            if cur_mode:
                i = handling_combo.findData(cur_mode)
                if i >= 0:
                    handling_combo.setCurrentIndex(i)
            handling_combo.currentIndexChanged.connect(lambda _i, p=pid: self._dirty.add(p))
            self._table.setCellWidget(row, self._col("Handling Option"), handling_combo)
            self._handling[pid] = handling_combo

    def _fill_combo(self, pid: str) -> None:
        """(Re)build the candidate dropdown and row highlight for *pid*."""
        combo = self._combos[pid]
        combo.blockSignals(True)
        combo.clear()
        records = list(self._candidate_map.get(pid, []))
        current = self._current.get(pid)

        # In edit mode make sure the currently used record is always offered.
        if current is not None and not any(self._same(current, r) for r in records):
            records.insert(0, current)
            self._candidate_map[pid] = records
        show_none_current = (self._mode == "edit" and current is None)

        forced = (self._mode == "load" and _row_needs_forced_choice(records))
        self._forced[pid] = forced

        if show_none_current:
            combo.addItem("● current: no DB benchmark (estimated / not resolved)", None)
        if not records:
            if not show_none_current:
                combo.addItem("(no DB match — use search / manual override or skip)", None)
        else:
            if forced:
                combo.addItem(_CHOOSE_PLACEHOLDER, None)
            for rec in records:
                label = self._format_record_label(rec)
                if current is not None and self._same(current, rec):
                    label = "● current  " + label
                combo.addItem(label, rec)
                idx = combo.count() - 1
                if getattr(rec, "topo_ok", None) is False:
                    combo.setItemData(idx, QBrush(_BAD_FG), Qt.ForegroundRole)
                tip = self._format_tooltip(rec)
                if tip:
                    # DB text (hearot etc.) must never be parsed as rich text
                    combo.setItemData(idx, html.escape(tip).replace(chr(10), "<br>"),
                                      Qt.ToolTipRole)

        # Default selection: current record (edit) → otherwise first real item
        sel = 0
        if current is not None:
            for i in range(combo.count()):
                d = combo.itemData(i)
                if d is not None and self._same(current, d):
                    sel = i
                    break
        combo.setCurrentIndex(sel)
        combo.blockSignals(False)

        # Row highlight
        row = self._target_points.index(pid)
        bg = _TWIN_BG if forced else None
        item = self._table.item(row, 0)
        if item is not None:
            item.setBackground(QBrush(bg) if bg is not None else QBrush())
            if forced:
                item.setToolTip("Twin points at the same location — choose one explicitly")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _same(a, b) -> bool:
        if a is None or b is None:
            return a is b
        ka = a.key() if hasattr(a, "key") else None
        kb = b.key() if hasattr(b, "key") else None
        if ka is not None and kb is not None and ka == kb:
            return True
        if getattr(a, "name", None) != getattr(b, "name", None):
            return False
        if getattr(a, "x", None) is None or getattr(b, "x", None) is None:
            return getattr(a, "x", None) is None and getattr(b, "x", None) is None
        return abs(a.x - b.x) < 1e-3 and abs(a.y - b.y) < 1e-3

    @staticmethod
    def _format_record_label(rec) -> str:
        """Build the human-readable dropdown label from a BenchmarkRecord."""
        e_str = f"{rec.x:.3f}" if rec.x is not None else "?"
        n_str = f"{rec.y:.3f}" if rec.y is not None else "?"
        h_str = f"{rec.gova_ort:.3f} m" if rec.gova_ort is not None else "no height"
        cls_str = rec.shem_darga_gova or "—"
        city_str = getattr(rec, "kfar_aher_name", None) or "—"
        parts = [
            f"{rec.name}",
            f"E {e_str}  N {n_str}",
            f"H {h_str}  ({cls_str})",
            city_str,
        ]
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

    @staticmethod
    def _format_tooltip(rec) -> str:
        bits = []
        hearot = getattr(rec, "hearot", None)
        if hearot:
            bits.append(f"Remarks: {hearot}")
        if getattr(rec, "taarih_gova_ort", None):
            bits.append(f"Height date: {rec.taarih_gova_ort}")
        if getattr(rec, "ot_nekuda_kfula", None) or getattr(rec, "mispar_nekuda_kfula", None):
            bits.append(f"Alternate name: {rec.mispar_nekuda_kfula or ''}"
                        f"{rec.ot_nekuda_kfula or ''}")
        if getattr(rec, "objectid", None) is not None:
            bits.append(f"objectid: {rec.objectid}")
        return "\n".join(bits)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_search(self, pid: str) -> None:
        if self._search_fn is None:
            return
        query = self._searches[pid].text().strip()
        if not query:
            return
        try:
            hits = self._search_fn(pid, query) or []
        except Exception as exc:
            QMessageBox.warning(self, "Search DB", f"Search failed:\n{exc}")
            return
        if not hits:
            QMessageBox.information(self, "Search DB", f"No DB point found for '{query}'.")
            return
        # Merge: new hits first, keep previously listed candidates after them
        merged = list(hits)
        for r in self._candidate_map.get(pid, []):
            if not any(self._same(r, h) for h in merged):
                merged.append(r)
        self._candidate_map[pid] = merged
        self._dirty.add(pid)
        self._fill_combo(pid)
        # Select the first search hit
        combo = self._combos[pid]
        for i in range(combo.count()):
            if combo.itemData(i) is not None and self._same(combo.itemData(i), hits[0]):
                combo.setCurrentIndex(i)
                break

    def _on_confirm(self) -> None:
        missing = []
        for pid in self._target_points:
            mode = self._handling[pid].currentData()
            if mode != "use_selected":
                continue
            if self._edits[pid].text().strip():
                continue
            if self._forced.get(pid) and self._combos[pid].currentData() is None:
                missing.append(pid)
        if missing:
            QMessageBox.warning(
                self, "Choose twin point",
                "These points coincide with another DB point under a different name "
                "(same location, different height).\nPlease choose one explicitly:\n\n  "
                + "\n  ".join(missing),
            )
            return
        self.accept()

    # ------------------------------------------------------------------
    # Public result accessor
    # ------------------------------------------------------------------

    def get_resolved(self) -> Dict[str, object]:
        """Return mapping of field PID → handling mode and resolution data.

        Must be called only after the dialog has been accepted.

        Returns a dict with keys:
          'records': Dict[pid → BenchmarkRecord | None]
          'modes':   Dict[pid → handling mode string]
          'manual':  Dict[pid → manual override name] (only rows with an override)
          'dirty':   set of pids whose row the user actually changed

        Handling modes:
          'use_selected' — use the DB candidate or manual override
          'exclude' — skip this point from the project
          'estimate' — point not found; ask engine to estimate from neighbors
          'pkt_average' — PKT point; use average of all neighbor positions
          'defer' — not decided; continue distance matching from other choices
        """
        from geolevel_db_manager import BenchmarkRecord  # local import — avoids circular dep

        records: Dict[str, object] = {}
        modes: Dict[str, str] = {}
        manual_names: Dict[str, str] = {}

        for pid in self._target_points:
            mode = self._handling[pid].currentData()
            modes[pid] = mode

            if mode in ("exclude", "defer"):
                records[pid] = None
                continue

            if mode in ("estimate", "pkt_average"):
                records[pid] = BenchmarkRecord(
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
                continue

            manual = self._edits[pid].text().strip()
            if manual:
                manual_names[pid] = manual
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
                records[pid] = self._combos[pid].currentData()

        return {"records": records, "modes": modes, "manual": manual_names,
                "dirty": set(self._dirty)}
