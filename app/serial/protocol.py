"""
BCT15-X / BCD996XT serial protocol layer.

Commands are sent as ASCII text terminated with CR (\r).
Responses are terminated with CR.  The response echoes the command name
followed by a comma and the data payload, e.g.:

    MDL,BCT15X\r
    VER,1.04.00\r
    PRG,OK\r
    ERR\r      (command rejected)

This module provides a thin, synchronous command layer on top of
a pyserial Serial object.  Long-running operations (upload/download)
drive this from a QThread so the UI remains responsive.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import serial

log = logging.getLogger(__name__)

COMMAND_TIMEOUT = 3.0   # seconds to wait for a response
SLOW_COMMAND_TIMEOUT = 90.0  # for commands like CLR/DSY ("dozens of seconds" per spec)

# Commands that take noticeably longer
_SLOW_COMMANDS = {"CLR", "DSY", "DLT"}


class ProtocolError(Exception):
    """Raised when the scanner returns ERR or no response."""


class ScannerProtocol:
    """
    Wraps a serial.Serial connection and provides high-level scanner commands.

    Usage::

        proto = ScannerProtocol(conn)
        model = proto.get_model()         # "BCT15X"
        version = proto.get_firmware_version()  # "1.04.00"
    """

    def __init__(self, conn: serial.Serial) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Low-level send / receive
    # ------------------------------------------------------------------

    def send_command(self, cmd: str, *params: str) -> str:
        """
        Send a command and return the response data payload.

        :param cmd:    Command name, e.g. "MDL", "CIN"
        :param params: Optional parameters
        :returns:      Response payload (everything after the first comma),
                       or empty string if the response has no payload.
        :raises ProtocolError: On timeout or ERR response.
        """
        if params:
            full_cmd = cmd + "," + ",".join(str(p) for p in params)
        else:
            full_cmd = cmd

        raw = full_cmd + "\r"
        log.debug("TX: %r", raw.strip())

        timeout = SLOW_COMMAND_TIMEOUT if cmd in _SLOW_COMMANDS else COMMAND_TIMEOUT

        self._conn.reset_input_buffer()
        self._conn.write(raw.encode("ascii"))

        response = self._read_line(timeout)
        log.debug("RX: %r", response)

        if response == "ERR":
            raise ProtocolError(f"Scanner returned ERR for command: {full_cmd!r}")
        if not response:
            raise ProtocolError(f"Timeout waiting for response to: {full_cmd!r}")

        # Strip echoed command prefix if present (e.g. "MDL,BCT15X" → "BCT15X")
        if "," in response:
            _, _, payload = response.partition(",")
            return payload
        return response

    def _read_line(self, timeout: float) -> str:
        """Read until CR, respecting timeout. Returns stripped response string."""
        deadline = time.monotonic() + timeout
        buf = b""
        while time.monotonic() < deadline:
            if self._conn.in_waiting:
                chunk = self._conn.read(self._conn.in_waiting)
                buf += chunk
                if b"\r" in buf:
                    line = buf.split(b"\r")[0]
                    return line.decode("ascii", errors="replace").strip()
            else:
                time.sleep(0.005)
        # Timeout
        if buf:
            log.warning("Partial response on timeout: %r", buf)
        return ""

    # ------------------------------------------------------------------
    # High-level commands
    # ------------------------------------------------------------------

    def get_model(self) -> str:
        """Return scanner model string, e.g. 'BCT15X'."""
        return self.send_command("MDL")

    def get_firmware_version(self) -> str:
        """Return firmware version string, e.g. '1.04.00'."""
        return self.send_command("VER")

    def get_memory_used(self) -> int:
        """Return memory used as a percentage (0-100)."""
        payload = self.send_command("MEM")
        try:
            return int(payload.split(",")[0])
        except (ValueError, IndexError):
            return 0

    def enter_program_mode(self) -> None:
        """Enter programming mode (PRG). Must call before reading/writing config."""
        result = self.send_command("PRG")
        if result != "OK":
            raise ProtocolError(f"Failed to enter program mode: {result!r}")

    def exit_program_mode(self) -> None:
        """Exit programming mode (EPG)."""
        self.send_command("EPG")

    def get_status(self) -> dict[str, str]:
        """
        Query scanner status via STS command (remote control mode).
        Returns a dict of named fields.
        """
        payload = self.send_command("STS")
        # STS returns multiple comma-separated fields; names from protocol spec
        fields = payload.split(",") if payload else []
        keys = [
            "display_notice", "rssi", "mute", "sql", "func",
            "turbo", "mon_key", "att", "rec", "p25_status",
            "msg_status", "bat_charge",
        ]
        return {keys[i]: fields[i] for i in range(min(len(keys), len(fields)))}

    def get_received_channel_info(self) -> Optional[dict[str, str]]:
        """
        Query currently-received channel info (GLG command).
        Returns None if scanner is idle.
        """
        try:
            payload = self.send_command("GLG")
        except ProtocolError:
            return None
        if not payload or payload.startswith("NG"):
            return None
        fields = payload.split(",")
        keys = ["frequency", "mod", "att", "ctcss", "delay", "lockout",
                "pri", "sys_name", "grp_name", "ch_name", "sql_code", "mute"]
        return {keys[i]: fields[i] for i in range(min(len(keys), len(fields)))}

    def send_key(self, key: str, mode: str = "P") -> None:
        """
        Send a virtual keypress to the scanner.
        key:  S=SCAN, H=HOLD, L=LOCKOUT, 0-9=number keys, etc.
        mode: P=Press (default), L=Long Press, H=Hold, R=Release
        """
        self.send_command("KEY", key, mode)
