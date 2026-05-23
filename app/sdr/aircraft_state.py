"""
ADS-B aircraft state tracking.

Aircraft   — dataclass holding the last-known state of one aircraft.
AircraftStateTracker — thread-safe dict of ICAO → Aircraft with staleness management.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Aircraft:
    icao: str
    callsign:      Optional[str]   = None
    lat:           Optional[float] = None
    lon:           Optional[float] = None
    altitude:      Optional[int]   = None  # feet
    speed:         Optional[int]   = None  # knots ground speed
    track:         Optional[float] = None  # degrees true
    vertical_rate: Optional[int]   = None  # ft/min
    squawk:        Optional[str]   = None
    alert:         bool = False
    emergency:     bool = False
    spi:           bool = False            # Special Position Ident
    first_seen:    datetime = field(default_factory=datetime.now)
    last_seen:     datetime = field(default_factory=datetime.now)


class AircraftStateTracker:
    """Thread-safe dictionary of ICAO → Aircraft with staleness classification."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._aircraft: dict[str, Aircraft] = {}

    def update(self, icao: str, **fields) -> None:
        """Merge partial SBS message fields into the Aircraft entry for `icao`."""
        with self._lock:
            ac = self._aircraft.get(icao)
            if ac is None:
                ac = Aircraft(icao=icao)
                self._aircraft[icao] = ac
            for key, value in fields.items():
                if value is not None and hasattr(ac, key):
                    setattr(ac, key, value)
            ac.last_seen = datetime.now()

    def snapshot(self) -> dict[str, Aircraft]:
        """Return a shallow copy of the current aircraft dict (safe to read from UI thread)."""
        with self._lock:
            return dict(self._aircraft)

    def purge(self, grey_secs: int = 60, remove_secs: int = 300) -> set[str]:
        """
        Remove aircraft not heard in remove_secs seconds.
        Returns the set of ICAOs that were removed.
        """
        now = datetime.now()
        removed: set[str] = set()
        with self._lock:
            for icao, ac in list(self._aircraft.items()):
                age = (now - ac.last_seen).total_seconds()
                if age > remove_secs:
                    del self._aircraft[icao]
                    removed.add(icao)
        return removed

    def clear(self) -> None:
        with self._lock:
            self._aircraft.clear()

    def age_secs(self, icao: str) -> float:
        """Seconds since this aircraft was last heard, or inf if not tracked."""
        with self._lock:
            ac = self._aircraft.get(icao)
            if ac is None:
                return float("inf")
            return (datetime.now() - ac.last_seen).total_seconds()
