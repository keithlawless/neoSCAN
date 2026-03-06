"""
Port manager — detects available serial ports and manages connect/disconnect.
"""
from __future__ import annotations

import serial
import serial.tools.list_ports
from serial.tools.list_ports_common import ListPortInfo


# Known USB VID:PID pairs for Uniden scanners (USB-serial adapters)
# BCT15X / BCD996XT use a Prolific or Silicon Labs USB-serial chip
_UNIDEN_VIDS = {0x10C4, 0x067B, 0x1A86, 0x0403}  # Silicon Labs, Prolific, CH340, FTDI

BAUD_RATE = 115200
BYTESIZE = serial.EIGHTBITS
PARITY = serial.PARITY_NONE
STOPBITS = serial.STOPBITS_ONE
TIMEOUT = 2.0  # seconds


def list_ports() -> list[ListPortInfo]:
    """Return all available serial ports, sorted with likely scanner ports first."""
    ports = list(serial.tools.list_ports.comports())
    ports.sort(key=_port_priority)
    return ports


def _port_priority(port: ListPortInfo) -> int:
    """Lower number = higher priority (shown first). Scanner-likely ports sort first."""
    if port.vid in _UNIDEN_VIDS:
        return 0
    name = (port.description or "").lower()
    if any(kw in name for kw in ("uniden", "scanner", "prolific", "silicon labs", "ch340", "usb serial")):
        return 1
    return 2


def is_likely_scanner(port: ListPortInfo) -> bool:
    """Heuristic: is this port likely to be a Uniden scanner?"""
    return _port_priority(port) < 2


def open_port(port_name: str) -> serial.Serial:
    """Open and return a configured serial.Serial connection to the scanner."""
    conn = serial.Serial(
        port=port_name,
        baudrate=BAUD_RATE,
        bytesize=BYTESIZE,
        parity=PARITY,
        stopbits=STOPBITS,
        timeout=TIMEOUT,
    )
    return conn


def close_port(conn: serial.Serial | None) -> None:
    """Safely close an open serial connection."""
    if conn and conn.is_open:
        conn.close()
