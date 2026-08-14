"""
Transcription pipeline for NeoSCAN.

TranscriptionManager  — owned by MainWindow; bridges the log panel, recorder,
                         worker thread, and transcript writer.
TranscriberWorker     — QThread that processes transcription jobs serially,
                         sending audio to a whisper-wrapper HTTP server.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt6.QtCore import (
    QMutex,
    QMutexLocker,
    QObject,
    QSemaphore,
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

_DEFAULT_MODEL = "base"
_DEFAULT_LANGUAGE = DEFAULT_LANGUAGE
_DEFAULT_SERVER_URL = "http://localhost:8000"
_MAX_QUEUE_DEPTH = 10   # drop new jobs rather than let memory and latency grow unbounded
_MAX_AUDIO_SECS = 60    # cap audio sent to server at this many seconds
_DEFAULT_VAD = False    # scanner audio is already squelch-gated; VAD tends to drop real speech

# Each radio gets its own TranscriberWorker thread, and _process() below does
# GIL-holding numpy work (normalize, has-speech) plus a blocking HTTP call.
# When multiple radios' workers do this at once, they starve the real-time
# audio capture callback of GIL time, which is what AudioRecorder's
# under-delivery warning ("suspect GIL/CPU starvation") is detecting. Capping
# how many workers may be inside _process() at the same time, process-wide,
# keeps that contention bounded regardless of how many radios are busy.
_MAX_CONCURRENT_TRANSCRIPTIONS = 1
_transcription_slots = QSemaphore(_MAX_CONCURRENT_TRANSCRIPTIONS)

# Loudness normalization. The reference level is a high percentile of the
# sample magnitude rather than the single peak or the RMS: a percentile ignores
# rare transients (key-up pops, static clicks) that otherwise pin the gain and
# leave real speech buried — the failure mode that produced ~50% "no audio".
# We scale that reference to a target, then hard-clip the few transient samples
# that exceed the ceiling instead of rescaling the whole clip back down.
_NORM_PERCENTILE = 90    # reference = this percentile of |sample|
_NORM_TARGET = 0.25      # map the reference to ~ -12 dBFS
_LIMIT_PEAK = 0.97       # hard ceiling just below full scale
_MAX_GAIN = 100.0        # cap on boost; a clean line input never needs more, and
                         # the speech guard below already rejects dead/quiet clips
_SILENCE_REF = 1e-4      # below this the clip is effectively silent; leave it alone

# Speech-presence guard. A real transmission has a fluctuating envelope (loud
# words, quiet gaps); a hum, steady tone, or DC offset has a near-constant
# envelope. We frame the DC-blocked signal and compare the loudest frame to the
# median frame — a flat envelope means no speech, so we skip transcription
# rather than let the server hallucinate "Thank you" on a dead input.
_SPEECH_FRAME = 480           # ~30 ms at 16 kHz
_SPEECH_DYNAMIC_RATIO = 2.0   # max-frame / median-frame RMS below this = no speech
_SPEECH_MIN_PEAK = 1e-2       # ~-40 dBFS. Below this the clip is at the noise
                              # floor (DC-blocked idle is ~2e-4) — real speech
                              # peaks at 0.1-1.0, so this rejects squelch tails
                              # and dead key-ups that otherwise hallucinate.

# Output-side hallucination filter. The envelope guard above rejects flat/quiet
# clips, but static and squelch noise with healthy peaks and natural envelope
# variation still pass it, and Whisper transcribes that low-information audio as
# canonical training-set filler ("Thank you", "Thanks for watching"). These
# never carry real information, so we drop them after the server responds.
#
# Phrases are stored already-normalized (see _normalize_phrase): lowercase, no
# surrounding punctuation, single-spaced. A transmission whose *entire* content
# reduces to one of these is suppressed; a clip that merely contains "thank you"
# inside a longer utterance is kept.
_HALLUCINATION_PHRASES = frozenset({
    "",
    "you",
    "bye",
    "thank you",
    "thank you very much",
    "thank you so much",
    "thank you for watching",
    "thanks for watching",
    "thank you for watching this video",
    "thanks for watching this video",
    "please subscribe",
    "like and subscribe",
    "please like and subscribe",
    "subscribe to my channel",
    "see you next time",
    "see you in the next video",
    "i'll see you next time",
})
_NO_SPEECH_PROB_DROP = 0.85   # if every segment is this confident there is no
                              # speech, drop the result. Conservative: genuine
                              # speech sits far below this, so real audio is
                              # never suppressed by this gate.
_REPEAT_DROP_COUNT = 3        # a single sentence repeated this many times is a
                              # Whisper decode loop, not a real transmission.
_PUNCT_RE = re.compile(r"[^\w\s']+")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+")


def _normalize_phrase(text: str) -> str:
    """Lowercase, drop surrounding punctuation, collapse whitespace."""
    text = _PUNCT_RE.sub(" ", text.lower())
    return " ".join(text.split())


def _filter_hallucination(text: str, segments: list) -> str:
    """Return text, or "" if it looks like a Whisper hallucination on noise.

    Three independent signals, any of which suppresses the clip:
      * every segment reports near-certain no-speech (`no_speech_prob`);
      * the transcript is one short sentence repeated in a decode loop;
      * the whole transcript reduces to a known filler phrase.
    A real utterance that merely *contains* "thank you" is preserved.
    """
    text = text.strip()
    if not text:
        return ""

    # No word characters at all — "!", "...", "! ! !". Pure punctuation Whisper
    # emits on noise; carries no information regardless of the phrase rules below.
    if not _normalize_phrase(text):
        return ""

    # Whisper's own no-speech confidence. Only acts when *all* segments agree, so
    # a single real word anywhere in the clip keeps it.
    if segments:
        probs = [float(s.get("no_speech_prob") or 0.0) for s in segments]
        if probs and min(probs) >= _NO_SPEECH_PROB_DROP:
            return ""

    sentences = [
        _normalize_phrase(s) for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()
    ]
    unique = set(sentences)

    # Decode loop: the same sentence over and over (e.g. "I'm going to take a
    # break." x6). Real speech does not repeat one sentence verbatim this often.
    if len(unique) == 1 and len(sentences) >= _REPEAT_DROP_COUNT:
        return ""

    # Whole transcript is nothing but filler. Covers "Thank you.",
    # "Thanks for watching!", and "Thank you. Thank you." (one unique sentence).
    if unique and unique <= _HALLUCINATION_PHRASES:
        return ""

    return text


def _has_speech(raw: np.ndarray) -> bool:
    """True if the clip looks like speech rather than steady noise/DC.

    Heuristic on the short-time envelope: speech swings between loud and quiet
    frames; a constant input (hum, tone, DC bias) does not. Clips too short to
    judge are allowed through.
    """
    if raw.size < _SPEECH_FRAME * 2:
        return True
    x = raw.astype(np.float32, copy=False)
    x = x - float(x.mean())
    if float(np.max(np.abs(x))) < _SPEECH_MIN_PEAK:
        return False
    n = (x.size // _SPEECH_FRAME) * _SPEECH_FRAME
    frame_rms = np.sqrt(np.mean(x[:n].reshape(-1, _SPEECH_FRAME) ** 2, axis=1))
    med = float(np.median(frame_rms))
    if med < 1e-9:
        return True  # near-silent with isolated bursts — treat as real speech onset
    return (float(np.max(frame_rms)) / med) >= _SPEECH_DYNAMIC_RATIO


def _normalize_level(raw: np.ndarray) -> np.ndarray:
    """Bring a clip to a consistent loudness, robust to transient clicks.

    Removes any DC bias, then uses a high-percentile reference (not peak or
    RMS) so a single full-scale sample can't defeat the gain, then hard-clips
    any transients above the ceiling. Returns float32.
    """
    if raw.size == 0:
        return raw
    out = raw.astype(np.float32, copy=True)
    out -= float(out.mean())  # strip DC so the gain reference reflects real audio
    ref = float(np.percentile(np.abs(out), _NORM_PERCENTILE))
    if ref < _SILENCE_REF:
        return out  # essentially silent — amplifying would only raise the noise floor
    gain = min(_NORM_TARGET / ref, _MAX_GAIN)
    out *= gain
    np.clip(out, -_LIMIT_PEAK, _LIMIT_PEAK, out=out)
    return out

_MULTIPART_BOUNDARY = b"NeoSCANBoundary"


def _multipart_field(name: str, value: str) -> bytes:
    return (
        b"--" + _MULTIPART_BOUNDARY + b"\r\n"
        b'Content-Disposition: form-data; name="' + name.encode() + b'"\r\n\r\n'
        + value.encode() + b"\r\n"
    )


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


class TranscriberWorker(QThread):
    """
    Processes transcription jobs serially on a background thread,
    posting audio to a whisper-wrapper HTTP server.

    Signals:
        transcription_ready(row_index, text, job)
    """

    transcription_ready = pyqtSignal(int, str, object)

    def __init__(
        self,
        server_url: str,
        model_size: str,
        language: Optional[str] = _DEFAULT_LANGUAGE,
        vad: bool = _DEFAULT_VAD,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._server_url = server_url.rstrip("/")
        self._model_size = model_size
        self._language: Optional[str] = language
        self._vad = vad
        self._queue: list[_TranscriptionJob] = []
        self._mutex = QMutex()
        self._cond = QWaitCondition()
        self._running = True

    def set_language(self, language: Optional[str]) -> None:
        """Update the transcription language. Takes effect on the next job."""
        self._language = language

    def set_vad(self, enabled: bool) -> None:
        """Enable/disable server-side voice-activity detection. Next job."""
        self._vad = enabled

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

            # Block here, not inside _process(), so a worker waiting for its
            # turn doesn't hold the queue mutex and can still be stopped.
            _transcription_slots.acquire()
            try:
                self._process(job)
            finally:
                _transcription_slots.release()

    def _post(self, audio_bytes: bytes, vad: bool) -> dict:
        """Send one raw-f32-16k clip to the whisper-wrapper server.

        Returns the decoded JSON result. Raises on network/HTTP errors, which
        _process handles. `vad` toggles server-side voice-activity detection
        per request so the retry path can re-send with VAD on.
        """
        body = (
            b"--" + _MULTIPART_BOUNDARY + b"\r\n"
            b'Content-Disposition: form-data; name="audio"; filename="audio.raw"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\n"
            + audio_bytes + b"\r\n"
            + _multipart_field("format", "raw_f32_16k")
            + _multipart_field("model", self._model_size)
            + _multipart_field("vad", "true" if vad else "false")
            + (_multipart_field("language", self._language) if self._language else b"")
            + b"--" + _MULTIPART_BOUNDARY + b"--\r\n"
        )
        req = urllib.request.Request(
            f"{self._server_url}/v1/transcribe",
            data=body,
            headers={
                "Content-Type": (
                    f"multipart/form-data; boundary={_MULTIPART_BOUNDARY.decode()}"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read())

    def _process(self, job: _TranscriptionJob) -> None:
        raw = job.audio
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

            # Reject clips with no speech dynamics (steady hum/tone/DC offset)
            # before they reach the server, where amplified noise reliably
            # transcribes as a "Thank you"-style hallucination.
            if not _has_speech(raw):
                log.info(
                    "Transcription row %d: no speech detected (flat envelope, "
                    "peak=%.4f) — skipping", job.row_index, peak,
                )
                self.transcription_ready.emit(job.row_index, "", job)
                return

            # Normalize so the server sees a consistent loudness regardless of
            # input gain, without a single transient click defeating the gain
            # (which max-peak normalization suffered from).
            normalized = _normalize_level(raw)

            # Cap audio at _MAX_AUDIO_SECS so server latency and queue drain stay bounded.
            max_samples = int(_MAX_AUDIO_SECS * SAMPLE_RATE)
            if len(normalized) > max_samples:
                log.debug(
                    "TranscriberWorker: truncating %.1fs to %ds for row %d",
                    len(normalized) / SAMPLE_RATE, _MAX_AUDIO_SECS, job.row_index,
                )
                normalized = normalized[:max_samples]

            audio_bytes = normalized.astype(np.float32).tobytes()

            result = self._post(audio_bytes, self._vad)
            segments = result.get("segments", [])
            text = result.get("text", "").strip()
            suppressed = _filter_hallucination(text, segments)

            # Retry-with-VAD-on fallback. With VAD off, whisper occasionally
            # decodes a perfectly clean clip as bare punctuation ("!") — the
            # VAD pass trims the leading non-speech that derails the decoder.
            # So when the no-VAD result is empty/filtered, re-send the same clip
            # once with VAD enabled before giving up. Costs one extra request
            # only on otherwise-dead results, and keeps VAD off as the default
            # (which preserves quiet/short speech the VAD would drop).
            if not suppressed and not self._vad:
                log.info(
                    "Transcription row %d: empty/filtered with VAD off — "
                    "retrying once with VAD on", job.row_index,
                )
                retry = self._post(audio_bytes, True)
                r_segments = retry.get("segments", [])
                r_text = retry.get("text", "").strip()
                r_suppressed = _filter_hallucination(r_text, r_segments)
                if r_suppressed:
                    result, segments, text, suppressed = (
                        retry, r_segments, r_text, r_suppressed,
                    )

            for seg in segments:
                log.debug(
                    "  row %d seg [%.1f-%.1fs] no_speech_prob=%.3f %r",
                    job.row_index, seg["start"], seg["end"],
                    seg.get("no_speech_prob") or 0.0, seg["text"][:80],
                )
            if suppressed != text:
                log.info(
                    "Transcription row %d: suppressed likely hallucination %r "
                    "(noise/static clip, peak=%.4f)",
                    job.row_index, text[:200], peak,
                )
                text = suppressed
            elif text:
                log.info("Transcription row %d: %r", job.row_index, text[:200])
            else:
                # Server itself returned nothing — distinct from a clip we
                # suppressed — so the cable/gain hint is still worth surfacing.
                log.warning(
                    "Transcription row %d: server returned empty text for %.1fs of audio — "
                    "check scanner audio cable, input device, and volume.",
                    job.row_index, duration,
                )
            self.transcription_ready.emit(job.row_index, text, job)

        except urllib.error.URLError as exc:
            log.warning(
                "Transcription row %d: could not reach whisper-wrapper server (%s) — skipping",
                job.row_index, exc.reason,
            )
            self.transcription_ready.emit(job.row_index, "", job)
        except TimeoutError:
            log.warning(
                "Transcription row %d: request to whisper-wrapper timed out — skipping",
                job.row_index,
            )
            self.transcription_ready.emit(job.row_index, "", job)
        except Exception as exc:
            log.error("Transcription failed for row %d: %s", job.row_index, exc)
            self.transcription_ready.emit(job.row_index, f"[error: {exc}]", job)


class TranscriptionManager(QObject):
    """
    Coordinates audio recording, whisper-wrapper transcription, and file writing.
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
        self._per_radio_enabled = enabled
        self._enabled = enabled
        self._server_url = _DEFAULT_SERVER_URL
        self._current_model_size = ""
        self._current_language: Optional[str] = _DEFAULT_LANGUAGE
        self._current_vad = _DEFAULT_VAD
        self._radio_label = radio_label
        if device_index is not None:
            self._recorder.set_device(device_index)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply_settings(self) -> None:
        """Read QSettings and (re)configure recorder, writer, and worker.

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

        vad = settings.value("transcription/vad_enabled", _DEFAULT_VAD, type=bool)
        if vad != self._current_vad:
            self._current_vad = vad
            if self._worker is not None:
                self._worker.set_vad(vad)

        global_enabled = settings.value("transcription/enabled", True, type=bool)
        self._enabled = self._per_radio_enabled and global_enabled

        server_url = settings.value(
            "transcription/whisper_server_url", _DEFAULT_SERVER_URL
        ).strip() or _DEFAULT_SERVER_URL
        model_size = settings.value("transcription/model_size", _DEFAULT_MODEL)

        if self._enabled:
            if (self._worker is None
                    or server_url != self._server_url
                    or model_size != self._current_model_size):
                self._server_url = server_url
                self._current_model_size = model_size
                self._start_worker()
        else:
            if not global_enabled:
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

    # --- Level meter support (driven by the per-radio control panel) ---

    def start_audio_monitoring(self) -> bool:
        """Open the input stream so the level meter reads live audio even when
        no transmission is in progress. No-op without an input device."""
        return self._recorder.start_monitoring()

    def current_audio_level(self) -> float:
        """Most recent input level in [0, 1] for the meter."""
        return self._recorder.current_level()

    def is_capturing_audio(self) -> bool:
        """True while the input stream is delivering audio (meter active)."""
        return self._recorder.is_capturing_audio()

    def maybe_recycle_stream(self) -> None:
        """Recycle the input stream if it has been open long enough to risk
        age-related degradation. No-op while a capture session is active, so it
        is safe to call every poll for an idle radio."""
        self._recorder.recycle_if_idle_and_stale()

    def on_transmission_started(self) -> None:
        if not self._enabled:
            return
        self._recorder.start_recording()

    def on_transmission_paused(self) -> None:
        """Squelch closed but the conversation may continue — pause capture so
        the inter-key-up gap is excised from the merged clip."""
        if not self._enabled:
            return
        self._recorder.pause_recording()

    def on_transmission_resumed(self) -> None:
        """The same channel keyed up again within the grace window — resume
        capturing into the same clip."""
        if not self._enabled:
            return
        self._recorder.resume_recording()

    def on_transmission_ended(self, row_index: int, entry) -> None:
        """
        Called when a transmission ends. Stops recording and enqueues a job.
        `entry` is a _TransmissionEntry from log_panel.
        """
        if not self._enabled:
            return
        audio = self._recorder.stop_recording()
        if audio is None:
            log.warning("TranscriptionManager: no audio captured for row %d "
                        "(recorder returned nothing)", row_index)
            self.transcription_ready.emit(row_index, "", None)
            return
        self._maybe_save_audio(audio, entry)
        if self._worker is None:
            log.warning("TranscriptionManager: worker not ready — dropping job for row %d",
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
        self._recorder.close()

        if self._worker:
            self._worker.stop()
            # Allow up to 15 s for any in-progress job to finish.
            if not self._worker.wait(15000):
                log.warning("TranscriptionManager: worker did not stop in time — terminating")
                self._worker.terminate()
                self._worker.wait(2000)
            self._worker = None

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _start_worker(self) -> None:
        """Create and start a fresh TranscriberWorker."""
        if self._worker:
            self._worker.stop()
            self._worker.wait(3000)
            self._worker = None

        self._worker = TranscriberWorker(
            self._server_url,
            self._current_model_size,
            language=self._current_language,
            vad=self._current_vad,
            parent=self,
        )
        self._worker.transcription_ready.connect(self._on_worker_transcription_ready)
        self._worker.start()
        log.info(
            "TranscriptionManager: worker started (server=%s, model=%s)",
            self._server_url, self._current_model_size,
        )

    @pyqtSlot(int, str, object)
    def _on_worker_transcription_ready(self, row_index: int, text: str, job) -> None:
        """Relay worker result to main thread, then re-emit for LogPanel."""
        self.transcription_ready.emit(row_index, text, job)
