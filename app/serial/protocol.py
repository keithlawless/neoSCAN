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
        Returns None if scanner is idle (squelch closed).

        GLG response fields (positions 0-11):
          0:FRQ/TGID  1:MOD  2:ATT  3:CTCSS/DCS
          4:NAME1(sys)  5:NAME2(grp)  6:NAME3(ch)
          7:SQL(0=closed/1=open)  8:MUT  9:SYS_TAG  10:CHAN_TAG  11:P25NAC
        """
        try:
            payload = self.send_command("GLG")
        except Exception:
            return None
        if not payload or payload.startswith("NG"):
            return None
        fields = payload.split(",")
        keys = ["frequency", "mod", "att", "ctcss",
                "sys_name", "grp_name", "ch_name",
                "sql", "mute", "sys_tag", "chan_tag", "p25nac"]
        info = {keys[i]: fields[i] for i in range(min(len(keys), len(fields)))}
        # When idle the scanner returns all-empty fields; frequency absent = no transmission
        if not info.get("frequency"):
            return None
        return info

    def send_key(self, key: str, mode: str = "P") -> None:
        """
        Send a virtual keypress to the scanner.
        key:  S=SCAN, H=HOLD, L=LOCKOUT, 0-9=number keys, etc.
        mode: P=Press (default), L=Long Press, H=Hold, R=Release
        """
        self.send_command("KEY", key, mode)

    # ------------------------------------------------------------------
    # Program mode — system index head
    # ------------------------------------------------------------------

    def get_system_index_head(self) -> int:
        """SIH — return index of first system, or -1 if none."""
        payload = self.send_command("SIH")
        try:
            return int(payload.split(",")[0])
        except (ValueError, IndexError):
            return -1

    # ------------------------------------------------------------------
    # Conventional system read/write
    # ------------------------------------------------------------------

    def get_system_info(self, sys_index: int) -> list[str]:
        """SIN,<idx> — return system info fields (0-based list)."""
        payload = self.send_command("SIN", str(sys_index))
        return payload.split(",")

    def set_system_info(self, sys_index: int, payload: str) -> None:
        """SIN,<idx>,<fields> — write system info."""
        self.send_command("SIN", *([str(sys_index)] + payload.split(",")))

    def create_system(self, sys_type: str = "CNV") -> int:
        """CSY,<type> — create new system slot, return its index."""
        payload = self.send_command("CSY", sys_type)
        try:
            return int(payload.split(",")[0])
        except (ValueError, IndexError):
            raise ProtocolError(f"CSY returned unexpected payload: {payload!r}")

    def get_group_info(self, grp_index: int) -> list[str]:
        """GIN,<idx> — return group info fields."""
        payload = self.send_command("GIN", str(grp_index))
        return payload.split(",")

    def set_group_info(self, grp_index: int, payload: str) -> None:
        """GIN,<idx>,<fields> — write group info."""
        self.send_command("GIN", *([str(grp_index)] + payload.split(",")))

    def add_group(self, sys_index: int) -> int:
        """AGC,<sys_idx> — add group to system, return new group index."""
        payload = self.send_command("AGC", str(sys_index))
        try:
            return int(payload.split(",")[0])
        except (ValueError, IndexError):
            raise ProtocolError(f"AGC returned unexpected payload: {payload!r}")

    def get_channel_info(self, ch_index: int) -> list[str]:
        """CIN,<idx> — return channel info fields."""
        payload = self.send_command("CIN", str(ch_index))
        return payload.split(",")

    def set_channel_info(self, ch_index: int, payload: str) -> None:
        """CIN,<idx>,<fields> — write channel info."""
        self.send_command("CIN", *([str(ch_index)] + payload.split(",")))

    def add_channel(self, grp_index: int) -> int:
        """ACC,<grp_idx> — add channel to group, return new channel index."""
        payload = self.send_command("ACC", str(grp_index))
        try:
            return int(payload.split(",")[0])
        except (ValueError, IndexError):
            raise ProtocolError(f"ACC returned unexpected payload: {payload!r}")

    def set_quick_group_lockout(self, sys_index: int, pattern: str) -> None:
        """QGL,<sys_idx>,<pattern> — set quick group lockout pattern."""
        self.send_command("QGL", str(sys_index), pattern)

    def delete_system(self, sys_index: int) -> None:
        """DSY,<sys_idx> — delete a system."""
        self.send_command("DSY", str(sys_index))

    # ------------------------------------------------------------------
    # Trunked system protocol commands
    # ------------------------------------------------------------------

    def get_trunking_params(self, sys_index: int) -> list[str]:
        """TRN,<sys_idx> — return trunking parameters (27-field response)."""
        payload = self.send_command("TRN", str(sys_index))
        return payload.split(",")

    def set_trunking_params(self, sys_index: int, *fields: str) -> None:
        """TRN,<sys_idx>,<fields> — write trunking parameters."""
        self.send_command("TRN", str(sys_index), *fields)

    def get_site_info(self, site_index: int) -> list[str]:
        """SIF,<site_idx> — return site info fields (22-field response)."""
        payload = self.send_command("SIF", str(site_index))
        return payload.split(",")

    def set_site_info(self, site_index: int, *fields: str) -> None:
        """SIF,<site_idx>,<fields> — write site info."""
        self.send_command("SIF", str(site_index), *fields)

    def append_site(self, sys_index: int) -> int:
        """AST,<sys_idx> — add site to trunked system, return site index."""
        payload = self.send_command("AST", str(sys_index))
        try:
            return int(payload.split(",")[0])
        except (ValueError, IndexError):
            raise ProtocolError(f"AST returned unexpected payload: {payload!r}")

    def get_trunk_freq(self, freq_index: int) -> list[str]:
        """TFQ,<freq_idx> — return trunk frequency fields (10-field response)."""
        payload = self.send_command("TFQ", str(freq_index))
        return payload.split(",")

    def set_trunk_freq(self, freq_index: int, *fields: str) -> None:
        """TFQ,<freq_idx>,<fields> — write trunk frequency."""
        self.send_command("TFQ", str(freq_index), *fields)

    def add_trunk_freq(self, site_index: int) -> int:
        """ACC,<site_idx> — add trunk frequency to site, return freq index.

        Note: ACC is reused for trunk frequencies (same command as add_channel,
        but the scanner context determines whether it creates a trunk freq or
        a conventional channel).
        """
        payload = self.send_command("ACC", str(site_index))
        try:
            return int(payload.split(",")[0])
        except (ValueError, IndexError):
            raise ProtocolError(f"ACC returned unexpected payload: {payload!r}")

    def append_tgid_group(self, sys_index: int) -> int:
        """AGT,<sys_idx> — add TGID group to trunked system, return group index."""
        payload = self.send_command("AGT", str(sys_index))
        try:
            return int(payload.split(",")[0])
        except (ValueError, IndexError):
            raise ProtocolError(f"AGT returned unexpected payload: {payload!r}")

    def get_tgid(self, tgid_index: int) -> list[str]:
        """TIN,<tgid_idx> — return talk group info fields (16-field response)."""
        payload = self.send_command("TIN", str(tgid_index))
        return payload.split(",")

    def set_tgid(self, tgid_index: int, *fields: str) -> None:
        """TIN,<tgid_idx>,<fields> — write talk group info."""
        self.send_command("TIN", str(tgid_index), *fields)

    def append_tgid(self, grp_index: int) -> int:
        """ACT,<grp_idx> — add talk group to TGID group, return tgid index."""
        payload = self.send_command("ACT", str(grp_index))
        try:
            return int(payload.split(",")[0])
        except (ValueError, IndexError):
            raise ProtocolError(f"ACT returned unexpected payload: {payload!r}")
