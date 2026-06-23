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

SAMPLE_RATE = 16_000       # Hz — Whisper's native sample rate
MIN_DURATION_SEC = 1.0     # discard recordings shorter than this
_NOISE_PROFILE_SECS = 2.0  # seconds of squelch audio used to build noise profile
# A healthy input stream fires its callback continuously (even between
# recordings). If no callback has arrived in this many seconds the stream is
# considered dead — e.g. after a system sleep/resume or USB re-enumeration.
_STREAM_STALE_SEC = 2.0


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

    Pass-through pipeline:
        input callback → _pt_raw_buffer → processing thread (NR) → _pt_out_buffer → output callback

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
        # Slow per-channel energy estimate used to pick the channel that
        # actually carries audio (a dead/unconnected channel is ignored).
        self._ch_energy: Optional[np.ndarray] = None
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._recording = False
        # monotonic timestamp of the most recent input callback; used to detect
        # a stream that has silently stopped delivering audio (sleep/resume).
        self._last_callback_ts: float = 0.0
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
        with self._lock:
            self._chunks = []
        self._recording = True
        log.debug("AudioRecorder: started on device %d", self._device_index)

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

    def stop_recording(self) -> Optional[np.ndarray]:
        """
        Stop accumulating audio and return the captured data.
        Returns None if nothing was captured or duration < MIN_DURATION_SEC.

        The stream is left open so it is ready for the next recording without
        a create/destroy cycle (which was the source of the SIGSEGV).
        """
        if not self._recording:
            return None
        self._recording = False

        with self._lock:
            chunks = list(self._chunks)
            self._chunks = []

        if not chunks:
            log.warning(
                "AudioRecorder: no audio captured during recording — the input "
                "stream stopped delivering audio (check for sleep/resume or a "
                "device disconnect). It will be reopened on the next transmission."
            )
            return None

        audio = np.concatenate(chunks, axis=0).flatten()
        duration = len(audio) / SAMPLE_RATE
        if duration < MIN_DURATION_SEC:
            log.debug("AudioRecorder: recording too short (%.2fs) — discarding", duration)
            return None

        log.debug("AudioRecorder: captured %.2fs of audio", duration)
        return audio

    def close(self) -> None:
        """Release audio resources. Call once on shutdown."""
        self._recording = False
        self._passthrough = False
        self._stop_pt_thread()
        self._close_out_stream()
        self._close_stream()

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

            # No noisereduce: pass through unchanged
            if not nr_available:
                self._pt_out_buffer.append(chunk)
                continue

            # Still building the noise profile: accumulate and pass through raw
            if not self._noise_ready:
                self._noise_accum.append(chunk.flatten())
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
                self._pt_out_buffer.append(chunk)
                continue

            # Apply noise reduction using the pre-computed profile
            try:
                flat = chunk.flatten()
                processed = nr.reduce_noise(
                    y=flat,
                    sr=SAMPLE_RATE,
                    y_noise=self._noise_profile,
                    prop_decrease=0.9,
                    stationary=True,
                )
                self._pt_out_buffer.append(
                    processed.reshape(chunk.shape).astype(np.float32)
                )
            except Exception as exc:
                log.debug("Pass-through NR error — passing raw: %s", exc)
                self._pt_out_buffer.append(chunk)

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
            try:
                max_in = int(sd.query_devices(self._device_index)["max_input_channels"])
            except Exception:
                max_in = 1
            self._channels = min(2, max(1, max_in))
            self._ch_energy = None
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=self._channels,
                dtype="float32",
                device=self._device_index,
                callback=self._audio_callback,
            )
            # Seed the liveness timestamp so the stream isn't judged stale in
            # the window before its first callback fires.
            self._last_callback_ts = time.monotonic()
            self._stream.start()
            log.debug("AudioRecorder: stream opened on device %d (%d ch)",
                      self._device_index, self._channels)
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
    # Private — callbacks
    # ------------------------------------------------------------------

    def _to_mono(self, indata: np.ndarray) -> np.ndarray:
        """Reduce a capture block to one DC-blocked mono channel (1-D float32).

        Removes each channel's DC bias — USB line inputs often carry a large
        constant offset that otherwise pins the level meter and defeats
        silence detection — then, for a multi-channel device, selects the
        channel with the most recent signal energy so a dead/unconnected
        channel is ignored instead of mixed in.
        """
        block = indata.astype(np.float32, copy=True)
        if block.ndim == 1:
            return block - float(block.mean())
        # DC-block every channel (column-wise mean removal).
        block = block - block.mean(axis=0, keepdims=True)
        if block.shape[1] == 1:
            return block[:, 0]
        # Slowly track per-channel energy and emit the liveliest channel.
        energies = np.sqrt(np.mean(block ** 2, axis=0))
        if self._ch_energy is None or self._ch_energy.shape[0] != energies.shape[0]:
            self._ch_energy = energies.copy()
        else:
            self._ch_energy += (energies - self._ch_energy) * 0.1
        return block[:, int(np.argmax(self._ch_energy))]

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status,
    ) -> None:
        """sounddevice input callback — runs on C audio thread."""
        # Liveness heartbeat: a healthy stream fires this continuously, so
        # start_recording() can tell a live stream from a dead one.
        self._last_callback_ts = time.monotonic()
        mono = self._to_mono(indata) if indata.size else indata
        # Track input level for the meter (peak with fast attack / slow decay).
        # Measured on the DC-blocked mono signal so a constant input bias no
        # longer holds the meter off zero when nothing is being received.
        if mono.size:
            block_peak = float(np.max(np.abs(mono)))
            if block_peak >= self._level:
                self._level = block_peak
            else:
                self._level += (block_peak - self._level) * 0.2
        if status:
            log.debug("AudioRecorder callback status: %s", status)
        if self._recording:
            with self._lock:
                self._chunks.append(mono.copy())
        if self._passthrough:
            self._pt_raw_buffer.append(mono.copy())

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
