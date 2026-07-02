"""
Remote control panel — virtual scanner keypad + live display.
"""
from __future__ import annotations

import logging
import math

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
    QSlider,
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


class _LevelMeter(QWidget):
    """Horizontal audio input level meter on a dBFS scale.

    Shows the live line level from a radio's audio input with green/amber/red
    zones (green = healthy, red = approaching clipping). Renders a flat grey
    "no audio" bar when the input stream isn't delivering audio.
    """

    _DB_MIN = -54.0          # bottom of the visible scale
    _DB_GREEN_TOP = -12.0    # green below this
    _DB_AMBER_TOP = -3.0     # amber below this, red above

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._level = 0.0      # smoothed display level, fraction 0..1 of scale
        self._active = False
        self.setMinimumHeight(18)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            if not active:
                self._level = 0.0
            self.update()

    def set_level(self, level: float) -> None:
        """Set the current input level (linear 0..1); converted to a dB scale."""
        frac = self._level_to_frac(level)
        # Mild smoothing so the bar reads steadily rather than flickering.
        self._level = frac if frac >= self._level else self._level * 0.6 + frac * 0.4
        if self._active:
            self.update()

    @classmethod
    def _level_to_frac(cls, level: float) -> float:
        if level <= 1e-6:
            return 0.0
        db = 20.0 * math.log10(min(level, 1.0))
        if db <= cls._DB_MIN:
            return 0.0
        return (db - cls._DB_MIN) / (0.0 - cls._DB_MIN)

    @classmethod
    def _db_frac(cls, db: float) -> float:
        return (db - cls._DB_MIN) / (0.0 - cls._DB_MIN)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        rect = self.rect().adjusted(0, 0, -1, -1)
        p.fillRect(rect, QColor("#101010"))

        if not self._active:
            p.setPen(QColor("#555"))
            p.drawRect(rect)
            p.setPen(QColor("#777"))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "no audio")
            p.end()
            return

        w = rect.width()
        h = rect.height()
        filled = int(w * self._level)
        green_end = int(w * self._db_frac(self._DB_GREEN_TOP))
        amber_end = int(w * self._db_frac(self._DB_AMBER_TOP))

        for start, end, color in (
            (0, green_end, QColor("#2ecc40")),
            (green_end, amber_end, QColor("#ffcc00")),
            (amber_end, w, QColor("#ff4136")),
        ):
            seg_end = min(filled, end)
            if seg_end > start:
                p.fillRect(rect.left() + start, rect.top(),
                           seg_end - start, h, color)

        # Tick at the clip threshold (-3 dB).
        p.setPen(QColor("#888"))
        p.drawLine(rect.left() + amber_end, rect.top(),
                   rect.left() + amber_end, rect.top() + h)
        p.setPen(QColor("#444"))
        p.drawRect(rect)
        p.end()

    def sizeHint(self) -> QSize:
        return QSize(180, 18)


# Number pad keys: (label, key_code)
_NUM_KEYS = [
    ("1","1"), ("2","2"), ("3","3"),
    ("4","4"), ("5","5"), ("6","6"),
    ("7","7"), ("8","8"), ("9","9"),
    (".","No"), ("0","0"), ("E/No","E"),
]

# Function keys shown in two rows below the d-pad
_FUNC_KEYS = [
    ("SCAN","S"), ("HOLD","H"), ("FUNC","F"), ("MENU","M"),
    ("SRCH","T"), ("AVOID","L"), ("ATT","Y"),  ("REV","V"),
]

VOL_MIN, VOL_MAX = 0, 29
SQL_MIN, SQL_MAX = 0, 19


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

    def __init__(self, radio_label: str = "", parent=None) -> None:
        super().__init__(parent)
        self.radio_label = radio_label
        self._proto: ScannerProtocol | None = None
        self._level_source = None
        self._build_ui()

        # Poll this radio's audio input level for the meter (~20 fps).
        self._level_timer = QTimer(self)
        self._level_timer.setInterval(50)
        self._level_timer.timeout.connect(self._poll_level)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ── Scanner display ──────────────────────────────────────────────
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

        # ── Keypad ───────────────────────────────────────────────────────
        keypad_group = QGroupBox("Keypad")
        kp_layout = QVBoxLayout(keypad_group)
        kp_layout.setSpacing(6)

        # Number pad (4 rows × 3 cols)
        num_grid = QGridLayout()
        num_grid.setSpacing(4)
        for i, (label, code) in enumerate(_NUM_KEYS):
            btn = _KeyButton(label, code)
            btn.clicked.connect(lambda checked, k=code: self._send_key(k))
            num_grid.addWidget(btn, i // 3, i % 3)
        kp_layout.addLayout(num_grid)

        # D-pad (cross shape)
        dpad_grid = QGridLayout()
        dpad_grid.setSpacing(4)
        for label, code, row, col in [
            ("▲", "U", 0, 1),
            ("◀", "L", 1, 0),
            ("▶", "R", 1, 2),
            ("▼", "D", 2, 1),
        ]:
            btn = _KeyButton(label, code)
            btn.clicked.connect(lambda checked, k=code: self._send_key(k))
            dpad_grid.addWidget(btn, row, col)
        # Fixed-size spacer in the centre so the cross stays square
        dpad_grid.setColumnMinimumWidth(1, 56)
        dpad_grid.setRowMinimumHeight(1, 36)
        kp_layout.addLayout(dpad_grid)

        # Function keys (2 rows × 4 cols)
        func_grid = QGridLayout()
        func_grid.setSpacing(4)
        for i, (label, code) in enumerate(_FUNC_KEYS):
            btn = _KeyButton(label, code)
            btn.clicked.connect(lambda checked, k=code: self._send_key(k))
            func_grid.addWidget(btn, i // 4, i % 4)
        kp_layout.addLayout(func_grid)

        layout.addWidget(keypad_group)

        # ── Volume & Squelch ─────────────────────────────────────────────
        vs_group = QGroupBox("Volume / Squelch")
        vs_layout = QVBoxLayout(vs_group)

        self._vol_slider, self._vol_label = self._make_level_row(
            vs_layout, "Vol", VOL_MIN, VOL_MAX,
            lambda v: self._send_level("VOL", v),
        )
        self._sql_slider, self._sql_label = self._make_level_row(
            vs_layout, "SQL", SQL_MIN, SQL_MAX,
            lambda v: self._send_level("SQL", v),
            value_fmt=self._sql_fmt,
        )
        layout.addWidget(vs_group)

        # ── Audio input level ────────────────────────────────────────────
        audio_group = QGroupBox("Audio Input Level")
        audio_layout = QVBoxLayout(audio_group)
        self._level_meter = _LevelMeter()
        audio_layout.addWidget(self._level_meter)
        hint = QLabel("Line level from this radio's audio input (for transcription).")
        hint.setStyleSheet("color: gray; font-size: 11px;")
        hint.setWordWrap(True)
        audio_layout.addWidget(hint)
        layout.addWidget(audio_group)

        layout.addStretch()

        # Connection status
        self._status_label = QLabel("Not connected — connect to scanner to enable remote control.")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self._status_label)

        self._set_enabled(False)

    def _make_level_row(
        self, parent_layout, label: str, min_val: int, max_val: int,
        on_change, value_fmt=None,
    ) -> tuple[QSlider, QLabel]:
        """Build a labelled slider row with – / + buttons. Returns (slider, value_label)."""
        row = QHBoxLayout()
        lbl = QLabel(f"{label}:")
        lbl.setFixedWidth(28)
        row.addWidget(lbl)

        btn_minus = QPushButton("–")
        btn_minus.setFixedSize(26, 26)
        row.addWidget(btn_minus)

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(min_val, max_val)
        slider.setValue((min_val + max_val) // 2)
        row.addWidget(slider)

        btn_plus = QPushButton("+")
        btn_plus.setFixedSize(26, 26)
        row.addWidget(btn_plus)

        val_lbl = QLabel()
        val_lbl.setFixedWidth(52)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(val_lbl)

        fmt = value_fmt or (lambda v: str(v))

        def _update(v: int) -> None:
            val_lbl.setText(fmt(v))
            on_change(v)

        slider.valueChanged.connect(_update)
        btn_minus.clicked.connect(lambda: slider.setValue(max(min_val, slider.value() - 1)))
        btn_plus.clicked.connect(lambda: slider.setValue(min(max_val, slider.value() + 1)))

        # Set initial label without triggering the command
        val_lbl.setText(fmt(slider.value()))

        parent_layout.addLayout(row)
        return slider, val_lbl

    @staticmethod
    def _sql_fmt(v: int) -> str:
        if v == 0:
            return "0 (Open)"
        if v == SQL_MAX:
            return f"{v} (Close)"
        return str(v)

    def set_protocol(self, proto: ScannerProtocol | None) -> None:
        self._proto = proto
        self._set_enabled(proto is not None)
        if proto:
            self._status_label.setText("Connected. Use the keypad to control the scanner.")
            self._init_levels()
        else:
            self._status_label.setText("Not connected — connect to scanner to enable remote control.")
            self._display_top.setText("---")
            self._display_bottom.setText("")

    def showEvent(self, event) -> None:
        # Re-sync the volume/squelch sliders each time this radio's controls
        # become visible, so they reflect any changes made with the physical
        # knobs while another tab was showing. No continuous polling is needed.
        super().showEvent(event)
        self._init_levels()

    def _init_levels(self) -> None:
        """Query and initialize volume/squelch sliders from scanner."""
        if not self._proto:
            return
        try:
            vol = self._proto.send_command("VOL")
            self._vol_slider.blockSignals(True)
            self._vol_slider.setValue(int(vol.split(",")[0]))
            self._vol_slider.blockSignals(False)
            self._vol_label.setText(str(self._vol_slider.value()))
        except (ProtocolError, ValueError):
            pass
        try:
            sql = self._proto.send_command("SQL")
            self._sql_slider.blockSignals(True)
            self._sql_slider.setValue(int(sql.split(",")[0]))
            self._sql_slider.blockSignals(False)
            self._sql_label.setText(self._sql_fmt(self._sql_slider.value()))
        except (ProtocolError, ValueError):
            pass

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
                if len(freq) == 8 and freq.isdigit():
                    freq_str = f"{int(freq) / 10000.0:.4f} MHz"
                elif "." in freq:
                    freq_str = f"{float(freq):.4f} MHz"
                else:
                    freq_str = f"TGID {int(float(freq))}"
            except ValueError:
                freq_str = freq
        else:
            freq_str = ""

        self._display_top.setText(ch_name or freq_str or "---")
        self._display_bottom.setText(
            "  ".join(filter(None, [sys_name, grp_name, freq_str if ch_name else ""]))
        )

    def set_level_source(self, source) -> None:
        """Attach the audio source (a TranscriptionManager) whose input level
        feeds the meter. `source` exposes current_audio_level() and
        is_capturing_audio(). Pass None to detach."""
        self._level_source = source
        if source is not None:
            self._level_timer.start()
        else:
            self._level_timer.stop()
            self._level_meter.set_active(False)

    def _poll_level(self) -> None:
        src = self._level_source
        if src is None:
            self._level_meter.set_active(False)
            return
        active = src.is_capturing_audio()
        self._level_meter.set_active(active)
        if active:
            self._level_meter.set_level(src.current_audio_level())

    def _send_key(self, key_code: str) -> None:
        if not self._proto:
            return
        try:
            self._proto.send_key(key_code)
            self.key_pressed.emit(key_code)
        except ProtocolError as e:
            log.warning("Key send failed: %s", e)

    def _send_level(self, cmd: str, value: int) -> None:
        if not self._proto:
            return
        try:
            self._proto.send_command(cmd, str(value))
        except ProtocolError as e:
            log.warning("%s command failed: %s", cmd, e)

    def _set_enabled(self, enabled: bool) -> None:
        for child in self.findChildren(_KeyButton):
            child.setEnabled(enabled)
        self._vol_slider.setEnabled(enabled)
        self._sql_slider.setEnabled(enabled)
