# NeoSCAN

A cross-platform desktop application for programming and remote-controlling
Uniden radio scanners via USB serial.

NeoSCAN is a modern replacement for the abandoned Windows-only FreeSCAN
application. It runs on macOS, Windows, and Linux.

## Features

- **Multi-Radio Support** — connect up to two scanners simultaneously, each
  with its own tab, channel list, and remote control panel
- **Channel Editor** — full tree view of Systems → Groups → Channels with
  inline editing and contextual help for every field
- **Trunked System Support** — Motorola, P25, EDACS, and LTR trunked systems
  with full TGID call-group download and upload
- **.996 File Support** — open and save FreeSCAN `.996` files with full
  round-trip fidelity
- **CSV Import** — import from any CSV file with intelligent header-based
  field mapping (RadioReference exports, etc.)
- **Upload to Scanner** — program the scanner over USB with a live progress log
- **Download from Scanner** — read the current channel list from the scanner
  into the editor
- **Remote Control** — virtual keypad to control the scanner from your
  computer, with a merged live transmission log across both radios
- **Audio Transcription** — optional Whisper-based speech-to-text for each
  radio; transcripts appear inline in the transmission log and are saved to a
  text file
- **Transmission Log Export** — save the session log to CSV

## Requirements

- Python 3.11 or newer
- A supported Uniden scanner connected via USB

## Quick Start

```bash
# Clone the repository
git clone <repo-url>
cd neo-scan

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate       # macOS / Linux
# .venv\Scripts\activate        # Windows

# Install dependencies
pip install -e .

# Run the app
python main.py
```

## Optional Dependencies

| Package        | Purpose                                              |
|----------------|------------------------------------------------------|
| `openai-whisper` | Audio transcription (speech-to-text)               |
| `sounddevice`  | Audio recording for transcription                    |
| `noisereduce`  | Stationary noise reduction before transcription      |

Install all optional features at once:

```bash
pip install openai-whisper sounddevice noisereduce
```

Transcription requires a supported audio input device (e.g. a USB sound card
connected to the scanner's audio output). The Whisper model is downloaded
automatically on first use.

## Development Setup

Install with development dependencies (includes pytest and pytest-qt):

```bash
pip install -e ".[dev]"
```

Run tests:

```bash
pytest tests/
```

## Building a Packaged App

NeoSCAN uses [PyInstaller](https://pyinstaller.org) to produce standalone executables
that do not require Python to be installed on the target machine.

### Prerequisites

Install PyInstaller into your virtual environment:

```bash
pip install pyinstaller
```

On macOS, installing `pyobjc` is also recommended so the app name appears correctly
in the Dock and menu bar when running from source:

```bash
pip install pyobjc
```

### Regenerate Icons (if you change the SVG)

```bash
python tools/generate_icons.py
```

### Build

Run PyInstaller from the project root using the provided spec file:

```bash
pyinstaller neoscan.spec
```

Output is placed in `dist/`:

| Platform | Output |
|----------|--------|
| macOS    | `dist/NeoSCAN.app` — drag to `/Applications` |
| Windows  | `dist/NeoSCAN.exe` — single self-contained executable |
| Linux    | `dist/NeoSCAN/` — directory; run `dist/NeoSCAN/neoscan` |

To clean previous builds before rebuilding:

```bash
rm -rf build/ dist/
pyinstaller neoscan.spec
```

### macOS: Creating a DMG

After building, you can package `NeoSCAN.app` into a distributable DMG with:

```bash
hdiutil create -volname NeoSCAN -srcfolder dist/NeoSCAN.app \
    -ov -format UDZO dist/NeoSCAN.dmg
```

### Windows: Code Signing (optional)

Sign the executable before distribution to avoid SmartScreen warnings:

```powershell
signtool sign /a /fd SHA256 /tr http://timestamp.digicert.com dist\NeoSCAN.exe
```

## Project Structure

```
neo-scan/
  main.py                          Entry point
  pyproject.toml                   Package metadata and dependencies
  app/
    serial/
      port_manager.py              Serial port detection and connect/disconnect
      protocol.py                  Scanner command send/receive layer
      scanner_model.py             Model-specific field translation tables
    data/
      models.py                    ScannerConfig, System, Group, Channel dataclasses
      file_996.py                  .996 file parser and writer
      file_csv.py                  CSV import with fuzzy field mapping
      radio_connection.py          Per-radio connection state (port, protocol, config)
    audio/
      recorder.py                  Audio capture via sounddevice
      transcriber.py               Whisper transcription manager and worker thread
      transcript_writer.py         Transcript file writer
    ui/
      main_window.py               Main application window (multi-radio tabs)
      editor/
        systems_panel.py           Tree view panel (Systems > Groups > Channels)
        channel_editor.py          Channel/group/system detail editor form
        csv_import_dialog.py       CSV import wizard dialog
      programmer/
        upload_dialog.py           Upload-to-scanner dialog with progress log
        download_dialog.py         Download-from-scanner dialog with progress log
      remote_control/
        control_panel.py           Virtual scanner keypad and display
        log_panel.py               Multi-radio transmission logger with CSV export
      settings/
        settings_dialog.py         Connection dialog (port, audio device, transcription)
        preferences_dialog.py      App preferences (theme, Whisper model, transcript path)
  resources/
    icons/                         SVG source + PNG icons at multiple sizes
  tools/
    generate_icons.py              Regenerate PNG icons from SVG source
  neoscan.spec                     PyInstaller build spec (all platforms)
  tests/                           Test suite
  sample-data/
    sample.996                     Sample FreeSCAN file for testing
  reference/
    BCD996XT_v1.04.00_Protocol.pdf   BCD996XT USB protocol specification
    BCD996P2_Remote_Protocol_ver_1_03.pdf  BCD996P2 USB protocol specification
    BCT15X_v1.03.00_Protocol.pdf     BCT15X USB protocol specification
```

## Scanner Compatibility

| Model    | Status | Notes |
|----------|--------|-------|
| BCT15X   | Tested | Conventional and trunked systems |
| BCD996XT | Tested | Conventional and trunked systems |
| BCD996P2 | Tested | Conventional, Motorola, and P25 trunked systems |

The protocol implementation targets the BCD996XT v1.04.00 and BCD996P2 v1.03
serial protocol specifications (included in `reference/`). Other Uniden scanners
using the same ASCII serial protocol should work with minor adjustments to
`scanner_model.py`.

Communication parameters: **115200 baud, 8N1, no flow control**

On macOS the scanner typically appears as `/dev/cu.usbserial-XXXXXXXX`.
On Windows it appears as `COMx`. NeoSCAN auto-detects and highlights the
most likely port in the connection dialog.

## Scanner Capacity

| Resource           | BCT15X / BCD996XT | BCD996P2 |
|--------------------|:-----------------:|:--------:|
| Systems            | 700               | 500      |
| Groups per system  | 277               | —        |
| Channels           | —                 | 25,000   |
| Trunk frequencies  | 6,000             | —        |
| Search lockouts    | 500               | —        |

## Key Dependencies

| Package    | Purpose                              |
|------------|--------------------------------------|
| PyQt6      | Cross-platform desktop UI toolkit    |
| pyserial   | USB/serial communication             |

## Reference Implementation

The FreeSCAN source code (Windows-only Visual Basic, now abandoned) was
consulted during development to understand the `.996` file format and
scanner protocol behaviour. It is not included in this repository.

## License

NeoSCAN is free software released under the GNU General Public License v3.
See the `LICENSE` file for the full license text.
