"""
Model-specific command sets and field translations.
Covers BCT15-X / BCD996XT (XT-series).
"""
from __future__ import annotations

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
