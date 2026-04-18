"""
RadioConnection — represents one connected scanner (port + protocol + config).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import serial

from app.serial.protocol import ScannerProtocol
from app.data.models import ScannerConfig


@dataclass
class RadioConnection:
    label: str                              # "Radio 1", "Radio 2", "Radio 3"
    port_name: str
    conn: serial.Serial
    proto: ScannerProtocol
    scanner_model: str
    audio_device_index: int | None          # sounddevice input device index
    config: ScannerConfig | None = None
    transcription_manager: Any | None = None  # TranscriptionManager, set after creation
