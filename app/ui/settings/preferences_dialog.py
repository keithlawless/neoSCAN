"""
Application preferences dialog (persistent via QSettings).
"""
from __future__ import annotations

from pathlib import Path

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
    QTabWidget,
    QVBoxLayout,
    QFileDialog,
    QHBoxLayout,
    QScrollArea,
    QWidget,
)

from app.audio.languages import DEFAULT_LANGUAGE, WHISPER_LANGUAGES
from app.audio.summary_generator import (
    AVAILABLE_MODELS,
    DEFAULT_MODEL as DEFAULT_SUMMARY_MODEL,
    DEFAULT_REPORT_DIR,
)
from app.serial.port_manager import list_ports


_ORG = "NeoSCAN"
_APP = "NeoSCAN"


def _whisper_installed() -> bool:
    """Probe-import openai-whisper to decide whether to enable its UI."""
    try:
        import whisper  # noqa: F401
        return True
    except ImportError:
        return False


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
    def __init__(self, parent=None, on_recapture_noise_profile=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(480)
        self._settings = load_prefs()
        self._on_recapture_noise_profile = on_recapture_noise_profile
        self._build_ui()
        self._load_values()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(self._build_general_tab(),       "General")
        tabs.addTab(self._build_logging_tab(),       "Logging")
        tabs.addTab(self._build_audio_tab(),         "Audio")
        tabs.addTab(self._build_transcription_tab(), "Transcription")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    def _build_general_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

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

        # --- Appearance ---
        appearance_box = QGroupBox("Appearance")
        appearance_form = QFormLayout(appearance_box)

        self._theme_combo = QComboBox()
        self._theme_combo.addItems(["System default", "Light", "Dark"])
        appearance_form.addRow("Theme:", self._theme_combo)

        layout.addWidget(appearance_box)
        layout.addStretch()
        return page

    def _build_logging_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

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
        layout.addStretch()
        return page

    def _build_audio_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        pt_box = QGroupBox("Audio Passthrough")
        pt_form = QFormLayout(pt_box)

        self._pt_enable = QCheckBox("Pass-through to speakers")
        self._pt_enable.stateChanged.connect(self._on_pt_enable_changed)
        pt_form.addRow("", self._pt_enable)

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
        pt_form.addRow(self._pt_device_label, pt_device_row)
        self._refresh_output_devices()

        self._pt_recapture_btn = QPushButton("Re-capture noise profile")
        self._pt_recapture_btn.setToolTip(
            "Discard the current noise profile and capture a new one.\n"
            "Trigger while the scanner is in squelch for best results."
        )
        self._pt_recapture_btn.clicked.connect(self._on_recapture_clicked)
        self._pt_recapture_label = QLabel("")
        pt_form.addRow(self._pt_recapture_label, self._pt_recapture_btn)

        layout.addWidget(pt_box)
        layout.addStretch()
        return page

    def _build_transcription_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        whisper_present = _whisper_installed()

        # Top: global enable + (optional) install warning
        self._tx_global_enable = QCheckBox(
            "Enable transcription (use Whisper to convert scanner audio to text)"
        )
        self._tx_global_enable.stateChanged.connect(self._on_global_enable_changed)
        layout.addWidget(self._tx_global_enable)

        if not whisper_present:
            warn = QLabel(
                "openai-whisper is not installed. Install it with "
                "<code>pip install -e \".[whisper]\"</code> to enable on-device "
                "transcription. Daily summaries can still be generated from "
                "transcript files produced by another machine."
            )
            warn.setWordWrap(True)
            warn.setStyleSheet(
                "background: #fff3cd; border: 1px solid #d6b656; "
                "border-radius: 4px; padding: 6px; color: #5a4500;"
            )
            layout.addWidget(warn)

        # --- Whisper config ---
        self._tx_whisper_box = QGroupBox("Whisper")
        tx_form = QFormLayout(self._tx_whisper_box)

        self._tx_model_combo = QComboBox()
        self._tx_model_combo.addItems(["tiny", "base", "small", "medium", "large"])
        tx_form.addRow("Whisper model:", self._tx_model_combo)

        self._tx_lang_combo = QComboBox()
        self._tx_lang_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._tx_lang_combo.addItem("Auto-detect", None)
        for name, code in WHISPER_LANGUAGES:
            self._tx_lang_combo.addItem(name, code)
        tx_form.addRow("Language:", self._tx_lang_combo)

        tx_dir_row = QHBoxLayout()
        self._tx_dir_edit = QLineEdit()
        self._tx_dir_edit.setPlaceholderText(
            str(Path.home() / "Documents" / "NeoSCAN" / "Transcripts")
        )
        tx_browse_btn = QPushButton("Browse…")
        tx_browse_btn.setFixedWidth(70)
        tx_browse_btn.clicked.connect(self._browse_transcript_dir)
        tx_dir_row.addWidget(self._tx_dir_edit, 1)
        tx_dir_row.addWidget(tx_browse_btn)
        tx_form.addRow("Transcript directory:", tx_dir_row)

        self._retain_audio_check = QCheckBox("Retain audio recordings")
        self._retain_audio_check.stateChanged.connect(self._on_retain_audio_changed)
        tx_form.addRow("", self._retain_audio_check)

        audio_dir_row = QHBoxLayout()
        self._audio_dir_edit = QLineEdit()
        self._audio_dir_edit.setPlaceholderText(
            str(Path.home() / "Documents" / "NeoSCAN" / "Recordings")
        )
        self._audio_dir_browse_btn = QPushButton("Browse…")
        self._audio_dir_browse_btn.setFixedWidth(70)
        self._audio_dir_browse_btn.clicked.connect(self._browse_audio_dir)
        audio_dir_row.addWidget(self._audio_dir_edit, 1)
        audio_dir_row.addWidget(self._audio_dir_browse_btn)
        self._audio_dir_label = QLabel("Audio save directory:")
        tx_form.addRow(self._audio_dir_label, audio_dir_row)

        layout.addWidget(self._tx_whisper_box)

        # --- Daily Summary (Anthropic) ---
        self._summary_box = QGroupBox("Daily Summary")
        summary_form = QFormLayout(self._summary_box)

        self._summary_enable = QCheckBox(
            "Generate a daily HTML summary with Claude (runs at midnight)"
        )
        self._summary_enable.stateChanged.connect(self._on_summary_enable_changed)
        summary_form.addRow("", self._summary_enable)

        api_row = QHBoxLayout()
        self._anthropic_key_edit = QLineEdit()
        self._anthropic_key_edit.setPlaceholderText("sk-ant-…")
        self._anthropic_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._show_key_btn = QPushButton("Show")
        self._show_key_btn.setCheckable(True)
        self._show_key_btn.setFixedWidth(60)
        self._show_key_btn.toggled.connect(self._on_show_key_toggled)
        api_row.addWidget(self._anthropic_key_edit, 1)
        api_row.addWidget(self._show_key_btn)
        summary_form.addRow("Anthropic API key:", api_row)

        self._anthropic_model_combo = QComboBox()
        for label, model_id in AVAILABLE_MODELS:
            self._anthropic_model_combo.addItem(label, model_id)
        summary_form.addRow("Claude model:", self._anthropic_model_combo)

        report_row = QHBoxLayout()
        self._report_dir_edit = QLineEdit()
        self._report_dir_edit.setPlaceholderText(DEFAULT_REPORT_DIR)
        report_browse_btn = QPushButton("Browse…")
        report_browse_btn.setFixedWidth(70)
        report_browse_btn.clicked.connect(self._browse_report_dir)
        report_row.addWidget(self._report_dir_edit, 1)
        report_row.addWidget(report_browse_btn)
        summary_form.addRow("Report directory:", report_row)

        layout.addWidget(self._summary_box)
        layout.addStretch()

        # If Whisper isn't installed, lock the global toggle off and grey the
        # Whisper subgroup. The summary subgroup remains usable so reports can
        # still be generated from transcript files copied in from elsewhere.
        if not whisper_present:
            self._tx_global_enable.setEnabled(False)
            self._tx_whisper_box.setEnabled(False)

        return page

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

    def _browse_audio_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Select audio save directory", self._audio_dir_edit.text()
        )
        if directory:
            self._audio_dir_edit.setText(directory)

    def _browse_report_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Select report directory",
            self._report_dir_edit.text() or DEFAULT_REPORT_DIR,
        )
        if directory:
            self._report_dir_edit.setText(directory)

    def _on_retain_audio_changed(self, state: int) -> None:
        enabled = bool(state)
        self._audio_dir_label.setEnabled(enabled)
        self._audio_dir_edit.setEnabled(enabled)
        self._audio_dir_browse_btn.setEnabled(enabled)

    def _on_recapture_clicked(self) -> None:
        if self._on_recapture_noise_profile is not None:
            self._on_recapture_noise_profile()

    def _on_pt_enable_changed(self, state: int) -> None:
        pt_on = bool(state)
        for w in (self._pt_device_label, self._pt_device_combo,
                  self._pt_recapture_label, self._pt_recapture_btn):
            w.setEnabled(pt_on)

    def _on_global_enable_changed(self, state: int) -> None:
        # Only governs the Whisper subgroup; the summary section can still be
        # used to generate reports from transcripts produced elsewhere.
        if _whisper_installed():
            self._tx_whisper_box.setEnabled(bool(state))

    def _on_summary_enable_changed(self, state: int) -> None:
        on = bool(state)
        for w in (self._anthropic_key_edit, self._show_key_btn,
                  self._anthropic_model_combo, self._report_dir_edit):
            w.setEnabled(on)

    def _on_show_key_toggled(self, checked: bool) -> None:
        self._anthropic_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        self._show_key_btn.setText("Hide" if checked else "Show")

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

        saved_lang = self._settings.value("transcription/language", DEFAULT_LANGUAGE)
        for i in range(self._tx_lang_combo.count()):
            if self._tx_lang_combo.itemData(i) == saved_lang:
                self._tx_lang_combo.setCurrentIndex(i)
                break

        tx_dir = self._settings.value("transcription/transcript_dir", "")
        self._tx_dir_edit.setText(tx_dir)

        retain = self._settings.value("transcription/retain_audio", False, type=bool)
        self._retain_audio_check.setChecked(retain)
        self._on_retain_audio_changed(int(retain))

        audio_dir = self._settings.value("transcription/audio_save_dir", "")
        self._audio_dir_edit.setText(audio_dir)

        # Global transcription enable (default on, for backwards compat).
        # If Whisper isn't installed, force off regardless of saved value.
        global_enabled = self._settings.value("transcription/enabled", True, type=bool)
        if not _whisper_installed():
            global_enabled = False
        self._tx_global_enable.setChecked(global_enabled)
        self._on_global_enable_changed(int(global_enabled))

        # Daily summary
        summary_enabled = self._settings.value("transcription/summary_enabled", False, type=bool)
        self._summary_enable.setChecked(summary_enabled)
        self._on_summary_enable_changed(int(summary_enabled))

        api_key = self._settings.value("transcription/anthropic_api_key", "")
        self._anthropic_key_edit.setText(api_key)

        saved_model = self._settings.value("transcription/anthropic_model", DEFAULT_SUMMARY_MODEL)
        for i in range(self._anthropic_model_combo.count()):
            if self._anthropic_model_combo.itemData(i) == saved_model:
                self._anthropic_model_combo.setCurrentIndex(i)
                break

        self._report_dir_edit.setText(self._settings.value("transcription/report_dir", ""))

    def _save_and_accept(self) -> None:
        self._settings.setValue("serial/default_port", self._port_combo.currentText())
        self._settings.setValue("serial/auto_connect", self._auto_connect.isChecked())
        self._settings.setValue("log/save_dir", self._log_path_edit.text())
        self._settings.setValue("appearance/theme", self._theme_combo.currentText())

        # Transcription
        self._settings.setValue("transcription/passthrough_enabled",
                                self._pt_enable.isChecked())
        self._settings.setValue("transcription/output_device_index",
                                self._pt_device_combo.currentData())
        self._settings.setValue("transcription/model_size",
                                self._tx_model_combo.currentText())
        self._settings.setValue("transcription/language",
                                self._tx_lang_combo.currentData())
        self._settings.setValue("transcription/transcript_dir", self._tx_dir_edit.text())
        self._settings.setValue("transcription/retain_audio", self._retain_audio_check.isChecked())
        self._settings.setValue("transcription/audio_save_dir", self._audio_dir_edit.text())

        self._settings.setValue("transcription/enabled", self._tx_global_enable.isChecked())
        self._settings.setValue("transcription/summary_enabled",
                                self._summary_enable.isChecked())
        self._settings.setValue("transcription/anthropic_api_key",
                                self._anthropic_key_edit.text().strip())
        self._settings.setValue("transcription/anthropic_model",
                                self._anthropic_model_combo.currentData() or DEFAULT_SUMMARY_MODEL)
        self._settings.setValue("transcription/report_dir",
                                self._report_dir_edit.text().strip())

        self._settings.sync()
        self.accept()
