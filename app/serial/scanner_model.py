"""
Model-specific command sets and field translations.
Covers BCT15-X / BCD996XT (XT-series) and BCD996P2 (digital).
"""
from __future__ import annotations

import re

# Modulation mode index → scanner string (for CIN upload)
MOD_MODE_STRINGS = ["AUTO", "AM", "FM", "NFM", "WFM", "FMB"]


def mod_mode_to_string(index: str | int) -> str:
    try:
        return MOD_MODE_STRINGS[int(index)]
    except (IndexError, ValueError):
        return "AUTO"


def string_to_mod_mode(s: str) -> str:
    """Return mod mode index string, or '0' for AUTO."""
    s = s.strip().upper()
    if s in MOD_MODE_STRINGS:
        return str(MOD_MODE_STRINGS.index(s))
    return "0"


def rev_mod_mode_lookup(scanner_str: str) -> str:
    """Convert scanner mod string (e.g. 'FM') to index string."""
    return string_to_mod_mode(scanner_str)


# System type string from SIN response → internal system type int
# Includes both the scanner's own CSY/SIN codes (MOT, EDC, EDS, LTR, P25S, P25F)
# and FreeSCAN's .996 codes (M81S, M82S, etc.) for file round-trip.
SYSTEM_TYPE_MAP: dict[str, int] = {
    # Scanner native SIN GET type strings (returned by SIN,<idx> on the scanner)
    "CNV":  1,   # Conventional
    "MOT":  2,   # Motorola (BCT15X/BCD996XT SIN response code)
    "EDC":  4,   # EDACS Narrow/Wide (scanner code; not yet supported)
    "EDS":  5,   # EDACS SCAT (scanner code; not yet supported)
    "LTR":  6,   # LTR (scanner code; not yet supported)
    "P25S": 5,   # P25 Standard (scanner code; not yet supported)
    "P25F": 7,   # P25 One-Frequency Trunk (scanner code; not yet supported)
    # FreeSCAN .996 file codes (also returned by older scanner firmware)
    "RACE": 1,   # Conventional alias (SC230)
    "M81S": 2,   "M81P": 2,  "M81C": 2,   # Motorola Type I
    "M82S": 3,   "M82P": 3,  "M92": 3,    # Motorola Type II
    "MV2":  3,   "MU2": 3,   "MP25": 3,   # Motorola
    "M82C": 3,
    "EDN":  4,   "EDW": 4,               # EDACS narrow/wide
    "TRBO": 8,   # MotoTRBO
    "DMR":  9,   # DMR One Frequency Trunk
}

INT_TO_SIN_TYPE: dict[int, str] = {
    1: "CNV",
    2: "M81S",
    3: "M82S",
    4: "EDN",
    5: "P25S",
    6: "LTR",
    7: "P25F",
    8: "TRBO",
    9: "DMR",
}

# CSY command accepts a simpler set of type codes than SIN.
# Maps internal system_type int → CSY type string.
# Internal constants (from models.py):
#   1=Conventional, 2=Motorola, 3=EDACS, 4=LTR, 5=P25, 6=EDACS ProVoice, 7=P25(EDACS)
INT_TO_CSY_TYPE: dict[int, str] = {
    1: "CNV",   # Conventional
    2: "MOT",   # Motorola Type I
    3: "MOT",   # Motorola Type II (type 3 in .996 = "Motorola Type II / EDACS"; use MOT)
    4: "LTR",   # LTR
    5: "P25S",  # P25 standard
    6: "EDS",   # EDACS SCAT / ProVoice
    7: "P25F",  # P25 one-frequency trunk
    8: "TRBO",  # MotoTRBO
    9: "DMR",   # DMR One Frequency Trunk
}


def sin_type_to_internal(sin_type: str) -> int:
    return SYSTEM_TYPE_MAP.get(sin_type.strip().upper(), 1)


def internal_to_sin_type(system_type: int) -> str:
    return INT_TO_SIN_TYPE.get(system_type, "CNV")


def internal_to_csy_type(system_type: int) -> str:
    """Return the CSY command type code for a given internal system type."""
    return INT_TO_CSY_TYPE.get(system_type, "CNV")


# ---------------------------------------------------------------------------
# CTCSS / DCS tone codes
# ---------------------------------------------------------------------------
# The scanner (and the .996 file) store the CTCSS/DCS tone as a numeric CODE,
# not the Hz value.  e.g. 114.8 Hz CTCSS is code 80, not "114.8".  Sending the
# raw Hz value programs the wrong (or an invalid) tone.  These tables come from
# the "CTCSS/DCS CODE LIST" in each model's protocol reference.
#
# NONE/All=0 and SEARCH=127 and the 50 CTCSS codes (64-113) are identical
# across all three supported models.  Only the DCS list differs: the digital
# BCD996P2 defines 8 extra DCS codes (232-239) that the analog BCT15X /
# BCD996XT do not.
TONE_NONE_CODE = "0"
TONE_SEARCH_CODE = "127"

# CTCSS Hz values in code order — index 0 is code 64, index 49 is code 113.
_CTCSS_BASE = 64
_CTCSS_HZ: list[float] = [
    67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5,
    94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3,
    131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9,
    171.3, 173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5,
    203.5, 206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1,
]

# DCS codes (3-digit octal labels) in code order — index 0 is code 128.
_DCS_BASE = 128
_DCS_COMMON: list[str] = [
    "023", "025", "026", "031", "032", "036", "043", "047", "051", "053",
    "054", "065", "071", "072", "073", "074", "114", "115", "116", "122",
    "125", "131", "132", "134", "143", "145", "152", "155", "156", "162",
    "165", "172", "174", "205", "212", "223", "225", "226", "243", "244",
    "245", "246", "251", "252", "255", "261", "263", "265", "266", "271",
    "274", "306", "311", "315", "325", "331", "332", "343", "346", "351",
    "356", "364", "365", "371", "411", "412", "413", "423", "431", "432",
    "445", "446", "452", "454", "455", "462", "464", "465", "466", "503",
    "506", "516", "523", "526", "532", "546", "565", "606", "612", "624",
    "627", "631", "632", "654", "662", "664", "703", "712", "723", "731",
    "732", "734", "743", "754",
]
# BCD996P2-only extension (codes 232-239).
_DCS_P2_EXTRA: list[str] = ["006", "007", "015", "017", "021", "050", "141", "214"]


def _model_has_extended_dcs(model: str | None) -> bool:
    """True for models with the extended DCS list (digital BCD996P2)."""
    return "996P2" in (model or "").upper()


def _dcs_labels(model: str | None) -> list[str]:
    labels = list(_DCS_COMMON)
    if _model_has_extended_dcs(model):
        labels += _DCS_P2_EXTRA
    return labels


def tone_label_for_code(code: str) -> str | None:
    """Human label for a stored tone code, e.g. '80' -> '114.8 PL'.

    Returns None if the code is not a recognised CTCSS/DCS code.  DCS labels
    are recognised across all models (superset) so codes downloaded from one
    model still display when editing under another.
    """
    code = (code or "").strip()
    if not code.isdigit():
        return None
    n = int(code)
    if n == 0:
        return "None"
    if n == int(TONE_SEARCH_CODE):
        return "Search"
    if _CTCSS_BASE <= n < _CTCSS_BASE + len(_CTCSS_HZ):
        return f"{_CTCSS_HZ[n - _CTCSS_BASE]:.1f} PL"
    all_dcs = _DCS_COMMON + _DCS_P2_EXTRA
    if _DCS_BASE <= n < _DCS_BASE + len(all_dcs):
        return f"DCS {all_dcs[n - _DCS_BASE]}"
    return None


def ctcss_dcs_tone_options(model: str | None = None) -> list[tuple[str, str]]:
    """Ordered (code, label) options for a tone drop-down, scoped to `model`.

    Falls back to the common (analog) DCS list when the model is unknown, so a
    disconnected editor never offers a code the target radio can't store.
    """
    options: list[tuple[str, str]] = [
        (TONE_NONE_CODE, "None"),
        (TONE_SEARCH_CODE, "Search"),
    ]
    for i, hz in enumerate(_CTCSS_HZ):
        options.append((str(_CTCSS_BASE + i), f"{hz:.1f} PL"))
    for i, label in enumerate(_dcs_labels(model)):
        options.append((str(_DCS_BASE + i), f"DCS {label}"))
    return options


def parse_tone_to_code(text: str, model: str | None = None) -> str | None:
    """Translate a human tone string to its scanner code, or None if unknown.

    Accepts the forms found in RadioReference exports and hand-entry:
      '', 'None', 'off'            -> '0'
      'Search'                     -> '127'
      '114.8', '114.8 PL', '114.8Hz', 'CTCSS 114.8'  -> '80'
      'D023', '023 DPL', 'DCS 023' -> DCS code
      an already-valid numeric code (e.g. '80') is passed through

    A bare integer that is not a valid code (e.g. '114', the truncated result
    of the old Hz-passthrough bug) returns None so the caller can surface it as
    invalid rather than silently mis-mapping it to DCS 114.
    """
    if text is None:
        return None
    s = text.strip()
    if s == "" or s.lower() in ("none", "off", "no tone", "no", "all"):
        return TONE_NONE_CODE
    if s.lower() in ("search", "srch"):
        return TONE_SEARCH_CODE

    # Already a valid numeric code?
    if s.isdigit() and tone_label_for_code(s) is not None:
        return s

    upper = s.upper()

    # DCS forms — require an explicit marker (DCS/DPL, or a leading 'D') so a
    # bare integer is never guessed as DCS. The marker may precede or follow
    # the digits ("D023", "023 DPL", "DCS 023").
    is_dcs = "DPL" in upper or "DCS" in upper or bool(re.match(r"D\s*\d", upper))
    if is_dcs:
        digit_m = re.search(r"(\d+)", s)
        if digit_m:
            digits = digit_m.group(1).zfill(3)
            labels = _dcs_labels(model)
            if digits in labels:
                return str(_DCS_BASE + labels.index(digits))
        return None

    # CTCSS forms — a decimal Hz value, optionally decorated with PL/Hz/CTCSS.
    ctcss_m = re.search(r"(\d+\.\d+)", s)
    if ctcss_m:
        hz = round(float(ctcss_m.group(1)), 1)
        for i, ref in enumerate(_CTCSS_HZ):
            if abs(ref - hz) < 0.05:
                return str(_CTCSS_BASE + i)
        return None

    return None
