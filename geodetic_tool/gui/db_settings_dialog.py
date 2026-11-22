"""
Database Connection dialog (Tkinter): PostgreSQL / PostGIS benchmark table
(the control-point table, e.g. cp_bangal / public.cp_table).

Host, port, database, user and table are saved to ~/.geodetic_tool/settings.json
(shared with the QGIS plugin).  The password is NEVER written there: it is
kept for this session, and - only if "Remember" is ticked and the optional
``keyring`` package is installed - in the operating-system keyring.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Optional

from ..db import credentials
from ..db.benchmark_db import get_db_manager
from .window_utils import fit_to_screen


def _current_params() -> dict:
    from ..config.settings_manager import get_settings_manager
    p = dict(get_settings_manager().get_db_connection() or {})
    p.setdefault("host", "localhost")
    p.setdefault("port", 5432)
    p.setdefault("dbname", "cp_bangal")
    p.setdefault("user", "postgres")
    p.setdefault("table", "public.cp_table")
    return p


def apply_params(params: dict) -> None:
    """Configure the shared DB manager with *params* (raises ValueError on a
    bad table name)."""
    get_db_manager().configure(host=params["host"], port=int(params["port"]),
                               dbname=params["dbname"], user=params["user"],
                               table=params["table"], authcfg=params.get("authcfg", ""))


class DBSettingsDialog(tk.Toplevel):
    FIELDS = (("host", "Host"), ("port", "Port"), ("dbname", "Database"),
              ("user", "User"), ("table", "Table  ([schema.]table)"))

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Database Connection / חיבור למסד נתונים")
        fit_to_screen(self, 520, 360)
        self.transient(parent)
        self.saved = False
        p = _current_params()
        self._vars = {k: tk.StringVar(value=str(p.get(k, ""))) for k, _ in self.FIELDS}
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)
        for r, (k, label) in enumerate(self.FIELDS):
            ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", pady=3)
            ttk.Entry(frm, textvariable=self._vars[k], width=36).grid(row=r, column=1, sticky="we")
        r = len(self.FIELDS)
        ttk.Label(frm, text="Password").grid(row=r, column=0, sticky="w", pady=3)
        self._pw = tk.StringVar(value="")
        ttk.Entry(frm, textvariable=self._pw, show="•", width=36).grid(row=r, column=1, sticky="we")
        self._remember = tk.BooleanVar(value=False)
        cb = ttk.Checkbutton(frm, text="Remember password in the system keyring",
                             variable=self._remember)
        cb.grid(row=r + 1, column=1, sticky="w")
        if not credentials.keyring_available():
            cb.state(["disabled"])
        note = ("The password is never saved in settings files. Leave it empty to use "
                "pgpass.conf / trusted login, or set GEOLEVEL_DB_PASSWORD.")
        if not credentials.keyring_available():
            note += "  (pip install keyring to enable 'Remember'.)"
        ttk.Label(frm, text=note, wraplength=470, foreground="#555").grid(
            row=r + 2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        frm.columnconfigure(1, weight=1)
        bar = ttk.Frame(self, padding=(12, 0, 12, 12))
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="Test Connection", command=self.test).pack(side=tk.LEFT)
        ttk.Button(bar, text="Save", command=self.save).pack(side=tk.RIGHT)
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=6)
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def params(self) -> dict:
        p = {k: v.get().strip() for k, v in self._vars.items()}
        try:
            p["port"] = int(p["port"] or 5432)
        except ValueError:
            raise ValueError("Port must be a number")
        p["authcfg"] = ""
        return p

    def _apply(self) -> Optional[dict]:
        try:
            p = self.params()
            apply_params(p)
        except ValueError as exc:
            messagebox.showerror("Database Connection", str(exc), parent=self)
            return None
        if self._pw.get():
            credentials.set_password(p, self._pw.get(), remember=self._remember.get())
        return p

    def test(self) -> bool:
        if self._apply() is None:
            return False
        ok, msg = get_db_manager().test_connection()
        (messagebox.showinfo if ok else messagebox.showerror)(
            "Test Connection", msg, parent=self)
        return ok

    def save(self) -> None:
        p = self._apply()
        if p is None:
            return
        from ..config.settings_manager import get_settings_manager
        get_settings_manager().set_db_connection(p["host"], p["port"], p["dbname"],
                                                 p["user"], p["table"], "")
        global _failed
        _failed = None
        self.saved = True
        self.destroy()


def ensure_db_ready(parent, log=None) -> bool:
    """Check the DB once before matching points.  Returns True when the DB
    can be queried, False when it is not configured or not reachable (loading
    then continues without DB coordinates).  On a password error the user is
    asked for the password (kept for the session; optionally remembered)."""
    mgr = get_db_manager()
    if not mgr.is_configured():
        return False
    if getattr(mgr, "_conn", None) is not None and not getattr(mgr._conn, "closed", 1):
        return True
    import time
    key = credentials._key(mgr._params)
    global _failed
    if _failed and _failed[0] == key and time.monotonic() - _failed[1] < _RETRY_S:
        return False          # just failed / declined: do not ask again in this load
    ok, msg = mgr.test_connection()
    tries = 0
    import os
    prompt = not os.environ.get(credentials.ENV_VAR)   # env password given: no prompt
    while prompt and not ok and credentials.is_auth_error(msg) and tries < 3:
        tries += 1
        pw = simpledialog.askstring(
            "Database password",
            f"Password for {mgr._params.get('user')}@{mgr._params.get('host')}/"
            f"{mgr._params.get('dbname')}:", show="•", parent=parent)
        if pw is None:
            break
        credentials.set_password(mgr._params, pw, remember=False)
        mgr.close_connection()
        ok, msg = mgr.test_connection()
    _failed = None if ok else (key, time.monotonic())
    if not ok:
        if log:
            log(f"Benchmark DB not available ({msg}) - loading without DB coordinates")
        global _warned
        if not _warned:                  # tell the user once per session
            _warned = True
            messagebox.showwarning(
                "Benchmark database",
                "The benchmark database could not be reached, so points are loaded "
                "without DB verification or coordinates.\n\n" + str(msg)[:400] +
                "\n\nCheck Settings > Database Connection.", parent=parent)
    return ok


_warned = False
_failed = None        # (connection key, time) of the last failed attempt
_RETRY_S = 60.0       # retry a failed connection after this many seconds
