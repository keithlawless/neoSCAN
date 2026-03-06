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

from app.data.models import Channel, Group, System, ScannerConfig, SYS_TYPE_CONVENTIONAL

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Known field names and their aliases (lowercase, stripped)
# ---------------------------------------------------------------------------

FIELD_DEFS: dict[str, list[str]] = {
    "name":         ["name", "channel", "channel name", "chan", "description", "desc", "label"],
    "frequency":    ["frequency", "freq", "mhz", "frequency (mhz)", "output", "output freq",
                     "rx freq", "receive freq", "rx", "receive"],
    "modulation":   ["mode", "modulation", "mod", "modmode", "type"],
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
) -> tuple[int, list[str]]:
    """
    Import channels from a CSV file into target_group using the given mappings.

    Returns (channels_added, list_of_warnings).
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
            ch = Channel()
            ch.group_id = target_group.group_id
            ch.modulation = "AUTO"

            for col_idx, field_name in field_map.items():
                if col_idx >= len(row):
                    continue
                raw = row[col_idx].strip()
                if not raw:
                    continue
                _apply_field(ch, field_name, raw, row_num, warnings)

            if not ch.name and not ch.frequency:
                continue  # skip meaningless rows

            # Truncate name to 16 chars
            ch.name = ch.name[:16]

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

    log.info("CSV import: added %d channels to group %r", added, target_group.name)
    return added, warnings


def _apply_field(
    ch: Channel, field: str, value: str, row_num: int, warnings: list[str]
) -> None:
    """Apply a raw CSV cell value to the appropriate Channel attribute."""
    try:
        if field == "name":
            ch.name = value
        elif field == "frequency":
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
            ch.modulation = _normalise_mod(value)
        elif field == "tone":
            ch.tone = value
        elif field == "lockout":
            ch.lockout = value.lower() in ("1", "true", "yes", "y", "locked", "lo")
        elif field == "priority":
            ch.priority = value.lower() in ("1", "true", "yes", "y")
        elif field == "attenuator":
            ch.attenuator = value.lower() in ("1", "true", "yes", "y", "on")
        elif field == "delay":
            ch.delay = value
        elif field == "comment":
            ch.comment = value
        elif field == "number_tag":
            ch.number_tag = value if value.isdigit() else "NONE"
        elif field == "tone_lockout":
            ch.tone_lockout = value.lower() in ("1", "true", "yes", "y")
        elif field == "volume_offset":
            ch.volume_offset = value
    except Exception as exc:
        warnings.append(f"Row {row_num}: error mapping {field!r}: {exc}")


def _normalise_mod(value: str) -> str:
    """Normalise a modulation string to the scanner's expected values."""
    v = value.strip().upper()
    mapping = {
        "FM": "FM", "NFM": "NFM", "AM": "AM", "WFM": "WFM",
        "FMB": "FMB", "AUTO": "AUTO",
        "NBFM": "NFM", "NARROW FM": "NFM", "NARROW": "NFM",
        "WIDE FM": "WFM", "BROADCAST": "WFM",
    }
    return mapping.get(v, "AUTO")
