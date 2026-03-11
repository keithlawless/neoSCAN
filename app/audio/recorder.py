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
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000       # Hz — Whisper's native sample rate
MIN_DURATION_SEC = 1.0     # discard recordings shorter than this


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
    """

    def __init__(self) -> None:
        self._device_index: Optional[int] = None
        self._stream = None
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._recording = False
        self._passthrough: bool = False
        self._out_device_index: Optional[int] = None
        self._out_stream = None
        self._pt_buffer: collections.deque = collections.deque()

    def set_passthrough(self, enabled: bool, output_device_index: Optional[int]) -> None:
        """Enable or disable real-time audio pass-through to an output device."""
        if not enabled or output_device_index is None:
            self._passthrough = False
            self._close_out_stream()
            self._pt_buffer.clear()
            return
        # Reopen output stream only if device changed
        if output_device_index != self._out_device_index:
            self._close_out_stream()
            self._out_device_index = output_device_index
            self._pt_buffer.clear()
            if not self._open_out_stream():
                return
        self._passthrough = True

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
        """Begin accumulating audio chunks. Opens the stream if not yet open."""
        if self._device_index is None:
            return
        if self._recording:
            return
        if self._stream is None:
            if not self._open_stream():
                return
        with self._lock:
            self._chunks = []
        self._recording = True
        log.debug("AudioRecorder: started on device %d", self._device_index)

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
        self._close_out_stream()
        self._close_stream()

    # ------------------------------------------------------------------
    # Private
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
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                device=self._device_index,
                callback=self._audio_callback,
            )
            self._stream.start()
            log.debug("AudioRecorder: stream opened on device %d", self._device_index)
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

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status,
    ) -> None:
        """sounddevice callback — runs on C audio thread."""
        if status:
            log.debug("AudioRecorder callback status: %s", status)
        if self._recording:
            with self._lock:
                self._chunks.append(indata.copy())
        if self._passthrough:
            self._pt_buffer.append(indata.copy())

    def _output_callback(
        self,
        outdata: np.ndarray,
        frames: int,
        time_info,
        status,
    ) -> None:
        """sounddevice output callback — runs on C audio thread."""
        try:
            chunk = self._pt_buffer.popleft()
            if len(chunk) >= frames:
                outdata[:] = chunk[:frames].reshape(frames, 1)
                if len(chunk) > frames:
                    self._pt_buffer.appendleft(chunk[frames:])
            else:
                outdata[:len(chunk)] = chunk.reshape(-1, 1)
                outdata[len(chunk):] = 0.0
        except IndexError:
            outdata[:] = 0.0
