"""
Remote control panel — virtual scanner keypad + live display.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter
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

_BG = "#1a1a1a"
_FG = "#88ff88"
_SCROLL_STEP_PX = 2    # pixels per tick
_SCROLL_INTERVAL_MS = 40  # ~25 fps
_HOLD_MS = 5000        # pause at end before restarting


class _ScrollingLabel(QWidget):
    """
    Single-line display that scrolls text horizontally when it overflows.
    Scrolls to the end, holds for _HOLD_MS, then restarts.
    """

    def __init__(self, font: QFont, parent=None) -> None:
        super().__init__(parent)
        self.setFont(font)
        self.setMinimumHeight(QFontMetrics(font).height() + 10)
        self._text = ""
        self._offset = 0

        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(_SCROLL_INTERVAL_MS)
        self._scroll_timer.timeout.connect(self._tick)

        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(_HOLD_MS)
        self._hold_timer.timeout.connect(self._restart)

    def setText(self, text: str) -> None:
        if text == self._text:
            return
        self._text = text
        self._offset = 0
        self._scroll_timer.stop()
        self._hold_timer.stop()
        self.update()
        self._maybe_start_scroll()

    def _text_width(self) -> int:
        return QFontMetrics(self.font()).horizontalAdvance(self._text)

    def _max_offset(self) -> int:
        return max(0, self._text_width() - self.width() + 8)

    def _maybe_start_scroll(self) -> None:
        if self._max_offset() > 0:
            self._scroll_timer.start()

    def _tick(self) -> None:
        self._offset = min(self._offset + _SCROLL_STEP_PX, self._max_offset())
        self.update()
        if self._offset >= self._max_offset():
            self._scroll_timer.stop()
            self._hold_timer.start()

    def _restart(self) -> None:
        self._offset = 0
        self.update()
        self._maybe_start_scroll()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Re-evaluate scroll need after resize; reset to start
        if not self._scroll_timer.isActive() and not self._hold_timer.isActive():
            self._offset = 0
            self._maybe_start_scroll()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(_BG))
        p.setFont(self.font())
        p.setPen(QColor(_FG))
        p.setClipRect(self.rect())
        fm = QFontMetrics(self.font())
        y = (self.height() + fm.ascent() - fm.descent()) // 2
        p.drawText(4 - self._offset, y, self._text)
        p.end()

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self.font())
        return QSize(200, fm.height() + 10)


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

        small_mono = QFont("Courier")
        small_mono.setPointSize(10)
        self._display_bottom = _ScrollingLabel(small_mono)

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
                freq_str = f"{float(freq):.4f} MHz"
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
