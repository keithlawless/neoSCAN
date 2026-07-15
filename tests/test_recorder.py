"""Tests for AudioRecorder channel reduction (_to_mono).

These exercise the stereo channel-pick path that regressed in c188a06, where
per-callback argmax selection flipped to a dead channel during inter-word gaps
and lagged on word onsets, fragmenting the captured audio. The latch + margin
logic must emit the live channel continuously without dropping quiet blocks.
"""
import numpy as np

from app.audio.recorder import AudioRecorder


def _block(*channels: np.ndarray) -> np.ndarray:
    """Stack 1-D channel arrays into a (frames, channels) float32 block."""
    return np.stack(channels, axis=1).astype(np.float32)


def _loud(n=320, amp=0.5):
    # A fluctuating tone — non-zero mean removal leaves real signal.
    t = np.arange(n)
    return (amp * np.sin(2 * np.pi * 440 * t / 16000)).astype(np.float32)


def _silent(n=320):
    return np.zeros(n, dtype=np.float32)


def _fresh_recorder() -> AudioRecorder:
    r = AudioRecorder()
    r._channels = 2
    r._ch_energy = None
    r._live_ch = 0
    return r


def test_live_channel_zero_dead_channel_one():
    """Signal on ch0, ch1 dead: every block must come out as ch0, including
    silent gaps (no flip to the dead channel)."""
    r = _fresh_recorder()
    seq = [_loud(), _silent(), _silent(), _loud(), _silent(), _loud()]
    for sig in seq:
        out = r._to_mono(_block(sig, _silent()))
        # Output should track ch0: loud blocks stay loud, silent blocks silent.
        assert float(np.max(np.abs(out))) == float(np.max(np.abs(sig - sig.mean())))


def test_latches_onto_live_channel_one():
    """Signal on ch1, ch0 dead: after the first loud block it must latch ch1
    and never drop a subsequent block to the dead ch0."""
    r = _fresh_recorder()
    # First block is loud on ch1 — selection should land on ch1.
    r._to_mono(_block(_silent(), _loud()))
    assert r._live_ch == 1
    # A following silent gap must hold ch1, not flip back to ch0.
    out = r._to_mono(_block(_silent(), _silent()))
    assert r._live_ch == 1
    assert float(np.max(np.abs(out))) == 0.0
    # And a resumed word on ch1 comes through at full amplitude (no onset drop).
    out = r._to_mono(_block(_silent(), _loud()))
    assert float(np.max(np.abs(out))) > 0.4


def test_no_flip_on_noise_floor_tie():
    """During silence both channels sit at the noise floor; tiny energy
    differences must not switch the selected channel."""
    r = _fresh_recorder()
    r._to_mono(_block(_loud(), _silent()))   # latch ch0
    rng = np.random.default_rng(0)
    flips = 0
    for _ in range(200):
        n0 = (rng.standard_normal(320) * 1e-4).astype(np.float32)
        n1 = (rng.standard_normal(320) * 1.1e-4).astype(np.float32)  # ch1 slightly louder noise
        r._to_mono(_block(n0, n1))
        if r._live_ch != 0:
            flips += 1
    assert flips == 0, "channel flipped on a noise-floor difference below the switch margin"


def test_switches_when_other_channel_decisively_louder():
    """A sustained, clearly louder channel should win even if another was
    latched first (e.g. user re-patches the cable to the other channel)."""
    r = _fresh_recorder()
    r._to_mono(_block(_loud(amp=0.3), _silent()))   # latch ch0
    assert r._live_ch == 0
    # ch1 now carries strong audio, ch0 dead — sustained over many blocks.
    for _ in range(50):
        r._to_mono(_block(_silent(), _loud(amp=0.8)))
    assert r._live_ch == 1


def test_mono_passthrough_dc_blocked():
    """1-D input is DC-blocked and returned unchanged in shape."""
    r = _fresh_recorder()
    sig = _loud() + 0.2  # add DC offset
    out = r._to_mono(sig)
    assert out.ndim == 1
    assert abs(float(out.mean())) < 1e-6   # DC removed


# ----------------------------------------------------------------------
# Per-recording under-delivery detection (stop_recording reopens a stream
# that delivered far fewer frames than wall-clock during the capture — the
# blind spot that truncated a 19s transmission into a choppy 4s clip while
# the idle-gap watchdog in start_recording saw a healthy stream).
# ----------------------------------------------------------------------

def _capture_recorder() -> AudioRecorder:
    r = AudioRecorder()
    r._device_index = 0
    r._capture_rate = 48_000
    r._stream = object()               # pretend an input stream is open
    r._stream_is_live = lambda: True   # and healthy at start_recording
    r._reopens = 0

    def _fake_open() -> bool:
        r._reopens += 1
        r._stream = object()
        return True

    r._open_stream = _fake_open        # count reopens instead of touching HW
    r._close_stream = lambda: None
    return r


def _feed(r: AudioRecorder, n: int) -> None:
    """Deliver n native-rate frames through the input callback."""
    t = np.arange(n)
    sig = (0.3 * np.sin(2 * np.pi * 300 * t / 48_000)).astype(np.float32)
    r._audio_callback(sig.reshape(-1, 1), n, None, None)


def test_underdelivered_capture_reopens_stream():
    import time
    r = _capture_recorder()
    r._cb_window_start = time.monotonic()   # healthy idle window at start
    r.start_recording()
    _feed(r, 200_000)                        # ~4.2s of 48k frames actually arrived
    r._cb_window_start -= 19.0               # but they spanned ~19s of wall-clock
    audio = r.stop_recording()
    assert audio is not None                 # clip is still returned
    assert r._reopens == 1                   # degraded stream reopened for next time


def test_full_delivery_does_not_reopen():
    import time
    r = _capture_recorder()
    r._cb_window_start = time.monotonic()
    r.start_recording()
    _feed(r, 240_000)                        # 5s of 48k frames
    r._cb_window_start -= 5.0                 # delivered over ~5s wall — full rate
    audio = r.stop_recording()
    assert audio is not None
    assert r._reopens == 0                    # healthy stream left alone


def test_short_capture_not_judged():
    """A capture shorter than the measurement window is never flagged."""
    import time
    r = _capture_recorder()
    r._cb_window_start = time.monotonic()
    r.start_recording()
    _feed(r, 150_000)                         # ~3.1s @48k → ~1.04s @16k
    r._cb_window_start -= 2.0                  # under _UNDERDELIVERY_WINDOW_SEC
    audio = r.stop_recording()
    assert audio is not None
    assert r._reopens == 0
