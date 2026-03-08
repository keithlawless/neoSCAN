"""
Frequency band plans for supported scanner models.

Each entry is a list of (low_MHz, high_MHz) inclusive ranges.
Unknown models fall back to DEFAULT (SDS200 — the broadest current Uniden model).
"""
from __future__ import annotations

FreqRange = tuple[float, float]  # (low_MHz, high_MHz), inclusive

BAND_PLANS: dict[str, list[FreqRange]] = {
    # BCT15X: 25–512, 764–775.987, 794–805.987, 806–823.987,
    #         849–868.987, 894.012–956, 1240–1300 MHz
    "BCT15X": [
        (25.0, 512.0),
        (764.0, 775.987),
        (794.0, 805.987),
        (806.0, 823.987),
        (849.0, 868.987),
        (894.012, 956.0),
        (1240.0, 1300.0),
    ],
    # BCD996XT: identical coverage to BCT15X
    "BCD996XT": [
        (25.0, 512.0),
        (764.0, 775.987),
        (794.0, 805.987),
        (806.0, 823.987),
        (849.0, 868.987),
        (894.012, 956.0),
        (1240.0, 1300.0),
    ],
    # SDS200: broader 700/800 MHz block, extends to 960 MHz
    "SDS200": [
        (25.0, 512.0),
        (758.0, 824.0),
        (849.0, 869.0),
        (895.0, 960.0),
        (1240.0, 1300.0),
    ],
    # SDS100: same coverage as SDS200
    "SDS100": [
        (25.0, 512.0),
        (758.0, 824.0),
        (849.0, 869.0),
        (895.0, 960.0),
        (1240.0, 1300.0),
    ],
    # BCD325P2: 25–512, 758–824, 849–869, 895–960 MHz (no 1240 band)
    "BCD325P2": [
        (25.0, 512.0),
        (758.0, 824.0),
        (849.0, 869.0),
        (895.0, 960.0),
    ],
    # BCD996P2: 25–512, 758–823.9875, 849.0125–868.9875, 894.0125–960, 1240–1300 MHz
    "BCD996P2": [
        (25.0, 512.0),
        (758.0, 823.9875),
        (849.0125, 868.9875),
        (894.0125, 960.0),
        (1240.0, 1300.0),
    ],
    # BCD536HP: same as SDS200
    "BCD536HP": [
        (25.0, 512.0),
        (758.0, 824.0),
        (849.0, 869.0),
        (895.0, 960.0),
        (1240.0, 1300.0),
    ],
}

# Default fallback — SDS200 (broadest current Uniden model)
BAND_PLANS["DEFAULT"] = BAND_PLANS["SDS200"]


def get_band_plan(model: str) -> list[FreqRange]:
    """Return the frequency ranges for the given model string.
    Falls back to DEFAULT (SDS200) for unknown or empty models."""
    return BAND_PLANS.get(model.strip().upper(), BAND_PLANS["DEFAULT"])


def is_frequency_valid(freq_mhz: float, model: str) -> bool:
    """Return True if freq_mhz falls within any supported range for model."""
    for lo, hi in get_band_plan(model):
        if lo <= freq_mhz <= hi:
            return True
    return False
