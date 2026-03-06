"""
Core data model for NeoSCAN.

Mirrors the in-memory structure used by FreeSCAN (varSite, ChanInfo, etc.)
but expressed as clean Python dataclasses.

System types (varSite[sys][0][0][3]):
  1 = Conventional
  2 = Motorola
  3 = EDACS
  4 = LTR
  5 = P25 (standard/Motorola)
  6 = EDACS ProVoice
  7 = P25 (EDACS)
  ...
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# -----------------------------------------------------------------------
# System type constants
# -----------------------------------------------------------------------
SYS_TYPE_CONVENTIONAL = 1
SYS_TYPE_MOTOROLA = 2
SYS_TYPE_EDACS = 3
SYS_TYPE_LTR = 4
SYS_TYPE_P25 = 5
SYS_TYPE_EDACS_PV = 6
SYS_TYPE_P25_EDACS = 7

SYSTEM_TYPE_NAMES = {
    SYS_TYPE_CONVENTIONAL: "Conventional",
    SYS_TYPE_MOTOROLA: "Motorola",
    SYS_TYPE_EDACS: "EDACS",
    SYS_TYPE_LTR: "LTR",
    SYS_TYPE_P25: "P25",
    SYS_TYPE_EDACS_PV: "EDACS ProVoice",
    SYS_TYPE_P25_EDACS: "P25 (EDACS)",
}

MAX_SYSTEMS = 700
MAX_GROUPS = 277
MAX_CHANNELS = 500         # per trunked group
MAX_TRUNK_FREQ = 6000
MAX_SEARCH_LOCKOUTS = 500
MAX_RADIO_SETTINGS = 200
MAX_SYSTEM_SETTINGS = 62   # MaxSetting
MAX_GROUP_SETTINGS = 62    # same
MAX_CHAN_SETTINGS = 30     # MaxChanSetting

FILE_HEADER_CURRENT = ".7BCD996T"
FILE_HEADER_LEGACY = ".08BCD996T"


# -----------------------------------------------------------------------
# Channel (conventional)
# -----------------------------------------------------------------------
@dataclass
class Channel:
    """A single conventional channel entry."""
    name: str = ""
    frequency: str = ""         # MHz as string, e.g. "471.425"
    modulation: str = ""        # e.g. "FM", "AM", "NFM", "AUTO"
    tone: str = "0"             # CTCSS/DCS index (0=NONE)
    tone_lockout: bool = False
    lockout: bool = False
    priority: bool = False
    attenuator: bool = False
    alert_tone: str = "0"       # alert tone type index
    alert_level: str = "0"
    audio_type: str = "0"       # 0=all, 1=analog, 2=digital
    delay: str = ""             # scan delay in seconds
    number_tag: str = "NONE"
    output: str = "OFF"
    volume_offset: str = "0"
    comment: str = ""
    group_id: str = ""          # links channel to a group
    step_size: str = "0"
    p25_wait_time: str = "0"

    # Raw settings array for fields not yet individually modeled
    # settings[1..30] — populated from file, used for round-trip
    _raw: list[str] = field(default_factory=lambda: [""] * 31, repr=False)

    @property
    def is_trunked(self) -> bool:
        """Conventional channels have numeric frequencies."""
        try:
            float(self.frequency)
            return False
        except (ValueError, TypeError):
            return True

    def display_frequency(self) -> str:
        """Return frequency formatted for display."""
        try:
            return f"{float(self.frequency):.4f}"
        except (ValueError, TypeError):
            return self.frequency


# -----------------------------------------------------------------------
# Trunked talk group (stored in ChanInfo in FreeSCAN, same array)
# -----------------------------------------------------------------------
@dataclass
class TalkGroup:
    """A trunked talk group / channel."""
    name: str = ""
    tgid: str = ""              # talk group ID
    lockout: bool = False
    alert_tone: str = "0"
    alert_level: str = "0"
    audio_type: str = "0"
    record: bool = False
    group_id: str = ""
    _raw: list[str] = field(default_factory=lambda: [""] * 31, repr=False)


# -----------------------------------------------------------------------
# Trunk frequency (used for trunked systems)
# -----------------------------------------------------------------------
@dataclass
class TrunkFrequency:
    """A single trunk control/voice channel frequency."""
    frequency: str = ""
    lcn: str = ""
    group_id: str = ""
    lockout: bool = False
    # extra params [4..10]
    params: list[str] = field(default_factory=lambda: [""] * 8)


# -----------------------------------------------------------------------
# Group (a.k.a. Channel Group, or Site for trunked systems)
# -----------------------------------------------------------------------
@dataclass
class Group:
    """
    A group of channels within a System.

    For conventional systems: a simple channel group.
    For trunked systems: can be a site (type=3) or group (type=2).
    """
    name: str = ""
    quick_key: str = "."
    group_type: str = "2"       # 2=group, 3=site
    lockout: bool = False
    group_id: str = ""          # unique ID tying channels to this group

    # For GPS-aware groups
    gps_enable: bool = False
    gps_lat: str = "00000000N"
    gps_lon: str = "00000000E"
    gps_range: str = ""

    # Raw settings array for round-trip fidelity
    _raw: list[str] = field(default_factory=lambda: [""] * 63, repr=False)

    # Channels belonging to this group (populated after file load)
    channels: list[Channel | TalkGroup] = field(default_factory=list, repr=False)

    @property
    def is_site(self) -> bool:
        return self.group_type == "3"


# -----------------------------------------------------------------------
# System
# -----------------------------------------------------------------------
@dataclass
class System:
    """A scanner system (conventional or trunked)."""
    name: str = ""
    system_type: int = SYS_TYPE_CONVENTIONAL
    quick_key: str = "."
    hold_time: str = "2"
    delay_time: str = "2"
    startup_key: str = "."
    lockout: bool = False
    data_skip: bool = False
    qgl: str = "1111111111"     # quick group lockout pattern
    group_id: str = ""          # unique system ID

    # Motorola / trunked options
    fleet_map: str = "16"
    custom_fleet_map: str = ""
    ignore_status_bit: bool = False
    end_code: bool = False
    afs_mode: bool = False
    icall: bool = False
    record_mode: str = "0"
    emg_alert_type: str = "NONE"
    emg_alert_level: str = "1"
    mot_dig_end_code: bool = False

    # P25 / APCO options
    apco_mode: str = "AUTO"
    apco_threshold: str = "8"

    # GPS
    gps_enable: bool = False
    gps_lat: str = "00000000N"
    gps_lon: str = "00000000E"
    gps_range: str = ""

    # Raw settings array for round-trip fidelity
    _raw: list[str] = field(default_factory=lambda: [""] * 63, repr=False)

    # Groups within this system
    groups: list[Group] = field(default_factory=list, repr=False)

    # Trunk frequencies (trunked systems only)
    trunk_frequencies: list[TrunkFrequency] = field(default_factory=list, repr=False)

    @property
    def type_name(self) -> str:
        return SYSTEM_TYPE_NAMES.get(self.system_type, f"Type {self.system_type}")

    @property
    def is_conventional(self) -> bool:
        return self.system_type == SYS_TYPE_CONVENTIONAL


# -----------------------------------------------------------------------
# Top-level scanner configuration
# -----------------------------------------------------------------------
@dataclass
class ScannerConfig:
    """The complete in-memory representation of a scanner configuration."""
    file_path: str = ""
    modified: bool = False

    # Global radio settings (200-element array, 1-indexed; index 0 unused)
    radio_settings: list[str] = field(
        default_factory=lambda: [""] * (MAX_RADIO_SETTINGS + 1)
    )

    # Custom search ranges (10 ranges × 17 settings, 1-indexed)
    custom_search: list[list[str]] = field(
        default_factory=lambda: [[""] * 17 for _ in range(11)]
    )

    # Systems
    systems: list[System] = field(default_factory=list)

    # Global trunk frequencies (shared pool linked via group_id)
    trunk_frequencies: list[TrunkFrequency] = field(default_factory=list)

    # Search lockout frequencies
    search_lockouts: list[str] = field(default_factory=list)

    def get_system_by_id(self, group_id: str) -> Optional[System]:
        for s in self.systems:
            if s.group_id == group_id:
                return s
        return None

    def get_group_by_id(self, group_id: str) -> Optional[Group]:
        for s in self.systems:
            for g in s.groups:
                if g.group_id == group_id:
                    return g
        return None
