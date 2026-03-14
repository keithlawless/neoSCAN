"""
CSV import with intelligent header-based field mapping.

Supports common CSV exports from RadioReference and other sources.
"""
from __future__ import annotations

import csv
import io
import logging
from difflib import SequenceMatcher
from typing import NamedTuple

from app.data.models import Channel, Group, System, ScannerConfig, SYS_TYPE_CONVENTIONAL, TalkGroup

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Known field names and their aliases (lowercase, stripped)
# ---------------------------------------------------------------------------

FIELD_DEFS: dict[str, list[str]] = {
    "name":         ["name", "channel", "channel name", "chan", "alpha tag", "label"],
    "tgid":         ["tgid", "decimal", "talk group", "talkgroup", "tg id", "tg"],
    "frequency":    ["frequency", "freq", "mhz", "frequency (mhz)", "output", "output freq",
                     "rx freq", "receive freq", "rx", "receive"],
    "modulation":   ["modulation", "mod", "modmode", "mode"],
    "audio_type":   ["audio type", "audio"],
    "tone":         ["tone", "ctcss", "dcs", "pl", "squelch tone", "ctcss/dcs",
                     "tone code", "pl tone", "rx tone"],
    "lockout":      ["lockout", "locked", "lo", "skip", "avoid"],
    "priority":     ["priority", "pri", "p"],
    "attenuator":   ["attenuator", "att", "atten", "attenuation"],
    "delay":        ["delay", "scan delay", "hold"],
    "comment":      ["comment", "comments", "notes", "description", "note", "remarks", "tags"],
    "number_tag":   ["number tag", "number", "tag", "num tag", "numtag"],
    "tone_lockout": ["tone lockout", "tlockout", "tlo"],
    "volume_offset":["volume offset", "vol offset", "volume", "vol"],
}

# Fields that map to a Channel attribute
IMPORTABLE_FIELDS = set(FIELD_DEFS.keys())

SKIP = "__skip__"  # sentinel: ignore this column


class FieldMapping(NamedTuple):
    """Maps a CSV column index to a Channel attribute name (or SKIP)."""
    col_index: int
    header: str
    field: str   # Channel attr name or SKIP


def suggest_mapping(headers: list[str]) -> list[FieldMapping]:
    """
    For each CSV header, suggest the best matching Channel field.
    Returns a list of FieldMapping, one per column.
    """
    used_fields: set[str] = set()
    mappings: list[FieldMapping] = []

    for i, header in enumerate(headers):
        h = header.strip().lower()
        best_field = SKIP
        best_score = 0.0

        for field, aliases in FIELD_DEFS.items():
            if field in used_fields:
                continue
            for alias in aliases:
                score = SequenceMatcher(None, h, alias).ratio()
                if h == alias:
                    score = 1.0
                if score > best_score:
                    best_score = score
                    best_field = field

        # Only accept matches above a threshold
        if best_score < 0.6:
            best_field = SKIP
        else:
            if best_field != SKIP:
                used_fields.add(best_field)

        mappings.append(FieldMapping(i, header, best_field))

    return mappings


def preview_rows(path: str, n: int = 5) -> tuple[list[str], list[list[str]]]:
    """
    Read the first N data rows from a CSV for preview.
    Returns (headers, rows).
    """
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.reader(f)
        headers = next(reader, [])
        rows = []
        for row in reader:
            rows.append(row)
            if len(rows) >= n:
                break
    return headers, rows


def import_csv(
    path: str,
    mappings: list[FieldMapping],
    target_group: Group,
    create_talkgroups: bool = False,
) -> tuple[int, list[str]]:
    """
    Import channels or talk groups from a CSV file into target_group.

    When create_talkgroups=True (trunked system group), creates TalkGroup
    objects instead of Channel objects — frequency validation is skipped and
    the tgid field is used instead.

    Returns (items_added, list_of_warnings).
    """
    added = 0
    warnings: list[str] = []

    field_map: dict[int, str] = {
        m.col_index: m.field for m in mappings if m.field != SKIP
    }

    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.reader(f)
        next(reader, None)  # skip header
        for row_num, row in enumerate(reader, start=2):
            if not any(cell.strip() for cell in row):
                continue  # skip blank rows

            if create_talkgroups:
                ch: Channel | TalkGroup = TalkGroup()
            else:
                ch = Channel()
                ch.modulation = "AUTO"
            ch.group_id = target_group.group_id

            for col_idx, field_name in field_map.items():
                if col_idx >= len(row):
                    continue
                raw = row[col_idx].strip()
                if not raw:
                    continue
                _apply_field(ch, field_name, raw, row_num, warnings)

            # Truncate name to 16 chars
            ch.name = ch.name[:16]

            if create_talkgroups:
                if not ch.name and not getattr(ch, "tgid", ""):
                    continue  # skip meaningless rows
            else:
                if not ch.name and not ch.frequency:
                    continue  # skip meaningless rows
                # Validate frequency is numeric
                if ch.frequency:
                    try:
                        float(ch.frequency)
                    except ValueError:
                        warnings.append(
                            f"Row {row_num}: '{ch.frequency}' is not a valid frequency — skipped"
                        )
                        continue

            target_group.channels.append(ch)
            added += 1

    label = "talk groups" if create_talkgroups else "channels"
    log.info("CSV import: added %d %s to group %r", added, label, target_group.name)
    return added, warnings


def _apply_field(
    ch: "Channel | TalkGroup", field: str, value: str, row_num: int, warnings: list[str]
) -> None:
    """Apply a raw CSV cell value to the appropriate Channel/TalkGroup attribute."""
    try:
        if field == "name":
            ch.name = value
        elif field == "tgid":
            if hasattr(ch, "tgid"):
                ch.tgid = value.strip()
        elif field == "audio_type":
            if hasattr(ch, "audio_type"):
                ch.audio_type = _normalise_audio_type(value)
        elif field == "frequency":
            if hasattr(ch, "frequency"):
                # Normalize: strip MHz suffix, handle kHz
                v = value.upper().replace("MHZ", "").replace("KHZ", "").strip()
                # If value looks like kHz (e.g. 154235), convert to MHz
                try:
                    fval = float(v)
                    if fval > 30000:  # definitely kHz
                        fval /= 1000.0
                    ch.frequency = f"{fval:.4f}"
                except ValueError:
                    ch.frequency = v
        elif field == "modulation":
            mod, audio = _classify_mode(value)
            if hasattr(ch, "modulation"):
                ch.modulation = mod
            if audio is not None and hasattr(ch, "audio_type"):
                ch.audio_type = audio
        elif field == "tone":
            if hasattr(ch, "tone"):
                ch.tone = value
        elif field == "lockout":
            ch.lockout = value.lower() in ("1", "true", "yes", "y", "locked", "lo")
        elif field == "priority":
            ch.priority = value.lower() in ("1", "true", "yes", "y")
        elif field == "attenuator":
            if hasattr(ch, "attenuator"):
                ch.attenuator = value.lower() in ("1", "true", "yes", "y", "on")
        elif field == "delay":
            if hasattr(ch, "delay"):
                ch.delay = value
        elif field == "comment":
            if hasattr(ch, "comment"):
                ch.comment = value
        elif field == "number_tag":
            ch.number_tag = value if value.isdigit() else "NONE"
        elif field == "tone_lockout":
            if hasattr(ch, "tone_lockout"):
                ch.tone_lockout = value.lower() in ("1", "true", "yes", "y")
        elif field == "volume_offset":
            ch.volume_offset = value
    except Exception as exc:
        warnings.append(f"Row {row_num}: error mapping {field!r}: {exc}")


def _normalise_audio_type(value: str) -> str:
    """
    Map a mode/audio-type string to a scanner AUDIO_TYPE code.

    RadioReference mode codes:
      D / DE  → Digital Only (2)
      A       → Analog Only (1)
      D/A     → All (0)
    Scanner codes 0/1/2 are passed through unchanged.
    """
    v = value.strip().upper()
    rr_map = {
        "D":   "2",   # Digital
        "DE":  "2",   # Digital Encrypted
        "A":   "1",   # Analog
        "D/A": "0",   # Both
        "DA":  "0",
    }
    if v in rr_map:
        return rr_map[v]
    if v in ("0", "1", "2"):
        return v
    return "0"  # default: All


def _classify_mode(value: str) -> tuple[str, str | None]:
    """
    Classify a RadioReference 'Mode' column value into (modulation, audio_type).

    RadioReference uses 'Mode' for two purposes:
      Trunked talk group exports:    D / DE / A / D/A  (audio filter codes)
      Conventional channel exports:  FMN / FM / P25 / AM  (modulation codes)

    Returns (modulation_string, audio_type_or_None).
    audio_type is None when the value does not imply a particular filter.
    """
    v = value.strip().upper()

    # RadioReference audio type codes (trunked talk group exports)
    rr_audio: dict[str, tuple[str, str]] = {
        "D":   ("AUTO", "2"),   # Digital
        "DE":  ("AUTO", "2"),   # Digital Encrypted
        "A":   ("AUTO", "1"),   # Analog
        "D/A": ("AUTO", "0"),   # Both
        "DA":  ("AUTO", "0"),
    }
    if v in rr_audio:
        return rr_audio[v]

    # RadioReference / standard modulation codes (conventional channel exports)
    # P25 and similar digital modes also imply audio_type = Digital Only.
    rr_mod: dict[str, tuple[str, str | None]] = {
        "FM":    ("FM",   None),
        "NFM":   ("NFM",  None),
        "FMN":   ("NFM",  None),   # FMN = Narrowband FM (RadioReference)
        "AM":    ("AM",   None),
        "WFM":   ("WFM",  None),
        "FMB":   ("FMB",  None),
        "AUTO":  ("AUTO", None),
        "NBFM":  ("NFM",  None),
        "NARROW FM": ("NFM", None),
        "WIDE FM":   ("WFM", None),
        "P25":   ("NFM",  "2"),    # P25 digital — NFM carrier, Digital Only
        "P25N":  ("NFM",  "2"),
        "P25D":  ("NFM",  "2"),
        "TDMA":  ("NFM",  "2"),
        "DMR":   ("NFM",  "2"),
    }
    if v in rr_mod:
        return rr_mod[v]

    return ("AUTO", None)
