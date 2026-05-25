# NeoSCAN

A cross-platform desktop application for programming and remote-controlling
Uniden radio scanners via USB serial.

NeoSCAN is a modern replacement for the abandoned Windows-only FreeSCAN
application. It runs on macOS, Windows, and Linux.

## Features

- **Multi-Radio Support** — connect up to three scanners simultaneously, each
  with its own tab, channel list, and remote control panel
- **Channel Editor** — full tree view of Systems → Groups → Channels with
  inline editing and contextual help for every field
- **Trunked System Support** — Motorola, P25, EDACS, and LTR trunked systems
  with full TGID call-group download and upload
- **.996 File Support** — open and save FreeSCAN `.996` files with full
  round-trip fidelity
- **CSV Import** — import conventional channels, P25/Motorola talk groups,
  and trunked sites from RadioReference CSV exports with automatic
  header-based field mapping and mode/audio-type detection
- **Upload to Scanner** — program the scanner over USB with a live progress log
- **Download from Scanner** — read the current channel list from the scanner
  into the editor
- **Remote Control** — virtual keypad to control the scanner from your
  computer, with a merged live transmission log across all connected radios
- **Audio Transcription** — optional speech-to-text for each radio via a
  running [whisper-wrapper-api-server](https://github.com/keithlawless/whisper-wrapper-api-server);
  transcripts appear inline in the transmission log and are saved to a daily
  text file
- **Daily AI Summaries** — optional Anthropic Claude integration that turns
  each day's transcript into a nicely formatted HTML report at midnight
- **Transmission Log Export** — save the session log to CSV
- **ADS-B Reception** — connect an RTL-SDR USB dongle to receive live aircraft
  transponder broadcasts on 1090 MHz; a live grid shows ICAO address, callsign,
  altitude, speed, track, position, squawk, and alert status for every aircraft
  in range, with traffic logged to daily CSV files

## Requirements

- Python 3.11 or newer

**For scanner programming and remote control:**
- One of the supported Uniden scanner models below, connected via USB:
  BCT15X, BCD996XT, or BCD996P2

**For ADS-B reception (optional, independent of scanner):**
- An RTL2832U-based RTL-SDR USB dongle — tested with RTL-SDR Blog V3 and V4;
  other RTL2832U devices (NooElec NESDR, generic sticks) should also work
- dump1090 installed on the host (see ADS-B setup below)

## Quick Start

```bash
# Clone the repository
git clone <repo-url>
cd neo-scan

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate       # macOS / Linux
# .venv\Scripts\activate        # Windows

# Install the app
pip install -e .

# Run the app
python main.py
```

## Optional Features

### Audio Transcription (whisper-wrapper-api-server)

NeoSCAN sends scanner audio to a separately running
[whisper-wrapper-api-server](https://github.com/keithlawless/whisper-wrapper-api-server)
for speech-to-text transcription. No Whisper libraries are installed inside
NeoSCAN itself.

**Setup:**

1. Clone and start the server (see its README for full instructions):
   ```bash
   git clone https://github.com/keithlawless/whisper-wrapper-api-server
   cd whisper-wrapper-api-server
   # follow the server README to install dependencies and start it
   uvicorn app:app --port 8000
   ```
2. In NeoSCAN, open **File → Preferences → Transcription**, tick
   **Enable transcription**, and confirm the server URL (default
   `http://localhost:8000`).
3. Choose a Whisper model size and language.

Transcription also requires a supported audio input device (e.g. a USB sound
card connected to the scanner's audio output). The server handles model
downloads on first use.

### Daily AI Summaries (Anthropic Claude)

NeoSCAN can optionally turn each day's transcript into a self-contained HTML
report by sending the text to Anthropic's Claude API. To enable this:

1. Open **File → Preferences → Transcription**.
2. In the *Daily Summary* section, tick **Generate a daily HTML summary with
   Claude**.
3. Paste your Anthropic API key (it is stored locally in QSettings).
4. Choose a Claude model (Opus 4.7, Sonnet 4.6, or Haiku 4.5).
5. Pick a folder for the reports (defaults to
   `~/Documents/NeoSCAN/Summaries`).

Reports are written as `YYYY-MM-DD.html` files. NeoSCAN runs the summary at
midnight while it is open, and on next launch automatically generates reports
for any past day that has a transcript but no report yet.

No new Python dependency is required — the API call uses the standard library.

### ADS-B Reception (RTL-SDR + dump1090)

NeoSCAN can receive live ADS-B aircraft transponder broadcasts using an
[RTL-SDR](https://www.rtl-sdr.com/) USB dongle and
[dump1090](https://github.com/flightaware/dump1090).

**Requirements:**

- An RTL-SDR USB dongle (RTL-SDR Blog V3/V4 or similar RTL2832U-based device)
- `dump1090` installed on the host:
  ```bash
  # macOS
  brew install dump1090-mutability
  # Linux (Debian/Ubuntu)
  sudo apt install dump1090-mutability
  ```
- `rtl-sdr` tools (for device enumeration in the connect dialog):
  ```bash
  # macOS
  brew install rtl-sdr
  # Linux
  sudo apt install rtl-sdr
  ```

**Connecting:**

1. Plug in the RTL-SDR dongle.
2. Open **SDR → Connect SDR (ADS-B)…**
3. Select your device from the list (detected automatically via `rtl_test`).
4. Choose a gain setting (Auto works well for most locations).
5. Click **Connect**. NeoSCAN launches dump1090 and connects to its data stream.

The **ADS-B** tab shows a live aircraft grid that updates every second.
Aircraft not heard for 60 seconds are shown in grey; those silent for 5 minutes
are removed. Emergency squawks highlight rows in red; alert squawks in yellow.

**macOS note:** If you see a "USB device is claimed by the macOS kernel driver"
error, unplug the dongle, wait 2 seconds, and reconnect it. This resolves the
conflict in most cases.

**Windows setup:** Windows requires some additional one-time steps:

1. **Install the WinUSB driver** using [Zadig](https://zadig.akeo.ie/):
   - Plug in the RTL-SDR dongle.
   - Open Zadig → Options → List All Devices.
   - Select **Bulk-In, Interface (Interface 0)** (or your dongle's name).
   - Choose **WinUSB** and click **Install Driver**.

2. **Install dump1090** — download a Windows build from the
   [dump1090-fa releases page](https://github.com/flightaware/dump1090/releases)
   and extract it to a folder (e.g. `C:\dump1090-fa\`).

3. **Point NeoSCAN to dump1090** — open **File → Preferences → ADS-B**,
   click Browse next to *dump1090 executable*, and select `dump1090.exe`.

4. **Device enumeration** — install `pyrtlsdr` for automatic device listing:
   ```
   pip install pyrtlsdr
   ```
   Alternatively, download the [rtl-sdr Windows binaries](https://osmocom.org/projects/rtl-sdr/wiki/Rtl-sdr)
   and add the folder to your system PATH so `rtl_test.exe` is accessible.

**Traffic logging:**

Enable logging in **File → Preferences → ADS-B**. When enabled, NeoSCAN writes
one CSV row per aircraft to a daily file (`adsb-YYYY-MM-DD.csv`) when the
aircraft leaves the grid or the SDR is disconnected. Fields recorded: ICAO,
callsign, first/last seen times, duration, altitude, speed, track, position,
squawk, and alert flags.

---

## CSV Import

NeoSCAN can import channel lists and talk group lists from RadioReference CSV
exports (and any CSV with compatible headers). There are two import paths
depending on the type of data.

### Conventional Channels — File > Import CSV…

Use this for conventional channel lists (analog and digital).

1. Open or create a configuration file with at least one conventional system
   and group.
2. Choose **File → Import CSV…**.
3. Select your CSV file. NeoSCAN auto-maps columns based on header names.
4. Adjust any incorrect mappings in the field-mapping row, then click **Import**.

**RadioReference conventional export columns and their mappings:**

| CSV Column | Maps To | Notes |
|---|---|---|
| Frequency Output | Frequency | RX frequency in MHz |
| Alpha Tag | Channel Name | Scanner display label |
| Mode | Modulation + Audio Type | `FMN`/`FM`/`AM` → modulation; `P25` → NFM + Digital Only |
| Description | Comment | |
| PL Output Tone | CTCSS/DCS Tone | |
| Tag | Number Tag | Numeric only; non-numeric values become NONE |

**Mode values recognised:**

| Mode | Modulation set | Audio Type set |
|---|---|---|
| `FM` | FM | All |
| `FMN` | NFM | All |
| `AM` | AM | All |
| `P25` | NFM | Digital Only |
| `DMR` | NFM | Digital Only |

---

### P25 / Motorola Talk Groups — File > Import CSV…

Use this for trunked system talk group lists. The target group must be inside
a P25 or Motorola system — NeoSCAN detects the system type and creates
`TalkGroup` objects instead of conventional channels.

**Workflow:**

1. Create a P25 or Motorola trunked system in the editor, then add a TGID group
   inside it.
2. Choose **File → Import CSV…**.
3. Select the RadioReference talk group export CSV.
4. In the **Import Into** dropdown, select the TGID group inside your trunked
   system.
5. Click **Import**.

**RadioReference talk group export columns and their mappings:**

| CSV Column | Maps To | Notes |
|---|---|---|
| Decimal | Talk Group ID | TGID number |
| Alpha Tag | Channel Name | Scanner display label (up to 16 chars) |
| Mode | Audio Type | `D`/`DE` → Digital Only; `A` → Analog Only; `D/A` → All |
| Description | Comment | |
| Tag | Number Tag | Numeric only |

**Mode values recognised:**

| Mode | Audio Type set |
|---|---|
| `D` | Digital Only |
| `DE` (Encrypted) | Digital Only |
| `A` | Analog Only |
| `D/A` | All |

---

### P25 / Motorola Sites and Trunk Frequencies — File > Import Sites from CSV…

Use this to populate a trunked system's sites and control/voice frequencies
from a RadioReference site list export.

**Workflow:**

1. Create a P25 or Motorola trunked system in the editor (it does not need
   any groups yet).
2. Choose **File → Import Sites from CSV…**.
3. Select the RadioReference sites export CSV.
4. In the **Import Into System** dropdown, select your trunked system.
5. Review the site preview table, then click **Import Sites**.

Each row in the CSV becomes a **Site group** in the system. All frequencies
listed for that site become **trunk frequencies** with auto-assigned LCNs.
Frequencies marked with a trailing `c` in the CSV (control channels) are
imported identically — the control-channel marker is stripped.

**Supported CSV layouts:**

| Column | Full export | Compact export |
|---|---|---|
| RFSS | ✓ | — (omitted) |
| Site Dec | ✓ | ✓ |
| Site Hex | ✓ | ✓ |
| Site NAC | ✓ | — (omitted) |
| Description | ✓ | ✓ |
| County Name | ✓ | ✓ |
| Lat / Lon | ✓ | ✓ |
| Range | ✓ | ✓ |
| Frequencies… | ✓ | ✓ |

Columns are identified by header name, so missing optional columns (RFSS,
Site NAC) do not shift the frequency data.

---

### Full P25 / Motorola System Import Workflow

To build a complete trunked system from RadioReference exports:

1. In the channel editor, create a new P25 or Motorola system.
2. Add a TGID group inside the system (for talk groups).
3. **File → Import Sites from CSV…** — select the sites CSV. This creates
   site groups and populates all trunk frequencies.
4. **File → Import CSV…** — select the talk groups CSV and target the TGID
   group created in step 2.
5. Save the configuration (**File → Save**) and upload to the scanner.

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
      transcriber.py               Transcription manager and worker (HTTP client to whisper-wrapper-api-server)
      transcript_writer.py         Transcript file writer
      summary_generator.py         Anthropic API client + HTML report renderer
      summary_scheduler.py         Midnight QTimer + catch-up scan for daily summaries
    sdr/
      dump1090_manager.py          dump1090 subprocess lifecycle manager
      adsb_receiver.py             SBS TCP client QThread (connects to dump1090 :30003)
      aircraft_state.py            Aircraft dataclass and state tracker
      adsb_logger.py               Daily CSV logger for ADS-B traffic
    ui/
      main_window.py               Main application window (multi-radio tabs)
      editor/
        systems_panel.py           Tree view panel (Systems > Groups > Channels)
        channel_editor.py          Channel/group/system detail editor form
        csv_import_dialog.py       CSV import wizard (channels and talk groups)
        trunk_site_import_dialog.py  Trunk site / frequency import wizard
      programmer/
        upload_dialog.py           Upload-to-scanner dialog with progress log
        download_dialog.py         Download-from-scanner dialog with progress log
      remote_control/
        control_panel.py           Virtual scanner keypad and display
        log_panel.py               Multi-radio transmission logger with CSV export
      adsb/
        adsb_panel.py              Live ADS-B aircraft grid tab
        connect_sdr_dialog.py      SDR device picker and gain selector
      settings/
        settings_dialog.py         Connection dialog (port, audio device, transcription)
        preferences_dialog.py      Tabbed app preferences: General / Logging / Audio / Transcription / ADS-B
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

Audio transcription is handled by the separate
[whisper-wrapper-api-server](https://github.com/keithlawless/whisper-wrapper-api-server)
— no Whisper or PyTorch libraries are required inside NeoSCAN.

## Reference Implementation

The FreeSCAN source code (Windows-only Visual Basic, now abandoned) was
consulted during development to understand the `.996` file format and
scanner protocol behaviour. It is not included in this repository.

## License

NeoSCAN is free software released under the GNU General Public License v3.
See the `LICENSE` file for the full license text.
