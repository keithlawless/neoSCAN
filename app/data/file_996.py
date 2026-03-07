"""
.996 file format parser and writer.

The .996 format is FreeSCAN's proprietary file format for BCT15-X / BCD996XT.
It stores values as quoted strings separated by CR (\r) or CRLF (\r\n).
Bare (unquoted) integers are used for section counts.

File structure:
  Line 1:       File header (".7BCD996T" or ".08BCD996T")
  Lines 2-201:  RadioSetting[1..200]
  Next 170:     CustSearch[1..10][0..16]   (10 × 17 values)
  Count:        Number of systems
  Per system:   62 settings (varSite[sys][0][0][1..62])
                Count of groups
                Per group: 62 settings (varSite[sys][grp][0][1..62])
  "TrunkSection"
  Count:        Number of trunk frequency entries
  Per trunk:    freq, lcn, group_id, params[3..10]
  "SEARCHLOCKOUTS"
  Count:        Number of search lockout entries
  Per lockout:  lockout frequency string
  "CHANDATA"
  Count:        Number of channels
  Per channel:  30 settings (ChanInfo[chan][1..30])

VarSite system settings (1-indexed):
  1  = system name         2  = lockout (0/1)
  3  = system type         4  = quick key
  5  = hold time           6  = delay time
  7  = startup key         8  = QGL pattern (10 chars)
  9  = data skip           10 = (internal group count, not used in file)
  11 = scan mode           12 = ignore Mot status bit
  13 = end code            14 = AFS/decimal mode
  15 = I-call              16 = fleet map
  17 = custom fleet map    19 = GPS lat
  20 = GPS lon             21 = GPS range
  22 = GPS enable          23 = group ID (UUID string)
  24 = record mode         25 = emg alert type
  26 = emg alert level     27 = Mot dig end code
  48 = APCO mode           49 = APCO threshold

VarSite group settings (1-indexed):
  1  = group name          4  = quick key
  5  = group type (2=group, 3=site)
  10 = group ID (UUID string)   (matches Channel.group_id)
  32 = state index (for sites)

Channel settings (ChanInfo, 1-indexed):
  1  = name                2  = frequency (or TGID)
  3  = TGID (trunked only, used as alert tone in some contexts)
  4  = modulation index (conv) or alert tone (trunked)
  5  = lockout             6  = attenuator (conv) / audio type (trunked)
  7  = priority (conv) / record (trunked)
  8  = alert tone type     9  = tone (CTCSS/DCS index)
  10 = group ID            11 = tone lockout
  12 = audio type (conv)   13 = alert level
  14 = comment             15 = delay
  16 = number tag          17 = output
  18 = P25 wait time       19 = step size
  20 = volume offset
"""
from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import Iterator

from app.data.models import (
    ScannerConfig,
    System,
    Group,
    Channel,
    TalkGroup,
    TrunkFrequency,
    FILE_HEADER_CURRENT,
    FILE_HEADER_LEGACY,
    MAX_RADIO_SETTINGS,
    MAX_SYSTEM_SETTINGS,
    MAX_CHAN_SETTINGS,
    SYS_TYPE_CONVENTIONAL,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_bool(val: str) -> bool:
    return val.strip() == "1"


def _parse_int(val: str, default: int = 0) -> int:
    try:
        return int(val.strip())
    except (ValueError, TypeError):
        return default


class _Reader:
    """
    Line-by-line reader that handles both quoted strings and bare values.
    Mirrors VB6 Input() semantics: quoted strings are unquoted, bare values
    are returned as-is.
    """

    def __init__(self, text: str) -> None:
        # Split on CR or CRLF; strip empty trailing lines
        self._lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self._pos = 0
        self._total = len(self._lines)

    @property
    def eof(self) -> bool:
        return self._pos >= self._total

    def read(self) -> str:
        """Read the next value, unquoting if necessary."""
        while self._pos < self._total:
            raw = self._lines[self._pos]
            self._pos += 1
            stripped = raw.strip()
            if not stripped and self._pos < self._total:
                # Blank lines inside quoted blocks get returned as ""
                return ""
            return self._unquote(stripped)
        raise EOFError("Unexpected end of .996 file")

    def read_int(self) -> int:
        """Read the next value as an integer (section count)."""
        val = self.read()
        return _parse_int(val)

    @staticmethod
    def _unquote(s: str) -> str:
        """Remove surrounding double-quotes if present."""
        if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
            return s[1:-1]
        return s

    def peek(self) -> str:
        """Return next value without advancing."""
        saved = self._pos
        val = self.read()
        self._pos = saved
        return val


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load(path: str | Path) -> ScannerConfig:
    """
    Parse a .996 file and return a ScannerConfig.
    Raises ValueError on format errors.
    """
    path = Path(path)
    text = path.read_text(encoding="latin-1", errors="replace")
    config = ScannerConfig(file_path=str(path))
    r = _Reader(text)

    # --- Header ---
    header = r.read()
    if header not in (FILE_HEADER_CURRENT, FILE_HEADER_LEGACY):
        raise ValueError(
            f"Unrecognised .996 file header: {header!r}. "
            f"Expected {FILE_HEADER_CURRENT!r} or {FILE_HEADER_LEGACY!r}."
        )
    legacy = (header == FILE_HEADER_LEGACY)
    max_radio = 100 if legacy else MAX_RADIO_SETTINGS

    # --- Radio settings ---
    for i in range(1, max_radio + 1):
        config.radio_settings[i] = r.read()

    # --- Custom search ranges (10 ranges × 17 settings each) ---
    for x in range(1, 11):
        for y in range(0, 17):
            config.custom_search[x][y] = r.read()

    # --- Systems ---
    num_systems = r.read_int()
    log.debug("Loading %d systems", num_systems)

    for sys_idx in range(1, num_systems + 1):
        sys = System()
        raw = [""] * 63
        for s in range(1, MAX_SYSTEM_SETTINGS + 1):
            raw[s] = r.read()
        sys._raw = raw
        _populate_system(sys, raw)

        # Groups within this system
        num_groups = r.read_int()
        for grp_idx in range(1, num_groups + 1):
            grp = Group()
            grp_raw = [""] * 63
            for s in range(1, MAX_SYSTEM_SETTINGS + 1):
                grp_raw[s] = r.read()
            grp._raw = grp_raw
            _populate_group(grp, grp_raw)
            sys.groups.append(grp)

        config.systems.append(sys)

    # --- Trunk section ---
    trunk_header = r.read()
    if trunk_header != "TrunkSection":
        log.warning("Expected 'TrunkSection', got %r", trunk_header)
    num_trunk = r.read_int()
    for _ in range(num_trunk):
        tf = TrunkFrequency()
        tf.frequency = r.read()
        lcn_raw = r.read()
        tf.group_id = r.read()
        # Process lockout bit embedded in LCN as "lcn!lockout"
        if "!" in lcn_raw:
            parts = lcn_raw.split("!")
            tf.lcn = parts[0]
            tf.lockout = parts[1].strip() == "1"
        else:
            tf.lcn = lcn_raw
            tf.lockout = False
        if not legacy:
            for p in range(len(tf.params)):
                tf.params[p] = r.read()
        config.trunk_frequencies.append(tf)

    # --- Search lockouts ---
    if not r.eof:
        sl_header = r.read()
        if sl_header != "SEARCHLOCKOUTS":
            log.warning("Expected 'SEARCHLOCKOUTS', got %r", sl_header)
        num_sl = r.read_int()
        for _ in range(num_sl):
            config.search_lockouts.append(r.read())

    # --- Channel data ---
    if not r.eof:
        chan_header = r.read()
        if chan_header != "CHANDATA":
            log.warning("Expected 'CHANDATA', got %r", chan_header)
        num_chans = r.read_int()

        # We'll collect all channels in a flat list, then assign to groups
        all_channels: list[Channel | TalkGroup] = []
        for _ in range(num_chans):
            ch_raw = [""] * (MAX_CHAN_SETTINGS + 1)
            for s in range(1, MAX_CHAN_SETTINGS + 1):
                ch_raw[s] = r.read()
            ch = _build_channel(ch_raw)
            all_channels.append(ch)

        # Assign channels to their groups via group_id
        group_map: dict[str, Group] = {}
        for sys in config.systems:
            for grp in sys.groups:
                if grp.group_id:
                    group_map[grp.group_id] = grp
        for ch in all_channels:
            grp = group_map.get(ch.group_id)
            if grp is not None:
                grp.channels.append(ch)
            else:
                log.debug(
                    "Channel %r has group_id %r not found in any group",
                    ch.name, ch.group_id,
                )

    # Assign trunk frequencies to systems via group_id
    sys_id_map: dict[str, System] = {s.group_id: s for s in config.systems if s.group_id}
    for tf in config.trunk_frequencies:
        sys = sys_id_map.get(tf.group_id)
        if sys:
            sys.trunk_frequencies.append(tf)

    log.info(
        "Loaded %d system(s) from %s",
        len(config.systems), path.name,
    )
    return config


def _populate_system(sys: System, raw: list[str]) -> None:
    sys.name = raw[1]
    sys.lockout = _parse_bool(raw[2])
    sys.system_type = _parse_int(raw[3], SYS_TYPE_CONVENTIONAL)
    sys.quick_key = raw[4]
    sys.hold_time = raw[5]
    sys.delay_time = raw[6]
    sys.startup_key = raw[7]
    sys.qgl = raw[8] if raw[8] else "1111111111"
    sys.data_skip = _parse_bool(raw[9])
    # 10 = internal group count (not used directly)
    # 11-15 trunked options
    sys.fleet_map = raw[16]
    sys.custom_fleet_map = raw[17]
    # 18 unused
    sys.gps_lat = raw[19]
    sys.gps_lon = raw[20]
    sys.gps_range = raw[21]
    sys.gps_enable = _parse_bool(raw[22])
    sys.group_id = raw[23]
    sys.record_mode = raw[24]
    sys.emg_alert_type = raw[25]
    sys.emg_alert_level = raw[26]
    sys.mot_dig_end_code = _parse_bool(raw[27])
    sys.apco_mode = raw[48] if len(raw) > 48 else "AUTO"
    sys.apco_threshold = raw[49] if len(raw) > 49 else "8"


def _populate_group(grp: Group, raw: list[str]) -> None:
    grp.name = raw[1]
    grp.lockout = _parse_bool(raw[2]) if len(raw) > 2 else False
    grp.quick_key = raw[4] if len(raw) > 4 else "."
    grp.group_type = raw[5] if len(raw) > 5 else "2"
    grp.group_id = raw[10] if len(raw) > 10 else ""
    grp.gps_lat = raw[6] if len(raw) > 6 else "00000000N"
    grp.gps_lon = raw[7] if len(raw) > 7 else "00000000E"


def _build_channel(raw: list[str]) -> Channel | TalkGroup:
    """Build a Channel or TalkGroup from a raw settings list."""
    group_id = raw[10]
    name = raw[1]
    # Determine if this is a conventional channel (raw[2] looks like a frequency)
    # or a trunked talk group (raw[2] is a TGID / integer string).
    # Frequencies in .996 files always contain a decimal point (e.g. "154.2350").
    # TGIDs are plain integers (e.g. "33776"), so the decimal point is the reliable
    # discriminator — the old "< 25" threshold failed for Motorola Type II TGIDs.
    freq_or_tgid = raw[2]
    is_conv = bool(freq_or_tgid) and "." in freq_or_tgid

    if is_conv:
        ch = Channel()
        ch._raw = raw
        ch.name = name
        ch.frequency = freq_or_tgid
        ch.modulation = raw[4]
        ch.lockout = _parse_bool(raw[5])
        ch.attenuator = _parse_bool(raw[6])
        ch.priority = _parse_bool(raw[7])
        ch.alert_tone = raw[8]
        ch.tone = raw[9]
        ch.group_id = group_id
        ch.tone_lockout = _parse_bool(raw[11])
        ch.audio_type = raw[12]
        ch.alert_level = raw[13]
        ch.comment = raw[14]
        ch.delay = raw[15]
        ch.number_tag = raw[16] if raw[16] else "NONE"
        ch.output = raw[17] if raw[17] else "OFF"
        ch.p25_wait_time = raw[18]
        ch.step_size = raw[19]
        ch.volume_offset = raw[20]
        return ch
    else:
        tg = TalkGroup()
        tg._raw = raw
        tg.name = name
        tg.tgid = freq_or_tgid
        tg.alert_tone = raw[4]
        tg.lockout = _parse_bool(raw[5])
        tg.audio_type = raw[6]
        tg.record = _parse_bool(raw[7])
        tg.alert_level = raw[13]
        tg.group_id = group_id
        return tg


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save(config: ScannerConfig, path: str | Path | None = None) -> None:
    """
    Serialise a ScannerConfig back to .996 format.
    Uses the file path stored in config if path is not given.
    """
    path = Path(path or config.file_path)
    lines: list[str] = []

    def w(val: str) -> None:
        """Append a quoted value."""
        lines.append(f'"{val}"')

    def w_int(val: int) -> None:
        """Append an unquoted integer."""
        lines.append(str(val))

    # Header
    w(FILE_HEADER_CURRENT)

    # Radio settings
    for i in range(1, MAX_RADIO_SETTINGS + 1):
        w(config.radio_settings[i])

    # Custom search
    for x in range(1, 11):
        for y in range(0, 17):
            w(config.custom_search[x][y])

    # Systems
    w_int(len(config.systems))
    for sys in config.systems:
        raw = _system_to_raw(sys)
        for s in range(1, MAX_SYSTEM_SETTINGS + 1):
            w(raw[s])
        w_int(len(sys.groups))
        for grp in sys.groups:
            grp_raw = _group_to_raw(grp)
            for s in range(1, MAX_SYSTEM_SETTINGS + 1):
                w(grp_raw[s])

    # Trunk section
    # Flatten all trunk frequencies from all systems
    all_trunk: list[TrunkFrequency] = list(config.trunk_frequencies)
    w("TrunkSection")
    w_int(len(all_trunk))
    for tf in all_trunk:
        w(tf.frequency)
        lcn = tf.lcn
        if tf.lockout:
            lcn = f"{lcn}!1"
        w(lcn)
        w(tf.group_id)
        for p in tf.params:
            w(p)

    # Search lockouts
    w("SEARCHLOCKOUTS")
    w_int(len(config.search_lockouts))
    for sl in config.search_lockouts:
        w(sl)

    # Channel data — collect all channels in order across all groups
    all_channels: list[Channel | TalkGroup] = []
    for sys in config.systems:
        for grp in sys.groups:
            all_channels.extend(grp.channels)

    w("CHANDATA")
    w_int(len(all_channels))
    for ch in all_channels:
        ch_raw = _channel_to_raw(ch)
        for s in range(1, MAX_CHAN_SETTINGS + 1):
            w(ch_raw[s])

    path.write_text("\r\n".join(lines) + "\r\n", encoding="latin-1")
    config.file_path = str(path)
    config.modified = False
    log.info("Saved %d system(s) to %s", len(config.systems), path.name)


def _system_to_raw(sys: System) -> list[str]:
    raw = list(sys._raw)
    if len(raw) < 63:
        raw = raw + [""] * (63 - len(raw))
    raw[1] = sys.name
    raw[2] = "1" if sys.lockout else "0"
    raw[3] = str(sys.system_type)
    raw[4] = sys.quick_key
    raw[5] = sys.hold_time
    raw[6] = sys.delay_time
    raw[7] = sys.startup_key
    raw[8] = sys.qgl
    raw[9] = "1" if sys.data_skip else "0"
    raw[16] = sys.fleet_map
    raw[17] = sys.custom_fleet_map
    raw[19] = sys.gps_lat
    raw[20] = sys.gps_lon
    raw[21] = sys.gps_range
    raw[22] = "1" if sys.gps_enable else "0"
    raw[23] = sys.group_id
    raw[24] = sys.record_mode
    raw[25] = sys.emg_alert_type
    raw[26] = sys.emg_alert_level
    raw[27] = "1" if sys.mot_dig_end_code else "0"
    raw[48] = sys.apco_mode
    raw[49] = sys.apco_threshold
    return raw


def _group_to_raw(grp: Group) -> list[str]:
    raw = list(grp._raw)
    if len(raw) < 63:
        raw = raw + [""] * (63 - len(raw))
    raw[1] = grp.name
    raw[2] = "1" if grp.lockout else "0"
    raw[4] = grp.quick_key
    raw[5] = grp.group_type
    raw[6] = grp.gps_lat
    raw[7] = grp.gps_lon
    raw[10] = grp.group_id
    return raw


def _channel_to_raw(ch: Channel | TalkGroup) -> list[str]:
    if isinstance(ch, Channel):
        raw = list(ch._raw)
        if len(raw) < MAX_CHAN_SETTINGS + 1:
            raw = raw + [""] * (MAX_CHAN_SETTINGS + 1 - len(raw))
        raw[1] = ch.name
        raw[2] = ch.frequency
        raw[4] = ch.modulation
        raw[5] = "1" if ch.lockout else "0"
        raw[6] = "1" if ch.attenuator else "0"
        raw[7] = "1" if ch.priority else "0"
        raw[8] = ch.alert_tone
        raw[9] = ch.tone
        raw[10] = ch.group_id
        raw[11] = "1" if ch.tone_lockout else "0"
        raw[12] = ch.audio_type
        raw[13] = ch.alert_level
        raw[14] = ch.comment
        raw[15] = ch.delay
        raw[16] = ch.number_tag
        raw[17] = ch.output
        raw[18] = ch.p25_wait_time
        raw[19] = ch.step_size
        raw[20] = ch.volume_offset
        return raw
    else:  # TalkGroup
        raw = list(ch._raw)
        if len(raw) < MAX_CHAN_SETTINGS + 1:
            raw = raw + [""] * (MAX_CHAN_SETTINGS + 1 - len(raw))
        raw[1] = ch.name
        raw[2] = ch.tgid
        raw[4] = ch.alert_tone
        raw[5] = "1" if ch.lockout else "0"
        raw[6] = ch.audio_type
        raw[7] = "1" if ch.record else "0"
        raw[10] = ch.group_id
        raw[13] = ch.alert_level
        return raw
