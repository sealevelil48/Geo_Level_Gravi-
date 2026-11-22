"""
Database password handling for the standalone app.

The QGIS plugin keeps the password in the QGIS Authentication Manager.
Outside QGIS the password is looked up, in this order:

1. ``GEOLEVEL_DB_PASSWORD`` environment variable (scripts / CLI);
2. the password entered in this session (memory only);
3. the operating-system keyring (Windows Credential Manager, macOS
   Keychain, Secret Service), only when the optional ``keyring`` package is
   installed AND the user ticked "remember";
4. nothing - libpq then uses ``pgpass.conf`` / ``.pgpass`` or trust
   authentication.

The password is never written to settings.json or any project file.
"""
from __future__ import annotations

import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)

ENV_VAR = "GEOLEVEL_DB_PASSWORD"
KEYRING_SERVICE = "GeoLevelGravi-PostgreSQL"

_session: Dict[str, str] = {}


def _key(params: dict) -> str:
    return "{user}@{host}:{port}/{dbname}".format(
        user=params.get("user", "") or "",
        host=params.get("host", "") or "",
        port=params.get("port", 5432) or 5432,
        dbname=params.get("dbname", "") or "",
    )


def _keyring():
    try:
        import keyring          # optional dependency
        return keyring
    except Exception:
        return None


def keyring_available() -> bool:
    return _keyring() is not None


def get_password(params: dict) -> Optional[str]:
    """Password for *params* or None (libpq then tries pgpass / trust)."""
    env = os.environ.get(ENV_VAR)
    if env:
        return env
    k = _key(params)
    if k in _session:
        return _session[k]
    kr = _keyring()
    if kr is not None:
        try:
            pw = kr.get_password(KEYRING_SERVICE, k)
            if pw:
                _session[k] = pw
                return pw
        except Exception as exc:          # locked / no backend
            logger.debug("keyring lookup failed: %s", exc)
    return None


def set_password(params: dict, password: str, remember: bool = False) -> bool:
    """Use *password* for this session; with *remember* also store it in the
    OS keyring.  Returns True when it was stored in the keyring."""
    k = _key(params)
    _session[k] = password
    if not remember:
        return False
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.set_password(KEYRING_SERVICE, k, password)
        return True
    except Exception as exc:
        logger.warning("Could not store the password in the OS keyring: %s", exc)
        return False


def forget_password(params: dict) -> None:
    """Drop the session password and the keyring entry (if any)."""
    k = _key(params)
    _session.pop(k, None)
    kr = _keyring()
    if kr is not None:
        try:
            kr.delete_password(KEYRING_SERVICE, k)
        except Exception:
            pass


def is_auth_error(message: str) -> bool:
    """True when a connection error message means a missing / wrong password."""
    m = (message or "").lower()
    return any(s in m for s in ("password authentication failed",
                                "no password supplied",
                                "fe_sendauth",
                                "authentication failed"))
