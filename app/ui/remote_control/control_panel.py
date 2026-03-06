"""
Remote control panel — virtual scanner keypad + live display.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.serial.protocol import ScannerProtocol, ProtocolError

log = logging.getLogger(__name__)

# Key definitions: (label_on_button, key_code_sent_to_scanner)
KEYPAD = [
    ("1", "1"), ("2", "2"),  ("3", "3"),
    ("4", "4"), ("5", "5"),  ("6", "6"),
    ("7", "7"), ("8", "8"),  ("9", "9"),
    (".",".No"),("0", "0"),  ("E/No","E"),
    ("SCAN","S"),("HOLD","H"),("FUNC","F"),
    ("MENU","M"),("▲","U"),  ("▼","D"),
    ("◀","L"),  ("▶","R"),   ("SRCH","T"),
    ("AVOID","A"),("ATT","Y"),("REV","V"),
]


class _KeyButton(QPushButton):
    def __init__(self, label: str, key_code: str, parent=None) -> None:
        super().__init__(label, parent)
        self.key_code = key_code
        self.setFixedSize(56, 36)


class ControlPanel(QWidget):
    """
    Virtual scanner keypad and display panel.
    Only active when connected to a scanner.
    """

    key_pressed = pyqtSignal(str)  # key_code sent to scanner

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._proto: ScannerProtocol | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Display
        display_group = QGroupBox("Scanner Display")
        display_layout = QVBoxLayout(display_group)

        self._display_top = QLabel("---")
        self._display_top.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mono = QFont("Courier")
        mono.setPointSize(14)
        mono.setBold(True)
        self._display_top.setFont(mono)
        self._display_top.setStyleSheet(
            "background: #1a1a1a; color: #00ff00; padding: 6px; border-radius: 4px;"
        )

        self._display_bottom = QLabel("")
        self._display_bottom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        small_mono = QFont("Courier")
        small_mono.setPointSize(10)
        self._display_bottom.setFont(small_mono)
        self._display_bottom.setStyleSheet(
            "background: #1a1a1a; color: #88ff88; padding: 4px; border-radius: 4px;"
        )

        display_layout.addWidget(self._display_top)
        display_layout.addWidget(self._display_bottom)
        layout.addWidget(display_group)

        # Keypad
        keypad_group = QGroupBox("Keypad")
        grid = QGridLayout(keypad_group)
        grid.setSpacing(4)

        for i, (label, code) in enumerate(KEYPAD):
            btn = _KeyButton(label, code)
            btn.clicked.connect(lambda checked, k=code: self._send_key(k))
            row, col = divmod(i, 3)
            grid.addWidget(btn, row, col)

        layout.addWidget(keypad_group)
        layout.addStretch()

        # Connection status
        self._status_label = QLabel("Not connected — connect to scanner to enable remote control.")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self._status_label)

        self._set_enabled(False)

    def set_protocol(self, proto: ScannerProtocol | None) -> None:
        self._proto = proto
        self._set_enabled(proto is not None)
        if proto:
            self._status_label.setText("Connected. Use the keypad to control the scanner.")
        else:
            self._status_label.setText("Not connected — connect to scanner to enable remote control.")
            self._display_top.setText("---")
            self._display_bottom.setText("")

    def update_display(self, channel_info: dict | None) -> None:
        """Called by the log panel with the latest received channel info."""
        if not channel_info:
            return
        freq = channel_info.get("frequency", "")
        ch_name = channel_info.get("ch_name", "")
        sys_name = channel_info.get("sys_name", "")
        grp_name = channel_info.get("grp_name", "")

        if freq:
            try:
                freq_str = f"{float(freq)/10000:.4f} MHz"
            except ValueError:
                freq_str = freq
        else:
            freq_str = ""

        self._display_top.setText(ch_name or freq_str or "---")
        self._display_bottom.setText(
            "  ".join(filter(None, [sys_name, grp_name, freq_str if ch_name else ""]))
        )

    def _send_key(self, key_code: str) -> None:
        if not self._proto:
            return
        try:
            self._proto.send_key(key_code)
            self.key_pressed.emit(key_code)
        except ProtocolError as e:
            log.warning("Key send failed: %s", e)

    def _set_enabled(self, enabled: bool) -> None:
        for child in self.findChildren(_KeyButton):
            child.setEnabled(enabled)
