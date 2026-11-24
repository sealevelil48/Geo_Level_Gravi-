"""
geodetic_tool/project_log.py
Log helpers for the standalone app (same format as the QGIS plugin's Log tab).

* :func:`format_entry` - ``HH:MM:SS LEVEL    [source] message``
* :class:`ProjectLogFile` - ``<project folder>/<name>.log``, appended entry
  by entry while the user works
* :func:`write_log_file` - "Export Log..." (UTF-8, header with project name)
* :class:`CallbackLogHandler` - Python ``logging`` records of the tool's own
  loggers (``geodetic_tool.*``) -> a callback, so they appear in the Log tab.
"""

import datetime as _dt
import logging
import os

LOGGER_NAME = "geodetic_tool"


def format_entry(level: str, source: str, message: str, when=None) -> str:
    """One log line; continuation lines are indented under the message."""
    when = when or _dt.datetime.now()
    head = f"{when:%H:%M:%S} {level:<8} [{source}] "
    lines = str(message).rstrip("\n").split("\n")
    pad = " " * len(head)
    return head + ("\n" + pad).join(lines)


def export_header(project_name: str = "", when=None, label: str = "Exported") -> str:
    when = when or _dt.datetime.now()
    lines = ["Geodetic Leveling Tool - log",
             f"{label}: {when:%d/%m/%Y %H:%M:%S}"]
    if project_name:
        lines.append(f"Project: {project_name}")
    return "\n".join(lines) + "\n" + "-" * 72 + "\n"


def write_log_file(path: str, text: str, project_name: str = "") -> str:
    """Write *text* to *path* (UTF-8); ``.log`` is added when the path has
    no extension.  Returns the path written."""
    if not os.path.splitext(path)[1]:
        path += ".log"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(export_header(project_name))
        fh.write(text.rstrip("\n") + "\n")
    return path


class ProjectLogFile:
    """``<name>.log`` in the project folder, kept up to date entry by entry.
    :meth:`open` starts it with the text the Log tab already shows."""

    def __init__(self):
        self.path = ""

    def open(self, path: str, initial_text: str = "", project_name: str = ""):
        if self.path and os.path.normcase(os.path.abspath(path)) == \
                os.path.normcase(os.path.abspath(self.path)):
            return
        self.path = path
        try:
            new = not os.path.exists(path)
            with open(path, "a", encoding="utf-8", newline="\n") as fh:
                if new:
                    fh.write(export_header(project_name, label="Started"))
                else:
                    fh.write("\n" + "=" * 72 + "\n"
                             + f"Session {_dt.datetime.now():%d/%m/%Y %H:%M:%S}\n")
                if isinstance(initial_text, str) and initial_text.strip():
                    fh.write(initial_text.rstrip("\n") + "\n")
        except OSError:
            self.path = ""

    def write_line(self, line: str):
        if not self.path:
            return
        try:
            with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line.rstrip("\n") + "\n")
        except OSError:
            pass

    def write(self, level: str, source: str, message: str):
        self.write_line(format_entry(str(level).upper(), source, message))

    def close(self):
        self.path = ""


class CallbackLogHandler(logging.Handler):
    """logging -> callback(level, source, message); never raises."""

    def __init__(self, callback, level=logging.INFO):
        super().__init__(level)
        self._cb = callback

    def emit(self, record):
        try:
            msg = record.getMessage()
            if record.exc_info:
                msg += "\n" + logging.Formatter().formatException(record.exc_info)
            self._cb(record.levelname, record.name.split(".")[-1], msg)
        except Exception:
            pass


def install_handler(callback, logger_name: str = LOGGER_NAME) -> CallbackLogHandler:
    """Attach a :class:`CallbackLogHandler` to the tool's logger tree (INFO)."""
    lg = logging.getLogger(logger_name)
    h = CallbackLogHandler(callback)
    lg.addHandler(h)
    if lg.level == logging.NOTSET or lg.level > logging.INFO:
        lg.setLevel(logging.INFO)
    return h
