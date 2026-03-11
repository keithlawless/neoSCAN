"""
Application preferences dialog (persistent via QSettings).
"""
from __future__ import annotations

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QFileDialog,
    QHBoxLayout,
    QScrollArea,
    QWidget,
)

from app.serial.port_manager import list_ports


_ORG = "NeoSCAN"
_APP = "NeoSCAN"


def load_prefs() -> QSettings:
    return QSettings(_ORG, _APP)


def apply_theme(theme: str) -> None:
    """
    Apply the named theme to the running QApplication instance.

    "Dark"  — Fusion style with a dark palette.
    "Light" — Fusion style with the default (light) palette.
    "System default" — no override; uses the platform native style.
                       Only effective on first launch before the window is shown;
                       switching back to it at runtime requires an app restart.
    """
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QPalette, QColor

    app = QApplication.instance()
    if app is None:
        return

    if theme == "Dark":
        app.setStyle("Fusion")
        p = QPalette()
        p.setColor(QPalette.ColorRole.Window,          QColor(53, 53, 53))
        p.setColor(QPalette.ColorRole.WindowText,      QColor(255, 255, 255))
        p.setColor(QPalette.ColorRole.Base,            QColor(35, 35, 35))
        p.setColor(QPalette.ColorRole.AlternateBase,   QColor(53, 53, 53))
        p.setColor(QPalette.ColorRole.ToolTipBase,     QColor(53, 53, 53))
        p.setColor(QPalette.ColorRole.ToolTipText,     QColor(255, 255, 255))
        p.setColor(QPalette.ColorRole.Text,            QColor(255, 255, 255))
        p.setColor(QPalette.ColorRole.Button,          QColor(53, 53, 53))
        p.setColor(QPalette.ColorRole.ButtonText,      QColor(255, 255, 255))
        p.setColor(QPalette.ColorRole.BrightText,      QColor(255, 0, 0))
        p.setColor(QPalette.ColorRole.Link,            QColor(42, 130, 218))
        p.setColor(QPalette.ColorRole.Highlight,       QColor(42, 130, 218))
        p.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,
                   QColor(128, 128, 128))
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText,
                   QColor(128, 128, 128))
        p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText,
                   QColor(128, 128, 128))
        app.setPalette(p)
    elif theme == "Light":
        app.setStyle("Fusion")
        app.setPalette(QPalette())


class PreferencesDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(480)
        self._settings = load_prefs()
        self._build_ui()
        self._load_values()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # --- Serial connection ---
        serial_box = QGroupBox("Serial Connection")
        serial_form = QFormLayout(serial_box)

        self._port_combo = QComboBox()
        self._port_combo.setEditable(True)
        self._refresh_ports()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setFixedWidth(70)
        refresh_btn.clicked.connect(self._refresh_ports)
        port_row = QHBoxLayout()
        port_row.addWidget(self._port_combo, 1)
        port_row.addWidget(refresh_btn)
        serial_form.addRow("Default port:", port_row)

        self._auto_connect = QCheckBox("Connect automatically on launch")
        serial_form.addRow("", self._auto_connect)

        layout.addWidget(serial_box)

        # --- Logging ---
        log_box = QGroupBox("Transmission Log")
        log_form = QFormLayout(log_box)

        log_path_row = QHBoxLayout()
        self._log_path_edit = QLineEdit()
        self._log_path_edit.setPlaceholderText("(leave blank to prompt each time)")
        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(70)
        browse_btn.clicked.connect(self._browse_log_dir)
        log_path_row.addWidget(self._log_path_edit, 1)
        log_path_row.addWidget(browse_btn)
        log_form.addRow("Default save directory:", log_path_row)

        layout.addWidget(log_box)

        # --- Appearance ---
        appearance_box = QGroupBox("Appearance")
        appearance_form = QFormLayout(appearance_box)

        self._theme_combo = QComboBox()
        self._theme_combo.addItems(["System default", "Light", "Dark"])
        appearance_form.addRow("Theme:", self._theme_combo)

        layout.addWidget(appearance_box)

        # --- Transcription ---
        tx_box = QGroupBox("Transcription")
        tx_form = QFormLayout(tx_box)

        self._tx_enable = QCheckBox("Enable audio transcription (requires openai-whisper)")
        self._tx_enable.stateChanged.connect(self._on_tx_enable_changed)
        tx_form.addRow("", self._tx_enable)

        # Audio input device
        device_row = QHBoxLayout()
        self._tx_device_combo = QComboBox()
        self._tx_device_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._tx_device_combo.setMinimumWidth(200)
        tx_refresh_btn = QPushButton("Refresh")
        tx_refresh_btn.setFixedWidth(70)
        tx_refresh_btn.clicked.connect(self._refresh_audio_devices)
        device_row.addWidget(self._tx_device_combo, 1)
        device_row.addWidget(tx_refresh_btn)
        self._tx_device_label = QLabel("Audio input device:")
        tx_form.addRow(self._tx_device_label, device_row)
        self._refresh_audio_devices()

        # Pass-through
        self._pt_enable = QCheckBox("Pass-through to speakers")
        self._pt_enable.stateChanged.connect(self._on_pt_enable_changed)
        self._pt_enable_label = QLabel("Audio pass-through:")
        tx_form.addRow(self._pt_enable_label, self._pt_enable)

        pt_device_row = QHBoxLayout()
        self._pt_device_combo = QComboBox()
        self._pt_device_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._pt_device_combo.setMinimumWidth(200)
        pt_refresh_btn = QPushButton("Refresh")
        pt_refresh_btn.setFixedWidth(70)
        pt_refresh_btn.clicked.connect(self._refresh_output_devices)
        pt_device_row.addWidget(self._pt_device_combo, 1)
        pt_device_row.addWidget(pt_refresh_btn)
        self._pt_device_label = QLabel("Output device:")
        tx_form.addRow(self._pt_device_label, pt_device_row)
        self._refresh_output_devices()

        # Whisper model size
        self._tx_model_combo = QComboBox()
        self._tx_model_combo.addItems(["tiny", "base", "small", "medium", "large"])
        self._tx_model_label = QLabel("Whisper model:")
        tx_form.addRow(self._tx_model_label, self._tx_model_combo)

        # Transcript directory
        tx_dir_row = QHBoxLayout()
        self._tx_dir_edit = QLineEdit()
        self._tx_dir_edit.setPlaceholderText(
            str(__import__("pathlib").Path.home() / "Documents" / "NeoSCAN" / "Transcripts")
        )
        tx_browse_btn = QPushButton("Browse…")
        tx_browse_btn.setFixedWidth(70)
        tx_browse_btn.clicked.connect(self._browse_transcript_dir)
        tx_dir_row.addWidget(self._tx_dir_edit, 1)
        tx_dir_row.addWidget(tx_browse_btn)
        self._tx_dir_label = QLabel("Transcript directory:")
        tx_form.addRow(self._tx_dir_label, tx_dir_row)

        layout.addWidget(tx_box)

        # --- Buttons ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _refresh_ports(self) -> None:
        current = self._port_combo.currentText()
        self._port_combo.clear()
        self._port_combo.addItem("")  # blank = no default
        for p in list_ports():
            self._port_combo.addItem(p.device)
        if current:
            idx = self._port_combo.findText(current)
            if idx >= 0:
                self._port_combo.setCurrentIndex(idx)

    def _browse_log_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Select log directory", self._log_path_edit.text()
        )
        if directory:
            self._log_path_edit.setText(directory)

    def _refresh_audio_devices(self) -> None:
        """Populate the audio input device combo from sounddevice."""
        current_data = self._tx_device_combo.currentData()
        self._tx_device_combo.clear()
        self._tx_device_combo.addItem("(none)", None)
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            for i, dev in enumerate(devices):
                if dev["max_input_channels"] > 0:
                    self._tx_device_combo.addItem(f"{i}: {dev['name']}", i)
        except Exception:
            self._tx_device_combo.addItem("(sounddevice not available)", None)
        # Restore previous selection
        for idx in range(self._tx_device_combo.count()):
            if self._tx_device_combo.itemData(idx) == current_data:
                self._tx_device_combo.setCurrentIndex(idx)
                break

    def _refresh_output_devices(self) -> None:
        """Populate the audio output device combo from sounddevice."""
        current_data = self._pt_device_combo.currentData()
        self._pt_device_combo.clear()
        self._pt_device_combo.addItem("(none)", None)
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            for i, dev in enumerate(devices):
                if dev["max_output_channels"] > 0:
                    self._pt_device_combo.addItem(f"{i}: {dev['name']}", i)
        except Exception:
            self._pt_device_combo.addItem("(sounddevice not available)", None)
        for idx in range(self._pt_device_combo.count()):
            if self._pt_device_combo.itemData(idx) == current_data:
                self._pt_device_combo.setCurrentIndex(idx)
                break

    def _browse_transcript_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Select transcript directory", self._tx_dir_edit.text()
        )
        if directory:
            self._tx_dir_edit.setText(directory)

    def _on_pt_enable_changed(self, state: int) -> None:
        pt_on = bool(state) and self._tx_enable.isChecked()
        self._pt_device_label.setEnabled(pt_on)
        self._pt_device_combo.setEnabled(pt_on)

    def _on_tx_enable_changed(self, state: int) -> None:
        enabled = bool(state)
        for w in (
            self._tx_device_label, self._tx_device_combo,
            self._pt_enable_label, self._pt_enable,
            self._tx_model_label, self._tx_model_combo,
            self._tx_dir_label, self._tx_dir_edit,
        ):
            w.setEnabled(enabled)
        self._on_pt_enable_changed(self._pt_enable.isChecked())

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def _load_values(self) -> None:
        port = self._settings.value("serial/default_port", "")
        idx = self._port_combo.findText(port)
        if idx >= 0:
            self._port_combo.setCurrentIndex(idx)
        else:
            self._port_combo.setCurrentText(port)

        auto = self._settings.value("serial/auto_connect", False, type=bool)
        self._auto_connect.setChecked(auto)

        log_path = self._settings.value("log/save_dir", "")
        self._log_path_edit.setText(log_path)

        theme = self._settings.value("appearance/theme", "System default")
        idx = self._theme_combo.findText(theme)
        if idx >= 0:
            self._theme_combo.setCurrentIndex(idx)

        # Transcription
        tx_enabled = self._settings.value("transcription/enabled", False, type=bool)
        self._tx_enable.setChecked(tx_enabled)
        # _on_tx_enable_changed fires from setChecked, but call explicitly in case state=0 (unchecked)
        self._on_tx_enable_changed(tx_enabled)

        saved_device = self._settings.value("transcription/device_index", None)
        if saved_device is not None:
            try:
                saved_device = int(saved_device)
            except (ValueError, TypeError):
                saved_device = None
        for i in range(self._tx_device_combo.count()):
            if self._tx_device_combo.itemData(i) == saved_device:
                self._tx_device_combo.setCurrentIndex(i)
                break

        pt_enabled = self._settings.value("transcription/passthrough_enabled", False, type=bool)
        self._pt_enable.setChecked(pt_enabled)

        saved_out_device = self._settings.value("transcription/output_device_index", None)
        if saved_out_device is not None:
            try:
                saved_out_device = int(saved_out_device)
            except (ValueError, TypeError):
                saved_out_device = None
        for i in range(self._pt_device_combo.count()):
            if self._pt_device_combo.itemData(i) == saved_out_device:
                self._pt_device_combo.setCurrentIndex(i)
                break

        model_size = self._settings.value("transcription/model_size", "base")
        idx = self._tx_model_combo.findText(model_size)
        if idx >= 0:
            self._tx_model_combo.setCurrentIndex(idx)

        tx_dir = self._settings.value("transcription/transcript_dir", "")
        self._tx_dir_edit.setText(tx_dir)

    def _save_and_accept(self) -> None:
        self._settings.setValue("serial/default_port", self._port_combo.currentText())
        self._settings.setValue("serial/auto_connect", self._auto_connect.isChecked())
        self._settings.setValue("log/save_dir", self._log_path_edit.text())
        self._settings.setValue("appearance/theme", self._theme_combo.currentText())

        # Transcription
        self._settings.setValue("transcription/enabled", self._tx_enable.isChecked())
        self._settings.setValue("transcription/device_index",
                                self._tx_device_combo.currentData())
        self._settings.setValue("transcription/passthrough_enabled",
                                self._pt_enable.isChecked())
        self._settings.setValue("transcription/output_device_index",
                                self._pt_device_combo.currentData())
        self._settings.setValue("transcription/model_size",
                                self._tx_model_combo.currentText())
        self._settings.setValue("transcription/transcript_dir", self._tx_dir_edit.text())

        self._settings.sync()
        self.accept()
