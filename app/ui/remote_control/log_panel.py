"""
Transmission log panel — polls the scanner and records all transmissions.
"""
from __future__ import annotations

import csv
import logging
import time
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread
from PyQt6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.serial.protocol import ScannerProtocol, ProtocolError

log = logging.getLogger(__name__)

POLL_INTERVAL_MS = 150   # poll scanner every 150ms

# Log table columns
COL_TIME = 0
COL_DURATION = 1
COL_CH_NAME = 2
COL_FREQ = 3
COL_SYS = 4
COL_GRP = 5
COL_MOD = 6
COL_TRANSCRIPT = 7
HEADERS = ["Time", "Duration", "Channel", "Freq / TGID", "System", "Group", "Mod", "Transcript"]


class _TransmissionEntry:
    def __init__(self, info: dict) -> None:
        self.start_time = datetime.now()
        self.end_time: Optional[datetime] = None
        self.channel = info.get("ch_name", "")
        self.frequency = info.get("frequency", "")
        self.system = info.get("sys_name", "")
        self.group = info.get("grp_name", "")
        self.modulation = info.get("mod", "")
        self.transcript: str = ""
        self.transcript_pending: bool = False

    @property
    def duration(self) -> str:
        if self.end_time:
            secs = (self.end_time - self.start_time).total_seconds()
        else:
            secs = (datetime.now() - self.start_time).total_seconds()
        return f"{secs:.1f}s"

    def freq_display(self) -> str:
        if not self.frequency:
            return ""
        # GLG field[0] is "FRQ/TGID": conventional frequencies have a decimal
        # point ("154.2350"), trunked TGIDs are plain integers ("33840").
        if "." not in self.frequency:
            try:
                return f"TGID {int(float(self.frequency))}"
            except (ValueError, TypeError):
                return self.frequency
        try:
            return f"{float(self.frequency):.4f} MHz"
        except (ValueError, TypeError):
            return self.frequency


class LogPanel(QWidget):
    """
    Polls the scanner for active transmissions and displays a running log.
    Emits channel_info_updated so the control panel can update its display.
    """

    channel_info_updated = pyqtSignal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._proto: ScannerProtocol | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)
        self._logging = False  # True only when user has started logging
        self._entries: list[_TransmissionEntry] = []
        self._active: Optional[_TransmissionEntry] = None
        self._last_info: Optional[dict] = None
        self._transcription_manager = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Controls row
        ctrl_row = QHBoxLayout()
        self._start_btn = QPushButton("Start Logging")
        self._start_btn.clicked.connect(self._start_logging)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_logging)
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._clear_log)
        self._export_btn = QPushButton("Export CSV…")
        self._export_btn.clicked.connect(self._export_csv)

        self._status_label = QLabel("Not logging.")
        self._status_label.setStyleSheet("font-size: 11px; color: gray;")

        ctrl_row.addWidget(self._start_btn)
        ctrl_row.addWidget(self._stop_btn)
        ctrl_row.addWidget(self._clear_btn)
        ctrl_row.addWidget(self._export_btn)
        ctrl_row.addStretch()
        ctrl_row.addWidget(self._status_label)
        layout.addLayout(ctrl_row)

        # Log table
        self._table = QTableWidget(0, len(HEADERS))
        self._table.setHorizontalHeaderLabels(HEADERS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(COL_TRANSCRIPT, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table)

        self._set_controls(connected=False, logging=False)

    def pause_polling(self) -> None:
        """Temporarily stop polling (e.g. while program mode is active). Preserves logging state."""
        self._timer.stop()

    def resume_polling(self) -> None:
        """Resume polling after a pause, if still connected."""
        if self._proto:
            self._timer.start()

    def set_protocol(self, proto: ScannerProtocol | None) -> None:
        self._proto = proto
        if proto:
            self._timer.start()
        else:
            self._logging = False
            self._timer.stop()
            if self._active:
                self._active.end_time = datetime.now()
                self._refresh_row(len(self._entries) - 1)
                self._active = None
        self._set_controls(connected=proto is not None, logging=self._logging)

    def set_transcription_manager(self, manager) -> None:
        """Wire up the TranscriptionManager. Call once from MainWindow after creation."""
        self._transcription_manager = manager
        manager.transcription_ready.connect(self._on_transcription_ready)

    def _on_transcription_ready(self, row_index: int, text: str, job) -> None:
        try:
            if row_index < 0 or row_index >= len(self._entries):
                return
            entry = self._entries[row_index]
            entry.transcript = text
            entry.transcript_pending = False
            self._refresh_row(row_index)
            if self._transcription_manager and job is not None:
                self._transcription_manager.on_transcription_done(row_index, text, job)
        except Exception:
            log.exception("Error handling transcription result for row %d", row_index)

    def _set_controls(self, connected: bool, logging: bool) -> None:
        self._start_btn.setEnabled(connected and not logging)
        self._stop_btn.setEnabled(logging)
        self._export_btn.setEnabled(len(self._entries) > 0)

    def _start_logging(self) -> None:
        if not self._proto:
            return
        self._logging = True
        self._status_label.setText("Logging…")
        self._status_label.setStyleSheet("font-size: 11px; color: green; font-weight: bold;")
        self._set_controls(connected=True, logging=True)
        log.info("Transmission logging started")

    def _stop_logging(self) -> None:
        self._logging = False
        if self._active:
            self._active.end_time = datetime.now()
            self._refresh_row(len(self._entries) - 1)
            self._active = None
        self._status_label.setText("Stopped.")
        self._status_label.setStyleSheet("font-size: 11px; color: gray;")
        self._set_controls(connected=self._proto is not None, logging=False)

    def _clear_log(self) -> None:
        self._entries.clear()
        self._active = None
        self._table.setRowCount(0)
        self._set_controls(connected=self._proto is not None, logging=self._timer.isActive())

    def _poll(self) -> None:
        if not self._proto:
            return
        try:
            info = self._proto.get_received_channel_info()

            if info:
                self.channel_info_updated.emit(info)
                if self._logging:
                    if self._active is None:
                        # New transmission started
                        entry = _TransmissionEntry(info)
                        self._entries.append(entry)
                        self._active = entry
                        self._add_table_row(entry)
                        self._set_controls(connected=True, logging=True)
                        if self._transcription_manager:
                            self._transcription_manager.on_transmission_started()
                    else:
                        # Ongoing transmission — update duration in place
                        self._refresh_row(len(self._entries) - 1)
            else:
                if self._logging and self._active is not None:
                    # Transmission ended
                    self._active.end_time = datetime.now()
                    row_index = len(self._entries) - 1
                    if self._transcription_manager:
                        self._active.transcript_pending = True
                    self._refresh_row(row_index)
                    if self._transcription_manager:
                        self._transcription_manager.on_transmission_ended(row_index, self._active)
                    self._active = None
        except Exception:
            log.exception("Error in poll — stopping timer")

    def _add_table_row(self, entry: _TransmissionEntry) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._fill_row(row, entry)
        self._table.scrollToBottom()

    def _refresh_row(self, row: int) -> None:
        if row < 0 or row >= self._table.rowCount():
            return
        if row < len(self._entries):
            self._fill_row(row, self._entries[row])

    def _fill_row(self, row: int, entry: _TransmissionEntry) -> None:
        def _cell(text: str) -> QTableWidgetItem:
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            return item

        self._table.setItem(row, COL_TIME, _cell(entry.start_time.strftime("%H:%M:%S")))
        self._table.setItem(row, COL_DURATION, _cell(entry.duration))
        self._table.setItem(row, COL_CH_NAME, _cell(entry.channel))
        self._table.setItem(row, COL_FREQ, _cell(entry.freq_display()))
        self._table.setItem(row, COL_SYS, _cell(entry.system))
        self._table.setItem(row, COL_GRP, _cell(entry.group))
        self._table.setItem(row, COL_MOD, _cell(entry.modulation))
        if entry.transcript_pending:
            tx_text = "transcribing\u2026"
        elif entry.transcript:
            tx_text = entry.transcript
        elif entry.end_time is not None:
            # Transcription finished but returned no speech
            tx_text = "(no speech)"
        else:
            tx_text = ""
        self._table.setItem(row, COL_TRANSCRIPT, _cell(tx_text))

    def _export_csv(self) -> None:
        if not self._entries:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Log", "transmission_log.csv", "CSV files (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Time", "End Time", "Duration (s)", "Channel",
                    "Frequency", "System", "Group", "Modulation", "Transcript"
                ])
                for e in self._entries:
                    end_str = e.end_time.strftime("%H:%M:%S") if e.end_time else ""
                    dur = (
                        (e.end_time - e.start_time).total_seconds()
                        if e.end_time else
                        (datetime.now() - e.start_time).total_seconds()
                    )
                    writer.writerow([
                        e.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                        end_str, f"{dur:.1f}",
                        e.channel, e.freq_display(),
                        e.system, e.group, e.modulation,
                        e.transcript,
                    ])
            QMessageBox.information(self, "Export Complete", f"Log exported to {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))
