"""
dump1090 subprocess manager.

Starts and stops the dump1090 process, monitors it, and emits status signals.
dump1090 must be installed on the host (e.g. `brew install dump1090-mutability`).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from collections import deque
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

log = logging.getLogger(__name__)

_STARTUP_WAIT_SECS = 5.0


def _translate_error(stderr: str) -> str:
    """
    Map known dump1090 / librtlsdr error patterns to actionable user messages.
    Falls back to a trimmed version of the raw stderr if no pattern matches.
    """
    s = stderr.lower()

    if "usb_claim_interface error" in s or "kernel driver is active" in s:
        import platform
        if platform.system() == "Darwin":
            return (
                "USB device is claimed by the macOS kernel driver.\n\n"
                "Fix: unplug the RTL-SDR dongle, wait 2 seconds, then reconnect it.\n"
                "That resolves this in most cases.\n\n"
                "If it keeps happening, you can release the USB composite driver —\n"
                "note this affects composite USB devices only, not USB-serial\n"
                "adapters (FTDI, Prolific, etc.) used by other equipment:\n"
                "  sudo kextunload -b com.apple.driver.AppleUSBHostCompositeDevice\n"
                "then reconnect the dongle and try again.\n"
                "The driver reloads automatically on next reboot."
            )
        return (
            "USB device is claimed by a kernel module (dvb_usb_rtl28xxu).\n\n"
            "Fix: run in Terminal:\n"
            "  sudo rmmod dvb_usb_rtl28xxu\n"
            "then try connecting again. To prevent this at boot, add it to\n"
            "/etc/modprobe.d/blacklist-rtlsdr.conf:\n"
            "  blacklist dvb_usb_rtl28xxu"
        )

    if "no supported" in s or "no rtlsdr" in s or "no such file or directory" in s:
        return (
            "No RTL-SDR device found.\n\n"
            "Check that the dongle is plugged in and recognised by the OS,\n"
            "then click Refresh in the Connect SDR dialog and try again."
        )

    if "permission" in s or "access denied" in s:
        return (
            "Permission denied opening RTL-SDR device.\n\n"
            "On Linux, add your user to the 'plugdev' group:\n"
            "  sudo usermod -aG plugdev $USER\n"
            "then log out and back in."
        )

    if stderr.strip():
        # Unknown error — show the last 3 lines of stderr
        lines = [l for l in stderr.splitlines() if l.strip()]
        return "dump1090 error: " + " | ".join(lines[-3:])

    return "dump1090 exited unexpectedly — check the NeoSCAN log for details"    # how long to wait for dump1090 SBS port to open
_MONITOR_INTERVAL_MS = 3000 # how often to check the subprocess is still alive


class Dump1090Manager(QObject):
    """
    Manages the dump1090 child process lifecycle.

    status_changed(running, message) — emitted on start, stop, and crash.
    """

    status_changed = pyqtSignal(bool, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._proc: Optional[subprocess.Popen] = None
        self._monitor = QTimer(self)
        self._monitor.setInterval(_MONITOR_INTERVAL_MS)
        self._monitor.timeout.connect(self._check_alive)
        self._device_index = 0
        self._gain = "auto"
        self._stderr_buf: deque[str] = deque(maxlen=30)
        self._stderr_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def is_installed() -> tuple[bool, str]:
        """Return (True, version_string) if dump1090 is on PATH, else (False, error)."""
        exe = shutil.which("dump1090") or shutil.which("dump1090-mutability")
        if exe is None:
            return False, (
                "dump1090 not found. Install with:\n"
                "  macOS:  brew install dump1090-mutability\n"
                "  Linux:  sudo apt install dump1090-mutability"
            )
        try:
            result = subprocess.run(
                [exe, "--version"],
                capture_output=True, text=True, timeout=3,
            )
            version = (result.stdout + result.stderr).strip().splitlines()[0]
            return True, version
        except Exception as exc:
            return False, str(exc)

    def start(self, device_index: int = 0, gain: str = "auto") -> None:
        """Launch dump1090 with --net enabled. Emits status_changed when ready."""
        if self._proc and self._proc.poll() is None:
            log.warning("Dump1090Manager: already running")
            return

        exe = shutil.which("dump1090") or shutil.which("dump1090-mutability")
        if exe is None:
            self.status_changed.emit(False, "dump1090 not found on PATH")
            return

        self._device_index = device_index
        self._gain = gain

        cmd = [exe, "--net", "--device-index", str(device_index)]
        if gain.lower() != "auto":
            cmd += ["--gain", gain]

        log.info("Dump1090Manager: starting %s", " ".join(cmd))
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,   # capture so we can surface errors
                text=True,
            )
        except OSError as exc:
            self.status_changed.emit(False, f"Failed to start dump1090: {exc}")
            return

        # Wait until the SBS port opens (or the process dies)
        import socket as _socket
        deadline = time.monotonic() + _STARTUP_WAIT_SECS
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                stderr_out = ""
                try:
                    stderr_out = self._proc.stderr.read().strip()
                except Exception:
                    pass
                msg = _translate_error(stderr_out)
                log.error("Dump1090Manager: dump1090 exited — %s", msg)
                self.status_changed.emit(False, msg)
                return
            try:
                with _socket.create_connection(("localhost", 30003), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            self._proc.terminate()
            self._proc = None
            self.status_changed.emit(False, "dump1090 started but SBS port did not open in time")

        # Drain stderr continuously on a background thread so the pipe buffer
        # never fills and we always have the latest output available for error
        # reporting when the process exits unexpectedly.
        self._stderr_buf.clear()
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True, name="dump1090-stderr"
        )
        self._stderr_thread.start()

        gain_label = gain if gain.lower() != "auto" else "auto (AGC)"
        msg = f"dump1090 running — device {device_index}, gain {gain_label}, 1090 MHz"
        log.info("Dump1090Manager: %s", msg)
        self.status_changed.emit(True, msg)
        self._monitor.start()

    def stop(self) -> None:
        """Terminate dump1090 cleanly."""
        self._monitor.stop()
        if self._proc is None:
            return
        if self._proc.poll() is None:
            log.info("Dump1090Manager: stopping dump1090")
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                log.warning("Dump1090Manager: kill after timeout")
                self._proc.kill()
                self._proc.wait(timeout=2)
        self._proc = None
        self.status_changed.emit(False, "Stopped")

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _drain_stderr(self) -> None:
        """Read dump1090 stderr into the circular buffer (runs on a daemon thread)."""
        try:
            for line in self._proc.stderr:
                line = line.rstrip()
                if line:
                    self._stderr_buf.append(line)
                    log.debug("dump1090: %s", line)
        except Exception:
            pass

    def _check_alive(self) -> None:
        if self._proc and self._proc.poll() is not None:
            self._monitor.stop()
            if self._stderr_thread:
                self._stderr_thread.join(timeout=0.5)
            stderr_text = "\n".join(self._stderr_buf)
            msg = _translate_error(stderr_text)
            log.warning("Dump1090Manager: dump1090 exited — %s", msg)
            self._proc = None
            self.status_changed.emit(False, msg)
