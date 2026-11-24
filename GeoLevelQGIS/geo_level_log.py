"""
geo_level_log.py
Unified plugin log for the dock's "Log" tab.

Everything the plugin reports ends up in one place, in real time:

* Python ``logging`` records from the plugin's own loggers (``core_logic.*``,
  ``GeoLevelGravi``, the DB manager, the layer builder, the plugin package),
  INFO and above;
* ``QgsMessageLog`` messages written under the plugin's tags
  (``GeoLevelPlugin``, ``GeoLevel``, ``LSA``);
* the dock's own ``log()`` messages and step timings (:func:`timed`).

Records can arrive from worker threads, so they go through a Qt signal of
:class:`LogHub` (queued into the GUI thread) before they touch a widget.
The formatting and file-writing helpers are plain functions so they can be
tested without Qt.
"""

import contextlib
import datetime as _dt
import logging
import time

from qgis.PyQt.QtCore import QObject, pyqtSignal

# QgsMessageLog tags written by this plugin.  "Geo Level Gravi" is the tag of
# the DB manager's logging->QgsMessageLog bridge; those records are already
# captured through Python logging, so that tag is deliberately not listed
# (listing it would show every DB message twice).
PLUGIN_TAGS = frozenset({"GeoLevelPlugin", "GeoLevel", "LSA"})

# Python logger names (top level) used by the plugin's modules
BASE_LOGGER_NAMES = ("core_logic", "GeoLevelGravi")

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def format_entry(level: str, source: str, message: str, when=None) -> str:
    """One log line: ``HH:MM:SS LEVEL    [source] message``.  Continuation
    lines of a multi-line message are indented under the message."""
    when = when or _dt.datetime.now()
    head = f"{when:%H:%M:%S} {level:<8} [{source}] "
    lines = str(message).rstrip("\n").split("\n")
    pad = " " * len(head)
    return head + ("\n" + pad).join(lines)


def qgis_level_name(level) -> str:
    """Map a Qgis.MessageLevel (enum or int) to a level name."""
    try:
        v = int(level)
    except Exception:
        v = getattr(level, "value", 0)
    # Qgis.Info=0, Warning=1, Critical=2, Success=3, NoLevel=4
    return {0: "INFO", 1: "WARNING", 2: "ERROR", 3: "INFO"}.get(v, "INFO")


def minimal_logger_names(names) -> list:
    """Drop names that have an ancestor in the list, so a record is handled
    once even though it propagates up the logger tree."""
    uniq = sorted({n for n in names if n})
    out = []
    for n in uniq:
        if not any(n.startswith(a + ".") for a in uniq if a != n):
            out.append(n)
    return out


def export_header(project_name: str = "", when=None, label: str = "Exported") -> str:
    when = when or _dt.datetime.now()
    lines = ["Geo Level Gravi - plugin log",
             f"{label}: {when:%d/%m/%Y %H:%M:%S}"]
    if project_name:
        lines.append(f"Project: {project_name}")
    return "\n".join(lines) + "\n" + "-" * 72 + "\n"


def write_log_file(path: str, text: str, project_name: str = "") -> str:
    """Write the log *text* to *path* (UTF-8, Windows line endings are left
    to the editor).  Adds ``.log`` when the path has no extension."""
    import os
    if not os.path.splitext(path)[1]:
        path += ".log"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(export_header(project_name))
        fh.write(text.rstrip("\n") + "\n")
    return path


class LogHub(QObject):
    """Thread-safe fan-in: anything may call :meth:`post`; the dock connects
    :attr:`entry` (level, source, message) to its Log tab."""

    entry = pyqtSignal(str, str, str)

    def post(self, level: str, source: str, message: str):
        try:
            self.entry.emit(str(level), str(source), str(message))
        except RuntimeError:
            pass  # hub already deleted (plugin unloading)


class PluginLogHandler(logging.Handler):
    """Python logging -> LogHub."""

    def __init__(self, hub: LogHub, level=logging.INFO):
        super().__init__(level)
        self._hub = hub

    def emit(self, record: logging.LogRecord):
        try:
            msg = record.getMessage()
            if record.exc_info:
                msg += "\n" + logging.Formatter().formatException(record.exc_info)
            src = record.name.split(".")[-1] if record.name else "python"
            self._hub.post(record.levelname, src, msg)
        except Exception:
            pass  # never let logging break the plugin


class PluginLogCapture:
    """Installs / removes the handlers that feed a :class:`LogHub`."""

    def __init__(self, hub: LogHub, logger_names=()):
        self.hub = hub
        self.handler = PluginLogHandler(hub)
        self._names = minimal_logger_names(tuple(BASE_LOGGER_NAMES) + tuple(logger_names))
        self._saved_levels = {}
        self._msglog = None

    # -- Python logging ------------------------------------------------- #
    def install(self):
        for name in self._names:
            lg = logging.getLogger(name)
            if self.handler not in lg.handlers:
                lg.addHandler(self.handler)
            if lg.level == logging.NOTSET or lg.level > logging.INFO:
                self._saved_levels[name] = lg.level
                lg.setLevel(logging.INFO)
        self._connect_message_log()
        return self

    def uninstall(self):
        for name in self._names:
            lg = logging.getLogger(name)
            if self.handler in lg.handlers:
                lg.removeHandler(self.handler)
            if name in self._saved_levels:
                lg.setLevel(self._saved_levels[name])
        self._saved_levels.clear()
        if self._msglog is not None:
            try:
                self._msglog.messageReceived.disconnect(self._on_message)
            except Exception:
                pass
            self._msglog = None

    # -- QgsMessageLog -------------------------------------------------- #
    def _connect_message_log(self):
        try:
            from qgis.core import QgsApplication
            ml = QgsApplication.messageLog()
            ml.messageReceived.connect(self._on_message)
            self._msglog = ml
        except Exception:
            self._msglog = None  # outside QGIS (tests)

    def _on_message(self, message, tag, level):
        if tag in PLUGIN_TAGS:
            self.hub.post(qgis_level_name(level), tag, message)


@contextlib.contextmanager
def timed(label: str, sink=None, logger_name: str = "GeoLevelGravi"):
    """Log how long a step took (``TIMING`` line).  *sink* is a callable
    ``sink(level, source, message)``; default: Python logging."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        msg = f"{label}: {time.perf_counter() - t0:.2f} s"
        if sink is not None:
            try:
                sink("TIMING", "timing", msg)
            except Exception:
                pass
        else:
            logging.getLogger(logger_name).info("TIMING %s", msg)


class ProjectLogFile:
    """The project's ``<name>.log`` inside its project folder, kept up to
    date entry by entry (UTF-8, appended).  :meth:`open` starts it with the
    text already shown in the Log tab, so the file holds the whole session."""

    def __init__(self):
        self.path = ""

    def open(self, path: str, initial_text: str = "", project_name: str = ""):
        import os
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(
                os.path.abspath(self.path or " ")):
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

    def write(self, level: str, source: str, message: str):
        if not self.path:
            return
        try:
            with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(format_entry(str(level).upper(), source, message) + "\n")
        except OSError:
            pass    # folder gone / read-only: the Log tab still has it

    def close(self):
        self.path = ""
