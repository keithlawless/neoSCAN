"""
ATC Mode radar panel.

Renders a real-time radar display of ADS-B traffic using QPainter.
The refresh timer runs only while the tab is visible.

Location resolution order:
  1. Manual lat/lon from Preferences → ADS-B
  2. System GPS via QtPositioning (if the package is installed and hardware present)
  3. IP geolocation (parallel background fetch, accepted only if GPS hasn't fired)
"""
from __future__ import annotations

import json
import math
import urllib.request
from datetime import datetime
from typing import Callable, Optional

from PyQt6.QtCore import QPointF, QSettings, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QPolygonF, QShortcut
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.sdr.aircraft_state import Aircraft

# ---------------------------------------------------------------------------
# Colour palette — classic radar green on black
# ---------------------------------------------------------------------------
_C_BG         = QColor(  3,  12,   3)
_C_RING       = QColor(  0,  55,   0)
_C_RING_LBL   = QColor(  0,  90,   0)
_C_CROSS      = QColor(  0,  35,   0)
_C_COMPASS    = QColor(  0, 120,   0)
_C_HOME       = QColor( 80, 200, 255)
_C_AC_NORMAL  = QColor(  0, 255,   0)
_C_AC_STALE   = QColor(  0, 110,   0)
_C_AC_CLIMB   = QColor( 80, 255, 120)
_C_AC_DESCEND = QColor(255, 180,  40)
_C_LABEL      = QColor(  0, 200,   0)

_STALE_SECS = 60


# ---------------------------------------------------------------------------
# Location fetcher — IP geolocation fallback
# ---------------------------------------------------------------------------

class _LocationFetcher(QThread):
    """Fetches approximate position from public IP-geolocation APIs."""

    found  = pyqtSignal(float, float, str)   # lat, lon, source_label
    failed = pyqtSignal()

    _URLS = [
        "https://ipinfo.io/json",
        "http://ip-api.com/json/",
    ]

    def run(self) -> None:
        for url in self._URLS:
            try:
                with urllib.request.urlopen(url, timeout=5) as r:
                    data = json.loads(r.read().decode("utf-8"))
                if "ipinfo" in url:
                    lat, lon = map(float, data["loc"].split(","))
                else:
                    lat, lon = float(data["lat"]), float(data["lon"])
                city = data.get("city", "unknown")
                self.found.emit(lat, lon, f"IP ({city})")
                return
            except Exception:
                continue
        self.failed.emit()


# ---------------------------------------------------------------------------
# Radar canvas
# ---------------------------------------------------------------------------

class RadarView(QWidget):
    """
    QPainter radar canvas. All layout is computed at paint time from
    _home, _aircraft, _range_nm, and _pan_nm.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._home: Optional[tuple[float, float]] = None
        self._aircraft: dict[str, Aircraft] = {}
        self._range_nm: float = 50.0
        self._pan_nm: tuple[float, float] = (0.0, 0.0)   # east_nm, north_nm
        self._drag_origin: Optional[QPointF] = None
        self._drag_pan0: tuple[float, float] = (0.0, 0.0)
        self._wheel_acc: float = 0.0   # accumulator for smooth-scroll pixel delta
        self.setMinimumSize(400, 400)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)

    # ------------------------------------------------------------------
    # Public setters
    # ------------------------------------------------------------------

    def set_home(self, lat: float, lon: float) -> None:
        self._home = (lat, lon)
        self.update()

    def set_aircraft(self, aircraft: dict[str, Aircraft]) -> None:
        self._aircraft = aircraft
        self.update()

    def reset_view(self) -> None:
        self._pan_nm = (0.0, 0.0)
        self._range_nm = 50.0
        self.update()

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), _C_BG)

        if self._home is None:
            p.setPen(_C_COMPASS)
            p.setFont(QFont("Monospace", 11))
            p.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Locating…\n\nSet coordinates in Preferences → ADS-B\nor wait for IP geolocation.",
            )
            return

        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        ppm = min(w, h) / 2.0 / self._range_nm   # pixels per nautical mile

        self._paint_rings(p, cx, cy, ppm)
        self._paint_crosshairs(p, cx, cy)
        self._paint_home(p, cx, cy, ppm)
        self._paint_aircraft(p, cx, cy, ppm)

    def _paint_rings(self, p: QPainter, cx: float, cy: float, ppm: float) -> None:
        pen_ring = QPen(_C_RING, 1, Qt.PenStyle.DotLine)
        p.setFont(QFont("Monospace", 7))
        for nm in self._ring_nms():
            r = nm * ppm
            p.setPen(pen_ring)
            p.drawEllipse(QPointF(cx, cy), r, r)
            p.setPen(_C_RING_LBL)
            p.drawText(QPointF(cx + r + 2, cy - 2), f"{int(nm)}nm")

    def _paint_crosshairs(self, p: QPainter, cx: float, cy: float) -> None:
        p.setPen(QPen(_C_CROSS, 1))
        p.drawLine(QPointF(cx, 0), QPointF(cx, self.height()))
        p.drawLine(QPointF(0, cy), QPointF(self.width(), cy))
        p.setPen(_C_COMPASS)
        p.setFont(QFont("Monospace", 9, QFont.Weight.Bold))
        m = 14
        p.drawText(QPointF(cx + 4, m),                    "N")
        p.drawText(QPointF(cx + 4, self.height() - 4),    "S")
        p.drawText(QPointF(m,      cy - 4),                "W")
        p.drawText(QPointF(self.width() - m, cy - 4),     "E")

    def _paint_home(self, p: QPainter, cx: float, cy: float, ppm: float) -> None:
        hx, hy = self._project(self._home[0], self._home[1], cx, cy, ppm)
        p.setPen(QPen(_C_HOME, 2))
        arm = 6
        p.drawLine(QPointF(hx - arm, hy), QPointF(hx + arm, hy))
        p.drawLine(QPointF(hx, hy - arm), QPointF(hx, hy + arm))
        p.drawEllipse(QPointF(hx, hy), arm, arm)

    def _paint_aircraft(self, p: QPainter, cx: float, cy: float, ppm: float) -> None:
        now = datetime.now()
        p.setFont(QFont("Monospace", 8))
        for ac in self._aircraft.values():
            if ac.lat is None or ac.lon is None:
                continue
            sx, sy = self._project(ac.lat, ac.lon, cx, cy, ppm)
            pad = 20
            if sx < -pad or sx > self.width() + pad or sy < -pad or sy > self.height() + pad:
                continue
            age = (now - ac.last_seen).total_seconds()
            self._paint_one(p, sx, sy, ac, age)

    def _paint_one(self, p: QPainter, x: float, y: float,
                   ac: Aircraft, age: float) -> None:
        trend = ac.vertical_trend
        if age > _STALE_SECS:
            color = _C_AC_STALE
        elif trend == "Climbing":
            color = _C_AC_CLIMB
        elif trend == "Descending":
            color = _C_AC_DESCEND
        else:
            color = _C_AC_NORMAL

        # Oriented arrowhead icon
        p.save()
        p.translate(x, y)
        if ac.track is not None:
            p.rotate(ac.track)
        icon = QPolygonF([
            QPointF( 0, -9),   # nose
            QPointF( 6,  4),   # right wing tip
            QPointF( 0,  1),   # tail indent
            QPointF(-6,  4),   # left wing tip
        ])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPolygon(icon)
        p.restore()

        # Labels: callsign / ICAO on line 1, FL + trend on line 2
        ident = ac.callsign or ac.icao
        p.setPen(color)
        lx, ly = x + 11, y - 4
        p.drawText(QPointF(lx, ly), ident)
        if ac.altitude is not None:
            sym = "↑" if trend == "Climbing" else ("↓" if trend == "Descending" else " ")
            p.drawText(QPointF(lx, ly + 10), f"{ac.altitude // 100:03d}{sym}")

    # ------------------------------------------------------------------
    # Zoom & pan
    # ------------------------------------------------------------------

    def wheelEvent(self, event) -> None:
        dy = event.angleDelta().y()
        if dy == 0:
            # macOS trackpad smooth-scroll: angleDelta is 0; use pixelDelta.
            # Accumulate until 20 px are crossed to keep sensitivity reasonable.
            self._wheel_acc += event.pixelDelta().y()
            if abs(self._wheel_acc) < 20:
                event.accept()
                return
            dy = self._wheel_acc
            self._wheel_acc = 0.0
        else:
            self._wheel_acc = 0.0
        factor = 0.85 if dy > 0 else 1.15
        self._range_nm = max(5.0, min(500.0, self._range_nm * factor))
        self.update()
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = QPointF(event.position())
            self._drag_pan0 = self._pan_nm

    def mouseMoveEvent(self, event) -> None:
        if self._drag_origin is None:
            return
        ppm = min(self.width(), self.height()) / 2.0 / self._range_nm
        dx = event.position().x() - self._drag_origin.x()
        dy = event.position().y() - self._drag_origin.y()
        self._pan_nm = (
            self._drag_pan0[0] - dx / ppm,
            self._drag_pan0[1] + dy / ppm,
        )
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = None

    def mouseDoubleClickEvent(self, _event) -> None:
        self._pan_nm = (0.0, 0.0)
        self.update()

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def _project(self, lat: float, lon: float,
                 cx: float, cy: float, ppm: float) -> tuple[float, float]:
        """Convert (lat, lon) to screen (x, y) honouring current pan."""
        hlat, hlon = self._home
        pan_e, pan_n = self._pan_nm
        cos_h = math.cos(math.radians(hlat))
        dlon_nm = (lon - hlon) * 60.0 * cos_h
        dlat_nm = (lat - hlat) * 60.0
        sx = cx + (dlon_nm - pan_e) * ppm
        sy = cy - (dlat_nm - pan_n) * ppm   # north = up
        return sx, sy

    def _ring_nms(self) -> list[float]:
        """Choose ring spacing so we get 3-5 visible rings."""
        r = self._range_nm
        for step in (5, 10, 25, 50, 100, 200):
            if r / step <= 5:
                return [step * i for i in range(1, int(r / step) + 1)]
        return [200.0 * i for i in range(1, int(r / 200) + 1)]


# ---------------------------------------------------------------------------
# Top-level tab panel
# ---------------------------------------------------------------------------

class ATCPanel(QWidget):
    """
    ATC Mode tab: toolbar + RadarView.

    The refresh timer is started on showEvent and stopped on hideEvent so
    no work is done while this tab is not visible.
    """

    def __init__(
        self,
        aircraft_snapshot_fn: Callable[[], dict[str, Aircraft]],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._snapshot_fn = aircraft_snapshot_fn
        self._home_set = False
        self._gps_source = None
        self._fetcher: Optional[_LocationFetcher] = None

        self._build_ui()
        self._setup_shortcuts()

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh)

        self._start_location()

    # ------------------------------------------------------------------
    # Visibility — start/stop timer with tab visibility
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:
        self._timer.start()
        self._refresh()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    # ------------------------------------------------------------------
    # Public API called by main_window
    # ------------------------------------------------------------------

    def apply_settings(self) -> None:
        """Re-read home coordinates from preferences (call after prefs saved)."""
        s = QSettings("NeoSCAN", "NeoSCAN")
        lat_s = s.value("adsb/home_lat", "")
        lon_s = s.value("adsb/home_lon", "")
        if lat_s and lon_s:
            try:
                self._set_home(float(lat_s), float(lon_s), "manual")
            except ValueError:
                pass

    def on_sdr_status_changed(self, running: bool, message: str) -> None:
        if running:
            self._sdr_label.setText(f"SDR: {message}")
            self._sdr_label.setStyleSheet("color: #00c000; font-size: 11px;")
        else:
            self._sdr_label.setText("SDR: Not connected")
            self._sdr_label.setStyleSheet("color: #555; font-size: 11px;")

    # ------------------------------------------------------------------
    # Keyboard shortcuts
    # ------------------------------------------------------------------

    def _setup_shortcuts(self) -> None:
        ctx = Qt.ShortcutContext.WidgetWithChildrenShortcut
        for key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            s = QShortcut(QKeySequence(key), self)
            s.setContext(ctx)
            s.activated.connect(self._zoom_in)
        s = QShortcut(QKeySequence(Qt.Key.Key_Minus), self)
        s.setContext(ctx)
        s.activated.connect(self._zoom_out)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Toolbar ---
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 4, 8, 4)

        self._sdr_label = QLabel("SDR: Not connected")
        self._sdr_label.setStyleSheet("color: #555; font-size: 11px;")
        bar.addWidget(self._sdr_label)

        bar.addSpacing(16)

        self._loc_label = QLabel("Locating…")
        self._loc_label.setStyleSheet("color: #555; font-size: 11px;")
        bar.addWidget(self._loc_label)

        bar.addStretch()

        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color: #00c000; font-size: 11px;")
        bar.addWidget(self._count_label)

        bar.addSpacing(16)

        self._range_label = QLabel("50 nm")
        self._range_label.setStyleSheet("color: #00a000; font-size: 11px;")
        bar.addWidget(self._range_label)

        bar.addSpacing(6)

        _btn_style = (
            "QPushButton { color: #99dd99; background: #0f2a0f;"
            " border: 1px solid #2a5a2a; border-radius: 3px; font-size: 14px; }"
            "QPushButton:hover { background: #1a4a1a; }"
            "QPushButton:pressed { background: #071507; }"
        )
        for symbol, tip, slot in [
            ("−", "Zoom out  (scroll down  /  −)", self._zoom_out),
            ("+", "Zoom in   (scroll up    /  +)", self._zoom_in),
            ("↺", "Reset view  (double-click radar)", self._reset_view),
        ]:
            btn = QPushButton(symbol)
            btn.setFixedSize(26, 24)
            btn.setToolTip(tip)
            btn.setStyleSheet(_btn_style)
            btn.clicked.connect(slot)
            bar.addWidget(btn)

        bar_widget = QWidget()
        bar_widget.setLayout(bar)
        bar_widget.setStyleSheet("background: #050f05;")
        root.addWidget(bar_widget)

        # --- Radar canvas ---
        self._radar = RadarView()
        self._radar.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        root.addWidget(self._radar)

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        aircraft = self._snapshot_fn()
        self._radar.set_aircraft(aircraft)
        n = len(aircraft)
        self._count_label.setText(f"{n} aircraft" if n else "")
        r = self._radar._range_nm
        self._range_label.setText(f"{r:.0f} nm" if r >= 10 else f"{r:.1f} nm")

    # ------------------------------------------------------------------
    # Zoom / pan actions
    # ------------------------------------------------------------------

    def _zoom_in(self) -> None:
        self._radar._range_nm = max(5.0, self._radar._range_nm * 0.8)
        self._radar.update()

    def _zoom_out(self) -> None:
        self._radar._range_nm = min(500.0, self._radar._range_nm * 1.25)
        self._radar.update()

    def _reset_view(self) -> None:
        self._radar.reset_view()
        self._range_label.setText("50 nm")

    # ------------------------------------------------------------------
    # Location resolution
    # ------------------------------------------------------------------

    def _start_location(self) -> None:
        # 1. Manual override in preferences
        s = QSettings("NeoSCAN", "NeoSCAN")
        lat_s = s.value("adsb/home_lat", "")
        lon_s = s.value("adsb/home_lon", "")
        if lat_s and lon_s:
            try:
                self._set_home(float(lat_s), float(lon_s), "manual")
                return
            except ValueError:
                pass

        # 2. System GPS via QtPositioning (if available)
        try:
            from PyQt6.QtPositioning import QGeoPositionInfoSource
            src = QGeoPositionInfoSource.createDefaultSource(self)
            if src is not None:
                src.positionUpdated.connect(self._on_gps_fix)
                src.startUpdates()
                self._gps_source = src
        except (ImportError, Exception):
            pass

        # 3. IP geolocation — runs in parallel; only accepted if GPS hasn't fired
        self._fetcher = _LocationFetcher(self)
        self._fetcher.found.connect(self._on_ip_location)
        self._fetcher.failed.connect(self._on_location_failed)
        self._fetcher.start()

    def _on_gps_fix(self, info) -> None:
        try:
            coord = info.coordinate()
            if coord.isValid():
                self._set_home(coord.latitude(), coord.longitude(), "GPS")
                if self._gps_source:
                    self._gps_source.stopUpdates()
        except Exception:
            pass

    def _on_ip_location(self, lat: float, lon: float, source: str) -> None:
        if not self._home_set:
            self._set_home(lat, lon, source)

    def _on_location_failed(self) -> None:
        if not self._home_set:
            self._loc_label.setText(
                "Location unavailable — set coordinates in Preferences → ADS-B"
            )
            self._loc_label.setStyleSheet("color: #c06000; font-size: 11px;")

    def _set_home(self, lat: float, lon: float, source: str) -> None:
        self._home_set = True
        self._radar.set_home(lat, lon)
        ns = "N" if lat >= 0 else "S"
        ew = "E" if lon >= 0 else "W"
        self._loc_label.setText(
            f"{abs(lat):.3f}°{ns}  {abs(lon):.3f}°{ew}  ({source})"
        )
        color = {
            "GPS":    "#00c0c0",
            "manual": "#00c000",
        }.get(source, "#888888")
        self._loc_label.setStyleSheet(f"color: {color}; font-size: 11px;")
