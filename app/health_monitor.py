"""
Process health sampling — main-thread responsiveness and CPU usage.

Background
----------
On 2026-08-14 the audio capture path lost ~40-50% of its frames for five hours
(13:00-17:59). The recorder's own watchdog correctly attributed it to
``callback body slow vs buffer period — suspect GIL/CPU starvation``, but the
culprit could never be identified: only ``app.audio.*`` emits log records, so
there was no record of what the rest of the process was doing at the time.

This module closes that gap. It samples two things the audio watchdog cannot
see, and writes them to the same log:

* **Event-loop stall** — how late a fixed-interval main-thread timer actually
  fires. A timer asked to fire every second that arrives 300 ms late means the
  main thread was blocked for 300 ms, and a Python audio callback needing the
  GIL was blocked along with it. This is the direct counterpart to the
  recorder's "callback body slow" measurement.
* **Process CPU utilisation** — CPU-seconds burned per wall-second, across all
  threads, expressed in cores. Distinguishes "this process is saturating the
  machine" from "something else on the machine is".

Both are cheap: one timer tick per second doing arithmetic. Stdlib only —
``time.process_time()`` and ``os.getloadavg()`` — so this adds no dependency.

The monitor is diagnostic, not corrective; it changes no behaviour beyond
writing log lines.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Callable, Optional

from PyQt6.QtCore import QObject, Qt, QTimer

log = logging.getLogger(__name__)

# How often we sample. One second is frequent enough to catch the multi-second
# stalls that damage capture, and cheap enough to leave running permanently.
SAMPLE_INTERVAL_MS = 1000

# A tick arriving this much later than requested means the main thread was
# blocked for at least that long. The audio buffer period is ~10.7 ms, so
# anything at this scale has already cost us frames.
_STALL_WARN_MS = 250.0

# Don't emit more than one stall warning per this many seconds; a sustained
# stall would otherwise flood the log (the periodic summary still reports it).
_STALL_WARN_COOLDOWN_SEC = 30.0

# How often to write the routine summary line, even when nothing is wrong. The
# steady-state baseline is what makes a later degradation obvious.
_SUMMARY_INTERVAL_SEC = 60.0


class HealthMonitor(QObject):
    """Samples main-thread stall and process CPU, and logs both.

    Create once, after the main window exists, and call :meth:`start`. The
    timer lives on the main thread deliberately — measuring how late *it* fires
    is the measurement.
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(SAMPLE_INTERVAL_MS)
        # Precise timing matters: coarse timers are allowed to drift by design,
        # which would show up as phantom "stalls".
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._sample)

        self._context_provider: Optional[Callable[[], str]] = None

        self._last_wall: float = 0.0
        self._last_cpu: float = 0.0
        self._last_summary: float = 0.0
        self._last_stall_warn: float = 0.0

        # Accumulators for the current summary window.
        self._stalls_ms: list[float] = []
        self._cpu_samples: list[float] = []
        self._worst_stall_ms: float = 0.0

        self._cores = os.cpu_count() or 1

    def set_context_provider(self, provider: Callable[[], str]) -> None:
        """Supply a callable returning a short string describing UI state.

        Used to record which screen was in front when a stall happened — the
        single most useful fact for narrowing a CPU regression to a panel, and
        exactly what was missing when 2026-08-14 was investigated. The provider
        must be cheap and must not raise; failures are swallowed.
        """
        self._context_provider = provider

    def start(self) -> None:
        now = time.monotonic()
        self._last_wall = now
        self._last_cpu = time.process_time()
        self._last_summary = now
        self._timer.start()
        log.info(
            "HealthMonitor: sampling every %d ms (%d cores); "
            "stall warnings above %.0f ms",
            SAMPLE_INTERVAL_MS, self._cores, _STALL_WARN_MS,
        )

    def stop(self) -> None:
        self._timer.stop()

    # -- internals ---------------------------------------------------------

    def _context(self) -> str:
        if self._context_provider is None:
            return ""
        try:
            return self._context_provider() or ""
        except Exception:  # diagnostics must never break the app
            return ""

    def _sample(self) -> None:
        now = time.monotonic()
        cpu = time.process_time()

        elapsed_ms = (now - self._last_wall) * 1000.0
        # Lateness beyond the interval we asked for == time the main thread was
        # not free to run us.
        stall_ms = max(0.0, elapsed_ms - SAMPLE_INTERVAL_MS)

        cpu_delta = cpu - self._last_cpu
        wall_delta = now - self._last_wall
        cores_used = (cpu_delta / wall_delta) if wall_delta > 0 else 0.0

        self._last_wall = now
        self._last_cpu = cpu

        self._stalls_ms.append(stall_ms)
        self._cpu_samples.append(cores_used)
        self._worst_stall_ms = max(self._worst_stall_ms, stall_ms)

        if stall_ms >= _STALL_WARN_MS and (
            now - self._last_stall_warn >= _STALL_WARN_COOLDOWN_SEC
        ):
            self._last_stall_warn = now
            ctx = self._context()
            log.warning(
                "HealthMonitor: main thread stalled %.0f ms (timer asked for "
                "%d ms, arrived at %.0f ms) — a Python audio callback needing "
                "the GIL was blocked for the same period, so capture frames "
                "may have been dropped. Process CPU %.2f cores of %d.%s",
                stall_ms, SAMPLE_INTERVAL_MS, elapsed_ms, cores_used,
                self._cores, f" Active UI: {ctx}." if ctx else "",
            )

        if now - self._last_summary >= _SUMMARY_INTERVAL_SEC:
            self._emit_summary(now)

    def _emit_summary(self, now: float) -> None:
        stalls = self._stalls_ms or [0.0]
        cpus = self._cpu_samples or [0.0]
        ordered = sorted(stalls)
        median_stall = ordered[len(ordered) // 2]
        worst = self._worst_stall_ms
        mean_cpu = sum(cpus) / len(cpus)
        peak_cpu = max(cpus)
        ctx = self._context()

        # Escalate the routine line when the window contained real stalling, so
        # a degradation is visible at WARNING without trawling INFO records.
        emit = log.warning if worst >= _STALL_WARN_MS else log.info
        emit(
            "HealthMonitor: over %.0fs — main-thread stall median %.1f ms, "
            "worst %.0f ms; process CPU mean %.2f / peak %.2f cores of %d%s%s",
            now - self._last_summary, median_stall, worst, mean_cpu, peak_cpu,
            self._cores, self._loadavg_suffix(),
            f"; active UI: {ctx}" if ctx else "",
        )

        self._last_summary = now
        self._stalls_ms.clear()
        self._cpu_samples.clear()
        self._worst_stall_ms = 0.0

    def _loadavg_suffix(self) -> str:
        """System-wide 1-minute load average, when the platform has one.

        Distinguishes "NeoSCAN is the CPU hog" from "the whole machine is
        loaded" — the question left unanswered on 2026-08-14. Absent on
        Windows, where getloadavg() does not exist.
        """
        try:
            one, _, _ = os.getloadavg()
        except (OSError, AttributeError):
            return ""
        return f"; system load {one:.2f}"
