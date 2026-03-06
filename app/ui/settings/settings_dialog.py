"""
Connection settings dialog — COM port picker and baud rate display.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
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


class ConnectionSettingsDialog(QDialog):
    """
    Lets the user choose a serial port and see connection parameters.
    Emits accepted() with self.selected_port set when the user clicks Connect.
    """

    def __init__(self, current_port: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Scanner Connection")
        self.setMinimumWidth(400)
        self.selected_port: str | None = None

        self._build_ui(current_port)

    def _build_ui(self, current_port: str | None) -> None:
        layout = QVBoxLayout(self)

        # Port selection group
        group = QGroupBox("Serial Port")
        form = QFormLayout(group)

        self._port_combo = QComboBox()
        self._port_combo.setMinimumWidth(250)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setFixedWidth(70)
        refresh_btn.clicked.connect(self._refresh_ports)

        port_row = QHBoxLayout()
        port_row.addWidget(self._port_combo, stretch=1)
        port_row.addWidget(refresh_btn)
        form.addRow("Port:", port_row)

        baud_label = QLabel("115200 baud  •  8N1  •  No flow control")
        baud_label.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow("Settings:", baud_label)

        layout.addWidget(group)

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

        self._refresh_ports(select=current_port)

    def _refresh_ports(self, checked=False, select: str | None = None) -> None:
        self._port_combo.clear()
        ports = port_manager.list_ports()
        if not ports:
            self._port_combo.addItem("No ports found")
            return
        for p in ports:
            label = f"{p.device}  —  {p.description or 'Unknown device'}"
            if port_manager.is_likely_scanner(p):
                label += "  ★"
            self._port_combo.addItem(label, userData=p.device)
        # Restore previous selection if possible
        if select:
            for i in range(self._port_combo.count()):
                if self._port_combo.itemData(i) == select:
                    self._port_combo.setCurrentIndex(i)
                    break

    def _on_accept(self) -> None:
        idx = self._port_combo.currentIndex()
        port = self._port_combo.itemData(idx)
        if port:
            self.selected_port = port
            self.accept()
