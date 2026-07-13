"""
Transmission log panel — polls one or more scanners and records all transmissions.
"""
from __future__ import annotations

import csv
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
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

if TYPE_CHECKING:
    from app.data.radio_connection import RadioConnection

log = logging.getLogger(__name__)

POLL_INTERVAL_MS = 150   # poll scanners every 150ms

# When squelch closes we do not finalize the transmission immediately. The
# entry is held open for this grace window; if the same channel keys up again
# within it, the key-ups are merged into one "conversation" entry and one audio
# clip, giving Whisper more surrounding context to transcribe accurately.
# Recording is paused during the gap (see AudioRecorder.pause_recording) to keep
# squelch-tail noise out; the gap is re-inserted as clean silence on resume so
# the merged clip keeps natural pacing (splicing the speech together made it
# sound sped up and hurt transcription). Only after the window elapses with no
# re-key is the transmission finalized and sent for transcription.
CONVERSATION_GAP_MS = 3000

# Capture is not paused the instant squelch reads closed. On marginal or trunked
# signals the SQL flag picket-fences — flicking closed for a poll or two mid-
# transmission — and pausing on the first closed poll gates most of a continuous
# transmission out (it captured as sub-second clips and got discarded). We debounce:
# keep recording through a brief dip and only pause once squelch has stayed closed
# this long. Must be < CONVERSATION_GAP_MS. The cost is up to this much squelch-tail
# audio at the end of each clip, so keep it small.
CAPTURE_PAUSE_DEBOUNCE_MS = 500

# Log table columns
COL_RADIO = 0
COL_TIME = 1
COL_DURATION = 2
COL_CH_NAME = 3
COL_FREQ = 4
COL_SYS = 5
COL_GRP = 6
COL_MOD = 7
COL_TRANSCRIPT = 8
HEADERS = ["Radio", "Time", "Duration", "Channel", "Freq / TGID", "System", "Group", "Mod", "Transcript"]


class _TransmissionEntry:
    def __init__(self, info: dict, radio_label: str = "") -> None:
        self.start_time = datetime.now()
        self.end_time: Optional[datetime] = None
        self.radio_label = radio_label
        self.channel = info.get("ch_name", "")
        self.frequency = info.get("frequency", "")
        self.system = info.get("sys_name", "")
        self.group = info.get("grp_name", "")
        self.modulation = info.get("mod", "")
        self.transcript: str = ""
        self.transcript_pending: bool = False
        # Set while the entry is held open in the post-squelch grace window
        # waiting to see if the same channel keys up again (conversation merge).
        self.pending_close: bool = False
        # Wall-clock time squelch last closed; used as the frozen end_time so the
        # duration stays squelch-gated even while the entry is held open.
        self.squelch_closed_at: Optional[datetime] = None

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
        # BCD996P2 conventional frequencies are exactly 8 zero-padded digits (Hz/100)
        if len(self.frequency) == 8 and self.frequency.isdigit():
            try:
                return f"{int(self.frequency) / 10000.0:.4f} MHz"
            except ValueError:
                return self.frequency
        # BCT15X: decimal point = conventional frequency, no dot = TGID
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
    Polls one or more scanners for active transmissions and displays a merged log.
    Emits channel_info_updated(radio_label, info) so each ControlPanel can filter
    on its own label and update its display.
    """

    channel_info_updated = pyqtSignal(str, dict)   # (radio_label, channel_info)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._radios: list[RadioConnection] = []
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)
        self._logging = False
        self._entries: list[_TransmissionEntry] = []
        self._active_entries: dict[str, _TransmissionEntry | None] = {}  # label → active entry
        self._active_entry_rows: dict[str, int] = {}                     # label → row index
        # label → monotonic time squelch closed, for entries in the grace window
        self._squelch_closed_mono: dict[str, float] = {}
        # labels whose recorder is currently paused (squelch stayed closed past
        # CAPTURE_PAUSE_DEBOUNCE_MS). Tracked so resume only un-pauses a recorder
        # that was actually paused — a dip shorter than the debounce never paused,
        # so capture ran straight through and there is no gap to re-insert.
        self._capture_paused: set[str] = set()
        self._paused_labels: set[str] = set()
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

    # ------------------------------------------------------------------
    # Public API — multi-radio
    # ------------------------------------------------------------------

    def add_radio(self, radio: RadioConnection) -> None:
        """Register a connected radio and begin polling it."""
        self._radios.append(radio)
        self._active_entries[radio.label] = None
        if radio.transcription_manager is not None:
            radio.transcription_manager.transcription_ready.connect(
                lambda ri, text, job, r=radio: self._on_transcription_ready(ri, text, job, r)
            )
        if not self._timer.isActive():
            self._timer.start()
        self._set_controls(connected=True, logging=self._logging)

    def remove_radio(self, label: str) -> None:
        """Unregister a radio (called on disconnect). Ends any active transmission."""
        entry = self._active_entries.get(label)
        if entry is not None:
            row_index = self._active_entry_rows.pop(label, None)
            entry.pending_close = False
            entry.end_time = entry.squelch_closed_at or datetime.now()
            if row_index is not None:
                self._refresh_row(row_index)
            self._active_entries[label] = None

        self._radios = [r for r in self._radios if r.label != label]
        self._active_entries.pop(label, None)
        self._squelch_closed_mono.pop(label, None)
        self._capture_paused.discard(label)
        self._paused_labels.discard(label)

        if not self._radios:
            self._logging = False
            self._timer.stop()
        self._set_controls(connected=len(self._radios) > 0, logging=self._logging)

    def pause_polling(self, label: str | None = None) -> None:
        """Pause polling for one radio (or all if label is None)."""
        if label is not None:
            self._paused_labels.add(label)
        else:
            for r in self._radios:
                self._paused_labels.add(r.label)

    def resume_polling(self, label: str | None = None) -> None:
        """Resume polling for one radio (or all if label is None)."""
        if label is not None:
            self._paused_labels.discard(label)
        else:
            self._paused_labels.clear()
        if self._radios and not self._timer.isActive():
            self._timer.start()

    # ------------------------------------------------------------------
    # Logging controls
    # ------------------------------------------------------------------

    def _set_controls(self, connected: bool, logging: bool) -> None:
        self._start_btn.setEnabled(connected and not logging)
        self._stop_btn.setEnabled(logging)
        self._export_btn.setEnabled(len(self._entries) > 0)

    def _start_logging(self) -> None:
        if not self._radios:
            return
        self._logging = True
        self._status_label.setText("Logging…")
        self._status_label.setStyleSheet("font-size: 11px; color: green; font-weight: bold;")
        self._set_controls(connected=True, logging=True)
        log.info("Transmission logging started")

    def _stop_logging(self) -> None:
        self._logging = False
        for label in list(self._active_entries.keys()):
            entry = self._active_entries.get(label)
            if entry is not None:
                row_index = self._active_entry_rows.pop(label, None)
                entry.pending_close = False
                entry.end_time = entry.squelch_closed_at or datetime.now()
                if row_index is not None:
                    self._refresh_row(row_index)
                self._active_entries[label] = None
        self._squelch_closed_mono.clear()
        self._capture_paused.clear()
        self._status_label.setText("Stopped.")
        self._status_label.setStyleSheet("font-size: 11px; color: gray;")
        self._set_controls(connected=len(self._radios) > 0, logging=False)

    def _clear_log(self) -> None:
        self._entries.clear()
        self._active_entries = {label: None for label in self._active_entries}
        self._active_entry_rows.clear()
        self._squelch_closed_mono.clear()
        self._capture_paused.clear()
        self._table.setRowCount(0)
        self._set_controls(connected=len(self._radios) > 0, logging=self._timer.isActive())

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _poll(self) -> None:
        now_mono = time.monotonic()
        for radio in list(self._radios):
            if radio.label in self._paused_labels:
                continue
            try:
                info = radio.proto.get_received_channel_info()
                label = radio.label

                if info:
                    self.channel_info_updated.emit(label, info)
                    if not self._logging:
                        continue
                    active = self._active_entries.get(label)
                    if active is None:
                        # New transmission
                        self._begin_entry(radio, info)
                    elif active.pending_close:
                        # Held open after squelch close: same channel back within
                        # the grace window → merge; different channel → hand off.
                        if self._same_source(active, info):
                            self._resume_entry(radio, active)
                        else:
                            self._finalize_entry(radio, label)
                            self._begin_entry(radio, info)
                    else:
                        # Ongoing transmission — update duration in place
                        self._refresh_row(self._active_entry_rows[label])
                else:
                    if not self._logging:
                        continue
                    active = self._active_entries.get(label)
                    if active is None:
                        continue
                    if not active.pending_close:
                        # Squelch just closed — freeze the duration (squelch-gated)
                        # and start the grace window. Do NOT pause capture yet:
                        # capture is held through the debounce so a brief squelch
                        # dip does not gate out a continuous transmission.
                        active.pending_close = True
                        active.squelch_closed_at = datetime.now()
                        active.end_time = active.squelch_closed_at
                        self._squelch_closed_mono[label] = now_mono
                        self._refresh_row(self._active_entry_rows[label])
                    else:
                        closed_for = now_mono - self._squelch_closed_mono.get(label, now_mono)
                        if (label not in self._capture_paused
                                and closed_for >= CAPTURE_PAUSE_DEBOUNCE_MS / 1000.0):
                            # Squelch has stayed closed past the debounce — this is a
                            # real gap, not a dip. Pause capture so squelch-tail noise
                            # and the inter-key-up gap are kept out of the clip.
                            self._capture_paused.add(label)
                            if radio.transcription_manager:
                                radio.transcription_manager.on_transmission_paused()
                        if closed_for >= CONVERSATION_GAP_MS / 1000.0:
                            # Grace window elapsed with no re-key — finalize.
                            self._finalize_entry(radio, label)
            except Exception:
                log.exception("Error polling %s — continuing", radio.label)

    @staticmethod
    def _same_source(entry: _TransmissionEntry, info: dict) -> bool:
        """True if `info` is the same channel/talkgroup as `entry` — the basis
        for merging consecutive key-ups into one conversation."""
        freq = info.get("frequency", "")
        if freq or entry.frequency:
            return freq == entry.frequency
        return info.get("ch_name", "") == entry.channel

    def _begin_entry(self, radio: RadioConnection, info: dict) -> None:
        """Open a new transmission entry and start recording."""
        label = radio.label
        self._capture_paused.discard(label)  # fresh session starts un-paused
        entry = _TransmissionEntry(info, radio_label=label)
        row_index = len(self._entries)
        self._entries.append(entry)
        self._active_entries[label] = entry
        self._active_entry_rows[label] = row_index
        self._add_table_row(entry)
        self._set_controls(connected=True, logging=True)
        if radio.transcription_manager:
            radio.transcription_manager.on_transmission_started()

    def _resume_entry(self, radio: RadioConnection, entry: _TransmissionEntry) -> None:
        """Same channel keyed up within the grace window — keep the entry and
        clip open and resume capturing into it."""
        label = radio.label
        entry.pending_close = False
        entry.end_time = None
        entry.squelch_closed_at = None
        self._squelch_closed_mono.pop(label, None)
        if label in self._capture_paused:
            # Capture was paused (the gap exceeded the debounce) — resume and let
            # the recorder re-insert the gap as silence to keep pacing.
            self._capture_paused.discard(label)
            if radio.transcription_manager:
                radio.transcription_manager.on_transmission_resumed()
        # Otherwise the dip was shorter than the debounce: capture never paused, so
        # recording ran straight through and there is no gap to re-insert.
        self._refresh_row(self._active_entry_rows[label])

    def _finalize_entry(self, radio: RadioConnection, label: str) -> None:
        """Close out a transmission entry: stop recording and enqueue it for
        transcription. Duration stays frozen at the last squelch close."""
        entry = self._active_entries.get(label)
        if entry is None:
            return
        row_index = self._active_entry_rows.pop(label, None)
        self._squelch_closed_mono.pop(label, None)
        self._capture_paused.discard(label)
        entry.pending_close = False
        if entry.end_time is None:
            entry.end_time = entry.squelch_closed_at or datetime.now()
        tx_active = (
            radio.transcription_manager is not None
            and radio.transcription_manager.is_enabled
        )
        if tx_active:
            entry.transcript_pending = True
        if row_index is not None:
            self._refresh_row(row_index)
        if tx_active and row_index is not None:
            radio.transcription_manager.on_transmission_ended(row_index, entry)
        self._active_entries[label] = None

    # ------------------------------------------------------------------
    # Transcription callback
    # ------------------------------------------------------------------

    def _on_transcription_ready(self, row_index: int, text: str, job, radio: RadioConnection) -> None:
        try:
            if row_index < 0 or row_index >= len(self._entries):
                return
            entry = self._entries[row_index]
            entry.transcript = text
            entry.transcript_pending = False
            self._refresh_row(row_index)
            if radio.transcription_manager is not None and job is not None:
                radio.transcription_manager.on_transcription_done(row_index, text, job)
        except Exception:
            log.exception("Error handling transcription result for row %d", row_index)

    # ------------------------------------------------------------------
    # Table helpers
    # ------------------------------------------------------------------

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

        self._table.setItem(row, COL_RADIO, _cell(entry.radio_label))
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
        elif entry.end_time is not None and not entry.pending_close:
            tx_text = "(no speech)"
        else:
            tx_text = ""
        self._table.setItem(row, COL_TRANSCRIPT, _cell(tx_text))

    # ------------------------------------------------------------------
    # CSV export
    # ------------------------------------------------------------------

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
                    "Radio", "Time", "End Time", "Duration (s)", "Channel",
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
                        e.radio_label,
                        e.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                        end_str, f"{dur:.1f}",
                        e.channel, e.freq_display(),
                        e.system, e.group, e.modulation,
                        e.transcript,
                    ])
            QMessageBox.information(self, "Export Complete", f"Log exported to {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))
