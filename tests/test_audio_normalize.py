"""Tests for transcription audio level normalization and the meter dB mapping."""
import numpy as np
import pytest

from app.audio.transcriber import (
    _normalize_level,
    _filter_hallucination,
    _LIMIT_PEAK,
    _NO_SPEECH_PROB_DROP,
)


def _rms(x):
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))


def test_quiet_clip_is_amplified_into_healthy_range():
    rng = np.random.default_rng(0)
    quiet = (rng.standard_normal(16000) * 0.003).astype(np.float32)
    out = _normalize_level(quiet)
    assert 0.05 < _rms(out) < 0.5
    assert np.max(np.abs(out)) <= _LIMIT_PEAK + 1e-6


def test_single_transient_does_not_defeat_normalization():
    """A lone full-scale click (key-up pop) must not leave the speech buried —
    this was the core bug behind ~50% 'no audio' results."""
    rng = np.random.default_rng(1)
    clip = (rng.standard_normal(16000) * 0.003).astype(np.float32)
    clip[100] = 1.0  # transient spike
    out = _normalize_level(clip)
    body = np.delete(out, 100)
    assert _rms(body) > 0.05, "transient pinned the gain and buried the speech"
    assert np.max(np.abs(out)) <= _LIMIT_PEAK + 1e-6, "peak ceiling exceeded"


def test_silence_is_left_untouched():
    sil = np.zeros(16000, dtype=np.float32)
    assert np.array_equal(_normalize_level(sil), sil)


def test_empty_input_is_safe():
    empty = np.zeros(0, dtype=np.float32)
    assert _normalize_level(empty).size == 0


def test_loud_clip_is_brought_down_not_clipped_to_death():
    rng = np.random.default_rng(2)
    loud = (rng.standard_normal(16000) * 0.4).astype(np.float32)
    out = _normalize_level(loud)
    assert np.max(np.abs(out)) <= _LIMIT_PEAK + 1e-6
    # Most samples should survive without hard-clipping (healthy crest factor).
    clipped_frac = np.mean(np.abs(out) >= _LIMIT_PEAK)
    assert clipped_frac < 0.02


# --- Hallucination filtering ---------------------------------------------

# Real "Thank you"-style filler observed in the overnight log; these clips were
# noise/static with healthy peaks, so the envelope guard let them through.
@pytest.mark.parametrize("text", [
    "Thank you.",
    "thank you",
    "Thank you. Thank you.",
    "Thanks for watching!",
    "Thank you for watching this video.",
    "Please subscribe",
    "you",
    "Bye.",
    "",
])
def test_filler_only_clips_are_suppressed(text):
    assert _filter_hallucination(text, []) == ""


@pytest.mark.parametrize("text", ["!", "...", "! ! ! ! !", "?!", ". . ."])
def test_punctuation_only_clips_are_suppressed(text):
    assert _filter_hallucination(text, []) == ""


def test_decode_loop_is_suppressed():
    looped = " ".join(["I'm going to take a break."] * 6)
    assert _filter_hallucination(looped, []) == ""


def test_real_speech_containing_thanks_is_kept():
    text = "Engine 4 on scene, thank you dispatch."
    assert _filter_hallucination(text, []) == text


def test_real_speech_is_untouched():
    text = "Unit 12 responding to the call on Main Street."
    assert _filter_hallucination(text, []) == text


def test_high_no_speech_prob_drops_result():
    segs = [{"no_speech_prob": _NO_SPEECH_PROB_DROP + 0.05}]
    assert _filter_hallucination("Some words here.", segs) == ""


def test_one_confident_speech_segment_keeps_result():
    # min() across segments stays below the gate if any segment has real speech.
    segs = [
        {"no_speech_prob": 0.95},
        {"no_speech_prob": 0.10},
    ]
    text = "Dispatch to all units."
    assert _filter_hallucination(text, segs) == text


def test_meter_db_mapping_is_monotonic_and_bounded():
    from app.ui.remote_control.control_panel import _LevelMeter as M
    assert M._level_to_frac(0.0) == 0.0
    assert abs(M._level_to_frac(1.0) - 1.0) < 1e-6
    fracs = [M._level_to_frac(10 ** (db / 20)) for db in (-60, -40, -20, -6, 0)]
    assert fracs == sorted(fracs)
    assert all(0.0 <= f <= 1.0 for f in fracs)
