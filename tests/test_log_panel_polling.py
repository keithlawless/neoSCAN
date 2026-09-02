"""
Tests for the background scanner poll thread.

Polling used to run on a main-thread QTimer, so each tick blocked the Qt event
loop for up to three serial round-trips — and for the full command timeout when
a radio stopped answering. These tests pin the replacement: serial I/O happens
off the main thread, results arrive on the main thread, and a slow radio no
longer stalls the UI.
"""
import threading
import time

from PyQt6.QtCore import QEventLoop, QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication

from app.ui.remote_control.log_panel import LogPanel

_APP = QApplication.instance() or QApplication([])


class _FakeProto:
    def __init__(self, info=None, delay=0.0):
        self.info = info or {}
        self.delay = delay
        self.calls = 0
        self.threads: set = set()

    def get_received_channel_info(self):
        self.calls += 1
        self.threads.add(threading.current_thread().name)
        if self.delay:
            time.sleep(self.delay)
        return self.info or None

    def wait_until_idle(self, timeout=5.0):
        return True


class _FakeTM(QObject):
    transcription_ready = pyqtSignal(int, str, object)
    is_enabled = True

    def on_transmission_started(self): pass
    def on_transmission_paused(self): pass
    def on_transmission_resumed(self): pass
    def on_transmission_ended(self, row, entry): pass
    def maybe_recycle_stream(self): pass


class _FakeRadio:
    def __init__(self, label, info=None, delay=0.0):
        self.label = label
        self.proto = _FakeProto(info, delay)
        self.transcription_manager = _FakeTM()


def _pump(ms: int) -> None:
    """Run the Qt event loop for `ms`, so queued cross-thread signals deliver."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


A = {"ch_name": "Westboro PD", "frequency": "154.2350", "mod": "FM"}


def test_polling_happens_off_the_main_thread():
    panel = LogPanel()
    radio = _FakeRadio("R1", info=A)
    panel.add_radio(radio)
    try:
        _pump(600)
        assert radio.proto.calls > 0, "poller never ran"
        main_name = threading.current_thread().name
        assert radio.proto.threads, "no thread recorded"
        assert main_name not in radio.proto.threads, (
            f"serial I/O ran on the main thread: {radio.proto.threads}"
        )
    finally:
        panel.shutdown()


def test_results_are_delivered_to_the_main_thread():
    panel = LogPanel()
    radio = _FakeRadio("R1", info=A)
    panel.add_radio(radio)
    seen: list = []
    panel.channel_info_updated.connect(
        lambda label, info: seen.append((label, threading.current_thread().name))
    )
    try:
        _pump(600)
        assert seen, "no poll results delivered"
        main_name = threading.current_thread().name
        assert all(t == main_name for _, t in seen), (
            "state machine ran off the main thread"
        )
    finally:
        panel.shutdown()


def test_slow_radio_does_not_block_the_main_thread():
    """A radio that takes ~300ms per poll must not stall the event loop."""
    panel = LogPanel()
    radio = _FakeRadio("R1", info=A, delay=0.3)
    panel.add_radio(radio)
    ticks = []
    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    try:
        _pump(700)
        timer.stop()
        assert radio.proto.calls > 0
        # ~35 ticks are possible in 700ms; allow generous slack for CI, but a
        # blocked main thread would produce only a handful.
        assert len(ticks) > 15, f"main thread starved: only {len(ticks)} ticks"
        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        assert max(gaps) < 0.25, f"main thread blocked for {max(gaps):.3f}s"
    finally:
        panel.shutdown()


def test_paused_radio_is_not_polled():
    panel = LogPanel()
    radio = _FakeRadio("R1", info=A)
    panel.add_radio(radio)
    try:
        panel.pause_polling("R1")
        _pump(200)
        before = radio.proto.calls
        _pump(500)
        assert radio.proto.calls == before, "paused radio was still polled"
        panel.resume_polling("R1")
        _pump(500)
        assert radio.proto.calls > before, "resume did not restart polling"
    finally:
        panel.shutdown()


def test_shutdown_stops_the_thread_and_can_restart():
    panel = LogPanel()
    radio = _FakeRadio("R1", info=A)
    panel.add_radio(radio)
    _pump(300)
    panel.shutdown()
    assert not panel._poller.isRunning()

    settled = radio.proto.calls
    _pump(300)
    assert radio.proto.calls == settled, "poller kept running after shutdown"

    # A radio reconnecting must bring polling back (the stop flag is cleared).
    panel.add_radio(_FakeRadio("R2", info=A))
    try:
        _pump(400)
        assert panel._poller.isRunning()
    finally:
        panel.shutdown()


def test_connection_lost_is_reported_once():
    from app.serial.protocol import SerialConnectionLost

    panel = LogPanel()
    radio = _FakeRadio("R1", info=A)

    def boom():
        raise SerialConnectionLost("device gone")

    radio.proto.get_received_channel_info = boom
    panel.add_radio(radio)
    lost: list = []
    panel.radio_connection_lost.connect(lost.append)
    try:
        _pump(500)
        assert "R1" in lost
    finally:
        panel.shutdown()
