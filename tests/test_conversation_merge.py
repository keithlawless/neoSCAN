"""Conversation merging: consecutive key-ups on the same channel within the
grace window (CONVERSATION_GAP_MS) fold into one log entry and one audio clip,
so Whisper gets more context. Recording is paused during the gap so the merged
clip stays speech-only and the reported duration stays squelch-gated.
"""
import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from app.audio.recorder import AudioRecorder
from app.ui.remote_control.log_panel import LogPanel

# Keep a live QApplication for the whole module: an unreferenced one is
# garbage-collected before the first QWidget is built, aborting the process.
_APP = QApplication.instance() or QApplication([])


# ----------------------------------------------------------------------
# AudioRecorder pause/resume
# ----------------------------------------------------------------------

def test_pause_reinserts_gap_as_silence():
    r = AudioRecorder()
    r._device_index = 0
    r._capture_rate = 16_000
    r._stream_is_live = lambda: True  # pretend the input stream is healthy

    def cb(n):  # feed n mono samples of real (non-DC) signal
        t = np.arange(n)
        sig = (0.3 * np.sin(2 * np.pi * 300 * t / 16_000)).astype(np.float32)
        r._audio_callback(sig.reshape(-1, 1), n, None, None)

    r.start_recording()
    assert r._active and r._recording
    cb(10_000)                       # speech 1

    r.pause_recording()
    assert r._active and not r._recording
    cb(10_000)                       # dropped — captured while paused
    r._pause_started -= 1.0          # simulate a 1.0 s squelch-closed gap

    r.resume_recording()
    assert r._active and r._recording
    cb(10_000)                       # speech 2

    audio = r.stop_recording()
    assert audio is not None
    # 10k speech + ~16k silence (1.0 s @ 16 kHz) + 10k speech; paused block dropped.
    assert abs(len(audio) - 36_000) <= 5
    # The middle second is the re-inserted silence; the ends carry signal.
    assert np.max(np.abs(audio[11_000:25_000])) < 1e-6
    assert np.max(np.abs(audio[:9_000])) > 0.1
    assert np.max(np.abs(audio[27_000:])) > 0.1
    assert not r._active and not r._recording


def test_gap_silence_is_capped():
    r = AudioRecorder()
    r._device_index = 0
    r._capture_rate = 16_000
    r._stream_is_live = lambda: True

    def cb(n):
        r._audio_callback((np.random.randn(n, 1) * 0.2).astype(np.float32), n, None, None)

    r.start_recording()
    cb(8_000)
    r.pause_recording()
    r._pause_started -= 30.0          # absurdly long gap
    r.resume_recording()
    cb(8_000)
    audio = r.stop_recording()
    # Silence is capped at _MAX_GAP_SILENCE_SEC (2.0 s = 32k @ 16 kHz), not 30 s.
    from app.audio.recorder import _MAX_GAP_SILENCE_SEC
    assert abs(len(audio) - (16_000 + int(_MAX_GAP_SILENCE_SEC * 16_000))) <= 5


def test_pause_resume_stop_are_noops_without_session():
    r = AudioRecorder()
    r.pause_recording()          # no active session
    assert not r._recording and not r._active
    r.resume_recording()
    assert not r._recording and not r._active
    assert r.stop_recording() is None


def _recorder_with_fake_stream(monkeypatch_open=True):
    """An AudioRecorder with a live fake stream and reopen counters, so recycling
    can be exercised without a real audio device."""
    import time
    r = AudioRecorder()
    r._device_index = 11
    r._capture_rate = 48_000
    calls = {"open": 0, "close": 0}

    class _FakeStream:
        active = True

    def fake_open():
        calls["open"] += 1
        r._stream = _FakeStream()
        r._last_callback_ts = time.monotonic()
        r._reset_delivery_window()
        r._stream_opened_at = time.monotonic()
        return True

    def fake_close():
        calls["close"] += 1
        r._stream = None

    r._open_stream = fake_open
    r._close_stream = fake_close
    r._stream = _FakeStream()
    r._last_callback_ts = time.monotonic()
    return r, calls


def test_recycle_recycles_stale_idle_stream():
    import time
    r, calls = _recorder_with_fake_stream()
    r._active = False
    r._stream_opened_at = time.monotonic() - 900     # older than _MAX_STREAM_AGE_SEC
    assert r.recycle_if_idle_and_stale() is True
    assert calls == {"open": 1, "close": 1}


def test_recycle_skips_fresh_stream():
    import time
    r, calls = _recorder_with_fake_stream()
    r._active = False
    r._stream_opened_at = time.monotonic() - 60      # well within the age limit
    assert r.recycle_if_idle_and_stale() is False
    assert calls == {"open": 0, "close": 0}


def test_recycle_never_interrupts_active_session():
    import time
    r, calls = _recorder_with_fake_stream()
    r._active = True                                 # a capture session is open
    r._stream_opened_at = time.monotonic() - 3600    # very old
    assert r.recycle_if_idle_and_stale() is False
    assert calls == {"open": 0, "close": 0}


# ----------------------------------------------------------------------
# LogPanel conversation merging
# ----------------------------------------------------------------------

class _FakeProto:
    def __init__(self):
        self.info: dict = {}

    def get_received_channel_info(self):
        return self.info


class _FakeTM(QObject):
    transcription_ready = pyqtSignal(int, str, object)
    is_enabled = True

    def __init__(self):
        super().__init__()
        self.events: list = []

    def on_transmission_started(self):
        self.events.append("start")

    def on_transmission_paused(self):
        self.events.append("pause")

    def on_transmission_resumed(self):
        self.events.append("resume")

    def on_transmission_ended(self, row, entry):
        self.events.append(("end", row))

    def maybe_recycle_stream(self):
        # Called every idle poll; a no-op for these merge tests (recycling never
        # runs while a session is active, and never emits transmission events).
        pass


class _FakeRadio:
    def __init__(self, label):
        self.label = label
        self.proto = _FakeProto()
        self.transcription_manager = _FakeTM()


def _panel_with_radio():
    panel = LogPanel()
    radio = _FakeRadio("R1")
    panel.add_radio(radio)
    panel._timer.stop()          # drive _poll manually
    panel._start_logging()
    return panel, radio


A = {"ch_name": "Framingham FD", "frequency": "154.2350", "mod": "FM"}
B = {"ch_name": "Natick PD", "frequency": "155.0100", "mod": "FM"}
OFF: dict = {}


def _step(panel, radio, info):
    radio.proto.info = info
    panel._poll()


def _hold_closed_past_debounce(panel, label="R1"):
    """Simulate squelch having stayed closed longer than the pause debounce, so
    the next empty poll pauses capture (the harness can't advance wall-clock)."""
    from app.ui.remote_control.log_panel import CAPTURE_PAUSE_DEBOUNCE_MS
    panel._squelch_closed_mono[label] -= (CAPTURE_PAUSE_DEBOUNCE_MS / 1000.0) + 0.1


def test_same_channel_keyups_merge_into_one_entry():
    panel, radio = _panel_with_radio()
    tm = radio.transcription_manager

    _step(panel, radio, A)        # start
    _step(panel, radio, A)        # ongoing
    _step(panel, radio, OFF)      # squelch close -> grace begins (no pause yet)
    _hold_closed_past_debounce(panel)
    _step(panel, radio, OFF)      # debounce elapsed -> pause
    assert len(panel._entries) == 1
    assert panel._active_entries["R1"] is not None   # still open

    _step(panel, radio, A)        # same channel back within grace -> resume
    _step(panel, radio, OFF)      # squelch close again -> grace (no pause yet)
    _hold_closed_past_debounce(panel)
    _step(panel, radio, OFF)      # debounce elapsed -> pause
    # Rewind the grace clock so the next empty poll trips finalization.
    panel._squelch_closed_mono["R1"] -= 10.0
    _step(panel, radio, OFF)      # grace elapsed -> finalize

    assert tm.events == ["start", "pause", "resume", "pause", ("end", 0)]
    assert len(panel._entries) == 1                  # one merged conversation
    assert panel._active_entries["R1"] is None
    entry = panel._entries[0]
    assert entry.end_time is not None and not entry.pending_close


def test_brief_squelch_dip_does_not_pause_capture():
    """A squelch dip shorter than the debounce must not pause capture — picket-
    fencing on a marginal/trunked signal should record straight through as one
    continuous clip rather than fragmenting into discarded sub-second pieces."""
    panel, radio = _panel_with_radio()
    tm = radio.transcription_manager

    _step(panel, radio, A)        # start
    _step(panel, radio, OFF)      # momentary dip -> grace begins, no pause yet
    _step(panel, radio, A)        # back within the debounce -> continuous

    assert tm.events == ["start"]                    # no pause, no resume
    assert "R1" not in panel._capture_paused
    assert len(panel._entries) == 1                  # still one entry
    assert not panel._entries[0].pending_close       # reopened cleanly


def test_sustained_close_pauses_only_after_debounce():
    panel, radio = _panel_with_radio()
    tm = radio.transcription_manager

    _step(panel, radio, A)        # start
    _step(panel, radio, OFF)      # first closed poll -> no pause yet
    assert tm.events == ["start"]
    assert "R1" not in panel._capture_paused

    _hold_closed_past_debounce(panel)
    _step(panel, radio, OFF)      # debounce elapsed -> pause
    assert tm.events == ["start", "pause"]
    assert "R1" in panel._capture_paused


def test_different_channel_hands_off_within_grace():
    panel, radio = _panel_with_radio()
    tm = radio.transcription_manager

    _step(panel, radio, A)        # start A
    _step(panel, radio, OFF)      # squelch close (within debounce -> A not paused)
    _step(panel, radio, B)        # different channel -> finalize A, start B

    # Quick hand-off: A closed for less than the debounce, so capture was never
    # paused — no "pause" event before the finalize.
    assert tm.events == ["start", ("end", 0), "start"]
    assert len(panel._entries) == 2
    assert panel._active_entries["R1"] is panel._entries[1]


def test_no_finalize_before_grace_elapses():
    panel, radio = _panel_with_radio()
    tm = radio.transcription_manager

    _step(panel, radio, A)        # start
    _step(panel, radio, OFF)      # pause + grace begins
    _step(panel, radio, OFF)      # still within grace -> no finalize
    _step(panel, radio, OFF)

    assert ("end", 0) not in tm.events
    assert panel._active_entries["R1"] is not None
