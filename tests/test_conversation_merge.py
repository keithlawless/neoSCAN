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

def test_pause_excises_gap_resume_continues_same_clip():
    r = AudioRecorder()
    r._device_index = 0
    r._capture_rate = 16_000
    r._stream_is_live = lambda: True  # pretend the input stream is healthy

    def cb(n):  # feed n mono samples through the input callback
        r._audio_callback(np.full((n, 1), 0.3, np.float32), n, None, None)

    r.start_recording()
    assert r._active and r._recording
    cb(10_000)

    r.pause_recording()
    assert r._active and not r._recording
    cb(10_000)  # dropped — captured while paused

    r.resume_recording()
    assert r._active and r._recording
    cb(10_000)

    audio = r.stop_recording()
    assert audio is not None
    # Only the two un-paused callbacks are retained (20k), not the paused one.
    assert abs(len(audio) - 20_000) <= 2
    assert not r._active and not r._recording


def test_pause_resume_stop_are_noops_without_session():
    r = AudioRecorder()
    r.pause_recording()          # no active session
    assert not r._recording and not r._active
    r.resume_recording()
    assert not r._recording and not r._active
    assert r.stop_recording() is None


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


def test_same_channel_keyups_merge_into_one_entry():
    panel, radio = _panel_with_radio()
    tm = radio.transcription_manager

    _step(panel, radio, A)        # start
    _step(panel, radio, A)        # ongoing
    _step(panel, radio, OFF)      # squelch close -> pause + grace
    assert len(panel._entries) == 1
    assert panel._active_entries["R1"] is not None   # still open

    _step(panel, radio, A)        # same channel back within grace -> resume
    _step(panel, radio, OFF)      # squelch close again -> pause
    # Rewind the grace clock so the next empty poll trips finalization.
    panel._squelch_closed_mono["R1"] -= 10.0
    _step(panel, radio, OFF)      # grace elapsed -> finalize

    assert tm.events == ["start", "pause", "resume", "pause", ("end", 0)]
    assert len(panel._entries) == 1                  # one merged conversation
    assert panel._active_entries["R1"] is None
    entry = panel._entries[0]
    assert entry.end_time is not None and not entry.pending_close


def test_different_channel_hands_off_within_grace():
    panel, radio = _panel_with_radio()
    tm = radio.transcription_manager

    _step(panel, radio, A)        # start A
    _step(panel, radio, OFF)      # pause A (grace)
    _step(panel, radio, B)        # different channel -> finalize A, start B

    assert tm.events == ["start", "pause", ("end", 0), "start"]
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
