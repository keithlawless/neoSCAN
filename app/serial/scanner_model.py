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
SYSTEM_TYPE_MAP: dict[str, int] = {
    "CNV":  1,   # Conventional
    "RACE": 1,   # Conventional alias (SC230)
    "M81S": 2,   "M81P": 2,  "M81C": 2,   # Motorola Type I
    "M82S": 3,   "M82P": 3,  "M92": 3,    # Motorola Type II
    "MV2":  3,   "MU2": 3,   "MP25": 3,   # Motorola
    "M82C": 3,
    "EDN":  4,   "EDW": 4,               # EDACS narrow/wide
    "EDS":  5,                            # EDACS standard
    "LTR":  6,                            # LTR
}

INT_TO_SIN_TYPE: dict[int, str] = {
    1: "CNV",
    2: "M81S",
    3: "M82S",
    4: "EDN",
    5: "EDS",
    6: "LTR",
}


def sin_type_to_internal(sin_type: str) -> int:
    return SYSTEM_TYPE_MAP.get(sin_type.strip().upper(), 1)


def internal_to_sin_type(system_type: int) -> str:
    return INT_TO_SIN_TYPE.get(system_type, "CNV")
