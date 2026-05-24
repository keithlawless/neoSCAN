"""
ADS-B tab panel.

Shows SDR connection status, aircraft count, and a live grid of ADS-B traffic.
Aircraft that have not been heard for >60 s are greyed out; those silent for
>5 min are removed.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.sdr.aircraft_state import Aircraft, AircraftStateTracker
from app.sdr.adsb_logger import ADSBLogger

log = logging.getLogger(__name__)

_GREY_SECS   = 60    # seconds before an aircraft is shown as stale
_REMOVE_SECS = 300   # seconds before an aircraft is removed from the grid
_REFRESH_MS  = 1000  # grid refresh interval

_HEADERS = [
    "ICAO", "Callsign", "Altitude (ft)", "Trend",
    "Speed (kt)", "Track (°)", "Lat", "Lon", "Squawk", "Alert", "Last Seen",
]

COL_ICAO      = 0
COL_CALLSIGN  = 1
COL_ALTITUDE  = 2
COL_TREND     = 3
COL_SPEED     = 4
COL_TRACK     = 5
COL_LAT       = 6
COL_LON       = 7
COL_SQUAWK    = 8
COL_ALERT     = 9
COL_LAST_SEEN = 10

_COLOR_EMERGENCY = QColor(255, 80, 80)
_COLOR_ALERT     = QColor(255, 200, 80)
_COLOR_STALE     = QColor(150, 150, 150)


def _fmt(value, fmt=None) -> str:
    if value is None:
        return ""
    if fmt:
        return format(value, fmt)
    return str(value)


def _alert_text(ac: Aircraft) -> str:
    parts = []
    if ac.emergency:
        parts.append("EMRG")
    if ac.alert:
        parts.append("ALRT")
    if ac.spi:
        parts.append("SPI")
    return " ".join(parts)


class ADSBPanel(QWidget):
    """Main ADS-B tab: status bar + live aircraft grid."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tracker = AircraftStateTracker()
        self._icao_to_row: dict[str, int] = {}  # ICAO → current table row
        self._logger: Optional[ADSBLogger] = None
        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self._refresh_grid)
        self._timer.start()

    # ------------------------------------------------------------------
    # Public API (called by MainWindow)
    # ------------------------------------------------------------------

    def set_logger(self, logger: Optional[ADSBLogger]) -> None:
        self._logger = logger

    def flush_logger(self) -> None:
        """Write all currently-tracked aircraft to the log (call on disconnect)."""
        if self._logger:
            self._logger.flush_all(self._tracker.snapshot().values())

    def on_aircraft_updated(self, icao: str, fields: dict) -> None:
        self._tracker.update(icao, **fields)

    def on_sdr_status_changed(self, running: bool, message: str) -> None:
        if running:
            self._status_label.setText(f"SDR: {message}")
            self._status_label.setStyleSheet("color: green; font-weight: bold;")
        else:
            self._status_label.setText(f"SDR: {message}")
            self._status_label.setStyleSheet("color: gray;")

    def on_receiver_status_changed(self, connected: bool, message: str) -> None:
        if connected:
            self._recv_label.setText(f"Stream: {message}")
            self._recv_label.setStyleSheet("color: green;")
        else:
            self._recv_label.setText(f"Stream: {message}")
            self._recv_label.setStyleSheet("color: gray;")

    def reset(self) -> None:
        """Clear the grid and tracker (called on disconnect)."""
        self._tracker.clear()
        self._table.setRowCount(0)
        self._icao_to_row.clear()
        self._status_label.setText("SDR: Not connected")
        self._status_label.setStyleSheet("color: gray;")
        self._recv_label.setText("")
        self._count_label.setText("")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        # Status bar
        status_row = QHBoxLayout()
        self._status_label = QLabel("SDR: Not connected")
        self._status_label.setStyleSheet("color: gray;")
        self._recv_label = QLabel("")
        self._recv_label.setStyleSheet("color: gray;")
        self._count_label = QLabel("")
        self._count_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._count_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        status_row.addWidget(self._status_label)
        status_row.addSpacing(20)
        status_row.addWidget(self._recv_label)
        status_row.addWidget(self._count_label)
        layout.addLayout(status_row)

        # Aircraft grid
        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

    # ------------------------------------------------------------------
    # Grid refresh
    # ------------------------------------------------------------------

    def _refresh_grid(self) -> None:
        removed = self._tracker.purge(grey_secs=_GREY_SECS, remove_secs=_REMOVE_SECS)
        snapshot = self._tracker.snapshot()
        now = datetime.now()

        # Resync row map from column 0 — sorting between ticks may have moved
        # rows, leaving _icao_to_row pointing at wrong indices.
        self._icao_to_row = {}
        for row in range(self._table.rowCount()):
            item = self._table.item(row, COL_ICAO)
            if item:
                self._icao_to_row[item.text()] = row

        # Log removed aircraft before we drop them from the grid.
        if removed and self._logger:
            for ac in removed.values():
                self._logger.log(ac)

        # Remove purged rows in descending row order so earlier indices stay
        # valid as rows are deleted, then rebuild the map from column 0.
        if removed:
            rows_to_delete = sorted(
                (self._icao_to_row[icao] for icao in removed if icao in self._icao_to_row),
                reverse=True,
            )
            for row in rows_to_delete:
                self._table.removeRow(row)
            for icao in removed:
                self._icao_to_row.pop(icao, None)
            # Resync the map — row numbers shift after every deletion
            self._icao_to_row = {}
            for row in range(self._table.rowCount()):
                item = self._table.item(row, COL_ICAO)
                if item:
                    self._icao_to_row[item.text()] = row

        # Upsert rows
        sorting_was_enabled = self._table.isSortingEnabled()
        self._table.setSortingEnabled(False)
        for icao, ac in snapshot.items():
            age = (now - ac.last_seen).total_seconds()
            row = self._icao_to_row.get(icao)
            if row is None:
                row = self._table.rowCount()
                self._table.insertRow(row)
                self._icao_to_row[icao] = row
            self._fill_row(row, ac, age)
        self._table.setSortingEnabled(sorting_was_enabled)

        # Update count label
        total = len(snapshot)
        stale = sum(
            1 for ic in snapshot
            if (now - snapshot[ic].last_seen).total_seconds() > _GREY_SECS
        )
        if total:
            self._count_label.setText(
                f"{total} aircraft in range" + (f" ({stale} stale)" if stale else "")
            )
        else:
            self._count_label.setText("")

    def _fill_row(self, row: int, ac: Aircraft, age: float) -> None:
        age_str = f"{int(age)}s ago" if age < 3600 else f"{int(age/60)}m ago"

        values = [
            ac.icao,
            ac.callsign or "",
            _fmt(ac.altitude),
            ac.vertical_trend,
            _fmt(ac.speed),
            _fmt(ac.track, ".1f") if ac.track is not None else "",
            _fmt(ac.lat, ".5f") if ac.lat is not None else "",
            _fmt(ac.lon, ".5f") if ac.lon is not None else "",
            ac.squawk or "",
            _alert_text(ac),
            age_str,
        ]

        # Row background for alert / emergency
        if ac.emergency:
            bg = _COLOR_EMERGENCY
        elif ac.alert:
            bg = _COLOR_ALERT
        else:
            bg = None

        # Text colour for stale aircraft
        fg = _COLOR_STALE if age > _GREY_SECS else None

        for col, text in enumerate(values):
            item = self._table.item(row, col)
            if item is None:
                item = QTableWidgetItem()
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, col, item)
            item.setText(text)
            if bg:
                item.setBackground(bg)
            else:
                item.setData(Qt.ItemDataRole.BackgroundRole, None)
            if fg:
                item.setForeground(fg)
            else:
                item.setData(Qt.ItemDataRole.ForegroundRole, None)
