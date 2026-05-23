"""
ADS-B traffic logger.

Appends one CSV row per aircraft when it leaves the tracking grid.
Files are named adsb-YYYY-MM-DD.csv in the configured directory.
The file rolls over at midnight; active aircraft carry over to the
new day's file and are written when they eventually time out.
"""
from __future__ import annotations

import csv
import logging
import os
import threading
from datetime import date
from typing import Iterable, Optional

from app.sdr.aircraft_state import Aircraft

log = logging.getLogger(__name__)

_HEADERS = [
    "date", "icao", "callsign",
    "first_seen", "last_seen", "duration_s",
    "altitude_ft", "speed_kt", "track_deg",
    "lat", "lon",
    "squawk", "alert", "emergency", "spi",
]

DEFAULT_DIR = os.path.join(
    os.path.expanduser("~"), "Documents", "NeoSCAN", "ADS-B"
)


class ADSBLogger:
    """
    Logs aircraft statistics to a daily CSV file when they leave the grid.

    Thread-safe; log() and flush_all() may be called from any thread.
    Call close() on app shutdown.
    """

    def __init__(self) -> None:
        self._enabled = False
        self._directory: Optional[str] = None
        self._file = None
        self._writer: Optional[csv.DictWriter] = None
        self._current_date: Optional[date] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure(self, enabled: bool, directory: Optional[str]) -> None:
        """Update settings live (called after preferences are saved)."""
        with self._lock:
            self._enabled = enabled
            self._directory = directory or None
            if not enabled:
                self._close_file()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log(self, ac: Aircraft) -> None:
        """Append one aircraft row. No-op when disabled or directory unset."""
        if not self._enabled or not self._directory:
            return
        with self._lock:
            try:
                self._ensure_file()
                duration = int((ac.last_seen - ac.first_seen).total_seconds())
                self._writer.writerow({
                    "date":        ac.last_seen.strftime("%Y-%m-%d"),
                    "icao":        ac.icao,
                    "callsign":    ac.callsign or "",
                    "first_seen":  ac.first_seen.strftime("%H:%M:%S"),
                    "last_seen":   ac.last_seen.strftime("%H:%M:%S"),
                    "duration_s":  duration,
                    "altitude_ft": ac.altitude if ac.altitude is not None else "",
                    "speed_kt":    ac.speed if ac.speed is not None else "",
                    "track_deg":   f"{ac.track:.1f}" if ac.track is not None else "",
                    "lat":         f"{ac.lat:.5f}" if ac.lat is not None else "",
                    "lon":         f"{ac.lon:.5f}" if ac.lon is not None else "",
                    "squawk":      ac.squawk or "",
                    "alert":       int(ac.alert),
                    "emergency":   int(ac.emergency),
                    "spi":         int(ac.spi),
                })
                self._file.flush()
            except Exception:
                log.exception("ADSBLogger: failed to write row for %s", ac.icao)

    def flush_all(self, aircraft: Iterable[Aircraft]) -> None:
        """Write all given aircraft (call on disconnect or app exit)."""
        for ac in aircraft:
            self.log(ac)

    def close(self) -> None:
        """Flush and close the current log file."""
        with self._lock:
            self._close_file()

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _ensure_file(self) -> None:
        """Open or roll the log file when the calendar date has changed."""
        today = date.today()
        if self._current_date == today and self._file is not None:
            return
        self._close_file()
        os.makedirs(self._directory, exist_ok=True)
        path = os.path.join(self._directory, f"adsb-{today}.csv")
        is_new = not os.path.exists(path)
        self._file = open(path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=_HEADERS, extrasaction="ignore")
        if is_new:
            self._writer.writeheader()
        self._current_date = today
        log.info("ADSBLogger: writing to %s", path)

    def _close_file(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None
            self._writer = None
            self._current_date = None
