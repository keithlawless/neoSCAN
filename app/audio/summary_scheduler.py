"""
SummaryScheduler — fires once a day (just after midnight) to generate the
previous day's HTML summary, and on startup catches up any missing days.

Wired into MainWindow. Reads its config from QSettings on every fire so
preference changes take effect without restart.
"""
from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path
from typing import Iterable, Optional

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from app.audio.summary_generator import (
    DEFAULT_MODEL,
    DEFAULT_REPORT_DIR,
    SummaryError,
    SummaryGenerator,
)
from app.ui.settings.preferences_dialog import load_prefs

log = logging.getLogger(__name__)

_DEFAULT_TRANSCRIPT_DIR = str(Path.home() / "Documents" / "NeoSCAN" / "Transcripts")


class _SummaryWorker(QThread):
    """Generate reports for a list of dates serially. Stops on first failure
    so a bad API key doesn't trigger a popup storm during catch-up."""

    finished_one = pyqtSignal(object)        # Path
    failed_one = pyqtSignal(object, str)     # _dt.date, error message

    def __init__(
        self,
        generator: SummaryGenerator,
        dates: Iterable[_dt.date],
        transcript_dir: str,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._generator = generator
        self._dates = list(dates)
        self._transcript_dir = transcript_dir

    def run(self) -> None:
        for d in self._dates:
            try:
                path = self._generator.generate(d, self._transcript_dir)
                self.finished_one.emit(path)
            except SummaryError as exc:
                log.error("SummaryWorker: failed for %s: %s", d, exc)
                self.failed_one.emit(d, str(exc))
                return
            except Exception as exc:
                log.exception("SummaryWorker: unexpected failure for %s", d)
                self.failed_one.emit(d, f"Unexpected error: {exc}")
                return


class SummaryScheduler(QObject):
    """
    Drives daily summary generation:
      • trigger_catch_up() — scan transcripts dir for past days missing a report
      • internal QTimer    — fires shortly after midnight each day
    """

    summary_ready = pyqtSignal(object)        # Path
    summary_failed = pyqtSignal(object, str)  # _dt.date, error message
    batch_finished = pyqtSignal()             # one batch (catch-up or midnight) done

    # Re-arm the midnight timer this many seconds after midnight to avoid
    # firing while the date hasn't ticked over yet on slow systems.
    _MIDNIGHT_OFFSET_SEC = 60

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._worker: Optional[_SummaryWorker] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_midnight)
        self._schedule_next_midnight()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def trigger_catch_up(self) -> None:
        """Generate reports for any past day with a transcript but no report."""
        cfg = self._load_config()
        if cfg is None:
            return
        gen, transcript_dir = cfg
        dates = self._missing_dates(gen, transcript_dir)
        if dates:
            log.info("SummaryScheduler: catch-up generating %d report(s): %s",
                     len(dates), [d.isoformat() for d in dates])
            self._spawn(gen, dates, transcript_dir)

    def shutdown(self) -> None:
        self._timer.stop()
        if self._worker_is_alive():
            if not self._worker.wait(5000):
                log.warning("SummaryScheduler: worker did not stop in time — terminating")
                self._worker.terminate()
                self._worker.wait(2000)
        self._worker = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_midnight(self) -> None:
        cfg = self._load_config()
        if cfg is not None:
            gen, transcript_dir = cfg
            yesterday = _dt.date.today() - _dt.timedelta(days=1)
            if gen.needs_report(yesterday, transcript_dir):
                self._spawn(gen, [yesterday], transcript_dir)
        self._schedule_next_midnight()

    def _schedule_next_midnight(self) -> None:
        now = _dt.datetime.now()
        midnight = (now + _dt.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        next_fire = midnight + _dt.timedelta(seconds=self._MIDNIGHT_OFFSET_SEC)
        msec = int((next_fire - now).total_seconds() * 1000)
        # Clamp into [60s, 24h+5min] for safety against clock skew or DST jumps.
        msec = max(60_000, min(msec, 24 * 60 * 60 * 1000 + 5 * 60 * 1000))
        self._timer.start(msec)
        log.debug("SummaryScheduler: next fire in %d ms (~%.1f h)",
                  msec, msec / 3_600_000)

    def _load_config(self) -> Optional[tuple[SummaryGenerator, str]]:
        s = load_prefs()
        if not s.value("transcription/summary_enabled", False, type=bool):
            return None
        api_key = (s.value("transcription/anthropic_api_key", "") or "").strip()
        if not api_key:
            log.debug("SummaryScheduler: skipping — no API key set")
            return None
        model = s.value("transcription/anthropic_model", DEFAULT_MODEL) or DEFAULT_MODEL
        report_dir = (s.value("transcription/report_dir", "") or "").strip() or DEFAULT_REPORT_DIR
        transcript_dir = (s.value("transcription/transcript_dir", "") or "").strip() or _DEFAULT_TRANSCRIPT_DIR
        return SummaryGenerator(api_key, model, report_dir), transcript_dir

    def _missing_dates(
        self,
        generator: SummaryGenerator,
        transcript_dir: str,
    ) -> list[_dt.date]:
        td = Path(transcript_dir)
        if not td.exists():
            return []
        today = _dt.date.today()
        out: list[_dt.date] = []
        for f in td.glob("*.txt"):
            try:
                d = _dt.date.fromisoformat(f.stem)
            except ValueError:
                continue
            if d < today and generator.needs_report(d, transcript_dir):
                out.append(d)
        return sorted(out)

    def _worker_is_alive(self) -> bool:
        """True iff self._worker still references a live QThread.

        Once a previous worker has emitted finished and been deleteLater'd,
        the underlying C++ object is gone — touching .isRunning() on the
        Python wrapper raises RuntimeError. Treat that as "not alive".
        """
        if self._worker is None:
            return False
        try:
            return self._worker.isRunning()
        except RuntimeError:
            self._worker = None
            return False

    def _spawn(
        self,
        generator: SummaryGenerator,
        dates: list[_dt.date],
        transcript_dir: str,
    ) -> None:
        if self._worker_is_alive():
            log.info("SummaryScheduler: worker already running — skipping spawn")
            return
        worker = _SummaryWorker(generator, dates, transcript_dir, parent=self)
        worker.finished_one.connect(self.summary_ready.emit)
        worker.failed_one.connect(self.summary_failed.emit)
        worker.finished.connect(self.batch_finished.emit)
        # Drop our reference before deleteLater runs so a subsequent _spawn
        # doesn't dereference a destroyed C++ object.
        worker.finished.connect(self._on_worker_finished)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_worker_finished(self) -> None:
        self._worker = None
