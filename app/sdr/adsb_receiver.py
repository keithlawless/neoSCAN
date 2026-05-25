"""
ADS-B SBS stream receiver.

Connects to dump1090's TCP port 30003 (SBS BaseStation format), parses
incoming messages, and emits signals for the UI to consume.
"""
from __future__ import annotations

import logging
import socket
import time
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

log = logging.getLogger(__name__)

_SBS_HOST = "localhost"
_SBS_PORT = 30003
_RECONNECT_DELAY = 1.0   # seconds between reconnection attempts
_RECV_BUFSIZE = 4096

# SBS field indices (0-based)
_F_MSG_TYPE   = 0
_F_TX_TYPE    = 1
_F_ICAO       = 4
_F_CALLSIGN   = 10
_F_ALTITUDE   = 11
_F_SPEED      = 12
_F_TRACK      = 13
_F_LAT        = 14
_F_LON        = 15
_F_VRATE      = 16
_F_SQUAWK     = 17
_F_ALERT      = 18
_F_EMERGENCY  = 19
_F_SPI        = 20


def _opt_int(fields: list[str], idx: int) -> Optional[int]:
    try:
        v = fields[idx].strip()
        return int(v) if v else None
    except (IndexError, ValueError):
        return None


def _opt_float(fields: list[str], idx: int) -> Optional[float]:
    try:
        v = fields[idx].strip()
        return float(v) if v else None
    except (IndexError, ValueError):
        return None


def _opt_str(fields: list[str], idx: int) -> Optional[str]:
    try:
        v = fields[idx].strip()
        return v or None
    except IndexError:
        return None


def _opt_bool(fields: list[str], idx: int) -> Optional[bool]:
    try:
        v = fields[idx].strip()
        return bool(int(v)) if v else None
    except (IndexError, ValueError):
        return None


def _parse_sbs_line(line: str) -> Optional[tuple[str, dict]]:
    """
    Parse one SBS line.  Returns (icao, partial_fields) or None if the line
    should be ignored.
    """
    if not line.startswith("MSG,"):
        return None
    fields = line.split(",")
    if len(fields) < 11:
        return None

    tx_type = _opt_int(fields, _F_TX_TYPE)
    icao = _opt_str(fields, _F_ICAO)
    if not icao or tx_type is None:
        return None
    icao = icao.upper()

    updates: dict = {}

    if tx_type == 1:
        updates["callsign"] = _opt_str(fields, _F_CALLSIGN)

    elif tx_type == 3:
        updates["altitude"] = _opt_int(fields, _F_ALTITUDE)
        updates["lat"]      = _opt_float(fields, _F_LAT)
        updates["lon"]      = _opt_float(fields, _F_LON)

    elif tx_type == 4:
        updates["speed"]         = _opt_int(fields, _F_SPEED)
        updates["track"]         = _opt_float(fields, _F_TRACK)
        updates["vertical_rate"] = _opt_int(fields, _F_VRATE)

    elif tx_type == 5:
        updates["altitude"] = _opt_int(fields, _F_ALTITUDE)

    elif tx_type == 6:
        updates["squawk"]    = _opt_str(fields, _F_SQUAWK)
        updates["alert"]     = _opt_bool(fields, _F_ALERT) or False
        updates["emergency"] = _opt_bool(fields, _F_EMERGENCY) or False
        updates["spi"]       = _opt_bool(fields, _F_SPI) or False

    else:
        return None  # MSG types 2, 7, 8 carry no useful data for our grid

    return icao, updates


class ADSBReceiver(QThread):
    """
    Background thread that maintains a TCP connection to dump1090's SBS port,
    parses incoming ADS-B messages, and emits signals for each aircraft update.

    Reconnects automatically on disconnect; stops cleanly when stop() is called.
    """

    aircraft_updated   = pyqtSignal(str, dict)   # (icao, partial_fields)
    connection_changed = pyqtSignal(bool, str)    # (connected, status_message)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._running = False
        self._sock: Optional[socket.socket] = None

    def stop(self) -> None:
        self._running = False
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def run(self) -> None:
        self._running = True
        buf = ""

        while self._running:
            try:
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._sock.settimeout(5.0)
                self._sock.connect((_SBS_HOST, _SBS_PORT))
                self._sock.settimeout(None)
                log.info("ADSBReceiver: connected to %s:%d", _SBS_HOST, _SBS_PORT)
                self.connection_changed.emit(True, f"Connected to dump1090 SBS stream")
                buf = ""

                while self._running:
                    try:
                        chunk = self._sock.recv(_RECV_BUFSIZE)
                    except OSError:
                        break
                    if not chunk:
                        break
                    buf += chunk.decode("ascii", errors="replace")
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        line = line.rstrip("\r")
                        if not line:
                            continue
                        parsed = _parse_sbs_line(line)
                        if parsed:
                            icao, updates = parsed
                            self.aircraft_updated.emit(icao, updates)

            except (ConnectionRefusedError, OSError, TimeoutError) as exc:
                log.debug("ADSBReceiver: connection failed — %s", exc)
            finally:
                if self._sock:
                    try:
                        self._sock.close()
                    except OSError:
                        pass
                    self._sock = None

            if self._running:
                self.connection_changed.emit(False, "Disconnected — retrying…")
                log.debug("ADSBReceiver: will retry in %.1fs", _RECONNECT_DELAY)
                time.sleep(_RECONNECT_DELAY)

        self.connection_changed.emit(False, "Stopped")
        log.info("ADSBReceiver: stopped")
