"""
Main application window.
"""
from __future__ import annotations

import logging
from pathlib import Path

import serial
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.serial import port_manager
from app.serial.protocol import ScannerProtocol, ProtocolError
from app.ui.settings.settings_dialog import ConnectionSettingsDialog
from app.ui.settings.preferences_dialog import PreferencesDialog, load_prefs, apply_theme
from app.ui.editor.systems_panel import SystemsPanel
from app.ui.editor.channel_editor import ChannelEditorPanel
from app.ui.editor.csv_import_dialog import CSVImportDialog
from app.ui.remote_control.control_panel import ControlPanel
from app.ui.remote_control.log_panel import LogPanel
from app.audio.transcriber import TranscriptionManager
from app.data import file_996
from app.data.models import ScannerConfig

log = logging.getLogger(__name__)

APP_NAME = "NeoSCAN"


class _ConnectWorker(QThread):
    success = pyqtSignal(str, str)
    failure = pyqtSignal(str)

    def __init__(self, port_name: str, parent=None) -> None:
        super().__init__(parent)
        self._port_name = port_name
        self.conn: serial.Serial | None = None

    def run(self) -> None:
        try:
            conn = port_manager.open_port(self._port_name)
            proto = ScannerProtocol(conn)
            model = proto.get_model()
            version = proto.get_firmware_version()
            self.conn = conn
            self.success.emit(model, version)
        except (ProtocolError, serial.SerialException, OSError) as exc:
            self.failure.emit(str(exc))


class MainWindow(QMainWindow):

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 780)

        self._conn: serial.Serial | None = None
        self._proto: ScannerProtocol | None = None
        self._current_port: str | None = None
        self._connect_worker: _ConnectWorker | None = None
        self._config: ScannerConfig | None = None
        self._transcription_manager = TranscriptionManager(parent=self)

        self._build_menu()
        self._build_central()
        self._build_status_bar()
        self._update_connection_ui()
        self._update_title()
        self._transcription_manager.apply_settings()

        # Auto-connect if the user has enabled it in preferences.
        settings = load_prefs()
        if settings.value("serial/auto_connect", False, type=bool):
            port = settings.value("serial/default_port", "")
            if port:
                QTimer.singleShot(500, lambda: self._start_connect(port))

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menubar = self.menuBar()

        # File
        file_menu = menubar.addMenu("&File")

        new_action = QAction("&New…", self)
        new_action.setShortcut(QKeySequence.StandardKey.New)
        new_action.setStatusTip("Create a new empty channel list")
        new_action.triggered.connect(self._on_file_new)
        file_menu.addAction(new_action)

        open_action = QAction("&Open…", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.setStatusTip("Open a .996 channel file")
        open_action.triggered.connect(self._on_file_open)
        file_menu.addAction(open_action)

        self._save_action = QAction("&Save", self)
        self._save_action.setShortcut(QKeySequence.StandardKey.Save)
        self._save_action.triggered.connect(self._on_file_save)
        self._save_action.setEnabled(False)
        file_menu.addAction(self._save_action)

        self._save_as_action = QAction("Save &As…", self)
        self._save_as_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self._save_as_action.triggered.connect(self._on_file_save_as)
        self._save_as_action.setEnabled(False)
        file_menu.addAction(self._save_as_action)

        file_menu.addSeparator()

        self._import_csv_action = QAction("Import &CSV…", self)
        self._import_csv_action.setStatusTip("Import channels from a CSV file")
        self._import_csv_action.triggered.connect(self._on_import_csv)
        self._import_csv_action.setEnabled(False)
        file_menu.addAction(self._import_csv_action)

        file_menu.addSeparator()

        prefs_action = QAction("&Preferences…", self)
        prefs_action.setShortcut(QKeySequence("Ctrl+,"))
        prefs_action.triggered.connect(self._on_preferences)
        file_menu.addAction(prefs_action)

        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # Scanner
        scanner_menu = menubar.addMenu("&Scanner")

        self._connect_action = QAction("&Connect…", self)
        self._connect_action.setStatusTip("Connect to scanner via USB/serial")
        self._connect_action.triggered.connect(self._on_connect)
        scanner_menu.addAction(self._connect_action)

        self._disconnect_action = QAction("&Disconnect", self)
        self._disconnect_action.triggered.connect(self._on_disconnect)
        scanner_menu.addAction(self._disconnect_action)

        scanner_menu.addSeparator()

        self._upload_action = QAction("&Upload to Scanner…", self)
        self._upload_action.triggered.connect(self._on_upload)
        self._upload_action.setEnabled(False)
        scanner_menu.addAction(self._upload_action)

        self._download_action = QAction("&Download from Scanner…", self)
        self._download_action.triggered.connect(self._on_download)
        self._download_action.setEnabled(False)
        scanner_menu.addAction(self._download_action)

        # Help
        help_menu = menubar.addMenu("&Help")
        about_action = QAction("&About NeoSCAN", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    # ------------------------------------------------------------------
    # Central widget
    # ------------------------------------------------------------------

    def _build_central(self) -> None:
        self._tabs = QTabWidget()

        # --- Editor tab ---
        editor_widget = QWidget()
        editor_layout = QVBoxLayout(editor_widget)
        editor_layout.setContentsMargins(0, 0, 0, 0)

        self._editor_splitter = QSplitter(Qt.Orientation.Horizontal)

        self._systems_panel = SystemsPanel()
        self._systems_panel.setMinimumWidth(260)
        self._systems_panel.setMaximumWidth(500)
        self._systems_panel.system_selected.connect(self._on_system_selected)
        self._systems_panel.group_selected.connect(self._on_group_selected)
        self._systems_panel.channel_selected.connect(self._on_channel_selected)

        self._channel_editor = ChannelEditorPanel()
        self._channel_editor.modified.connect(self._on_editor_modified)

        self._editor_splitter.addWidget(self._systems_panel)
        self._editor_splitter.addWidget(self._channel_editor)
        self._editor_splitter.setStretchFactor(0, 1)
        self._editor_splitter.setStretchFactor(1, 2)

        editor_layout.addWidget(self._editor_splitter)
        self._tabs.addTab(editor_widget, "Channel Editor")

        # --- Remote Control tab ---
        rc_widget = QWidget()
        rc_layout = QVBoxLayout(rc_widget)
        rc_layout.setContentsMargins(0, 0, 0, 0)

        rc_splitter = QSplitter(Qt.Orientation.Horizontal)

        self._control_panel = ControlPanel()
        self._control_panel.setMaximumWidth(260)

        self._log_panel = LogPanel()
        self._log_panel.channel_info_updated.connect(self._control_panel.update_display)
        self._log_panel.set_transcription_manager(self._transcription_manager)

        rc_splitter.addWidget(self._control_panel)
        rc_splitter.addWidget(self._log_panel)
        rc_splitter.setStretchFactor(0, 0)
        rc_splitter.setStretchFactor(1, 1)

        rc_layout.addWidget(rc_splitter)
        self._tabs.addTab(rc_widget, "Remote Control")

        self.setCentralWidget(self._tabs)

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------

    def _build_status_bar(self) -> None:
        bar = QStatusBar()
        self.setStatusBar(bar)
        self._status_conn_label = QLabel()
        self._status_model_label = QLabel()
        self._status_file_label = QLabel()
        bar.addPermanentWidget(self._status_conn_label)
        bar.addPermanentWidget(QLabel("  |  "))
        bar.addPermanentWidget(self._status_model_label)
        bar.addPermanentWidget(QLabel("  |  "))
        bar.addPermanentWidget(self._status_file_label)

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _update_connection_ui(self) -> None:
        connected = self._conn is not None and self._conn.is_open
        self._connect_action.setEnabled(not connected)
        self._disconnect_action.setEnabled(connected)
        self._upload_action.setEnabled(connected and self._config is not None)
        self._download_action.setEnabled(connected)
        self._control_panel.set_protocol(self._proto if connected else None)
        self._log_panel.set_protocol(self._proto if connected else None)

        if connected:
            self._status_conn_label.setText(f"Connected: {self._current_port}")
            self._status_conn_label.setStyleSheet("color: green; font-weight: bold;")
        else:
            self._status_conn_label.setText("Not connected")
            self._status_conn_label.setStyleSheet("color: gray;")
            self._status_model_label.setText("")

    def _update_title(self) -> None:
        if self._config and self._config.file_path:
            name = Path(self._config.file_path).name
            mod = " *" if self._config.modified else ""
            self.setWindowTitle(f"{name}{mod} — {APP_NAME}")
        else:
            self.setWindowTitle(APP_NAME)
        has_config = self._config is not None
        self._save_action.setEnabled(has_config)
        self._save_as_action.setEnabled(has_config)
        self._import_csv_action.setEnabled(has_config)
        self._upload_action.setEnabled(self._conn is not None and has_config)

    def _update_file_status(self) -> None:
        if self._config and self._config.file_path:
            sys_count = len(self._config.systems)
            ch_count = sum(
                len(g.channels)
                for s in self._config.systems
                for g in s.groups
            )
            self._status_file_label.setText(
                f"{Path(self._config.file_path).name}  "
                f"({sys_count} systems, {ch_count} channels)"
            )
        else:
            self._status_file_label.setText("")

    # ------------------------------------------------------------------
    # Editor events
    # ------------------------------------------------------------------

    def _on_system_selected(self, s_idx: int) -> None:
        if self._config:
            self._channel_editor.show_system(self._config, s_idx)

    def _on_group_selected(self, s_idx: int, g_idx: int) -> None:
        if self._config:
            self._channel_editor.show_group(self._config, s_idx, g_idx)

    def _on_channel_selected(self, s_idx: int, g_idx: int, c_idx: int) -> None:
        if self._config:
            self._channel_editor.show_channel(self._config, s_idx, g_idx, c_idx)

    def _on_editor_modified(self) -> None:
        self._systems_panel.refresh_selected_item()
        self._update_title()

    # ------------------------------------------------------------------
    # File actions
    # ------------------------------------------------------------------

    def _on_file_new(self) -> None:
        if self._config and self._config.modified:
            reply = QMessageBox.question(
                self, "Unsaved Changes",
                "You have unsaved changes. Create a new file anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._config = ScannerConfig()
        self._systems_panel.load_config(self._config)
        self._channel_editor.set_config(self._config)
        self._channel_editor.clear()
        self._update_title()
        self._update_file_status()
        self._update_connection_ui()

    def _on_file_open(self) -> None:
        if self._config and self._config.modified:
            reply = QMessageBox.question(
                self, "Unsaved Changes",
                "You have unsaved changes. Open a new file anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        path, _ = QFileDialog.getOpenFileName(
            self, "Open Scanner File", "", "FreeSCAN files (*.996);;All files (*)"
        )
        if not path:
            return
        try:
            config = file_996.load(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open Failed", f"Could not open file:\n\n{exc}")
            log.exception("Failed to open %s", path)
            return

        self._config = config
        self._systems_panel.load_config(config)
        self._channel_editor.set_config(config)
        self._channel_editor.clear()
        self._update_title()
        self._update_file_status()
        self._update_connection_ui()
        self.statusBar().showMessage(
            f"Opened {Path(path).name} — {len(config.systems)} systems loaded", 5000
        )

    def _on_file_save(self) -> None:
        if not self._config:
            return
        if not self._config.file_path:
            self._on_file_save_as()
            return
        try:
            file_996.save(self._config)
            self._update_title()
            self.statusBar().showMessage("Saved", 3000)
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", f"Could not save file:\n\n{exc}")

    def _on_file_save_as(self) -> None:
        if not self._config:
            return
        default = self._config.file_path or ""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save As", default, "FreeSCAN files (*.996);;All files (*)"
        )
        if not path:
            return
        if not path.endswith(".996"):
            path += ".996"
        try:
            file_996.save(self._config, path)
            self._update_title()
            self._update_file_status()
            self.statusBar().showMessage(f"Saved to {Path(path).name}", 3000)
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", f"Could not save file:\n\n{exc}")

    def _on_import_csv(self) -> None:
        if not self._config:
            return
        dlg = CSVImportDialog(self._config, parent=self)
        if dlg.exec():
            self._systems_panel.load_config(self._config)
            self._update_title()
            self._update_file_status()

    # ------------------------------------------------------------------
    # Scanner connection
    # ------------------------------------------------------------------

    def _on_connect(self) -> None:
        dlg = ConnectionSettingsDialog(current_port=self._current_port, parent=self)
        if dlg.exec() != ConnectionSettingsDialog.DialogCode.Accepted:
            return
        port_name = dlg.selected_port
        if not port_name:
            return
        self._start_connect(port_name)

    def _start_connect(self, port_name: str) -> None:
        """Begin an async connection attempt to the named serial port."""
        self.statusBar().showMessage(f"Connecting to {port_name}…")
        self._connect_action.setEnabled(False)

        worker = _ConnectWorker(port_name, parent=self)
        worker.success.connect(
            lambda m, v: self._on_connect_success(port_name, worker.conn, m, v)
        )
        worker.failure.connect(self._on_connect_failure)
        worker.finished.connect(worker.deleteLater)
        self._connect_worker = worker
        worker.start()

    def _on_connect_success(
        self, port_name: str, conn: serial.Serial, model: str, version: str
    ) -> None:
        self._conn = conn
        self._proto = ScannerProtocol(conn)
        self._current_port = port_name
        self._update_connection_ui()
        self._status_model_label.setText(f"Model: {model}  FW: {version}")
        self.statusBar().showMessage(f"Connected to {model} on {port_name}", 5000)
        log.info("Connected to %s (FW %s) on %s", model, version, port_name)

    def _on_connect_failure(self, error: str) -> None:
        self._update_connection_ui()
        self.statusBar().showMessage("Connection failed", 5000)
        QMessageBox.critical(
            self, "Connection Failed",
            f"Could not connect to the scanner:\n\n{error}\n\n"
            "Please check that:\n"
            "• The scanner is powered on\n"
            "• The USB cable is connected\n"
            "• The correct port is selected",
        )

    def _on_disconnect(self) -> None:
        port_manager.close_port(self._conn)
        self._conn = None
        self._proto = None
        self._current_port = None
        self._update_connection_ui()
        self.statusBar().showMessage("Disconnected", 3000)

    # ------------------------------------------------------------------
    # Upload / Download
    # ------------------------------------------------------------------

    def _on_upload(self) -> None:
        if not self._conn or not self._config:
            QMessageBox.warning(self, "Not Ready", "Connect to the scanner and open a file first.")
            return
        from app.ui.programmer.upload_dialog import UploadDialog
        self._log_panel.pause_polling()
        try:
            dlg = UploadDialog(self._proto, self._config, parent=self)
            dlg.exec()
        finally:
            self._log_panel.resume_polling()

    def _on_download(self) -> None:
        if not self._conn:
            QMessageBox.warning(self, "Not Connected", "Connect to the scanner first.")
            return
        from app.ui.programmer.download_dialog import DownloadDialog
        self._log_panel.pause_polling()
        dlg = DownloadDialog(self._proto, parent=self)
        try:
            result = dlg.exec()
        finally:
            self._log_panel.resume_polling()
        if result and dlg.downloaded_config:
            config = dlg.downloaded_config
            if self._config and self._config.modified:
                reply = QMessageBox.question(
                    self, "Replace Current Config",
                    "Load downloaded config? This will replace any unsaved changes.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            self._config = config
            self._systems_panel.load_config(config)
            self._channel_editor.set_config(config)
            self._channel_editor.clear()
            self._update_title()
            self._update_file_status()
            self._update_connection_ui()

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _on_preferences(self) -> None:
        dlg = PreferencesDialog(parent=self)
        if dlg.exec():
            settings = load_prefs()
            apply_theme(settings.value("appearance/theme", "System default"))
            self._transcription_manager.apply_settings()

    def _on_about(self) -> None:
        from pathlib import Path
        from PyQt6.QtGui import QPixmap
        icon_path = Path(__file__).resolve().parents[3] / "resources" / "icons" / "neoscan_64.png"
        box = QMessageBox(self)
        box.setWindowTitle(f"About {APP_NAME}")
        if icon_path.exists():
            box.setIconPixmap(QPixmap(str(icon_path)))
        box.setText(
            "<h2>NeoSCAN</h2>"
            "<p>Version 1.0</p>"
            "<p>Cross-platform programmer and remote control for<br>"
            "Uniden BCT15-X and BCD996XT radio scanners.</p>"
            "<p>Released under the GNU General Public License v3.</p>"
        )
        box.exec()

    def closeEvent(self, event) -> None:
        if self._config and self._config.modified:
            reply = QMessageBox.question(
                self, "Unsaved Changes",
                "You have unsaved changes. Quit anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._transcription_manager.shutdown()
        port_manager.close_port(self._conn)
        super().closeEvent(event)
