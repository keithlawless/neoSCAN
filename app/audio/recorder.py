"""
AudioRecorder — captures audio from a sounddevice input device.

Designed for use with the NeoSCAN transmission logger: call start_recording()
when a transmission begins and stop_recording() when it ends to get the
captured audio as a float32 numpy array at 16 kHz (Whisper's native rate).
"""
from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000       # Hz — Whisper's native sample rate; output of the
                           # capture pipeline after resampling
MIN_DURATION_SEC = 1.0     # discard recordings shorter than this
_NOISE_PROFILE_SECS = 2.0  # seconds of squelch audio used to build noise profile
# A healthy input stream fires its callback continuously (even between
# recordings). If no callback has arrived in this many seconds the stream is
# considered dead — e.g. after a system sleep/resume or USB re-enumeration.
_STREAM_STALE_SEC = 2.0
# Under-delivery watchdog. A persistent input stream that has been open a long
# time (days of uptime, sleep/wake cycles) can degrade into delivering only a
# fraction of its frames per wall-second while still firing callbacks — so
# _STREAM_STALE_SEC above never trips, yet a 25 s transmission is captured as
# ~5 s. We measure the effective sample rate over a rolling window and reopen
# the stream when it falls below this fraction of the rate the device claims.
_UNDERDELIVERY_FRACTION = 0.6   # reopen if effective rate < 60% of _capture_rate
_UNDERDELIVERY_WINDOW_SEC = 4.0  # minimum callback history before judging
# Proactive stream recycling. The under-delivery reopen above is reactive — it
# only heals a stream once it has already degraded, which for a long transmission
# happens mid-clip and loses audio. The real trigger is age: a persistent stream
# stays healthy for hours, then degrades. So we also recycle a stream once it has
# been open this long, but only while idle (between transmissions), so it never
# ages into the degraded state and a recycle never interrupts a live recording.
_MAX_STREAM_AGE_SEC = 600.0     # recycle an idle stream older than this (10 min)
# ADC-clock gap deadband. Capture timestamps carry sub-millisecond jitter, so
# only a gap larger than this counts as the HAL actually skipping hardware
# buffers. A real dropped buffer is one buffer period (several ms) or more.
_ADC_GAP_EPSILON_SEC = 0.001
# Upper bound on how long stop_recording waits for the capture worker to drain
# the last queued blocks into the clip. The frames are safe in the queue; this
# only caps the wait so a starved worker cannot hang the caller (Qt) thread.
_CAPTURE_DRAIN_TIMEOUT_SEC = 2.0
# When key-ups are merged into one conversation clip, the squelch-closed gap
# between them is re-inserted as clean silence (up to this cap) instead of being
# spliced out. Deleting the pauses made conversations run together and sound
# sped up, hurting intelligibility and Whisper's utterance segmentation; a real
# pause preserves natural pacing and gives Whisper a segmentation cue.
_MAX_GAP_SILENCE_SEC = 2.0


def _resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Resample 1-D float32 audio from src_rate to dst_rate.

    Uses scipy's polyphase resampler (anti-aliased) when available, falling
    back to numpy linear interpolation otherwise. Capturing at the device's
    native rate and resampling here — rather than asking the input stream for
    16 kHz directly — avoids the garbled/aliased audio that cheap USB capture
    dongles produce when forced to a sample rate their hardware doesn't truly
    support (they advertise it but resample badly or not at all).
    """
    if audio.size == 0 or src_rate == dst_rate:
        return audio.astype(np.float32, copy=False)
    try:
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(int(src_rate), int(dst_rate))
        return resample_poly(audio, dst_rate // g, src_rate // g).astype(np.float32)
    except Exception:
        n_out = int(round(audio.size * dst_rate / src_rate))
        if n_out <= 0:
            return np.zeros(0, dtype=np.float32)
        x_old = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
        return np.interp(x_new, x_old, audio).astype(np.float32)


class AudioRecorder:
    """
    Captures audio from a sounddevice input in float32 at 16 kHz.

    The stream is opened once when start_recording() is first called and kept
    open persistently between recordings.  The callback is a no-op when
    _recording is False, so there is no meaningful CPU overhead.

    Keeping the stream open eliminates the create/destroy cycle that caused a
    SIGSEGV: CoreAudio's IO thread can fire one more hardware-scheduled callback
    after Pa_AbortStream returns, and if the stream has already been closed (and
    its libffi closure freed) by that point, the callback crashes at 0x4.  With
    a persistent stream the closure is never freed while the stream is active.

    Thread safety: _chunks and _recording are accessed from both the Qt main
    thread (start/stop calls) and the C audio thread (callback).  _chunks is
    protected by _lock.  _recording is a plain bool; writes from the main thread
    are visible to the callback quickly enough for this use case — the worst
    case is one extra chunk appended after stop_recording() returns, which is
    harmless because we take a snapshot of _chunks under the lock.

    The input stream is opened at the device's native sample rate and audio is
    resampled to SAMPLE_RATE (16 kHz) before leaving the recorder — recordings
    in stop_recording(), pass-through chunks in the processing thread. Forcing
    the stream to 16 kHz instead produced garbled audio on USB capture dongles
    that don't truly support that rate.

    Pass-through pipeline:
        input callback → _pt_raw_buffer → processing thread (resample + NR) → _pt_out_buffer → output callback

    The processing thread accumulates the first _NOISE_PROFILE_SECS of audio
    as a noise profile, then applies noisereduce per-chunk.  Audio passes
    through unprocessed while the profile is being captured.  If noisereduce
    is not installed, audio passes through without filtering.
    """

    def __init__(self) -> None:
        self._device_index: Optional[int] = None
        self._stream = None
        # Number of input channels the stream is opened with. Many USB cards
        # expose two input channels even when only one carries the line signal,
        # so we capture all of them and reduce to a single live channel.
        self._channels: int = 1
        # Sample rate the input stream is actually opened at — the device's
        # native/default rate. Captured audio is resampled to SAMPLE_RATE
        # (16 kHz) before it leaves the recorder.
        self._capture_rate: int = SAMPLE_RATE
        # Slow per-channel energy estimate used to pick the channel that
        # actually carries audio (a dead/unconnected channel is ignored).
        self._ch_energy: Optional[np.ndarray] = None
        # Index of the channel currently being emitted. Latched: it is held
        # through silence and only changes when another channel is decisively
        # and sustainedly louder, so inter-word gaps and word onsets are never
        # dropped by selection flipping to a dead channel.
        self._live_ch: int = 0
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._recording = False
        # A capture session is open (start_recording called, stop_recording not
        # yet). Distinct from _recording so a session can be paused between
        # key-ups within a conversation: _recording goes False (stop appending)
        # while _active stays True and the buffered chunks are retained.
        self._active = False
        # monotonic time the current pause began (0.0 = not paused). Used to
        # re-insert the gap as silence on resume so merged clips keep pacing.
        self._pause_started = 0.0
        # monotonic timestamp of the most recent input callback; used to detect
        # a stream that has silently stopped delivering audio (sleep/resume).
        self._last_callback_ts: float = 0.0
        # Rolling frame-delivery accounting for the under-delivery watchdog:
        # frames seen since the window opened, and when it opened. Lets us
        # compute the effective sample rate and catch a degraded stream that
        # still fires callbacks but delivers far fewer frames than its rate.
        self._cb_frames: int = 0
        self._cb_window_start: float = 0.0
        # ADC-clock delivery diagnostics (per recording window). These separate
        # the two candidate causes of under-delivery: hardware/HAL loss (the
        # device's ADC capture timestamp jumps more than the frames delivered
        # imply — frames vanished upstream of us) versus callback starvation
        # (our Python callback runs slow on the real-time audio thread, e.g.
        # GIL/CPU contention, and misses IO deadlines). Reset with the delivery
        # window; summarized by _delivery_diagnostics() when a capture degrades.
        self._prev_adc_time: float = 0.0        # previous callback's ADC timestamp
        self._prev_cb_n: int = 0                # previous callback's frame count
        self._diag_skipped_frames: float = 0.0  # frames the HAL skipped (ADC gap)
        self._diag_max_body_ms: float = 0.0     # slowest callback body seen
        self._diag_num_cb: int = 0              # callbacks counted this window
        # monotonic time the current input stream was opened; drives proactive
        # recycling of a stream that has been open long enough to risk degrading.
        self._stream_opened_at: float = 0.0
        # smoothed input level in [0, 1] (peak with fast attack / slow decay),
        # updated on every callback; drives the control-panel level meter.
        self._level: float = 0.0

        # Pass-through state
        self._passthrough: bool = False
        self._out_device_index: Optional[int] = None
        self._out_stream = None
        self._pt_raw_buffer: collections.deque = collections.deque()
        self._pt_out_buffer: collections.deque = collections.deque()

        # Noise profile (built from the first few seconds of pass-through audio)
        self._noise_profile: Optional[np.ndarray] = None
        self._noise_accum: list[np.ndarray] = []
        self._noise_ready: bool = False

        # Processing thread
        self._pt_thread: Optional[threading.Thread] = None
        self._pt_thread_running: bool = False

        # Capture-processing thread. The input callback does the minimum possible
        # work — copy the raw block and enqueue it — so a slow numpy op or GIL
        # contention on the real-time audio thread cannot make it miss the IO
        # deadline and drop hardware frames (the GIL/CPU-starvation failure the
        # ADC-clock diagnostic surfaced). This thread drains the queue and does
        # the mono reduction, level metering, channel selection, and chunk /
        # pass-through routing off the real-time thread.
        self._cap_raw_buffer: collections.deque = collections.deque()
        self._cap_thread: Optional[threading.Thread] = None
        self._cap_thread_running: bool = False
        # Monotonic counters coordinating stop_recording's drain: the callback is
        # the sole writer of _cap_enqueued and the processor the sole writer of
        # _cap_processed, so stop_recording can wait until every block captured
        # before the stop has reached _chunks without a lock.
        self._cap_enqueued: int = 0
        self._cap_processed: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_passthrough(self, enabled: bool, output_device_index: Optional[int]) -> None:
        """Enable or disable real-time audio pass-through to an output device."""
        if not enabled or output_device_index is None:
            self._passthrough = False
            self._stop_pt_thread()
            self._close_out_stream()
            self._pt_raw_buffer.clear()
            self._pt_out_buffer.clear()
            return
        # Reopen output stream only if device changed
        if output_device_index != self._out_device_index:
            self._passthrough = False
            self._stop_pt_thread()
            self._close_out_stream()
            self._out_device_index = output_device_index
            self._pt_raw_buffer.clear()
            self._pt_out_buffer.clear()
            self._reset_noise_profile()
            if not self._open_out_stream():
                return
            self._start_pt_thread()
        self._passthrough = True

    def recapture_noise_profile(self) -> None:
        """Discard the current noise profile and begin capturing a new one.

        Call while the scanner is in squelch for best results.  The new
        profile will be ready after _NOISE_PROFILE_SECS seconds.
        """
        self._reset_noise_profile()
        log.info("AudioRecorder: noise profile reset — capturing new profile over %.0fs",
                 _NOISE_PROFILE_SECS)

    def set_device(self, device_index: Optional[int]) -> None:
        """Set the input device index (from sounddevice.query_devices())."""
        if device_index == self._device_index:
            return
        if self._recording:
            log.warning("AudioRecorder: device changed while recording — ignoring")
            return
        # Device changed — close the persistent stream so it is reopened on
        # the new device at the next start_recording() call.
        self._close_stream()
        self._device_index = device_index

    def start_recording(self) -> None:
        """Begin accumulating audio chunks. Opens (or reopens) the stream if
        it is not currently live."""
        if self._device_index is None:
            return
        if self._recording:
            return
        # The stream is kept open persistently, but a system sleep/resume or a
        # USB re-enumeration can leave it as a non-None but dead PortAudio
        # stream that no longer delivers callbacks. Detect that and reopen so
        # capture self-heals instead of silently returning "no audio" forever.
        if not self._stream_is_live():
            if self._stream is not None:
                log.warning(
                    "AudioRecorder: input stream is no longer live (likely "
                    "system sleep/resume or device change) — reopening on "
                    "device %s", self._device_index,
                )
                self._close_stream()
            if not self._open_stream():
                return
        elif self._stream_underdelivering():
            # Stream is alive but degraded (delivering a fraction of its frames,
            # so long transmissions capture as a few seconds). It logged the
            # rate; reopen a fresh stream before this recording so it — and every
            # subsequent one — captures fully.
            self._close_stream()
            if not self._open_stream():
                return
        with self._lock:
            self._chunks = []
        # Start a fresh frame-delivery window at the moment recording begins so
        # stop_recording() can measure how fully the stream delivered *this*
        # transmission (see _recording_underdelivered). The health checks above
        # measured the idle gap before this call; this measures the capture.
        self._reset_delivery_window()
        self._active = True
        self._recording = True
        self._pause_started = 0.0
        log.debug("AudioRecorder: started on device %d", self._device_index)

    def pause_recording(self) -> None:
        """Stop appending audio but keep the buffered clip and the session open.

        Used between key-ups within a single conversation: nothing is captured
        while paused (so squelch-tail noise is kept out), while the speech
        accumulated so far is retained for one merged transcription. resume_
        recording() re-inserts the gap as silence so pacing is preserved. No-op
        if no session is active. Call stop_recording() to finalize the clip.
        """
        if self._active:
            self._recording = False
            self._pause_started = time.monotonic()

    def resume_recording(self) -> None:
        """Resume a paused session, appending to the already-buffered audio.

        The squelch-closed gap is re-inserted as clean silence (capped at
        _MAX_GAP_SILENCE_SEC) so the merged clip keeps the natural pause between
        key-ups rather than splicing speech together. Self-heals a stream that
        died during the pause (sleep/resume, USB re-enumeration) by reopening it
        — without clearing the buffered chunks, so earlier speech is preserved.
        """
        if not self._active:
            return
        if not self._stream_is_live():
            if self._stream is not None:
                log.warning(
                    "AudioRecorder: input stream died during pause — reopening "
                    "on device %s", self._device_index,
                )
                self._close_stream()
            if not self._open_stream():
                return
        # Note: we deliberately do NOT reopen a merely under-delivering stream
        # here. Reopening mid-conversation drops the in-flight audio, and a
        # transmission already degrading is a lost cause; proactive idle
        # recycling keeps streams fresh so this rarely arises, and the reactive
        # reopen in start_recording still heals it before the next transmission.
        if self._pause_started > 0.0:
            gap = min(time.monotonic() - self._pause_started, _MAX_GAP_SILENCE_SEC)
            self._pause_started = 0.0
            n = int(gap * self._capture_rate)
            if n > 0:
                # Enqueue the gap as a sentinel so the capture worker inserts the
                # silence into _chunks in capture order — between the pre-pause
                # and post-resume audio — rather than out-of-band ahead of blocks
                # still queued for processing. Added at the native capture rate;
                # stop_recording resamples the whole buffer to 16 kHz uniformly.
                self._cap_raw_buffer.append((None, n))
                self._cap_enqueued += 1
        self._recording = True

    def start_monitoring(self) -> bool:
        """Open the input stream (if not already live) so input levels and
        pass-through flow continuously without recording.

        Used to drive the live level meter while a radio is connected. No-op
        when no input device is configured. Safe to call repeatedly.
        """
        if self._device_index is None:
            return False
        if self._stream_is_live():
            return True
        if self._stream is not None:
            self._close_stream()
        return self._open_stream()

    def recycle_if_idle_and_stale(self) -> bool:
        """Recycle a healthy input stream that has simply been open too long.

        Persistent streams degrade after hours of uptime; recycling before they
        get that old keeps capture reliable. Only acts when no capture session is
        open (``not _active``) so a live or paused recording is never interrupted,
        and only on a live stream older than ``_MAX_STREAM_AGE_SEC`` — a dead or
        missing stream is left to the reopen paths in start/resume. Cheap to call
        every poll; returns True only when it actually recycled.
        """
        if self._stream is None or self._active:
            return False
        if not self._stream_is_live():
            return False   # start_recording()/start_monitoring() will reopen it
        if time.monotonic() - self._stream_opened_at < _MAX_STREAM_AGE_SEC:
            return False
        log.debug("AudioRecorder: recycling idle stream on device %s after "
                  "%.0fs open to avoid age-related degradation",
                  self._device_index, time.monotonic() - self._stream_opened_at)
        self._close_stream()
        return self._open_stream()

    def current_level(self) -> float:
        """Most recent smoothed input level in [0, 1]. Returns 0 when the
        stream is not delivering audio, so the meter reads empty."""
        if not self._stream_is_live():
            return 0.0
        return self._level

    def is_capturing_audio(self) -> bool:
        """True if the input stream is open and actively delivering callbacks."""
        return self._stream_is_live()

    def _stream_is_live(self) -> bool:
        """True if the persistent input stream is open and still delivering audio.

        The callback runs continuously while the stream is healthy (even
        between recordings, as a no-op), so a stale ``_last_callback_ts`` is a
        reliable signal that the audio thread has stopped. We also consult
        ``.active``, which PortAudio clears when it aborts a stream on device
        loss; any error reading it is treated as "not live" so we reopen
        defensively.
        """
        if self._stream is None:
            return False
        try:
            if not self._stream.active:
                return False
        except Exception:
            return False
        return (time.monotonic() - self._last_callback_ts) < _STREAM_STALE_SEC

    def _reset_delivery_window(self) -> None:
        """Start a fresh frame-rate measurement window (call on (re)open)."""
        self._cb_frames = 0
        self._cb_window_start = time.monotonic()
        self._prev_adc_time = 0.0
        self._prev_cb_n = 0
        self._diag_skipped_frames = 0.0
        self._diag_max_body_ms = 0.0
        self._diag_num_cb = 0

    def _delivery_diagnostics(self) -> str:
        """One-line summary distinguishing HAL frame loss from callback
        starvation over the current delivery window.

        HAL/device/USB loss shows up as ADC-clock gaps (frames skipped between
        callbacks) while our callback body stays fast; GIL/CPU starvation shows
        up as a slow callback body (numpy work on the real-time thread losing
        the GIL) eating a large fraction of the buffer period. Empty-ish string
        when no callbacks fired.
        """
        if self._diag_num_cb == 0:
            return "no input callbacks fired (stream dead)"
        elapsed = max(time.monotonic() - self._cb_window_start, 1e-6)
        expected = elapsed * self._capture_rate
        skipped_pct = 100.0 * self._diag_skipped_frames / expected if expected > 0 else 0.0
        mean_period_ms = (
            (self._cb_frames / self._diag_num_cb) / self._capture_rate * 1000.0
            if self._capture_rate > 0 else 0.0
        )
        if mean_period_ms > 0 and self._diag_max_body_ms >= 0.5 * mean_period_ms:
            hint = ("callback body slow vs %.1fms buffer period — suspect "
                    "GIL/CPU starvation" % mean_period_ms)
        elif self._diag_skipped_frames > 0:
            hint = "callback body fast — loss is upstream (device/HAL/USB)"
        else:
            hint = "no ADC-clock gaps — delivery shortfall not explained by drops"
        return ("ADC-skipped ~%.0f frames (%.0f%%), max callback body %.1fms "
                "over %d callbacks; %s"
                % (self._diag_skipped_frames, skipped_pct,
                   self._diag_max_body_ms, self._diag_num_cb, hint))

    def _stream_underdelivering(self) -> bool:
        """True if the stream is alive but delivering far fewer frames than its
        rate implies — a degraded persistent stream (long uptime / sleep-wake)
        that _stream_is_live() cannot catch because callbacks keep firing.

        Measured over a rolling window: returns False until at least
        _UNDERDELIVERY_WINDOW_SEC of callback history has accrued, then compares
        the effective sample rate to the device's rate and resets the window.
        Checked at start_recording/resume so a reopen heals every subsequent
        transmission (the degradation persists for hours once it sets in).
        """
        if self._stream is None or self._capture_rate <= 0:
            return False
        elapsed = time.monotonic() - self._cb_window_start
        if elapsed < _UNDERDELIVERY_WINDOW_SEC:
            return False
        effective_rate = self._cb_frames / elapsed
        self._reset_delivery_window()
        degraded = effective_rate < self._capture_rate * _UNDERDELIVERY_FRACTION
        if degraded:
            log.warning(
                "AudioRecorder: input stream under-delivering on device %s — "
                "%.0f Hz effective vs %d Hz expected (%.0f%%); reopening",
                self._device_index, effective_rate, self._capture_rate,
                100.0 * effective_rate / self._capture_rate,
            )
        return degraded

    def _recording_underdelivered(self) -> tuple[bool, float]:
        """Measure how fully the stream delivered the just-finished recording.

        _stream_underdelivering() only runs at the *start* of a transmission,
        over the idle gap before it, so a stream that delivers fine while idle
        but collapses under capture load slips past it and truncates a long
        transmission into a choppy few-second clip. This runs at stop_recording
        over the recording window (reset in start_recording), catching exactly
        that case.

        Frame accounting counts every callback, including any while paused, so
        this reflects raw stream health (frames vs wall-clock) and is not
        confused by pause-gated gaps. Returns (degraded, effective_rate_hz);
        judges nothing until _UNDERDELIVERY_WINDOW_SEC of history has accrued.

        Reopening cannot un-drop this clip's audio, but it keeps the degraded
        stream from truncating every following transmission too.
        """
        if self._stream is None or self._capture_rate <= 0:
            return (False, 0.0)
        elapsed = time.monotonic() - self._cb_window_start
        if elapsed < _UNDERDELIVERY_WINDOW_SEC:
            return (False, 0.0)  # too short to judge reliably
        effective_rate = self._cb_frames / elapsed
        degraded = effective_rate < self._capture_rate * _UNDERDELIVERY_FRACTION
        return (degraded, effective_rate)

    def stop_recording(self) -> Optional[np.ndarray]:
        """
        Stop accumulating audio and return the captured data.
        Returns None if nothing was captured or duration < MIN_DURATION_SEC.

        The stream is left open so it is ready for the next recording without
        a create/destroy cycle (which was the source of the SIGSEGV).
        """
        if not self._active:
            return None
        self._recording = False
        self._active = False

        # Drain the capture pipeline so every recorded block (and any gap
        # sentinel) has been processed into _chunks before we snapshot it — the
        # mono reduction and routing run on the worker thread, off the RT path.
        self._await_capture_drain()

        with self._lock:
            chunks = list(self._chunks)
            self._chunks = []

        # Check stream delivery health for THIS capture *before* the early
        # returns below. The worst degradations produce an empty or
        # sub-MIN_DURATION clip, so deferring this until after those returns
        # (the original bug) meant the most-broken streams — the ones that most
        # need it — never got reopened. Reopen while idle regardless of whether
        # a usable clip results, so the next transmission captures fully.
        self._maybe_reopen_underdelivered()

        if not chunks:
            log.warning(
                "AudioRecorder: no audio captured during recording — the input "
                "stream stopped delivering audio (check for sleep/resume or a "
                "device disconnect). It will be reopened on the next transmission."
            )
            return None

        audio = np.concatenate(chunks, axis=0).flatten()
        # Resample the whole clip at once (cleaner than per-block) from the
        # device's native capture rate down to Whisper's 16 kHz.
        audio = _resample(audio, self._capture_rate, SAMPLE_RATE)
        duration = len(audio) / SAMPLE_RATE
        if duration < MIN_DURATION_SEC:
            log.debug("AudioRecorder: recording too short (%.2fs) — discarding", duration)
            return None

        log.debug("AudioRecorder: captured %.2fs of audio (%d Hz → %d Hz)",
                  duration, self._capture_rate, SAMPLE_RATE)
        return audio

    def _maybe_reopen_underdelivered(self) -> None:
        """Reopen the stream if it under-delivered during the just-ended capture.

        The stream may have delivered far fewer frames than wall-clock implies
        (load-induced degradation the idle-gap watchdog in start_recording
        cannot see). That already truncated this clip; reopen a fresh stream
        now, while idle, so the next transmission is not also truncated.
        Reopening cannot recover the frames already dropped from this clip.
        """
        degraded, effective_rate = self._recording_underdelivered()
        diag = self._delivery_diagnostics()
        if not degraded:
            if self._diag_num_cb:
                log.debug("AudioRecorder: capture delivery on device %s — %s",
                          self._device_index, diag)
            return
        wall = time.monotonic() - self._cb_window_start
        log.warning(
            "AudioRecorder: input stream under-delivered during capture on "
            "device %s — %.0f Hz effective vs %d Hz expected (%.0f%%) over "
            "%.1fs wall; clip is truncated/choppy or dropped. %s. Reopening so "
            "following transmissions capture fully.",
            self._device_index, effective_rate, self._capture_rate,
            100.0 * effective_rate / self._capture_rate, wall, diag,
        )
        self._close_stream()
        self._open_stream()

    def close(self) -> None:
        """Release audio resources. Call once on shutdown."""
        self._recording = False
        self._active = False
        self._passthrough = False
        self._stop_pt_thread()
        self._close_out_stream()
        self._close_stream()
        self._stop_capture_worker()

    # ------------------------------------------------------------------
    # Private — noise profile
    # ------------------------------------------------------------------

    def _reset_noise_profile(self) -> None:
        self._noise_profile = None
        self._noise_accum = []
        self._noise_ready = False

    # ------------------------------------------------------------------
    # Private — processing thread
    # ------------------------------------------------------------------

    def _start_pt_thread(self) -> None:
        self._pt_thread_running = True
        self._pt_thread = threading.Thread(
            target=self._pt_processing_loop, daemon=True, name="pt-nr"
        )
        self._pt_thread.start()

    def _stop_pt_thread(self) -> None:
        self._pt_thread_running = False
        if self._pt_thread is not None:
            self._pt_thread.join(timeout=1.0)
            self._pt_thread = None

    def _pt_processing_loop(self) -> None:
        """
        Background thread: reads raw chunks, applies noise reduction, writes
        to the output buffer consumed by the output callback.

        During the initial _NOISE_PROFILE_SECS window, audio passes through
        unprocessed while the noise profile is accumulated.  After that, each
        chunk is filtered using the pre-computed profile.  Falls back to
        passthrough if noisereduce is not installed.
        """
        try:
            import noisereduce as nr
            nr_available = True
        except ImportError:
            log.debug("noisereduce not installed — pass-through without noise reduction")
            nr_available = False

        while self._pt_thread_running:
            try:
                chunk = self._pt_raw_buffer.popleft()
            except IndexError:
                time.sleep(0.005)
                continue

            # Resample the raw (native-rate) chunk to 16 kHz up front so the
            # noise profile, noise reduction, and the 16 kHz output stream all
            # operate on a single, consistent rate.
            flat = _resample(chunk.flatten(), self._capture_rate, SAMPLE_RATE)

            # No noisereduce: pass through unchanged
            if not nr_available:
                self._pt_out_buffer.append(flat)
                continue

            # Still building the noise profile: accumulate and pass through raw
            if not self._noise_ready:
                self._noise_accum.append(flat)
                total = sum(len(c) for c in self._noise_accum)
                if total >= int(_NOISE_PROFILE_SECS * SAMPLE_RATE):
                    self._noise_profile = np.concatenate(self._noise_accum)
                    self._noise_ready = True
                    self._noise_accum.clear()
                    log.info(
                        "AudioRecorder: noise profile captured (%.1fs) — "
                        "pass-through noise reduction active",
                        _NOISE_PROFILE_SECS,
                    )
                self._pt_out_buffer.append(flat)
                continue

            # Apply noise reduction using the pre-computed profile
            try:
                processed = nr.reduce_noise(
                    y=flat,
                    sr=SAMPLE_RATE,
                    y_noise=self._noise_profile,
                    prop_decrease=0.9,
                    stationary=True,
                )
                self._pt_out_buffer.append(processed.astype(np.float32))
            except Exception as exc:
                log.debug("Pass-through NR error — passing raw: %s", exc)
                self._pt_out_buffer.append(flat)

    # ------------------------------------------------------------------
    # Private — streams
    # ------------------------------------------------------------------

    def _open_out_stream(self) -> bool:
        """Open and start the pass-through output stream. Returns True on success."""
        try:
            import sounddevice as sd
            self._out_stream = sd.OutputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                device=self._out_device_index,
                latency="low",
                callback=self._output_callback,
            )
            self._out_stream.start()
            log.debug("AudioRecorder: output stream opened on device %d", self._out_device_index)
            return True
        except Exception as exc:
            self._out_stream = None
            log.warning("AudioRecorder: failed to open output stream — %s", exc)
            return False

    def _close_out_stream(self) -> None:
        """Stop and close the pass-through output stream."""
        if self._out_stream is not None:
            try:
                self._out_stream.stop()
            except Exception:
                pass
            try:
                self._out_stream.close()
            except Exception:
                pass
            self._out_stream = None
            self._out_device_index = None

    def _open_stream(self) -> bool:
        """Open and start the persistent input stream. Returns True on success."""
        try:
            import sounddevice as sd  # deferred — optional dependency
            # Capture up to two input channels. USB cards often present the
            # line input as one channel of a stereo pair (the other dead), so
            # we open the pair and pick the live channel in _to_mono().
            #
            # Open at the device's native sample rate, not a forced 16 kHz.
            # Cheap USB capture dongles advertise 16 kHz but deliver garbled,
            # aliased audio when actually run at it; we resample to SAMPLE_RATE
            # ourselves (see _resample) so Whisper still sees clean 16 kHz.
            try:
                info = sd.query_devices(self._device_index)
                max_in = int(info["max_input_channels"])
                native_sr = int(round(float(info["default_samplerate"])))
            except Exception:
                max_in = 1
                native_sr = SAMPLE_RATE
            self._channels = min(2, max(1, max_in))
            self._capture_rate = native_sr if native_sr > 0 else SAMPLE_RATE
            self._ch_energy = None
            self._live_ch = 0
            self._stream = sd.InputStream(
                samplerate=self._capture_rate,
                channels=self._channels,
                dtype="float32",
                device=self._device_index,
                callback=self._audio_callback,
            )
            # Seed the liveness timestamp so the stream isn't judged stale in
            # the window before its first callback fires, and start a fresh
            # frame-rate window so the new stream isn't immediately judged
            # under-delivering on stale counters.
            self._last_callback_ts = time.monotonic()
            self._reset_delivery_window()
            self._stream_opened_at = time.monotonic()
            # Bring the capture-processing worker up before the stream starts
            # firing callbacks so the first blocks are drained immediately.
            self._ensure_capture_worker()
            self._stream.start()
            log.debug("AudioRecorder: stream opened on device %d (%d ch, %d Hz)",
                      self._device_index, self._channels, self._capture_rate)
            return True
        except Exception as exc:
            self._stream = None
            log.warning("AudioRecorder: failed to open stream — %s", exc)
            return False

    def _close_stream(self) -> None:
        """
        Stop and close the persistent stream.

        Uses stop() rather than abort() so that Pa_StopStream blocks until the
        callback thread has fully exited before Pa_CloseStream frees the stream
        resources (including the libffi closure).  This prevents the SIGSEGV
        that occurs when the IO thread calls the closure after it has been freed.
        """
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    # ------------------------------------------------------------------
    # Private — capture processing (off the real-time callback thread)
    # ------------------------------------------------------------------

    def _ensure_capture_worker(self) -> None:
        """Start the capture-processing thread if it is not already running."""
        if self._cap_thread is not None and self._cap_thread.is_alive():
            return
        self._cap_thread_running = True
        self._cap_thread = threading.Thread(
            target=self._capture_processing_loop, daemon=True, name="capture-proc"
        )
        self._cap_thread.start()

    def _stop_capture_worker(self) -> None:
        """Stop the capture-processing thread and discard any unprocessed input."""
        self._cap_thread_running = False
        if self._cap_thread is not None:
            self._cap_thread.join(timeout=1.0)
            self._cap_thread = None
        self._cap_raw_buffer.clear()

    def _capture_processing_loop(self) -> None:
        """Drain queued raw blocks and process each off the real-time thread."""
        while self._cap_thread_running:
            try:
                item = self._cap_raw_buffer.popleft()
            except IndexError:
                time.sleep(0.002)
                continue
            self._process_capture_item(item)

    def _drain_capture_buffer_once(self) -> None:
        """Process everything currently queued, synchronously. Used at stop when
        no worker thread is running (e.g. unit tests)."""
        while True:
            try:
                item = self._cap_raw_buffer.popleft()
            except IndexError:
                break
            self._process_capture_item(item)

    def _await_capture_drain(self) -> None:
        """Ensure every block enqueued so far has reached _chunks before the
        caller snapshots it. The worker drains when running; otherwise we drain
        inline. Bounded so a starved worker cannot hang the caller."""
        if self._cap_thread is not None and self._cap_thread.is_alive():
            target = self._cap_enqueued
            deadline = time.monotonic() + _CAPTURE_DRAIN_TIMEOUT_SEC
            while self._cap_processed < target and time.monotonic() < deadline:
                time.sleep(0.002)
            if self._cap_processed < target:
                log.warning(
                    "AudioRecorder: capture worker did not drain in time "
                    "(%d/%d blocks processed) — clip may be truncated",
                    self._cap_processed, target,
                )
        else:
            self._drain_capture_buffer_once()

    def _process_capture_item(self, item) -> None:
        """Reduce one queued block to mono, update the level meter, and route it
        to the recording buffer and/or pass-through. Runs off the RT thread.

        item is either (raw_block, was_recording) or (None, n_silence) — the
        latter a gap sentinel enqueued by resume_recording so the re-inserted
        silence lands in _chunks in capture order, between the pre-pause and
        post-resume audio rather than out-of-band ahead of still-queued blocks.
        """
        raw, flag = item
        if raw is None:
            with self._lock:
                self._chunks.append(np.zeros(int(flag), dtype=np.float32))
            self._cap_processed += 1
            return
        mono = self._to_mono(raw) if raw.size else raw
        # Track input level for the meter (peak with fast attack / slow decay),
        # measured on the DC-blocked mono signal.
        if mono.size:
            block_peak = float(np.max(np.abs(mono)))
            if block_peak >= self._level:
                self._level = block_peak
            else:
                self._level += (block_peak - self._level) * 0.2
        if flag:
            with self._lock:
                self._chunks.append(mono.copy())
        if self._passthrough:
            self._pt_raw_buffer.append(mono.copy())
        self._cap_processed += 1

    # ------------------------------------------------------------------
    # Private — callbacks
    # ------------------------------------------------------------------

    # Channel-selection tuning. The energy estimate is updated slowly so it
    # reflects long-term loudness rather than per-word swings, and a channel is
    # only switched to when it is decisively louder than the one in use. Without
    # this hysteresis, argmax flips to a dead/quiet channel on every inter-word
    # gap (where both channels sit at the noise floor) and lags by several
    # callbacks when speech resumes — chopping word onsets and producing the
    # fragmented "choppy" audio that defeats transcription.
    _CH_ENERGY_SMOOTHING = 0.05   # per-callback EMA weight for channel energy
    _CH_SWITCH_MARGIN = 2.0       # switch only if the alternative is this many
                                  # times louder than the current channel

    def _to_mono(self, indata: np.ndarray) -> np.ndarray:
        """Reduce a capture block to one DC-blocked mono channel (1-D float32).

        Removes each channel's DC bias — USB line inputs often carry a large
        constant offset that otherwise pins the level meter and defeats
        silence detection — then, for a multi-channel device, emits a single
        latched channel: the one carrying audio. Selection is held through
        silence and only changes when another channel is decisively and
        sustainedly louder, so a dead/unconnected channel is ignored without
        dropping inter-word gaps or word onsets.
        """
        block = indata.astype(np.float32, copy=True)
        if block.ndim == 1:
            return block - float(block.mean())
        # DC-block every channel (column-wise mean removal).
        block = block - block.mean(axis=0, keepdims=True)
        if block.shape[1] == 1:
            return block[:, 0]
        # Slowly track per-channel energy.
        energies = np.sqrt(np.mean(block ** 2, axis=0))
        if self._ch_energy is None or self._ch_energy.shape[0] != energies.shape[0]:
            self._ch_energy = energies.copy()
            self._live_ch = int(np.argmax(energies))
        else:
            self._ch_energy += (energies - self._ch_energy) * self._CH_ENERGY_SMOOTHING
            best = int(np.argmax(self._ch_energy))
            # Only switch on a clear, sustained margin — never on a noise-floor
            # tie during silence, which would drop the next word's onset.
            if (best != self._live_ch
                    and self._ch_energy[best]
                    > self._ch_energy[self._live_ch] * self._CH_SWITCH_MARGIN):
                self._live_ch = best
        # Clamp in case the channel count shrank between callbacks.
        if self._live_ch >= block.shape[1]:
            self._live_ch = int(np.argmax(self._ch_energy))
        return block[:, self._live_ch]

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status,
    ) -> None:
        """sounddevice input callback — runs on the C real-time audio thread.

        Kept as short as possible: copy the raw block, enqueue it, update the
        cheap accounting, and return. All numpy work (mono reduction, level
        metering, channel selection) and chunk routing happen on the capture-
        processing thread, so contention there can never make this callback miss
        its IO deadline and drop hardware frames.
        """
        # Liveness heartbeat: a healthy stream fires this continuously, so
        # start_recording() can tell a live stream from a dead one.
        t_entry = time.monotonic()
        self._last_callback_ts = t_entry
        # Frame accounting for the under-delivery watchdog (see
        # _stream_underdelivering). Counts frames actually handed to us, which
        # is what drops when a long-lived stream degrades.
        self._cb_frames += frames
        self._diag_num_cb += 1
        # ADC-clock gap accounting: if the device's capture timestamp advanced
        # by more than the previous buffer's duration, the HAL skipped hardware
        # frames between callbacks — loss upstream of us (device/USB), not our
        # scheduling. time_info is None under unit tests and its field access is
        # via CFFI, so guard defensively — this must never throw on the RT thread.
        if time_info is not None and self._capture_rate > 0:
            try:
                adc = float(time_info.inputBufferAdcTime)
            except Exception:
                adc = 0.0
            if adc > 0.0 and self._prev_adc_time > 0.0 and self._prev_cb_n > 0:
                gap = (adc - self._prev_adc_time) - (self._prev_cb_n / self._capture_rate)
                if gap > _ADC_GAP_EPSILON_SEC:
                    self._diag_skipped_frames += gap * self._capture_rate
            self._prev_adc_time = adc
            self._prev_cb_n = frames
        if status:
            log.debug("AudioRecorder callback status: %s", status)
        # Hand the raw block off. The copy is mandatory — sounddevice reuses
        # indata after we return. Tag with the recording state at capture time so
        # a later flag flip can't misroute this block.
        self._cap_raw_buffer.append((indata.copy(), self._recording))
        self._cap_enqueued += 1
        # Slowest callback body over the window. Now that the body is just a copy
        # and an enqueue, a body still eating a large slice of the buffer period
        # points at GIL/CPU starvation upstream of us rather than our own work.
        body_ms = (time.monotonic() - t_entry) * 1000.0
        if body_ms > self._diag_max_body_ms:
            self._diag_max_body_ms = body_ms

    def _output_callback(
        self,
        outdata: np.ndarray,
        frames: int,
        time_info,
        status,
    ) -> None:
        """sounddevice output callback — runs on C audio thread."""
        try:
            chunk = self._pt_out_buffer.popleft()
            if len(chunk) >= frames:
                outdata[:] = chunk[:frames].reshape(frames, 1)
                if len(chunk) > frames:
                    self._pt_out_buffer.appendleft(chunk[frames:])
            else:
                outdata[:len(chunk)] = chunk.reshape(-1, 1)
                outdata[len(chunk):] = 0.0
        except IndexError:
            outdata[:] = 0.0
