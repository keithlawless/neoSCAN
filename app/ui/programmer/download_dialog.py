"""
Download dialog — reads the channel list from the scanner into a new ScannerConfig.
"""
from __future__ import annotations

import logging
import time
import uuid

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from app.data.models import (
    ScannerConfig, System, Group, Channel,
    SYS_TYPE_CONVENTIONAL,
)
from app.serial.protocol import ScannerProtocol, ProtocolError
from app.serial.scanner_model import sin_type_to_internal, mod_mode_to_string

log = logging.getLogger(__name__)


def _para(payload: str, idx: int) -> str:
    """Extract the 0-based idx-th field from a comma-delimited payload."""
    parts = payload.split(",")
    if idx < len(parts):
        return parts[idx].strip()
    return ""


class _DownloadWorker(QThread):
    """Background thread for scanner download."""

    progress = pyqtSignal(int)
    log_line = pyqtSignal(str)
    status = pyqtSignal(str)
    finished_ok = pyqtSignal(object)   # ScannerConfig
    finished_err = pyqtSignal(str)

    def __init__(self, proto: ScannerProtocol, parent=None) -> None:
        super().__init__(parent)
        self._proto = proto
        self._abort = False

    def abort(self) -> None:
        self._abort = True

    def run(self) -> None:
        try:
            config = self._do_download()
            self.finished_ok.emit(config)
        except Exception as exc:
            log.exception("Download failed")
            self.finished_err.emit(str(exc))

    def _do_download(self) -> ScannerConfig:
        proto = self._proto
        config = ScannerConfig()

        # Check whether the scanner is in normal scan/monitor mode before we
        # interrupt it.  STS succeeds in normal operation; it fails (or returns
        # ERR) inside program mode, so a successful response here confirms the
        # scanner was scanning/monitoring and should be restored afterward.
        resume_scan = False
        try:
            proto.get_status()
            resume_scan = True
            self.log_line.emit("Scanner is in scan mode — will resume after download.")
        except ProtocolError:
            pass

        self.log_line.emit("Entering program mode…")
        proto.enter_program_mode()

        # SIH = System Index Head — returns the first system's memory index,
        # or -1 if no systems are programmed.  (FreeSCAN frmCommsDownload.vb:2344)
        self.log_line.emit("Querying system list…")
        try:
            sih = proto.send_command("SIH")
        except ProtocolError as e:
            proto.exit_program_mode()
            raise RuntimeError(f"SIH failed: {e}") from e

        try:
            sys_index = int(sih)
        except ValueError:
            sys_index = -1

        if sys_index == -1:
            proto.exit_program_mode()
            self.log_line.emit("Scanner reports no systems programmed.")
            return config

        self.log_line.emit(f"First system index: {sys_index}")
        sys_count = 0
        ch_count = 0

        # The linked list ends when the next-system field == -1.
        # next-system field is at position 13 (1-indexed) = index 12 (0-indexed)
        # for BCT15-X / BCD996XT.  (FreeSCAN: intPos=13, ParaParse 1-indexed)
        while sys_index != -1 and not self._abort:
            self.status.emit(f"Downloading system {sys_count + 1}…")
            try:
                sin = proto.send_command(f"SIN,{sys_index}")
            except ProtocolError as e:
                self.log_line.emit(f"  SIN error at index {sys_index}: {e}")
                break

            if not sin or sin == "ERR":
                break

            sys_type_str = _para(sin, 0)
            sys_name = _para(sin, 1)
            sys_qk = _para(sin, 2)
            sys_hold = _para(sin, 3)
            sys_lockout = _para(sin, 4)
            sys_delay = _para(sin, 5)
            sys_data_skip = _para(sin, 6)
            # field 13 (0-indexed) = first group index for conventional
            first_grp_index_field = _para(sin, 13)

            self.log_line.emit(f"\n[System] {sys_name} ({sys_type_str})")

            sys_obj = System()
            sys_obj.name = sys_name
            sys_obj.system_type = sin_type_to_internal(sys_type_str)
            sys_obj.quick_key = sys_qk or "."
            sys_obj.hold_time = sys_hold
            sys_obj.lockout = sys_lockout == "1"
            sys_obj.delay_time = sys_delay
            sys_obj.data_skip = sys_data_skip == "1"
            sys_obj.group_id = uuid.uuid4().hex[:16].upper()
            sys_obj.apco_mode = _para(sin, 9) or "AUTO"
            sys_obj.apco_threshold = _para(sin, 10) or "8"
            sys_obj.record_mode = _para(sin, 17) or "0"

            if sys_obj.is_conventional:
                try:
                    first_grp_index = int(first_grp_index_field)
                except ValueError:
                    first_grp_index = 0

                grp_index = first_grp_index
                while grp_index not in (-1, 0) and not self._abort:
                    try:
                        gin = proto.send_command(f"GIN,{grp_index}")
                    except ProtocolError as e:
                        self.log_line.emit(f"  GIN error: {e}")
                        break
                    if not gin or gin == "ERR":
                        break

                    grp_name = _para(gin, 1)
                    grp_qk = _para(gin, 2)
                    grp_lockout = _para(gin, 3)
                    first_chan_index_str = _para(gin, 7)
                    next_grp_index_str = _para(gin, 5)

                    self.log_line.emit(f"  [Group] {grp_name}")
                    grp_obj = Group()
                    grp_obj.name = grp_name
                    grp_obj.quick_key = grp_qk or "."
                    grp_obj.lockout = grp_lockout == "1"
                    grp_obj.group_id = uuid.uuid4().hex[:16].upper()

                    try:
                        chan_index = int(first_chan_index_str)
                    except ValueError:
                        chan_index = 0

                    while chan_index not in (-1, 0) and not self._abort:
                        try:
                            cin = proto.send_command(f"CIN,{chan_index}")
                        except ProtocolError as e:
                            self.log_line.emit(f"    CIN error: {e}")
                            break
                        if not cin or cin == "ERR":
                            break

                        ch_name = _para(cin, 0)
                        ch_freq_raw = _para(cin, 1)
                        ch_mod_str = _para(cin, 2)
                        ch_tone = _para(cin, 3)
                        ch_tone_lock = _para(cin, 4)
                        ch_lockout = _para(cin, 5)
                        ch_priority = _para(cin, 6)
                        ch_att = _para(cin, 7)
                        ch_alert = _para(cin, 8)
                        ch_alert_lvl = _para(cin, 9)
                        next_chan_index_str = _para(cin, 11)   # FWD_INDEX
                        ch_record = _para(cin, 14)             # RECORD (after 4 index fields)

                        try:
                            freq_mhz = float(ch_freq_raw) / 10000.0
                            freq_str = f"{freq_mhz:.4f}"
                        except ValueError:
                            freq_str = ch_freq_raw

                        ch_obj = Channel()
                        ch_obj.name = ch_name
                        ch_obj.frequency = freq_str
                        ch_obj.modulation = ch_mod_str or "AUTO"
                        ch_obj.tone = ch_tone
                        ch_obj.tone_lockout = ch_tone_lock == "1"
                        ch_obj.lockout = ch_lockout == "1"
                        ch_obj.priority = ch_priority == "1"
                        ch_obj.attenuator = ch_att == "1"
                        ch_obj.alert_tone = ch_alert
                        ch_obj.alert_level = ch_alert_lvl
                        ch_obj.output = "ON" if ch_record == "1" else "OFF"
                        ch_obj.group_id = grp_obj.group_id
                        grp_obj.channels.append(ch_obj)

                        self.log_line.emit(f"    {ch_name}  {freq_str} MHz")
                        ch_count += 1

                        try:
                            chan_index = int(next_chan_index_str)
                        except ValueError:
                            chan_index = -1

                    sys_obj.groups.append(grp_obj)
                    try:
                        grp_index = int(next_grp_index_str)
                    except ValueError:
                        grp_index = -1

            config.systems.append(sys_obj)
            sys_count += 1
            self.progress.emit(min(90, sys_count * 10))

            # Advance to next system — field index 12 (0-based) = position 13 (1-based)
            next_sys_str = _para(sin, 12)
            try:
                sys_index = int(next_sys_str)
            except ValueError:
                sys_index = -1

        try:
            proto.exit_program_mode()
            self.log_line.emit("\nExited program mode.")
        except ProtocolError as e:
            self.log_line.emit(f"Warning: EPG error: {e}")

        if resume_scan:
            time.sleep(1.5)
            try:
                proto.send_key("S")
                self.log_line.emit("Scan mode restored.")
            except ProtocolError as e:
                self.log_line.emit(f"Warning: could not resume scan: {e}")

        self.progress.emit(100)
        self.log_line.emit(
            f"\nDownload complete: {sys_count} system(s), {ch_count} channel(s)."
        )
        return config


class DownloadDialog(QDialog):
    """Dialog for downloading channel data from the scanner."""

    downloaded_config: ScannerConfig | None = None

    def __init__(self, proto: ScannerProtocol, parent=None) -> None:
        super().__init__(parent)
        self._proto = proto
        self._worker: _DownloadWorker | None = None
        self.downloaded_config = None

        self.setWindowTitle("Download from Scanner")
        self.setMinimumSize(520, 420)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        info = QLabel(
            "This will download all systems, groups, and channels from the scanner.\n"
            "Any unsaved changes in the editor will not be overwritten until you "
            "accept the downloaded configuration."
        )
        info.setWordWrap(True)
        info.setStyleSheet("font-size: 11px; color: #444;")
        layout.addWidget(info)

        self._status_label = QLabel("Ready.")
        layout.addWidget(self._status_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        layout.addWidget(self._progress)

        log_group = QGroupBox("Download Log")
        log_layout = QVBoxLayout(log_group)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFontFamily("Courier")
        log_layout.addWidget(self._log)
        layout.addWidget(log_group)

        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("Start Download")
        self._start_btn.clicked.connect(self._start_download)
        self._abort_btn = QPushButton("Abort")
        self._abort_btn.setEnabled(False)
        self._abort_btn.clicked.connect(self._abort)
        self._close_btn = QPushButton("Cancel")
        self._close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._start_btn)
        btn_row.addWidget(self._abort_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

    def _start_download(self) -> None:
        self._start_btn.setEnabled(False)
        self._abort_btn.setEnabled(True)
        self._close_btn.setEnabled(False)
        self._log.clear()

        worker = _DownloadWorker(self._proto, parent=self)
        worker.progress.connect(self._progress.setValue)
        worker.log_line.connect(self._log.append)
        worker.status.connect(self._status_label.setText)
        worker.finished_ok.connect(self._on_done)
        worker.finished_err.connect(self._on_error)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _abort(self) -> None:
        if self._worker:
            self._worker.abort()
        self._abort_btn.setEnabled(False)

    def _on_done(self, config: ScannerConfig) -> None:
        self.downloaded_config = config
        self._status_label.setText(
            f"Downloaded {len(config.systems)} system(s). "
            "Click 'Load into Editor' to use this data."
        )
        self._start_btn.setEnabled(True)
        self._abort_btn.setEnabled(False)
        self._close_btn.setText("Load into Editor")
        self._close_btn.setEnabled(True)
        self._close_btn.clicked.disconnect()
        self._close_btn.clicked.connect(self.accept)

    def _on_error(self, msg: str) -> None:
        self._status_label.setText("Download failed.")
        self._log.append(f"\nERROR: {msg}")
        self._start_btn.setEnabled(True)
        self._abort_btn.setEnabled(False)
        self._close_btn.setEnabled(True)

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.abort()
            self._worker.wait(3000)
        super().closeEvent(event)
