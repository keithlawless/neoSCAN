# NeoSCAN

A cross-platform desktop application for programming and remote-controlling
Uniden BCT15-X radio scanners (and future Uniden models) via USB serial.

NeoSCAN is a modern replacement for the abandoned Windows-only FreeSCAN
application. It runs on macOS, Windows, and Linux.

## Features

- **Channel Editor** — full tree view of Systems → Groups → Channels with
  inline editing and contextual help for every field
- **.996 File Support** — open and save FreeSCAN `.996` files with full
  round-trip fidelity
- **CSV Import** — import from any CSV file with intelligent header-based
  field mapping (RadioReference exports, etc.)
- **Upload to Scanner** — program the BCT15-X/BCD996XT over USB with a
  live progress log
- **Download from Scanner** — read the current channel list from the scanner
  into the editor
- **Remote Control** — virtual keypad to control the scanner from your
  computer, with a live transmission log (channel, frequency, duration)
- **Transmission Log Export** — save the remote control session log to CSV

## Requirements

- Python 3.11 or newer
- A Uniden BCT15-X or BCD996XT scanner connected via USB

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

## Development Setup

Install with development dependencies (includes pytest and pytest-qt):

```bash
pip install -e ".[dev]"
```

Run tests:

```bash
pytest tests/
```

## Project Structure

```
neo-scan/
  main.py                          Entry point
  pyproject.toml                   Package metadata and dependencies
  app/
    serial/
      port_manager.py              Serial port detection and connect/disconnect
      protocol.py                  BCT15-X command send/receive layer
      scanner_model.py             Model-specific field translation tables
    data/
      models.py                    ScannerConfig, System, Group, Channel dataclasses
      file_996.py                  .996 file parser and writer
      file_csv.py                  CSV import with fuzzy field mapping
    ui/
      main_window.py               Main application window
      editor/
        systems_panel.py           Tree view panel (Systems > Groups > Channels)
        channel_editor.py          Channel/group/system detail editor form
        csv_import_dialog.py       CSV import wizard dialog
      programmer/
        upload_dialog.py           Upload-to-scanner dialog with progress log
        download_dialog.py         Download-from-scanner dialog with progress log
      remote_control/
        control_panel.py           Virtual scanner keypad and display
        log_panel.py               Transmission logger with CSV export
      settings/
        settings_dialog.py         COM port selection dialog
  resources/
    icons/                         App icons (to be added)
    help_text/                     Help text files (to be added)
  tests/                           Test suite
  sample-data/
    sample.996                     Sample FreeSCAN file for testing
  reference/
    BCD996XT_v1.04.00_Protocol.pdf Scanner USB protocol specification
```

## Scanner Compatibility

Tested with: **BCT15X**, **BCD996XT**

The protocol implementation follows the BCD996XT v1.04.00 serial protocol
specification (included in `reference/`). Other Uniden scanners using the
same ASCII serial protocol should work with minor adjustments to
`scanner_model.py`.

Communication parameters: **115200 baud, 8N1, no flow control**

On macOS the scanner typically appears as `/dev/cu.usbserial-XXXXXXXX`.
On Windows it appears as `COMx`. NeoSCAN auto-detects and highlights the
most likely port in the connection dialog.

## Scanner Capacity (BCT15X / BCD996XT)

| Resource           | Maximum |
|--------------------|---------|
| Systems            | 700     |
| Groups             | 277     |
| Trunk channels     | 500     |
| Trunk frequencies  | 6,000   |
| Search lockouts    | 500     |

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
