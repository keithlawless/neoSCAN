"""
Application logging setup.

The GUI binaries are built with ``console=False`` (see ``neoscan.spec``), which
means ``sys.stderr`` is ``None`` and anything logged through the default
``logging.basicConfig`` StreamHandler is silently discarded. That made the
packaged Windows/macOS apps impossible to diagnose in the field.

This module always installs a rotating *file* handler at a known per-user
location, so there is a log to read regardless of whether a console exists. A
console handler is still added when stderr is available (i.e. during
development). The log directory and level are read from QSettings so they can
be configured from Preferences → Logging.
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PyQt6.QtCore import QSettings

_ORG = "NeoSCAN"
_APP = "NeoSCAN"

LOG_FILENAME = "neoscan.log"
_MAX_BYTES = 1_000_000
_BACKUP_COUNT = 5
_FORMAT = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"

LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]
DEFAULT_LEVEL = "INFO"

# Module-level handle to the file handler so callers (e.g. the About box or a
# "view log" action) can locate the active log file after startup.
_file_handler: RotatingFileHandler | None = None


def default_log_dir() -> Path:
    """Platform-appropriate default directory for application logs."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / _APP / "Logs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / _APP
    # Linux / other: follow the XDG state spec.
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / _APP / "logs"


def configured_log_dir() -> Path:
    """The log directory from settings, falling back to the platform default."""
    s = QSettings(_ORG, _APP)
    override = (s.value("log/app_log_dir", "") or "").strip()
    return Path(override) if override else default_log_dir()


def configured_level() -> str:
    s = QSettings(_ORG, _APP)
    level = (s.value("log/app_log_level", DEFAULT_LEVEL) or DEFAULT_LEVEL).strip().upper()
    return level if level in LEVELS else DEFAULT_LEVEL


def current_log_file() -> Path:
    """Absolute path of the active log file (may not exist yet)."""
    if _file_handler is not None:
        return Path(_file_handler.baseFilename)
    return configured_log_dir() / LOG_FILENAME


def init_logging() -> Path:
    """
    Install file + (when available) console logging on the root logger.

    Safe to call once at startup, before the main window is created. Returns
    the path of the log file so the caller can report it.
    """
    global _file_handler

    level_name = configured_level()
    level = getattr(logging, level_name, logging.INFO)

    log_dir = configured_log_dir()
    log_file = log_dir / LOG_FILENAME

    root = logging.getLogger()
    root.setLevel(level)
    # Clear any handlers a stray basicConfig() may have installed so we don't
    # double-log or keep a dead None-stream handler around.
    for h in list(root.handlers):
        root.removeHandler(h)

    formatter = logging.Formatter(_FORMAT)

    # --- File handler (always) ---
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_file, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        fh.setFormatter(formatter)
        root.addHandler(fh)
        _file_handler = fh
    except OSError as exc:
        # If we genuinely can't write the log file, fall back to a console
        # handler (if any) rather than crashing the app at startup.
        _file_handler = None
        if sys.stderr is not None:
            sys.stderr.write(f"NeoSCAN: could not open log file {log_file}: {exc}\n")

    # --- Console handler (only when a real stderr exists, i.e. dev runs) ---
    if sys.stderr is not None:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(formatter)
        root.addHandler(ch)

    # The serial protocol layer is extremely chatty at DEBUG; keep it quiet so
    # the interesting audio/summary/SDR messages stay readable.
    logging.getLogger("app.serial.protocol").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging initialised at %s level — writing to %s", level_name, log_file,
    )
    return log_file


def apply_level(level_name: str) -> None:
    """Update the active log level at runtime (used when preferences change)."""
    level = getattr(logging, (level_name or DEFAULT_LEVEL).upper(), logging.INFO)
    logging.getLogger().setLevel(level)
    logging.getLogger("app.serial.protocol").setLevel(logging.WARNING)
