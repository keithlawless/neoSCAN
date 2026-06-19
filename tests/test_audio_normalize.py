"""Tests for transcription audio level normalization and the meter dB mapping."""
import numpy as np

from app.audio.transcriber import _normalize_level, _LIMIT_PEAK


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


def test_meter_db_mapping_is_monotonic_and_bounded():
    from app.ui.remote_control.control_panel import _LevelMeter as M
    assert M._level_to_frac(0.0) == 0.0
    assert abs(M._level_to_frac(1.0) - 1.0) < 1e-6
    fracs = [M._level_to_frac(10 ** (db / 20)) for db in (-60, -40, -20, -6, 0)]
    assert fracs == sorted(fracs)
    assert all(0.0 <= f <= 1.0 for f in fracs)
