"""
Connection settings dialog — COM port picker, baud rate display, and audio/transcription options.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
)

from app.serial import port_manager
from app.ui.settings.preferences_dialog import load_prefs

try:
    import sounddevice as sd
    _SD_AVAILABLE = True
except Exception:
    _SD_AVAILABLE = False


class ConnectionSettingsDialog(QDialog):
    """
    Lets the user choose a serial port, whether to transcribe audio, and the
    audio input device (enabled only when transcription is on).

    Pass excluded_ports to mark already-connected ports in the list.
    """

    def __init__(
        self,
        excluded_ports: set[str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._excluded_ports: set[str] = excluded_ports or set()
        self.setWindowTitle("Scanner Connection")
        self.setMinimumWidth(420)
        self.selected_port: str | None = None
        self.selected_audio_device_index: int | None = None
        self.selected_transcription_enabled: bool = False

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Banner shown when scanners are already connected
        if self._excluded_ports:
            banner = QLabel(
                f"Already connected: {', '.join(sorted(self._excluded_ports))}\n"
                "Select a different port to connect an additional scanner."
            )
            banner.setWordWrap(True)
            banner.setStyleSheet(
                "background: #e8f4e8; border: 1px solid #aaa; "
                "border-radius: 4px; padding: 6px; font-size: 11px;"
            )
            layout.addWidget(banner)

        # --- Serial port ---
        port_group = QGroupBox("Serial Port")
        port_form = QFormLayout(port_group)

        self._port_combo = QComboBox()
        self._port_combo.setMinimumWidth(250)
        self._port_combo.currentIndexChanged.connect(self._on_port_changed)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setFixedWidth(70)
        refresh_btn.clicked.connect(self._refresh_ports)

        port_row = QHBoxLayout()
        port_row.addWidget(self._port_combo, stretch=1)
        port_row.addWidget(refresh_btn)
        port_form.addRow("Port:", port_row)

        baud_label = QLabel("115200 baud  •  8N1  •  No flow control")
        baud_label.setStyleSheet("color: gray; font-size: 11px;")
        port_form.addRow("Settings:", baud_label)

        layout.addWidget(port_group)

        # --- Audio / transcription ---
        self._audio_group = QGroupBox("Audio & Transcription")
        audio_form = QFormLayout(self._audio_group)

        self._transcribe_check = QCheckBox("Transcribe audio for this radio")
        self._transcribe_check.stateChanged.connect(self._on_transcribe_changed)
        audio_form.addRow("", self._transcribe_check)

        self._audio_device_label = QLabel("Input device:")
        self._audio_combo = QComboBox()
        self._audio_combo.setMinimumWidth(250)
        audio_refresh_btn = QPushButton("Refresh")
        audio_refresh_btn.setFixedWidth(70)
        audio_refresh_btn.clicked.connect(self._refresh_audio_devices)
        audio_device_row = QHBoxLayout()
        audio_device_row.addWidget(self._audio_combo, stretch=1)
        audio_device_row.addWidget(audio_refresh_btn)
        audio_form.addRow(self._audio_device_label, audio_device_row)

        # If transcription is globally disabled in preferences, hide the
        # per-radio transcription controls — they would be no-ops.
        global_tx_enabled = load_prefs().value("transcription/enabled", True, type=bool)
        if not global_tx_enabled:
            self._transcribe_check.setChecked(False)
            self._transcribe_check.setVisible(False)
            self._audio_device_label.setVisible(False)
            self._audio_combo.setVisible(False)
            audio_refresh_btn.setVisible(False)
            disabled_note = QLabel(
                "Transcription is disabled in Preferences → Transcription."
            )
            disabled_note.setStyleSheet("color: gray; font-size: 11px;")
            audio_form.addRow("", disabled_note)

        if _SD_AVAILABLE:
            self._populate_audio_devices()
        else:
            self._audio_group.setVisible(False)

        layout.addWidget(self._audio_group)

        # Help text
        help_label = QLabel(
            "Select the serial port your scanner is connected to. "
            "On macOS this usually appears as /dev/cu.usbserial-XXXX. "
            "On Windows it will be COMx."
        )
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(help_label)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Connect")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_ports()
        # Set initial enabled state (checkbox starts unchecked)
        self._on_transcribe_changed(0)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _populate_audio_devices(self) -> None:
        self._audio_combo.clear()
        self._audio_combo.addItem("(none)", userData=None)
        try:
            devices = sd.query_devices()
            for i, dev in enumerate(devices):
                if dev.get("max_input_channels", 0) > 0:
                    label = f"{dev['name']}  [{dev.get('hostapi', '')}]"
                    self._audio_combo.addItem(label, userData=i)
        except Exception:
            pass

    def _refresh_audio_devices(self) -> None:
        """Force PortAudio to re-scan devices, then repopulate the combo."""
        if not _SD_AVAILABLE:
            return
        try:
            # sounddevice wraps PortAudio; terminate+reinitialize forces a device rescan
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
        self._populate_audio_devices()
        # Re-apply saved selection for the current port after refresh
        self._on_port_changed()

    def _on_transcribe_changed(self, state: int) -> None:
        enabled = bool(state)
        self._audio_device_label.setEnabled(enabled)
        self._audio_combo.setEnabled(enabled)

    def _on_port_changed(self) -> None:
        """Pre-select saved transcription settings for the newly selected port."""
        port = self._port_combo.currentData()
        if not port:
            return
        settings = load_prefs()

        # Restore transcription enabled
        tx_enabled = settings.value(f"serial/{port}/transcription_enabled", False, type=bool)
        self._transcribe_check.setChecked(tx_enabled)

        if not _SD_AVAILABLE:
            return

        # Restore audio device
        saved = settings.value(f"serial/{port}/audio_device_index", None)
        if saved is None:
            return
        try:
            saved = int(saved)
        except (ValueError, TypeError):
            return
        for i in range(self._audio_combo.count()):
            if self._audio_combo.itemData(i) == saved:
                self._audio_combo.setCurrentIndex(i)
                break

    def _refresh_ports(self, checked=False) -> None:
        self._port_combo.clear()
        ports = port_manager.list_ports()
        if not ports:
            self._port_combo.addItem("No ports found")
            return
        first_free = -1
        for p in ports:
            label = f"{p.device}  —  {p.description or 'Unknown device'}"
            if port_manager.is_likely_scanner(p):
                label += "  ★"
            if p.device in self._excluded_ports:
                label += "  (connected)"
            self._port_combo.addItem(label, userData=p.device)
            if first_free < 0 and p.device not in self._excluded_ports:
                first_free = self._port_combo.count() - 1
        # Pre-select the first port that isn't already in use
        if first_free >= 0:
            self._port_combo.setCurrentIndex(first_free)

    def _on_accept(self) -> None:
        idx = self._port_combo.currentIndex()
        port = self._port_combo.itemData(idx)
        if not port:
            return

        self.selected_port = port
        self.selected_transcription_enabled = self._transcribe_check.isChecked()

        settings = load_prefs()
        settings.setValue(f"serial/{port}/transcription_enabled", self.selected_transcription_enabled)

        if _SD_AVAILABLE and self.selected_transcription_enabled:
            self.selected_audio_device_index = self._audio_combo.currentData()
            if self.selected_audio_device_index is not None:
                settings.setValue(
                    f"serial/{port}/audio_device_index",
                    self.selected_audio_device_index,
                )
            else:
                settings.remove(f"serial/{port}/audio_device_index")
        else:
            self.selected_audio_device_index = None

        self.accept()
