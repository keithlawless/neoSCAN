# whisper-wrapper catchup features

NeoSCAN's local Whisper integration applied several transcription-quality optimizations
before calling the model. Now that transcription is offloaded to whisper-wrapper, these
should be implemented server-side so the quality is preserved for all clients.

Each item below is safe to add as a new **optional request parameter** with the recommended
value as the server-side default, so existing clients continue to work without changes.

---

## 1. Tuned VAD parameters

The local pipeline used silero-vad with parameters tuned for scanner traffic, where
utterances are often just a few words and squelch tails are common:

| Parameter | Recommended value | Rationale |
|---|---|---|
| `vad_threshold` | `0.5` | Silero default; catches most speech without too many false positives |
| `vad_min_speech_ms` | `250` | Short scanner transmissions (single word, unit ID) would be dropped at higher values |
| `vad_min_silence_ms` | `100` | Keeps adjacent words from being split across chunks |
| `vad_speech_pad_ms` | `150` | Adds context around each speech chunk so Whisper doesn't lose word onsets/offsets |

These map to silero-vad's `get_speech_timestamps()` arguments (or faster-whisper's VAD
options if using the built-in VAD filter).

---

## 2. Peak normalization

Before transcription, the audio was normalized to peak = 1.0:

```python
normalized = raw / peak if peak > 0.0 else raw
```

Scanner audio varies widely in gain depending on cable setup, input device, and system
type. Without normalization, quiet clips can fall below Whisper's internal energy
threshold and produce empty output or hallucinations.

NeoSCAN still does this client-side before sending the raw bytes, but the server should
also normalize if it receives audio via other formats (WAV, etc.) from clients that do not
pre-normalize.

---

## 3. End-of-clip silence padding

The local pipeline appended 1 second of zeros after the audio before calling Whisper:

```python
audio = np.concatenate([normalized, np.zeros(SAMPLE_RATE, dtype=np.float32)])
```

Without this, Whisper's decoder often drops the last incomplete segment when audio ends
abruptly (e.g., squelch closing mid-word). Adding padding forces the decoder to flush.

This should be applied server-side after VAD processing, before the model call.

---

## 4. `no_speech_threshold = 0.8`

The local call used a raised no-speech threshold:

```python
result = model.transcribe(audio, no_speech_threshold=0.8, ...)
```

Whisper's default is 0.6. Raising it to 0.8 suppresses hallucinations on
static-heavy or near-silent segments that are common in scanner recordings (carrier
tails, digital bursts, CTCSS tones). Expose this as an optional request parameter
with 0.8 as the default when `vad=true`.

---

## 5. `condition_on_previous_text = False`

```python
result = model.transcribe(audio, condition_on_previous_text=False, ...)
```

Each scanner transmission is independent. Enabling conditioning allows one segment's
hallucination to infect the next. This should be disabled by default on the server.

---

## 6. `fp16 = False`

```python
result = model.transcribe(audio, fp16=False, ...)
```

The original openai-whisper required `fp16=False` on CPU-only machines to avoid a
runtime warning and potential accuracy loss. faster-whisper handles precision via its
`compute_type` setting at model load time, so this may be a no-op on the server, but
it is worth documenting for completeness and for any openai-whisper fallback path.
