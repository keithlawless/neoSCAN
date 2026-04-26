"""
Transcription pipeline for NeoSCAN.

TranscriptionManager  — owned by MainWindow; bridges the log panel, recorder,
                         worker thread, and transcript writer.
TranscriberWorker     — QThread that processes transcription jobs serially.
_ModelLoaderThread    — QThread that loads the Whisper model in the background.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt6.QtCore import (
    QMutex,
    QMutexLocker,
    QObject,
    QThread,
    QWaitCondition,
    pyqtSignal,
    pyqtSlot,
)

from app.audio.languages import DEFAULT_LANGUAGE, WHISPER_LANGUAGES  # noqa: F401 (re-exported)
from app.audio.recorder import AudioRecorder, SAMPLE_RATE
from app.audio.transcript_writer import TranscriptWriter
from app.ui.settings.preferences_dialog import load_prefs

log = logging.getLogger(__name__)

try:
    import whisper as _whisper_probe  # noqa: F401  (probe import only)
    WHISPER_AVAILABLE = True
    del _whisper_probe
except ImportError:
    WHISPER_AVAILABLE = False

_DEFAULT_MODEL = "base"
_DEFAULT_LANGUAGE = DEFAULT_LANGUAGE
_MAX_QUEUE_DEPTH = 10   # drop new jobs rather than let memory and latency grow unbounded
_MAX_AUDIO_SECS = 60    # truncate clips longer than this before noise reduction and Whisper


def _reduce_noise(audio: np.ndarray) -> np.ndarray:
    """
    Apply stationary noise reduction using noisereduce.

    Uses the whole clip to estimate the noise profile (stationary=True),
    which works well for the consistent background hiss / squelch static
    present in scanner audio.  prop_decrease=0.75 leaves a little residual
    noise so Whisper is not confused by over-processed silence.

    Falls back to the original audio if noisereduce is not installed or
    raises an unexpected error.
    """
    try:
        import noisereduce as nr  # deferred — optional dependency
        reduced = nr.reduce_noise(
            y=audio,
            sr=SAMPLE_RATE,
            stationary=True,
            prop_decrease=0.75,
        )
        return reduced.astype(np.float32)
    except ImportError:
        log.debug("noisereduce not installed — skipping noise reduction")
        return audio
    except Exception as exc:
        log.warning("Noise reduction failed — using raw audio: %s", exc)
        return audio


@dataclass
class _TranscriptionJob:
    audio: np.ndarray
    row_index: int
    entry_start_iso: str
    channel: str
    frequency: str
    system: str
    group: str
    radio: str = ""


class _ModelLoaderThread(QThread):
    model_loaded = pyqtSignal(object)
    load_failed = pyqtSignal(str)

    def __init__(self, model_size: str, parent=None) -> None:
        super().__init__(parent)
        self._model_size = model_size

    def run(self) -> None:
        try:
            import whisper  # deferred — optional dependency
            log.info("Loading Whisper model '%s'…", self._model_size)
            model = whisper.load_model(self._model_size)
            log.info("Whisper model '%s' loaded", self._model_size)
            self.model_loaded.emit(model)
        except Exception as exc:
            log.error("Failed to load Whisper model: %s", exc)
            self.load_failed.emit(str(exc))


class TranscriberWorker(QThread):
    """
    Processes transcription jobs serially on a background thread.

    Signals:
        transcription_ready(row_index, text, job)
    """

    transcription_ready = pyqtSignal(int, str, object)

    def __init__(self, model, language: Optional[str] = _DEFAULT_LANGUAGE, parent=None) -> None:
        super().__init__(parent)
        self._model = model
        self._language: Optional[str] = language
        self._queue: list[_TranscriptionJob] = []
        self._mutex = QMutex()
        self._cond = QWaitCondition()
        self._running = True

    def set_language(self, language: Optional[str]) -> None:
        """Update the transcription language. Takes effect on the next job."""
        self._language = language

    def enqueue(self, job: _TranscriptionJob) -> None:
        with QMutexLocker(self._mutex):
            if len(self._queue) >= _MAX_QUEUE_DEPTH:
                log.warning(
                    "TranscriberWorker: queue full (%d jobs pending) — "
                    "dropping transcription for row %d",
                    _MAX_QUEUE_DEPTH, job.row_index,
                )
                self.transcription_ready.emit(job.row_index, "[dropped — queue full]", None)
                return
            self._queue.append(job)
            log.debug("TranscriberWorker: enqueued row %d (%d jobs pending)",
                      job.row_index, len(self._queue))
            self._cond.wakeOne()

    def stop(self) -> None:
        with QMutexLocker(self._mutex):
            self._running = False
            self._cond.wakeOne()

    def run(self) -> None:
        while True:
            self._mutex.lock()
            while self._running and not self._queue:
                self._cond.wait(self._mutex)
            if not self._running and not self._queue:
                self._mutex.unlock()
                break
            job = self._queue.pop(0)
            self._mutex.unlock()

            self._process(job)

    def _process(self, job: _TranscriptionJob) -> None:
        raw = job.audio
        max_samples = int(_MAX_AUDIO_SECS * SAMPLE_RATE)
        if len(raw) > max_samples:
            log.debug("TranscriberWorker: truncating %.1fs clip to %ds for row %d",
                      len(raw) / SAMPLE_RATE, _MAX_AUDIO_SECS, job.row_index)
            raw = raw[:max_samples]
        duration = len(raw) / SAMPLE_RATE
        peak = float(np.max(np.abs(raw)))
        try:
            log.info("Transcribing row %d (%.1fs, peak=%.4f, language=%s)…",
                     job.row_index, duration, peak, self._language or "auto")
            if peak < 0.001:
                log.warning(
                    "Transcription row %d: audio peak %.4f is extremely low — "
                    "check input device gain and cable connection",
                    job.row_index, peak,
                )
            # Normalize to a consistent peak level so Whisper's VAD always
            # sees adequate signal regardless of input gain or system type.
            normalized = raw / peak if peak > 0.0 else raw
            # Pad with 1 s of silence so Whisper flushes the final segment.
            # Without this, audio that ends abruptly (squelch closing mid-word)
            # is often dropped from the last incomplete segment.
            padded = np.concatenate([normalized,
                                     np.zeros(SAMPLE_RATE, dtype=np.float32)])
            audio = _reduce_noise(padded)
            result = self._model.transcribe(
                audio,
                fp16=False,
                language=self._language,
                condition_on_previous_text=False,
                no_speech_threshold=0.8,
            )
            for seg in result.get("segments", []):
                log.debug(
                    "  row %d seg [%.1f-%.1fs] no_speech_prob=%.3f %r",
                    job.row_index, seg["start"], seg["end"],
                    seg.get("no_speech_prob", 0), seg["text"][:80],
                )
            text = result.get("text", "").strip()
            if text:
                log.info("Transcription row %d: %r", job.row_index, text[:200])
            else:
                log.warning(
                    "Transcription row %d: Whisper returned empty text for %.1fs of "
                    "audio — check that the scanner audio cable is connected to the "
                    "selected input device and the volume is adequate.",
                    job.row_index, duration,
                )
            self.transcription_ready.emit(job.row_index, text, job)
        except Exception as exc:
            log.error("Transcription failed for row %d: %s", job.row_index, exc)
            self.transcription_ready.emit(job.row_index, f"[error: {exc}]", job)


class TranscriptionManager(QObject):
    """
    Coordinates audio recording, Whisper transcription, and file writing.
    Owned by MainWindow. Call apply_settings() on startup and after Prefs.

    transcription_ready signal is forwarded from the worker so LogPanel can
    connect to it directly.
    """

    transcription_ready = pyqtSignal(int, str, object)

    def __init__(
        self,
        parent=None,
        device_index: int | None = None,
        radio_label: str = "",
        enabled: bool = False,
    ) -> None:
        super().__init__(parent)
        self._recorder = AudioRecorder()
        self._writer = TranscriptWriter()
        self._worker: Optional[TranscriberWorker] = None
        self._loader: Optional[_ModelLoaderThread] = None
        self._model = None
        # _per_radio_enabled comes from the connection dialog; _enabled is the
        # effective state after factoring in the global toggle and whether
        # Whisper is actually importable. apply_settings() recomputes _enabled.
        self._per_radio_enabled = enabled
        self._enabled = enabled and WHISPER_AVAILABLE
        self._current_model_size = ""
        self._current_language: Optional[str] = _DEFAULT_LANGUAGE
        self._radio_label = radio_label
        if device_index is not None:
            self._recorder.set_device(device_index)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply_settings(self) -> None:
        """Read QSettings and (re)configure recorder, writer, and model.

        device_index is set at construction time and is not read here.
        Effective _enabled is recomputed each call from per-radio + global flags.
        """
        settings = load_prefs()

        pt_enabled = settings.value("transcription/passthrough_enabled", False, type=bool)
        out_device_index = settings.value("transcription/output_device_index", None)
        if out_device_index is not None:
            try:
                out_device_index = int(out_device_index)
            except (ValueError, TypeError):
                out_device_index = None
        self._recorder.set_passthrough(pt_enabled, out_device_index)

        transcript_dir = settings.value("transcription/transcript_dir", "")
        self._writer.set_directory(transcript_dir)

        language = settings.value("transcription/language", _DEFAULT_LANGUAGE) or None
        if language != self._current_language:
            self._current_language = language
            if self._worker is not None:
                self._worker.set_language(language)

        global_enabled = settings.value("transcription/enabled", True, type=bool)
        self._enabled = self._per_radio_enabled and global_enabled and WHISPER_AVAILABLE

        model_size = settings.value("transcription/model_size", _DEFAULT_MODEL)
        if self._enabled and model_size != self._current_model_size:
            self._load_model(model_size)

        if not self._enabled:
            # Keep model in memory if already loaded — just don't use it
            if not WHISPER_AVAILABLE:
                log.debug("TranscriptionManager: openai-whisper not installed")
            elif not global_enabled:
                log.debug("TranscriptionManager: globally disabled in preferences")
            else:
                log.debug("TranscriptionManager: per-radio transcription disabled")

    def set_transcript_writer(self, writer: TranscriptWriter) -> None:
        """Inject a shared TranscriptWriter so multiple radios write to one file."""
        self._writer = writer

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def recapture_noise_profile(self) -> None:
        """Discard the pass-through noise profile and capture a fresh one."""
        self._recorder.recapture_noise_profile()

    def on_transmission_started(self) -> None:
        if not self._enabled:
            return
        self._recorder.start_recording()

    def on_transmission_ended(self, row_index: int, entry) -> None:
        """
        Called when a transmission ends. Stops recording and enqueues a job.
        `entry` is a _TransmissionEntry from log_panel.
        """
        if not self._enabled:
            return
        audio = self._recorder.stop_recording()
        if audio is None:
            log.debug("TranscriptionManager: no audio captured for row %d", row_index)
            self.transcription_ready.emit(row_index, "", None)
            return
        self._maybe_save_audio(audio, entry)
        if self._worker is None or self._model is None:
            log.warning("TranscriptionManager: model not ready — dropping job for row %d",
                        row_index)
            return
        job = _TranscriptionJob(
            audio=audio,
            row_index=row_index,
            entry_start_iso=entry.start_time.isoformat(),
            channel=entry.channel,
            frequency=entry.frequency,
            system=entry.system,
            group=entry.group,
            radio=self._radio_label,
        )
        self._worker.enqueue(job)

    def _maybe_save_audio(self, audio, entry) -> None:
        """Save the audio clip to disk if the retain-audio preference is enabled."""
        settings = load_prefs()
        if not settings.value("transcription/retain_audio", False, type=bool):
            return
        save_dir = settings.value("transcription/audio_save_dir", "").strip()
        if not save_dir:
            save_dir = str(Path.home() / "Documents" / "NeoSCAN" / "Recordings")
        try:
            out_dir = Path(save_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            radio_name = re.sub(r"[^\w\-]", "_", self._radio_label or "radio")
            timestamp = entry.start_time.strftime("%Y%m%d-%H%M%S")
            filename = out_dir / f"{radio_name}-{timestamp}.wav"
            import numpy as np
            from scipy.io import wavfile
            pcm = (audio * 32767).clip(-32768, 32767).astype(np.int16)
            wavfile.write(str(filename), 16000, pcm)
            log.info("TranscriptionManager: saved audio to %s", filename)
        except Exception as exc:
            log.warning("TranscriptionManager: failed to save audio — %s", exc)

    def on_transcription_done(self, row_index: int, text: str, job: _TranscriptionJob) -> None:
        """Called by LogPanel after it has updated the table row."""
        self._writer.append(
            start_iso=job.entry_start_iso,
            channel=job.channel,
            frequency=job.frequency,
            system=job.system,
            group=job.group,
            text=text,
            radio=job.radio,
        )

    def shutdown(self) -> None:
        """Stop all background threads cleanly."""
        # Shut down the loky executor used internally by noisereduce / joblib
        # to prevent leaked semaphore warnings at process exit.
        try:
            from joblib.externals.loky import get_reusable_executor
            get_reusable_executor().shutdown(wait=False)
        except Exception:
            pass

        self._recorder.close()

        if self._worker:
            self._worker.stop()
            # Allow up to 15 s for any in-progress Whisper job to finish.
            # If it still hasn't stopped, terminate() it so the QThread is
            # not destroyed while still running (causes abort on macOS).
            if not self._worker.wait(15000):
                log.warning("TranscriptionManager: worker did not stop in time — terminating")
                self._worker.terminate()
                self._worker.wait(2000)
            self._worker = None

        if self._loader:
            # Model loader has no stop mechanism; wait up to 30 s then force-terminate.
            if not self._loader.wait(30000):
                log.warning("TranscriptionManager: model loader did not finish in time — terminating")
                self._loader.terminate()
                self._loader.wait(2000)
            self._loader = None

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _load_model(self, model_size: str) -> None:
        # Shut down existing worker before replacing model
        if self._worker:
            self._worker.stop()
            self._worker.wait(3000)
            self._worker = None

        self._current_model_size = model_size

        if self._loader:
            self._loader.wait(3000)

        self._loader = _ModelLoaderThread(model_size, parent=self)
        self._loader.model_loaded.connect(self._on_model_loaded)
        self._loader.load_failed.connect(self._on_model_load_failed)
        self._loader.finished.connect(self._loader.deleteLater)
        self._loader.start()

    def _on_model_loaded(self, model) -> None:
        self._model = model
        self._loader = None
        self._worker = TranscriberWorker(model, language=self._current_language, parent=self)
        # Use an explicit slot (not signal-to-signal) so Qt's auto-connection
        # correctly marshals the call to the main thread via a queued connection.
        self._worker.transcription_ready.connect(self._on_worker_transcription_ready)
        self._worker.start()
        log.info("TranscriptionManager: worker started with model '%s'",
                 self._current_model_size)

    @pyqtSlot(int, str, object)
    def _on_worker_transcription_ready(self, row_index: int, text: str, job) -> None:
        """Relay worker result to main thread, then re-emit for LogPanel."""
        self.transcription_ready.emit(row_index, text, job)

    def _on_model_load_failed(self, error: str) -> None:
        self._loader = None
        log.error("TranscriptionManager: model load failed — %s", error)
