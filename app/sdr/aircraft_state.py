"""
ADS-B aircraft state tracking.

Aircraft   — dataclass holding the last-known state of one aircraft.
AircraftStateTracker — thread-safe dict of ICAO → Aircraft with staleness management.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# Vertical trend classification parameters
_ALT_HISTORY_MAXLEN = 30    # max altitude observations stored per aircraft
_TREND_MIN_POINTS   = 5     # minimum observations before classifying trend
_TREND_MIN_RANGE_FT = 500   # altitude excursion below this → always "Level"
_TREND_CLIMB_FPM    = 300.0 # |slope| above this threshold → Climbing/Descending
_TREND_WINDOW_SECS  = 300   # only use observations from the last 5 minutes


def _linear_slope(points: list[tuple[float, float]]) -> float:
    """Ordinary-least-squares slope (y per x unit) for a list of (x, y) pairs."""
    n = len(points)
    if n < 2:
        return 0.0
    sum_x  = sum(x for x, _ in points)
    sum_y  = sum(y for _, y in points)
    sum_xx = sum(x * x for x, _ in points)
    sum_xy = sum(x * y for x, y in points)
    denom  = n * sum_xx - sum_x ** 2
    return 0.0 if denom == 0 else (n * sum_xy - sum_x * sum_y) / denom


@dataclass
class Aircraft:
    icao: str
    callsign:        Optional[str]   = None
    lat:             Optional[float] = None
    lon:             Optional[float] = None
    altitude:        Optional[int]   = None  # feet
    speed:           Optional[int]   = None  # knots ground speed
    track:           Optional[float] = None  # degrees true
    vertical_rate:   Optional[int]   = None  # ft/min (raw from transponder)
    squawk:          Optional[str]   = None
    alert:           bool = False
    emergency:       bool = False
    spi:             bool = False            # Special Position Ident
    first_seen:      datetime = field(default_factory=datetime.now)
    last_seen:       datetime = field(default_factory=datetime.now)
    # Timestamped altitude observations for trend computation.
    # Stored as (unix_timestamp, altitude_ft) to allow time-windowing.
    altitude_history: deque = field(
        default_factory=lambda: deque(maxlen=_ALT_HISTORY_MAXLEN)
    )

    @property
    def vertical_trend(self) -> str:
        """
        Classify vertical movement as 'Climbing', 'Descending', 'Level', or ''
        (insufficient data).

        Algorithm:
          1. Restrict to observations within the last _TREND_WINDOW_SECS seconds.
          2. Require at least _TREND_MIN_POINTS observations.
          3. If the altitude range in the window is < _TREND_MIN_RANGE_FT, the
             variation is within noise — return 'Level'.
          4. Fit a linear regression; slope in ft/min determines the label.
        """
        cutoff = time.time() - _TREND_WINDOW_SECS
        points = [(ts, alt) for ts, alt in self.altitude_history if ts >= cutoff]

        if len(points) < _TREND_MIN_POINTS:
            return ""

        altitudes = [alt for _, alt in points]
        if max(altitudes) - min(altitudes) < _TREND_MIN_RANGE_FT:
            return "Level"

        # Normalise timestamps to seconds from the first observation so the
        # regression numerics stay small regardless of the epoch value.
        t0 = points[0][0]
        slope_fpm = _linear_slope([(ts - t0, alt) for ts, alt in points]) * 60.0

        if slope_fpm > _TREND_CLIMB_FPM:
            return "Climbing"
        if slope_fpm < -_TREND_CLIMB_FPM:
            return "Descending"
        return "Level"


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
            # Record altitude observation for trend tracking.
            if "altitude" in fields and fields["altitude"] is not None:
                ac.altitude_history.append((time.time(), fields["altitude"]))
            ac.last_seen = datetime.now()

    def snapshot(self) -> dict[str, Aircraft]:
        """Return a shallow copy of the current aircraft dict (safe to read from UI thread)."""
        with self._lock:
            return dict(self._aircraft)

    def purge(self, grey_secs: int = 60, remove_secs: int = 300) -> dict[str, Aircraft]:
        """
        Remove aircraft not heard in remove_secs seconds.
        Returns a dict of ICAO → Aircraft for each removed aircraft.
        """
        now = datetime.now()
        removed: dict[str, Aircraft] = {}
        with self._lock:
            for icao, ac in list(self._aircraft.items()):
                age = (now - ac.last_seen).total_seconds()
                if age > remove_secs:
                    del self._aircraft[icao]
                    removed[icao] = ac
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
