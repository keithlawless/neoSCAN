"""
AudioRecorder — captures audio from a sounddevice input device.

Designed for use with the NeoSCAN transmission logger: call start_recording()
when a transmission begins and stop_recording() when it ends to get the
captured audio as a float32 numpy array at 16 kHz (Whisper's native rate).
"""
from __future__ import annotations

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

    Thread safety: _chunks is protected by _lock because sounddevice's
    callback runs on a C audio thread while start/stop are called from
    the Qt main thread.
    """

    def __init__(self) -> None:
        self._device_index: Optional[int] = None
        self._stream = None
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._recording = False

    def set_device(self, device_index: Optional[int]) -> None:
        """Set the input device index (from sounddevice.query_devices())."""
        if self._recording:
            log.warning("AudioRecorder: device changed while recording — ignoring")
            return
        self._device_index = device_index

    def start_recording(self) -> None:
        """Open the audio stream and start accumulating chunks."""
        if self._device_index is None:
            return
        if self._recording:
            return
        # Close any previous stopped stream before opening a new one.
        # This is the safe point to call close() — the IO thread has fully
        # exited by the time stop_recording() returns, so there is no
        # risk of a use-after-free on the libffi closure.
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        try:
            import sounddevice as sd  # deferred — optional dependency
            with self._lock:
                self._chunks = []
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                device=self._device_index,
                callback=self._audio_callback,
            )
            self._stream.start()
            self._recording = True
            log.debug("AudioRecorder: started on device %d", self._device_index)
        except Exception as exc:
            self._recording = False
            self._stream = None
            log.warning("AudioRecorder: failed to start — %s", exc)

    def stop_recording(self) -> Optional[np.ndarray]:
        """
        Stop the stream and return the captured audio.
        Returns None if nothing was captured or duration < MIN_DURATION_SEC.

        The stream is aborted immediately but intentionally NOT closed here.
        Calling close() while the CoreAudio IO thread is still unwinding its
        last callback causes the libffi closure backing the Python callback to
        be freed mid-call, producing a SIGSEGV at 0x4.  The stream object is
        kept alive and closed lazily at the start of the next recording (in
        start_recording()) or on explicit shutdown (in close()).
        """
        if not self._recording:
            return None
        self._recording = False
        try:
            if self._stream:
                self._stream.abort()  # immediate halt; close is deferred
        except Exception as exc:
            log.warning("AudioRecorder: error aborting stream — %s", exc)

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
        """Release audio resources. Call once on shutdown after recording has stopped."""
        self._recording = False
        if self._stream is not None:
            try:
                self._stream.abort()
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
        with self._lock:
            self._chunks.append(indata.copy())
