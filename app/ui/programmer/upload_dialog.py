"""
Upload dialog — programs the scanner with the current channel list.
Runs the upload in a background QThread so the UI stays responsive.
"""
from __future__ import annotations

import logging
import uuid

from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QCheckBox,
)

import serial

from app.data.models import ScannerConfig, System, Group, Channel, TalkGroup, SYS_TYPE_CONVENTIONAL
from app.serial.protocol import ScannerProtocol, ProtocolError
from app.serial.scanner_model import mod_mode_to_string, internal_to_sin_type

log = logging.getLogger(__name__)


def _para(payload: str, idx: int) -> str:
    """Extract the idx-th comma-separated field from a protocol response."""
    parts = payload.split(",")
    if idx < len(parts):
        return parts[idx]
    return ""


class _UploadWorker(QThread):
    """
    Background thread that performs the scanner upload.
    Emits progress/log signals for the dialog to display.
    """
    progress = pyqtSignal(int)          # 0-100
    log_line = pyqtSignal(str)          # text to append to log
    status = pyqtSignal(str)            # short status for the label
    finished_ok = pyqtSignal(int, int)  # systems_done, channels_done
    finished_err = pyqtSignal(str)      # error message

    def __init__(
        self,
        proto: ScannerProtocol,
        config: ScannerConfig,
        selected_systems: list[int],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._proto = proto
        self._config = config
        self._selected = selected_systems
        self._abort = False

    def abort(self) -> None:
        self._abort = True

    def run(self) -> None:
        try:
            self._do_upload()
        except Exception as exc:
            log.exception("Upload failed")
            self.finished_err.emit(str(exc))

    def _do_upload(self) -> None:
        proto = self._proto
        config = self._config
        systems = [config.systems[i] for i in self._selected]
        total_steps = max(1, sum(
            1 + len(s.groups) + sum(len(g.channels) for g in s.groups)
            for s in systems
        ))
        done = 0
        sys_count = 0
        ch_count = 0

        self.log_line.emit("Entering program mode…")
        proto.enter_program_mode()

        for sys in systems:
            if self._abort:
                break
            self.status.emit(f"Uploading system: {sys.name}")
            self.log_line.emit(f"\n[System] {sys.name} ({sys.type_name})")

            sys_type_str = internal_to_sin_type(sys.system_type)
            if sys.is_conventional:
                sys_type_str = "CNV"

            # Create system slot on scanner.
            # CSY requires [SYS_TYPE],[PROTECT] — PROTECT=0 means unprotected.
            try:
                sys_index = proto.send_command("CSY", sys_type_str, "0")
                sys_index = sys_index.strip()
            except ProtocolError as e:
                self.log_line.emit(f"  ERROR creating system: {e}")
                continue

            if not sys_index or sys_index in ("-1", "ERR"):
                self.log_line.emit(
                    f"  ERROR: Scanner returned invalid system index ({sys_index!r}). "
                    "Scanner memory may be full — try clearing the scanner first."
                )
                continue

            # Configure system via SIN.
            # SET format: SIN,[INDEX],[NAME],[QUICK_KEY],[HLD],[LOUT],[DLY],
            #   [RSV]*5,[START_KEY],[RECORD],[RSV]*5,[NUMBER_TAG],
            #   [AGC_ANALOG],[AGC_DIGITAL],[P25WAITING]
            # Empty fields ("," only) are left unchanged by the scanner.
            qk = sys.quick_key or "."
            lout = 1 if sys.lockout else 0
            cmd = (
                f"SIN,{sys_index},{sys.name},{qk},"
                f"{sys.hold_time},{lout},{sys.delay_time},"
                f",,,,,,,,,,,"  # 11 commas → 12 total empty fields (RSV*5, START_KEY, RECORD, RSV*5)
                f"NONE,0,0,0"   # NUMBER_TAG, AGC_ANALOG, AGC_DIGITAL, P25WAITING
            )
            try:
                proto.send_command(cmd)
            except ProtocolError as e:
                self.log_line.emit(f"  Warning: SIN error: {e}")

            done += 1
            self.progress.emit(int(done / total_steps * 100))

            if sys.is_conventional:
                for grp in sys.groups:
                    if self._abort:
                        break
                    self.log_line.emit(f"  [Group] {grp.name}")
                    # Add group
                    try:
                        grp_index = proto.send_command("AGC", sys_index)
                        grp_index = grp_index.strip()
                    except ProtocolError as e:
                        self.log_line.emit(f"    ERROR adding group: {e}")
                        done += 1 + len(grp.channels)
                        self.progress.emit(int(done / total_steps * 100))
                        continue

                    # Configure group via GIN.
                    # SET format: GIN,[GRP_INDEX],[NAME],[QUICK_KEY],[LOUT],
                    #   [LATITUDE],[LONGITUDE],[RANGE],[GPS_ENABLE]
                    grp_qk = grp.quick_key or "."
                    grp_lout = 1 if grp.lockout else 0
                    try:
                        proto.send_command(
                            f"GIN,{grp_index},{grp.name},"
                            f"{grp_qk},{grp_lout},,,,"  # trailing empty geo fields
                        )
                    except ProtocolError as e:
                        self.log_line.emit(f"    Warning: GIN error: {e}")

                    done += 1
                    self.progress.emit(int(done / total_steps * 100))

                    for ch in grp.channels:
                        if self._abort:
                            break
                        if not isinstance(ch, Channel):
                            done += 1
                            continue
                        try:
                            freq_raw = float(ch.frequency)
                        except (ValueError, TypeError):
                            done += 1
                            continue
                        if freq_raw <= 0:
                            done += 1
                            continue

                        freq_int = int(freq_raw * 10000)
                        mod = mod_mode_to_string(
                            ch.modulation if ch.modulation else "0"
                        )
                        # Allocate channel slot
                        try:
                            ch_index = proto.send_command("ACC", grp_index)
                            ch_index = ch_index.strip()
                        except ProtocolError as e:
                            self.log_line.emit(f"    ERROR allocating channel: {e}")
                            done += 1
                            continue

                        # Upload channel via CIN.
                        # SET format: CIN,[INDEX],[NAME],[FRQ],[MOD],[CTCSS/DCS],
                        #   [TLOCK],[LOUT],[PRI],[ATT],[ALT],[ALTL],
                        #   [RECORD],[AUDIO_TYPE],[P25NAC],[NUMBER_TAG],
                        #   [ALT_COLOR],[ALT_PATTERN],[VOL_OFFSET]
                        tone = ch.tone or "0"
                        alt = ch.alert_tone or "0"
                        altl = ch.alert_level or "0"
                        try:
                            proto.send_command(
                                f"CIN,{ch_index},{ch.name},{freq_int},{mod},"
                                f"{tone},{1 if ch.tone_lockout else 0},"
                                f"{1 if ch.lockout else 0},{1 if ch.priority else 0},"
                                f"{1 if ch.attenuator else 0},{alt},{altl},"
                                f"0,0,0,NONE,OFF,0,0"  # RECORD,AUDIO_TYPE,P25NAC,NUMBER_TAG,ALT_COLOR,ALT_PATTERN,VOL_OFFSET
                            )
                            self.log_line.emit(
                                f"    {ch.name}  {freq_raw:.4f} MHz  {mod}"
                            )
                            ch_count += 1
                        except ProtocolError as e:
                            self.log_line.emit(f"    ERROR on CIN: {e}")

                        done += 1
                        self.progress.emit(int(done / total_steps * 100))

                # Upload QGL (quick group lockout) for this system
                try:
                    qgl = sys.qgl or "1111111111"
                    proto.send_command(f"QGL,{sys_index},{qgl}")
                except ProtocolError:
                    pass

            sys_count += 1

        try:
            proto.exit_program_mode()
            self.log_line.emit("\nExited program mode.")
        except ProtocolError as e:
            self.log_line.emit(f"\nWarning: EPG error: {e}")

        self.progress.emit(100)
        self.finished_ok.emit(sys_count, ch_count)


class UploadDialog(QDialog):
    """Dialog for uploading the channel list to the scanner."""

    def __init__(
        self,
        proto: ScannerProtocol,
        config: ScannerConfig,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._proto = proto
        self._config = config
        self._worker: _UploadWorker | None = None

        self.setWindowTitle("Upload to Scanner")
        self.setMinimumSize(560, 500)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # System selection
        sys_group = QGroupBox("Select Systems to Upload")
        sys_layout = QVBoxLayout(sys_group)
        self._sys_list = QListWidget()
        for i, sys in enumerate(self._config.systems):
            item = QListWidgetItem(
                f"[{sys.type_name}] {sys.name or f'System {i+1}'}"
                f"  ({sum(len(g.channels) for g in sys.groups)} channels)"
            )
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, i)
            self._sys_list.addItem(item)
        self._sys_list.setMaximumHeight(140)
        sys_layout.addWidget(self._sys_list)
        layout.addWidget(sys_group)

        # Status
        self._status_label = QLabel("Ready to upload.")
        layout.addWidget(self._status_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        layout.addWidget(self._progress)

        # Log
        log_group = QGroupBox("Upload Log")
        log_layout = QVBoxLayout(log_group)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFontFamily("Courier")
        log_layout.addWidget(self._log)
        layout.addWidget(log_group)

        # Buttons
        btn_row = QHBoxLayout()
        self._upload_btn = QPushButton("Start Upload")
        self._upload_btn.clicked.connect(self._start_upload)
        self._abort_btn = QPushButton("Abort")
        self._abort_btn.setEnabled(False)
        self._abort_btn.clicked.connect(self._abort_upload)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._upload_btn)
        btn_row.addWidget(self._abort_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

    def _selected_systems(self) -> list[int]:
        result = []
        for i in range(self._sys_list.count()):
            item = self._sys_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(item.data(Qt.ItemDataRole.UserRole))
        return result

    def _start_upload(self) -> None:
        selected = self._selected_systems()
        if not selected:
            self._log.append("No systems selected.")
            return
        self._upload_btn.setEnabled(False)
        self._abort_btn.setEnabled(True)
        self._close_btn.setEnabled(False)
        self._log.clear()

        worker = _UploadWorker(self._proto, self._config, selected, parent=self)
        worker.progress.connect(self._progress.setValue)
        worker.log_line.connect(self._log.append)
        worker.status.connect(self._status_label.setText)
        worker.finished_ok.connect(self._on_done)
        worker.finished_err.connect(self._on_error)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _abort_upload(self) -> None:
        if self._worker:
            self._worker.abort()
        self._status_label.setText("Aborting…")
        self._abort_btn.setEnabled(False)

    def _on_done(self, sys_count: int, ch_count: int) -> None:
        self._status_label.setText(
            f"Done. Uploaded {sys_count} system(s), {ch_count} channel(s)."
        )
        self._upload_btn.setEnabled(True)
        self._abort_btn.setEnabled(False)
        self._close_btn.setEnabled(True)
        self._log.append(f"\nUpload complete: {sys_count} systems, {ch_count} channels.")

    def _on_error(self, msg: str) -> None:
        self._status_label.setText("Upload failed.")
        self._log.append(f"\nERROR: {msg}")
        self._upload_btn.setEnabled(True)
        self._abort_btn.setEnabled(False)
        self._close_btn.setEnabled(True)

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.abort()
            self._worker.wait(3000)
        super().closeEvent(event)
