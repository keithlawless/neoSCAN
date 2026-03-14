"""
Trunk site import dialog — imports P25/Motorola sites and trunk frequencies
from a RadioReference-format CSV into a trunked system.

RadioReference sites CSV format:
  RFSS, Site Dec, Site Hex, Site NAC, Description, County Name,
  Lat, Lon, Range, Frequencies...

Frequencies appear as variable-length extra columns starting at index 9.
A trailing "c" on a frequency marks it as a control channel.
"""
from __future__ import annotations

import csv
import uuid
import logging
from typing import NamedTuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QComboBox,
    QHeaderView,
)

from app.data.models import Group, System, ScannerConfig, TrunkFrequency

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class _SiteRow(NamedTuple):
    rfss: str
    site_dec: str
    site_hex: str
    site_nac: str
    description: str
    county: str
    lat: str
    lon: str
    frequencies: list[str]   # raw strings, may have trailing "c" for control ch


# ---------------------------------------------------------------------------
# CSV parsing helpers
# ---------------------------------------------------------------------------

def _parse_sites_csv(path: str) -> tuple[list[str], list[_SiteRow]]:
    """
    Parse a RadioReference sites CSV.
    Returns (headers, site_rows).

    Supports both RadioReference export variants:
      Full:    RFSS, Site Dec, Site Hex, Site NAC, Description, County Name,
               Lat, Lon, Range, Frequencies...
      Compact: Site Dec, Site Hex, Description, County Name,
               Lat, Lon, Range, Frequencies...

    Column positions are resolved by header name so either layout works.
    Frequencies are every column from the "Frequencies" header onward.
    """
    rows: list[_SiteRow] = []
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.reader(f)
        headers = next(reader, [])

        # Map normalised header name → column index
        h_idx: dict[str, int] = {h.strip().lower(): i for i, h in enumerate(headers)}

        def _cidx(name: str) -> int:
            """Return column index for header name, or -1 if absent."""
            return h_idx.get(name.lower(), -1)

        rfss_col        = _cidx("rfss")
        site_dec_col    = _cidx("site dec")
        site_hex_col    = _cidx("site hex")
        site_nac_col    = _cidx("site nac")
        desc_col        = _cidx("description")
        county_col      = _cidx("county name")
        lat_col         = _cidx("lat")
        lon_col         = _cidx("lon")
        freq_start_col  = _cidx("frequencies")   # first freq column

        if freq_start_col == -1:
            # Fall back: frequencies start after the last known fixed column
            freq_start_col = max(
                rfss_col, site_dec_col, site_hex_col, site_nac_col,
                desc_col, county_col, lat_col, lon_col,
                _cidx("range"),
            ) + 1

        def _col(row: list[str], idx: int) -> str:
            return row[idx].strip() if 0 <= idx < len(row) else ""

        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            freqs = [
                row[i].strip()
                for i in range(freq_start_col, len(row))
                if row[i].strip()
            ]
            rows.append(_SiteRow(
                rfss        = _col(row, rfss_col),
                site_dec    = _col(row, site_dec_col),
                site_hex    = _col(row, site_hex_col),
                site_nac    = _col(row, site_nac_col),
                description = _col(row, desc_col),
                county      = _col(row, county_col),
                lat         = _col(row, lat_col),
                lon         = _col(row, lon_col),
                frequencies = freqs,
            ))
    return headers, rows


def is_sites_csv(headers: list[str]) -> bool:
    """Return True if the headers look like a RadioReference sites CSV."""
    h_lower = {h.strip().lower() for h in headers}
    return "site dec" in h_lower and "frequencies" in h_lower


# ---------------------------------------------------------------------------
# Core import logic (separated so it's testable without UI)
# ---------------------------------------------------------------------------

def import_sites(
    rows: list[_SiteRow],
    target_system: System,
    config: ScannerConfig,
) -> tuple[int, int, list[str]]:
    """
    Import site rows into target_system, appending new Site groups and
    TrunkFrequency objects.

    Returns (sites_added, freqs_added, warnings).
    """
    sites_added = 0
    freqs_added = 0
    warnings: list[str] = []

    for row in rows:
        site_name = (row.description or f"Site {row.site_dec}").strip()[:16]

        site_grp = Group()
        site_grp.name = site_name
        site_grp.group_type = "3"   # site
        site_grp.quick_key = "."
        site_grp.group_id = uuid.uuid4().hex[:16].upper()

        lcn = 0
        for raw_freq in row.frequencies:
            # Trailing "c" marks a control channel — strip it, record it
            is_control = raw_freq.endswith("c")
            freq_clean = raw_freq.rstrip("c").strip()
            try:
                freq_mhz = float(freq_clean)
            except ValueError:
                warnings.append(
                    f"Site {row.description!r}: invalid frequency {raw_freq!r} — skipped"
                )
                continue

            lcn += 1
            tf = TrunkFrequency()
            tf.frequency = f"{freq_mhz:.4f}"
            tf.lcn = str(lcn)
            tf.lockout = False
            # group_id links trunk freq to the system (matches .996 file convention)
            tf.group_id = target_system.group_id
            target_system.trunk_frequencies.append(tf)
            config.trunk_frequencies.append(tf)
            freqs_added += 1

        target_system.groups.append(site_grp)
        sites_added += 1

    log.info(
        "Site import: %d sites, %d freqs into system %r",
        sites_added, freqs_added, target_system.name,
    )
    return sites_added, freqs_added, warnings


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class TrunkSiteImportDialog(QDialog):
    """
    Dialog for importing RadioReference sites CSV into a trunked system.

    Usage::
        dlg = TrunkSiteImportDialog(config, parent=self)
        if dlg.exec():
            # Sites have been appended to the selected system
    """

    def __init__(self, config: ScannerConfig, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._path: str = ""
        self._site_rows: list[_SiteRow] = []

        self.setWindowTitle("Import Trunk Sites from CSV")
        self.setMinimumSize(740, 560)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ── Step 1: File ──────────────────────────────────────────────
        file_group = QGroupBox("1. Select Sites CSV File")
        file_row = QHBoxLayout(file_group)
        self._path_label = QLabel("No file selected")
        self._path_label.setWordWrap(True)
        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse)
        file_row.addWidget(self._path_label, stretch=1)
        file_row.addWidget(browse_btn)
        layout.addWidget(file_group)

        # ── Step 2: Target system ─────────────────────────────────────
        target_group = QGroupBox("2. Import Into System")
        target_form = QVBoxLayout(target_group)
        self._target_combo = QComboBox()
        self._populate_system_combo()
        target_form.addWidget(self._target_combo)
        hint = QLabel(
            "Only P25 and Motorola trunked systems are shown. "
            "Sites will be appended to the selected system."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 11px; color: #555;")
        target_form.addWidget(hint)
        layout.addWidget(target_group)

        # ── Step 3: Preview ───────────────────────────────────────────
        preview_group = QGroupBox("3. Preview")
        preview_layout = QVBoxLayout(preview_group)
        self._preview_table = QTableWidget()
        self._preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._preview_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._preview_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._preview_table.setMinimumHeight(220)
        preview_layout.addWidget(self._preview_table)
        layout.addWidget(preview_group)

        # ── Buttons ───────────────────────────────────────────────────
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import Sites")
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self._buttons.accepted.connect(self._on_import)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    def _populate_system_combo(self) -> None:
        self._target_combo.clear()
        for i, sys in enumerate(self._config.systems):
            if sys.is_trunked:
                label = f"[{sys.type_name}]  {sys.name or f'System {i+1}'}"
                self._target_combo.addItem(label, userData=i)
        if self._target_combo.count() == 0:
            self._target_combo.addItem(
                "(no trunked systems — create a P25 or Motorola system first)"
            )

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Sites CSV", "", "CSV files (*.csv);;All files (*)"
        )
        if not path:
            return
        try:
            headers, rows = _parse_sites_csv(path)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Could not read CSV file:\n{exc}")
            return

        if not is_sites_csv(headers):
            QMessageBox.warning(
                self,
                "Unrecognised Format",
                "This file does not appear to be a RadioReference sites export.\n\n"
                "Expected columns: RFSS, Site Dec, Site Hex, Site NAC, Description, "
                "County Name, Lat, Lon, Range, Frequencies…\n\n"
                "Proceed anyway? Use 'Import CSV' for conventional channel lists.",
            )

        self._path = path
        self._site_rows = rows
        self._path_label.setText(path)
        self._build_preview_table()
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            bool(rows) and self._target_combo.count() > 0
            and self._target_combo.currentData() is not None
        )

    def _build_preview_table(self) -> None:
        cols = ["RFSS", "Site", "NAC", "Name", "County", "# Freqs", "Frequencies"]
        self._preview_table.clear()
        self._preview_table.setColumnCount(len(cols))
        self._preview_table.setHorizontalHeaderLabels(cols)
        self._preview_table.setRowCount(len(self._site_rows))

        for r, row in enumerate(self._site_rows):
            freq_sample = ", ".join(
                f[:10] for f in row.frequencies[:4]
            )
            if len(row.frequencies) > 4:
                freq_sample += f" (+{len(row.frequencies) - 4} more)"

            for c, val in enumerate([
                row.rfss,
                row.site_dec,
                row.site_nac,
                row.description[:30],
                row.county[:20],
                str(len(row.frequencies)),
                freq_sample,
            ]):
                item = QTableWidgetItem(val)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._preview_table.setItem(r, c, item)

    def _on_import(self) -> None:
        s_idx = self._target_combo.currentData()
        if s_idx is None:
            QMessageBox.warning(self, "No Target", "Please select a trunked system.")
            return

        target_sys = self._config.systems[s_idx]
        try:
            sites_added, freqs_added, warnings = import_sites(
                self._site_rows, target_sys, self._config
            )
        except Exception as exc:
            QMessageBox.critical(self, "Import Failed", f"Import error:\n{exc}")
            return

        self._config.modified = True
        msg = (
            f"Imported {sites_added} site(s) with {freqs_added} trunk "
            f"frequency(ies) into '{target_sys.name}'."
        )
        if warnings:
            msg += f"\n\nWarnings ({len(warnings)}):\n" + "\n".join(warnings[:10])
        QMessageBox.information(self, "Import Complete", msg)
        self.accept()
