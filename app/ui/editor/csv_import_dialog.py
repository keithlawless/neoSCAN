"""
CSV import wizard — file picker → field mapping → import.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QComboBox,
    QHeaderView,
    QFrame,
)

from app.data import file_csv
from app.data.models import Group, ScannerConfig

log = logging.getLogger(__name__)

# Choices shown in each column's mapping combo
FIELD_OPTIONS = [file_csv.SKIP] + sorted(file_csv.IMPORTABLE_FIELDS)
FIELD_LABELS = {
    file_csv.SKIP: "(skip)",
    "name": "Channel Name",
    "tgid": "Talk Group ID",
    "frequency": "Frequency (MHz)",
    "modulation": "Modulation",
    "audio_type": "Audio Type (D/A/D/A)",
    "tone": "CTCSS/DCS Tone",
    "lockout": "Lockout",
    "priority": "Priority",
    "attenuator": "Attenuator",
    "delay": "Scan Delay",
    "comment": "Comment",
    "number_tag": "Number Tag",
    "tone_lockout": "Tone Lockout",
    "volume_offset": "Volume Offset",
}


class CSVImportDialog(QDialog):
    """
    Multi-step CSV import dialog.

    Usage::
        dlg = CSVImportDialog(config, parent=self)
        if dlg.exec():
            # channels have been added to dlg.target_group
    """

    def __init__(self, config: ScannerConfig, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._path: str = ""
        self._headers: list[str] = []
        self._preview_rows: list[list[str]] = []
        self._mappings: list[file_csv.FieldMapping] = []
        self._column_combos: list[QComboBox] = []

        self.setWindowTitle("Import CSV")
        self.setMinimumSize(700, 500)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # File picker row
        file_group = QGroupBox("1. Select CSV File")
        file_row = QHBoxLayout(file_group)
        self._path_label = QLabel("No file selected")
        self._path_label.setWordWrap(True)
        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse)
        file_row.addWidget(self._path_label, stretch=1)
        file_row.addWidget(browse_btn)
        layout.addWidget(file_group)

        # Target group picker
        target_group = QGroupBox("2. Import Into")
        target_form = QFormLayout(target_group)
        self._target_combo = QComboBox()
        self._populate_target_combo()
        target_form.addRow("System / Group:", self._target_combo)
        layout.addWidget(target_group)

        # Field mapping
        mapping_group = QGroupBox("3. Field Mapping")
        mapping_layout = QVBoxLayout(mapping_group)
        mapping_hint = QLabel(
            "NeoSCAN has detected the best field mapping based on your CSV headers. "
            "Adjust any incorrect mappings using the drop-downs."
        )
        mapping_hint.setWordWrap(True)
        mapping_hint.setStyleSheet("font-size: 11px; color: #555;")
        mapping_layout.addWidget(mapping_hint)

        self._mapping_area = QScrollArea()
        self._mapping_area.setWidgetResizable(False)
        self._mapping_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._mapping_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._mapping_area.setFixedHeight(110)
        mapping_layout.addWidget(self._mapping_area)
        layout.addWidget(mapping_group)

        # Preview table
        preview_group = QGroupBox("4. Preview (first 5 rows)")
        preview_layout = QVBoxLayout(preview_group)
        self._preview_table = QTableWidget()
        self._preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._preview_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._preview_table.setMinimumHeight(140)
        preview_layout.addWidget(self._preview_table)
        layout.addWidget(preview_group)

        # Buttons
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self._buttons.accepted.connect(self._on_import)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    def _populate_target_combo(self) -> None:
        self._target_combo.clear()
        for s_idx, sys in enumerate(self._config.systems):
            for g_idx, grp in enumerate(sys.groups):
                label = f"{sys.name or f'System {s_idx+1}'} → {grp.name or f'Group {g_idx+1}'}"
                self._target_combo.addItem(label, userData=(s_idx, g_idx))
        if self._target_combo.count() == 0:
            self._target_combo.addItem("(no groups — create one in the editor first)")

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open CSV File", "", "CSV files (*.csv);;All files (*)"
        )
        if not path:
            return
        self._path = path
        self._path_label.setText(path)
        try:
            self._headers, self._preview_rows = file_csv.preview_rows(path, n=5)
            self._mappings = file_csv.suggest_mapping(self._headers)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Could not read CSV file:\n{exc}")
            return
        self._build_mapping_ui()
        self._build_preview_table()
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            self._target_combo.count() > 0 and bool(self._headers)
        )

    def _build_mapping_ui(self) -> None:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._column_combos = []

        for m in self._mappings:
            col_widget = QFrame()
            col_layout = QVBoxLayout(col_widget)
            col_layout.setSpacing(2)
            col_layout.setContentsMargins(4, 4, 4, 4)

            hdr_label = QLabel(f"<b>{m.header[:20]}</b>")
            hdr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col_layout.addWidget(hdr_label)

            combo = QComboBox()
            for field in FIELD_OPTIONS:
                combo.addItem(FIELD_LABELS.get(field, field), userData=field)
            # Set to suggested value
            idx = combo.findData(m.field)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            col_layout.addWidget(combo)
            self._column_combos.append(combo)

            col_widget.setFixedWidth(130)
            col_widget.setFrameShape(QFrame.Shape.StyledPanel)  # type: ignore
            layout.addWidget(col_widget)

        self._mapping_area.setWidget(container)

    def _build_preview_table(self) -> None:
        self._preview_table.clear()
        self._preview_table.setColumnCount(len(self._headers))
        self._preview_table.setHorizontalHeaderLabels(self._headers)
        self._preview_table.setRowCount(len(self._preview_rows))
        for r, row in enumerate(self._preview_rows):
            for c, cell in enumerate(row):
                item = QTableWidgetItem(cell)
                self._preview_table.setItem(r, c, item)

    def _on_import(self) -> None:
        if not self._path:
            return
        target_data = self._target_combo.currentData()
        if not target_data:
            QMessageBox.warning(self, "No Target", "Please select a group to import into.")
            return

        s_idx, g_idx = target_data
        target_sys = self._config.systems[s_idx]
        target_group = target_sys.groups[g_idx]
        create_talkgroups = target_sys.is_trunked

        # Build final mappings from combos
        final_mappings = []
        for i, combo in enumerate(self._column_combos):
            field = combo.currentData()
            final_mappings.append(
                file_csv.FieldMapping(i, self._headers[i], field)
            )

        try:
            added, warnings = file_csv.import_csv(
                self._path, final_mappings, target_group,
                create_talkgroups=create_talkgroups,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Import Failed", f"Import error:\n{exc}")
            return

        self._config.modified = True
        label = "talk group(s)" if create_talkgroups else "channel(s)"
        msg = f"Imported {added} {label} into '{target_group.name}'."
        if warnings:
            msg += f"\n\nWarnings ({len(warnings)}):\n" + "\n".join(warnings[:10])
        QMessageBox.information(self, "Import Complete", msg)
        self.accept()
