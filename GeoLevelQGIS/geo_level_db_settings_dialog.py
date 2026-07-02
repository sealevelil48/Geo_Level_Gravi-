"""
geo_level_db_settings_dialog.py
Database connection settings dialog for Geo Level Gravi.

Non-sensitive fields (host, port, dbname, user, table) are stored in
~/.geodetic_tool/settings.json.  The password is NEVER stored there —
it is managed exclusively via QGIS Authentication Manager (QgsAuthManager)
through the embedded QgsAuthSettingsWidget.
"""

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLineEdit, QSpinBox, QPushButton, QLabel, QSizePolicy, QWidget
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor


class GeoLevelDBSettingsDialog(QDialog):
    """
    Connection-settings dialog for the national benchmark PostgreSQL database.

    Layout
    ------
    ┌ Connection ──────────────────────────────┐
    │ Host:        [_______________]            │
    │ Port:        [5432]                       │
    │ Database:    [_______________]            │
    │ Username:    [_______________]            │
    │ Auth Config: [QgsAuthSettingsWidget]      │
    │ Table name:  [_______________]            │
    └──────────────────────────────────────────┘
    [ Test Connection ]   status label
    ─────────────────────────────────────────────
    [    Save    ]  [  Cancel  ]
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Database Connection / חיבור מסד נתונים")
        self.setMinimumWidth(480)
        self._build_ui()
        self._load_existing_params()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)

        # ── Connection group ──────────────────────────────────────────
        grp = QGroupBox("PostgreSQL Connection / חיבור PostgreSQL")
        form = QFormLayout(grp)
        form.setLabelAlignment(Qt.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self.edit_host = QLineEdit()
        self.edit_host.setPlaceholderText("e.g. 192.168.1.10")
        form.addRow("Host:", self.edit_host)

        self.spin_port = QSpinBox()
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(5432)
        form.addRow("Port:", self.spin_port)

        self.edit_dbname = QLineEdit()
        self.edit_dbname.setPlaceholderText("database name")
        form.addRow("Database:", self.edit_dbname)

        self.edit_user = QLineEdit()
        self.edit_user.setPlaceholderText("username")
        form.addRow("Username:", self.edit_user)

        # QGIS Auth Settings Widget — no password field of our own
        self.auth_widget = self._create_auth_widget()
        form.addRow("Auth Config:", self.auth_widget)

        self.edit_table = QLineEdit()
        self.edit_table.setPlaceholderText("benchmark table name (required)")
        form.addRow("Table name:", self.edit_table)

        root.addWidget(grp)

        # ── Test Connection row ───────────────────────────────────────
        test_row = QHBoxLayout()
        btn_test = QPushButton("Test Connection / בדוק חיבור")
        btn_test.setFixedWidth(200)
        btn_test.clicked.connect(self._on_test)
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        test_row.addWidget(btn_test)
        test_row.addWidget(self.lbl_status, 1)
        root.addLayout(test_row)

        # ── Buttons ───────────────────────────────────────────────────
        root.addStretch()
        btn_row = QHBoxLayout()
        btn_save = QPushButton("Save / שמור")
        btn_save.setStyleSheet(
            "font-weight: bold; background: #1565c0; color: white; padding: 5px 16px;"
        )
        btn_save.clicked.connect(self._on_save)
        btn_cancel = QPushButton("Cancel / בטל")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_cancel)
        root.addLayout(btn_row)

    def _create_auth_widget(self) -> QWidget:
        """
        Return QgsAuthSettingsWidget if available, otherwise a plain QLineEdit
        that accepts an authcfg ID string (fallback for environments without full
        QGIS GUI stack, e.g. tests or stripped installs).
        """
        try:
            from qgis.gui import QgsAuthSettingsWidget
            w = QgsAuthSettingsWidget(self)
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self._auth_is_native = True
            return w
        except Exception:
            fallback = QLineEdit()
            fallback.setPlaceholderText("authcfg ID (7-char string)")
            fallback.setToolTip(
                "Enter a QGIS Authentication Configuration ID.\n"
                "Create one in QGIS → Settings → Authentication."
            )
            self._auth_is_native = False
            return fallback

    # ------------------------------------------------------------------ #
    # Load / save helpers
    # ------------------------------------------------------------------ #

    def _load_existing_params(self):
        """Pre-populate fields from settings.json if a connection was saved before."""
        try:
            from core_logic.config.settings_manager import get_settings_manager
            params = get_settings_manager().get_db_connection()
        except Exception:
            return

        if not params:
            return

        self.edit_host.setText(params.get("host", ""))
        self.spin_port.setValue(int(params.get("port", 5432)))
        self.edit_dbname.setText(params.get("dbname", ""))
        self.edit_user.setText(params.get("user", ""))
        self.edit_table.setText(params.get("table", ""))

        authcfg = params.get("authcfg", "")
        if authcfg:
            self._set_authcfg(authcfg)

    def _get_authcfg(self) -> str:
        if self._auth_is_native:
            return self.auth_widget.configId()
        return self.auth_widget.text().strip()

    def _set_authcfg(self, authcfg_id: str):
        if self._auth_is_native:
            self.auth_widget.setConfigId(authcfg_id)
        else:
            self.auth_widget.setText(authcfg_id)

    def _collect_params(self) -> dict:
        return {
            "host":    self.edit_host.text().strip(),
            "port":    self.spin_port.value(),
            "dbname":  self.edit_dbname.text().strip(),
            "user":    self.edit_user.text().strip(),
            "table":   self.edit_table.text().strip(),
            "authcfg": self._get_authcfg(),
        }

    # ------------------------------------------------------------------ #
    # Slots
    # ------------------------------------------------------------------ #

    def _on_test(self):
        """Apply params to db_manager and test the connection."""
        params = self._collect_params()
        if not params["table"]:
            self._set_status(False, "Table name is required.")
            return
        if not params["authcfg"]:
            self._set_status(False, "No authentication configuration selected.")
            return

        try:
            from geolevel_db_manager import get_db_manager
            mgr = get_db_manager()
            mgr.configure(**params)
            ok, msg = mgr.test_connection()
            self._set_status(ok, msg)
        except Exception as exc:
            self._set_status(False, str(exc))

    def _on_save(self):
        """Validate, persist to settings.json, reconfigure db_manager, close."""
        params = self._collect_params()

        if not params["host"]:
            self._set_status(False, "Host is required.")
            return
        if not params["dbname"]:
            self._set_status(False, "Database name is required.")
            return
        if not params["table"]:
            self._set_status(False, "Table name is required.")
            return
        if not params["authcfg"]:
            self._set_status(False,
                "Please select or create an Authentication Configuration.")
            return

        try:
            from core_logic.config.settings_manager import get_settings_manager
            get_settings_manager().set_db_connection(
                host=params["host"],
                port=params["port"],
                dbname=params["dbname"],
                user=params["user"],
                table=params["table"],
                authcfg=params["authcfg"],
            )
            from geolevel_db_manager import get_db_manager
            get_db_manager().configure(**params)
        except Exception as exc:
            self._set_status(False, f"Failed to save: {exc}")
            return

        self.accept()

    def _set_status(self, ok: bool, message: str):
        """Update the status label with green (ok) or red (fail) text."""
        color = "#2e7d32" if ok else "#c62828"
        self.lbl_status.setStyleSheet(f"color: {color}; font-weight: bold;")
        self.lbl_status.setText(message)
