"""
dump1090 subprocess manager.

Starts and stops the dump1090 process, monitors it, and emits status signals.
dump1090 must be installed on the host (e.g. `brew install dump1090-mutability`).
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import threading
import time
from collections import deque
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

log = logging.getLogger(__name__)

_STARTUP_WAIT_SECS = 5.0

# Common Windows locations where dump1090 is typically extracted.
_WIN_DUMP1090_CANDIDATES = [
    r"C:\dump1090-fa\dump1090.exe",
    r"C:\dump1090\dump1090.exe",
    r"C:\Program Files\dump1090-fa\dump1090.exe",
    r"C:\Program Files (x86)\dump1090-fa\dump1090.exe",
    os.path.join(os.path.expanduser("~"), "dump1090-fa", "dump1090.exe"),
    os.path.join(os.path.expanduser("~"), "Downloads", "dump1090-fa", "dump1090.exe"),
]


def _find_dump1090_exe(custom_path: Optional[str] = None) -> Optional[str]:
    """Return the path to a working dump1090 executable, or None."""
    if custom_path and os.path.isfile(custom_path):
        return custom_path
    # Standard PATH search (works on macOS/Linux after brew/apt install)
    exe = shutil.which("dump1090") or shutil.which("dump1090-mutability") or shutil.which("dump1090-fa")
    if exe:
        return exe
    if platform.system() == "Windows":
        for path in _WIN_DUMP1090_CANDIDATES:
            if os.path.isfile(path):
                return path
    return None


def _no_window_kwargs() -> dict:
    """
    Extra Popen/run kwargs to suppress the console window that Windows pops up
    for child processes. No-op on other platforms.
    """
    if platform.system() == "Windows":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def _probe_version(exe: str, timeout: float = 4.0) -> str:
    """
    Run ``<exe> --version`` and return its first non-empty output line.

    We can't simply wait for the process to exit: some dump1090 forks — notably
    the Mongoose-based Windows build (ver 0.4.x) — print the version line
    immediately but then start their event loop instead of exiting, so a plain
    ``subprocess.run(..., timeout=N)`` always raises TimeoutExpired even though
    the version string was already available. Instead we read the first line of
    output on a helper thread and kill the process once we have it (or once the
    timeout elapses).
    """
    proc = subprocess.Popen(
        [exe, "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        **_no_window_kwargs(),
    )
    captured: dict[str, str] = {}

    def _read_first_line() -> None:
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                line = line.strip()
                if line:
                    captured["line"] = line
                    return
        except Exception:
            pass

    reader = threading.Thread(target=_read_first_line, daemon=True)
    reader.start()
    reader.join(timeout)

    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
    reader.join(0.5)
    return captured.get("line", "")


def _kill_stale_dump1090() -> bool:
    """
    Kill any dump1090 left over from a previous session so it doesn't hold the
    network ports we're about to bind. Returns True if something was killed.
    """
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["taskkill", "/F", "/IM", "dump1090.exe"],
                capture_output=True, **_no_window_kwargs(),
            )
            # taskkill exits 0 when it killed something, 128 when no match.
            return result.returncode == 0
        result = subprocess.run(["pkill", "-f", "dump1090"], capture_output=True)
        return result.returncode == 0
    except Exception:
        return False


def _translate_error(stderr: str) -> str:
    """
    Map known dump1090 / librtlsdr error patterns to actionable user messages.
    Falls back to a trimmed version of the raw stderr if no pattern matches.
    """
    s = stderr.lower()

    if "usb_claim_interface error" in s or "kernel driver is active" in s or \
            ("libusb" in s and "error" in s):
        if platform.system() == "Windows":
            return (
                "RTL-SDR USB driver not installed or incorrect.\n\n"
                "Windows requires the WinUSB driver. To install it:\n"
                "1. Download Zadig from https://zadig.akeo.ie\n"
                "2. Plug in the RTL-SDR dongle.\n"
                "3. In Zadig: Options → List All Devices, then select\n"
                "   'Bulk-In, Interface (Interface 0)'.\n"
                "4. Choose 'WinUSB' and click 'Install Driver'.\n"
                "Then reconnect the dongle and try again."
            )
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
        if platform.system() == "Windows":
            return (
                "No RTL-SDR device found.\n\n"
                "Check that the dongle is plugged in, then verify that the\n"
                "WinUSB driver is installed via Zadig (https://zadig.akeo.ie).\n"
                "Click Refresh in the Connect SDR dialog and try again."
            )
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

    if "address already in use" in s:
        manual = ("  taskkill /F /IM dump1090.exe" if platform.system() == "Windows"
                  else "  pkill -f dump1090")
        return (
            "A dump1090 process from a previous session is still running and\n"
            "holding the network ports.\n\n"
            "Fix: disconnect and reconnect SDR — NeoSCAN will stop the old\n"
            "process automatically. If the problem persists, run manually:\n"
            + manual
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
    def is_installed(custom_path: Optional[str] = None) -> tuple[bool, str]:
        """Return (True, version_string) if dump1090 is found, else (False, error)."""
        exe = _find_dump1090_exe(custom_path)
        if exe is None:
            if platform.system() == "Windows":
                return False, (
                    "dump1090 not found.\n\n"
                    "Download dump1090-fa for Windows from:\n"
                    "  https://github.com/flightaware/dump1090/releases\n"
                    "Extract it, then set the path in Preferences → ADS-B."
                )
            return False, (
                "dump1090 not found. Install with:\n"
                "  macOS:  brew install dump1090-mutability\n"
                "  Linux:  sudo apt install dump1090-mutability"
            )
        try:
            version = _probe_version(exe)
            # Found and runnable, even if the build didn't print a parseable
            # version line — fall back to the executable name so the dialog
            # still shows it as installed rather than failing.
            return True, version or os.path.basename(exe)
        except Exception as exc:
            return False, str(exc)

    def start(self, device_index: int = 0, gain: str = "auto",
              exe_path: Optional[str] = None) -> None:
        """Launch dump1090 with --net enabled. Emits status_changed when ready."""
        if self._proc and self._proc.poll() is None:
            log.warning("Dump1090Manager: already running")
            return

        exe = _find_dump1090_exe(exe_path)
        if exe is None:
            self.status_changed.emit(False, "dump1090 not found — set path in Preferences → ADS-B")
            return

        # Kill any stale dump1090 left over from a previous session so it
        # doesn't hold the network ports we're about to bind.
        if _kill_stale_dump1090():
            log.info("Dump1090Manager: killed stale dump1090 process")
            time.sleep(0.5)  # give OS time to release port bindings

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
                **_no_window_kwargs(),
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
