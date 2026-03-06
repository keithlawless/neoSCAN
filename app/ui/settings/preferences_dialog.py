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
)

from app.serial.port_manager import list_ports


_ORG = "NeoSCAN"
_APP = "NeoSCAN"


def load_prefs() -> QSettings:
    return QSettings(_ORG, _APP)


class PreferencesDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(440)
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

    def _save_and_accept(self) -> None:
        self._settings.setValue("serial/default_port", self._port_combo.currentText())
        self._settings.setValue("serial/auto_connect", self._auto_connect.isChecked())
        self._settings.setValue("log/save_dir", self._log_path_edit.text())
        self._settings.setValue("appearance/theme", self._theme_combo.currentText())
        self._settings.sync()
        self.accept()
