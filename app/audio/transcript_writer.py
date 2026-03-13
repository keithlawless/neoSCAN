"""
TranscriptWriter — appends transcriptions to daily .txt files.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULT_DIR = str(Path.home() / "Documents" / "NeoSCAN" / "Transcripts")

_SEPARATOR = "=" * 40

_DAY_HEADER_TEMPLATE = (
    "{sep}\n"
    "NeoSCAN Transcript — {date}\n"
    "{sep}\n\n"
)

_ENTRY_TEMPLATE = (
    "[{time}]  Channel: {channel}  Freq: {frequency}  Sys: {system}  Grp: {group}\n"
    "{text}\n\n"
)


class TranscriptWriter:
    """
    Writes transcription entries to YYYY-MM-DD.txt files in a configurable directory.
    Creates the directory and per-day header on first write for each day.
    """

    def __init__(self) -> None:
        self._directory = _DEFAULT_DIR

    def set_directory(self, path: str) -> None:
        self._directory = path or _DEFAULT_DIR

    def append(
        self,
        start_iso: str,
        channel: str,
        frequency: str,
        system: str,
        group: str,
        text: str,
    ) -> None:
        """Append one transcription entry to today's file. No-op if text is empty."""
        text = text.strip()
        if not text:
            log.debug("TranscriptWriter: skipping entry with empty transcription")
            return
        try:
            dir_path = Path(self._directory)
            dir_path.mkdir(parents=True, exist_ok=True)

            today = datetime.now()
            file_path = dir_path / today.strftime("%Y-%m-%d.txt")
            is_new = not file_path.exists()

            # Parse time from ISO string (YYYY-MM-DDTHH:MM:SS...)
            try:
                dt = datetime.fromisoformat(start_iso)
                time_str = dt.strftime("%H:%M:%S")
            except (ValueError, TypeError):
                time_str = start_iso

            with open(file_path, "a", encoding="utf-8") as f:
                if is_new:
                    f.write(
                        _DAY_HEADER_TEMPLATE.format(
                            sep=_SEPARATOR,
                            date=today.strftime("%Y-%m-%d"),
                        )
                    )
                f.write(
                    _ENTRY_TEMPLATE.format(
                        time=time_str,
                        channel=channel or "(unknown)",
                        frequency=_fmt_freq(frequency),
                        system=system or "(unknown)",
                        group=group or "(unknown)",
                        text=text,
                    )
                )
            log.debug("TranscriptWriter: appended entry to %s", file_path)
        except Exception as exc:
            log.error("TranscriptWriter: failed to write — %s", exc)


def _fmt_freq(frequency: str) -> str:
    if not frequency:
        return "(unknown)"
    # BCD996P2: 8-digit zero-padded integer (Hz/100), e.g. "01542350"
    if len(frequency) == 8 and frequency.isdigit():
        try:
            return f"{int(frequency) / 10000.0:.4f} MHz"
        except ValueError:
            return frequency
    # No decimal point → TGID
    if "." not in frequency:
        try:
            return f"TGID {int(float(frequency))}"
        except (ValueError, TypeError):
            return frequency
    try:
        return f"{float(frequency):.4f} MHz"
    except (ValueError, TypeError):
        return frequency
