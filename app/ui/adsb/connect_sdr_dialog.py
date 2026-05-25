"""
Connect SDR (ADS-B) dialog.

Enumerates attached RTL-SDR devices via `rtl_test` so the user can pick
from a list of named devices rather than guessing an index number.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from app.sdr.dump1090_manager import Dump1090Manager

# Common Windows locations for rtl_test.exe (part of the rtl-sdr binary package)
_WIN_RTL_TEST_CANDIDATES = [
    r"C:\rtl-sdr\rtl_test.exe",
    r"C:\Program Files\rtl-sdr\rtl_test.exe",
    r"C:\Program Files (x86)\rtl-sdr\rtl_test.exe",
    os.path.join(os.path.expanduser("~"), "rtl-sdr", "rtl_test.exe"),
    os.path.join(os.path.expanduser("~"), "Downloads", "rtl-sdr", "rtl_test.exe"),
]


@dataclass
class _SDRDevice:
    index: int
    description: str  # e.g. "Realtek, RTL2838UHIDIR, SN: 00000001"

    def __str__(self) -> str:
        return f"[{self.index}]  {self.description}"


def _scan_devices() -> tuple[list[_SDRDevice], str]:
    """
    Enumerate attached RTL-SDR devices. Returns (device_list, status_message).

    Tries pyrtlsdr first (cleaner, cross-platform), then falls back to the
    rtl_test subprocess (part of the rtl-sdr tools package).
    """
    # --- pyrtlsdr (preferred) ---
    try:
        from rtlsdr import RtlSdr  # type: ignore[import]
        count = RtlSdr.get_device_count()
        if count == 0:
            return [], _no_device_message()
        devices = []
        for i in range(count):
            try:
                name = RtlSdr.get_device_name(i)
                serial = RtlSdr.get_device_serial(i)
                desc = f"{name}, SN: {serial}" if serial else name
            except Exception:
                desc = f"RTL-SDR device {i}"
            devices.append(_SDRDevice(index=i, description=desc))
        return devices, f"{count} device(s) found"
    except ImportError:
        pass  # pyrtlsdr not installed — fall through to rtl_test
    except OSError:
        pass  # native librtlsdr not found — fall through to rtl_test

    # --- rtl_test subprocess (fallback) ---
    exe = shutil.which("rtl_test")
    if exe is None and platform.system() == "Windows":
        for path in _WIN_RTL_TEST_CANDIDATES:
            if os.path.isfile(path):
                exe = path
                break

    if exe is None:
        if platform.system() == "Windows":
            return [], (
                "Cannot enumerate devices: pyrtlsdr not installed and rtl_test.exe\n"
                "not found. Install pyrtlsdr (pip install pyrtlsdr) or download\n"
                "the rtl-sdr Windows package from https://osmocom.org/projects/rtl-sdr"
            )
        return [], "rtl_test not found — install rtl-sdr (brew install rtl-sdr)"

    try:
        # rtl_test runs indefinitely; the device list is printed immediately,
        # so we kill it after a short timeout.
        proc = subprocess.Popen(
            [exe],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        output_lines: list[str] = []
        try:
            stdout, _ = proc.communicate(timeout=2)
            output_lines = stdout.splitlines()
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, _ = proc.communicate()
            output_lines = stdout.splitlines()
    except OSError as exc:
        return [], f"Could not run rtl_test: {exc}"

    devices: list[_SDRDevice] = []
    pattern = re.compile(r"^\s*(\d+):\s+(.+)$")
    for line in output_lines:
        m = pattern.match(line)
        if m:
            devices.append(_SDRDevice(index=int(m.group(1)), description=m.group(2).strip()))

    if not devices:
        combined = " ".join(output_lines).lower()
        if "no supported" in combined or "found 0" in combined:
            return [], _no_device_message()
        return [], "No devices detected in rtl_test output — check USB connection"

    return devices, f"{len(devices)} device(s) found"


def _no_device_message() -> str:
    if platform.system() == "Windows":
        return (
            "No RTL-SDR devices found.\n"
            "Check the USB connection and verify the WinUSB driver is installed\n"
            "via Zadig (https://zadig.akeo.ie)."
        )
    return "No RTL-SDR devices found — check USB connection"


class ConnectSDRDialog(QDialog):
    """
    Dialog for choosing an RTL-SDR device and gain before starting dump1090.
    Scans for attached devices automatically on open; a Refresh button re-scans.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connect SDR (ADS-B)")
        self.setMinimumWidth(480)
        self._devices: list[_SDRDevice] = []
        settings = QSettings("NeoSCAN", "NeoSCAN")
        self._dump1090_path: str = settings.value("adsb/dump1090_path", "") or ""
        self._build_ui()
        self._refresh_devices()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # dump1090 install status
        custom = self._dump1090_path or None
        installed, version_or_error = Dump1090Manager.is_installed(custom_path=custom)
        self._dump1090_label = QLabel(version_or_error)
        self._dump1090_label.setWordWrap(True)
        if installed:
            self._dump1090_label.setStyleSheet("color: green;")
        else:
            self._dump1090_label.setStyleSheet(
                "background: #fff3cd; border: 1px solid #d6b656; "
                "border-radius: 4px; padding: 6px; color: #5a4500;"
            )
        layout.addWidget(self._dump1090_label)

        form = QFormLayout()

        # Device row: combo + Refresh button
        device_row = QHBoxLayout()
        self._device_combo = QComboBox()
        self._device_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._device_combo.setMinimumWidth(300)
        device_row.addWidget(self._device_combo, 1)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setFixedWidth(70)
        refresh_btn.clicked.connect(self._refresh_devices)
        device_row.addWidget(refresh_btn)
        form.addRow("Device:", device_row)

        self._scan_status_label = QLabel("")
        self._scan_status_label.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow("", self._scan_status_label)

        self._gain_combo = QComboBox()
        self._gain_combo.addItems(["Auto (AGC)", "0 dB", "10 dB", "20 dB", "30 dB", "40 dB", "50 dB"])
        form.addRow("Gain:", self._gain_combo)

        layout.addLayout(form)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._connect_btn = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._connect_btn.setText("Connect")
        self._connect_btn.setEnabled(installed)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    # ------------------------------------------------------------------
    # Device scanning
    # ------------------------------------------------------------------

    def _refresh_devices(self) -> None:
        self._scan_status_label.setText("Scanning for devices…")
        self._device_combo.clear()
        self._devices, status = _scan_devices()

        if self._devices:
            for dev in self._devices:
                self._device_combo.addItem(str(dev), userData=dev.index)
            self._device_combo.setEnabled(True)
            self._scan_status_label.setText(status)
            self._scan_status_label.setStyleSheet("color: green; font-size: 11px;")
        else:
            self._device_combo.addItem("No devices found")
            self._device_combo.setEnabled(False)
            self._scan_status_label.setText(status)
            self._scan_status_label.setStyleSheet("color: #c0392b; font-size: 11px;")

        # Connect button requires both dump1090 installed and a device selected
        installed, _ = Dump1090Manager.is_installed(custom_path=self._dump1090_path or None)
        self._connect_btn.setEnabled(installed and bool(self._devices))

    # ------------------------------------------------------------------
    # Properties read by MainWindow after accept()
    # ------------------------------------------------------------------

    @property
    def device_index(self) -> int:
        idx = self._device_combo.currentData()
        return idx if idx is not None else 0

    @property
    def gain(self) -> str:
        text = self._gain_combo.currentText()
        if text.startswith("Auto"):
            return "auto"
        return text.split()[0]  # strip " dB" suffix
