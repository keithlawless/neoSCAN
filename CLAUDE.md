# CLAUDE.md — NeoSCAN Developer Guide

This file guides Claude (and human developers) working on NeoSCAN.
Keep it up to date as the project evolves.

---

## Project Overview

NeoSCAN is a cross-platform desktop app for programming and remote-controlling
Uniden BCT15-X / BCD996XT radio scanners over USB serial. It replaces the
abandoned Windows-only FreeSCAN application.

**Stack:** Python 3.11+ · PyQt6 · pyserial

**Run:**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
python main.py
```

---

## Repository Layout

```
main.py                          Entry point — creates QApplication and MainWindow
pyproject.toml                   Dependencies: PyQt6, pyserial
app/
  serial/
    port_manager.py              Serial port detection and open/close
    protocol.py                  BCT15-X command protocol layer
    scanner_model.py             Mod-mode and system-type translation tables
  data/
    models.py                    Core dataclasses (ScannerConfig, System, Group, Channel)
    file_996.py                  .996 file parser and writer
    file_csv.py                  CSV import with fuzzy field mapping
  ui/
    main_window.py               Top-level window — menus, tabs, connection management
    editor/
      systems_panel.py           Left-pane tree: Systems > Groups > Channels
      channel_editor.py          Right-pane detail form with per-field help text
      csv_import_dialog.py       4-step CSV import wizard
    programmer/
      upload_dialog.py           Upload-to-scanner dialog (QThread worker)
      download_dialog.py         Download-from-scanner dialog (QThread worker)
    remote_control/
      control_panel.py           Virtual 24-key scanner keypad + display
      log_panel.py               Transmission logger, 150 ms poll, CSV export
    settings/
      settings_dialog.py         COM port selection dialog
reference/
  BCD996XT_v1.04.00_Protocol.pdf  Full USB protocol specification — read this first
sample-data/
  sample.996                     Real FreeSCAN file used for parser testing
```

---

## FreeSCAN Reference

The FreeSCAN VB source is at `../FreeSCAN/FreeSCAN/` (sibling directory, not
committed here). Consult it when implementing protocol commands or parsing
behaviour. Key files:

| File | What it contains |
|------|-----------------|
| `Module1.vb` | Global constants: `MaxSystems=700`, `MaxGroups=277`, `MaxSetting=62`, `MaxChanSetting=30`, `MaxRadioSetting=200`, file header `.7BCD996T` |
| `frmCommsDownload.vb` | Download workflow, `SIH` usage, `SIN`/`GIN`/`CIN` linked-list traversal |
| `frmComms.vb` | Upload workflow, `CSY`/`SIN`/`AGC`/`GIN`/`ACC`/`CIN`/`QGL` sequence, `SendCMD` implementation |
| `Resources/Main Editor/frmSystemEditor.vb` | `.996` load/save logic, `varSite` field indices, channel grid handling |
| `Resources/Virtual Control/frmLog.vb` | Transmission log fields |
| `Mod996.vb` | Band plan helpers |

---

## Scanner Protocol (BCT15-X / BCD996XT)

### Connection
- **115200 baud, 8N1, no flow control**
- USB-serial adapter chips: FTDI (VID 0x0403), Prolific (0x067B),
  Silicon Labs (0x10C4), CH340 (0x1A86)
- macOS port: `/dev/cu.usbserial-XXXXXXXX`
- Windows port: `COMx`

### Command format
```
TX:  CMD\r              (no params)
TX:  CMD,p1,p2,p3\r    (with params)
RX:  CMD,data\r         (response echoes command name)
RX:  ERR\r              (command rejected)
```

`protocol.py::send_command()` strips the echoed command prefix automatically,
so callers receive only the data payload.

### Key commands

| Command | Direction | Description |
|---------|-----------|-------------|
| `MDL` | R | Scanner model string, e.g. `BCT15X` |
| `VER` | R | Firmware version, e.g. `1.04.00` |
| `MEM` | R | Memory usage percentage |
| `PRG` | R/W | Enter programming mode — required before SIN/GIN/CIN/CSY/AGC/ACC |
| `EPG` | W | Exit programming mode |
| `SIH` | R | **System Index Head** — index of first system, or `-1` if none |
| `SIN,<idx>` | R | Get system info by index |
| `SIN,<idx>,<fields>` | W | Set system info |
| `CSY,CNV` | W | Create new conventional system slot, returns its index |
| `GIN,<idx>` | R | Get group info |
| `GIN,<idx>,<fields>` | W | Set group info |
| `AGC,<sys_idx>` | W | Add group to system, returns new group index |
| `CIN,<idx>` | R | Get channel info |
| `CIN,<idx>,<fields>` | W | Set channel info |
| `ACC,<grp_idx>` | W | Add channel to group, returns new channel index |
| `QGL,<sys_idx>,<pattern>` | W | Set Quick Group Lockout (10-char binary string) |
| `GLG` | R | Currently-received channel info (remote control mode) |
| `KEY,<code>` | W | Send virtual keypress |
| `STS` | R | Scanner status word |

### Download traversal — CRITICAL

The scanner stores systems/groups/channels as a **singly-linked list**.
Each response contains the index of the next item, terminating with **-1**
(not 0 — 0 is a valid index).

```
SIH  →  first_sys_index  (or -1 = empty)

SIN,<idx>  →  ...,next_sys_index     field index 12 (0-based) = position 13 (1-based)
             ...,first_grp_index     field index 13 (0-based) = position 14 (1-based)

GIN,<idx>  →  ...,next_grp_index     field index 5  (0-based) = position 6  (1-based)
             ...,first_chan_index    field index 7  (0-based) = position 8  (1-based)

CIN,<idx>  →  ...,next_chan_index    field index 11 (0-based) = position 12 (1-based)
```

> **Bug history:** An earlier version used `GLF` instead of `SIH`, used
> field index 15 for next-system, and terminated loops at `> 0`.
> All three bugs together produced zero output with no error messages.
> See commit `cd4d957` for the fix.

### Upload sequence (conventional system)

```
PRG
CSY,CNV                          → sys_index
SIN,<sys_index>,<name>,<qk>,<hold>,<lockout>,<delay>,<data_skip>,,,AUTO,8
  AGC,<sys_index>                → grp_index
  GIN,<grp_index>,<name>,<qk>,<lockout>
    ACC,<grp_index>              → ch_index
    CIN,<ch_index>,<name>,<freq_x10000>,<mod>,<ctcss>,<tone_lock>,
        <lockout>,<priority>,<att>,<alert_tone>,<alert_level>
QGL,<sys_index>,<qgl_pattern>
EPG
```

Frequency is sent as integer × 10000 (e.g. 154.2350 MHz → 1542350).

### ParaParse index convention

FreeSCAN's `ParaParse(str, n)` is **1-indexed**. Our `_para(str, n)` in
`download_dialog.py` is **0-indexed**. Always subtract 1 when translating
FreeSCAN field references.

---

## .996 File Format

File header: `.7BCD996T` (current) or `.08BCD996T` (legacy, 100 radio settings)

Structure (values are quoted strings, one per line; counts are bare integers):

```
".7BCD996T"
RadioSetting[1..200]          200 lines
CustSearch[1..10][0..16]      170 lines  (10 ranges × 17 fields)
<num_systems>
  Per system:
    varSite[sys][0][0][1..62]  62 lines of system settings
    <num_groups>
      Per group:
        varSite[sys][grp][0][1..62]  62 lines of group settings
"TrunkSection"
<num_trunk_freqs>
  Per trunk freq: freq, lcn, group_id, params[0..7]
"SEARCHLOCKOUTS"
<num_lockouts>
  Per lockout: frequency string
"CHANDATA"
<num_channels>
  Per channel: ChanInfo[1..30]  30 lines
```

Channels are stored in a **flat global pool** at the end of the file and
linked to groups via a 16-char hex `group_id` string (field index 10,
0-based; position 11, 1-based). The parser in `file_996.py` assigns channels
to their groups after loading the full file.

### Key varSite field indices (1-based, as in FreeSCAN)

System (group=0):
`1`=name, `2`=lockout, `3`=sys_type, `4`=qk, `5`=hold, `6`=delay,
`7`=startup_key, `8`=QGL, `9`=data_skip, `16`=fleet_map, `17`=custom_fleet,
`19`=gps_lat, `20`=gps_lon, `21`=gps_range, `22`=gps_enable, `23`=group_id,
`24`=record_mode, `25`=emg_alert_type, `26`=emg_alert_level, `48`=apco_mode,
`49`=apco_threshold

Group:
`1`=name, `2`=lockout, `4`=qk, `5`=group_type (2=group, 3=site), `10`=group_id

Channel (ChanInfo):
`1`=name, `2`=frequency, `4`=modulation_index, `5`=lockout, `6`=attenuator,
`7`=priority, `8`=alert_tone, `9`=tone, `10`=group_id, `11`=tone_lockout,
`12`=audio_type, `13`=alert_level, `14`=comment, `15`=delay, `16`=number_tag,
`17`=output, `18`=p25_wait, `19`=step_size, `20`=volume_offset

### System type values

`1`=Conventional, `2`=Motorola Type I, `3`=Motorola Type II / EDACS,
`4`=EDACS narrow/wide, `5`=EDACS standard, `6`=LTR

---

## Data Model

The core classes are in `app/data/models.py`:

- `ScannerConfig` — top-level container; holds systems, trunk freqs, search lockouts
- `System` — a scanner system (conventional or trunked)
- `Group` — a channel group within a system (or a trunked site)
- `Channel` — a conventional channel; has `group_id` linking it to a `Group`
- `TalkGroup` — a trunked talk group (same storage as Channel in .996 format)
- `TrunkFrequency` — a trunk control/voice frequency

All classes carry a `_raw: list[str]` field that preserves the original
settings array for round-trip fidelity. Populate named fields for display
and editing; `_raw` is used only for serialisation.

---

## UI Patterns

- **Long operations** (connect, upload, download) run in `QThread` subclasses
  and communicate via `pyqtSignal`. Never block the main thread.
- **Model write-back** in `channel_editor.py` happens immediately on field
  change (no Apply button). Each field widget connects to a `_set_*_field()`
  helper that calls `setattr` and sets `config.modified = True`.
- **Tree rebuilds** in `systems_panel.py` rebuild the entire `QStandardItemModel`
  from scratch via `_rebuild_tree()`. This is fast enough for current data sizes.
- **Help text** is defined as module-level `HELP` dict in `channel_editor.py`.
  Each field has a tooltip (set via `setToolTip`) and an inline `_help_label()`
  beneath it in the form.

---

## Implementation Status

| Phase | Status | Notes |
|-------|--------|-------|
| 1 — Serial connection | Complete | Port detection, connect/disconnect, status bar |
| 2 — .996 import | Complete | Parser + writer, round-trip verified (359 channels) |
| 3 — Channel editor | Complete | Tree + form, add/delete, help text |
| 4 — CSV import | Complete | Fuzzy field mapping, 4-step wizard |
| 5 — Upload/download | Complete | Bug-fixed; see commit cd4d957 |
| 6 — Remote control | Complete | Keypad, transmission log, CSV export |
| 7 — Polish & packaging | Complete | .spec, icons, prefs dialog (auto-connect + theme), GPL license |

### Suggested future features

- **Duplicate frequency detection** — warn when two channels share a frequency
- **Frequency range validator** — cross-check against BCT15-X band plan
- **Quick Key visual editor** — drag-and-drop system/group → quick key assignment
- **Transmission log analysis** — most-active channels, time-of-day heatmap
- **Auto-backup on download** — save timestamped `.996` copy on every download
- **RadioReference API import** — requires paid account + API key from user

---

## Testing

```bash
pip install -e ".[dev]"
pytest tests/
```

The `sample-data/sample.996` file is used for parser tests. A round-trip
test (load → save → reload, compare system/channel counts) is the minimum
bar for any changes to `file_996.py`.

To test without a physical scanner, use the offscreen Qt platform:
```bash
QT_QPA_PLATFORM=offscreen python main.py
```

---

## Known Gotchas

1. **`.996` line endings** — the format uses CRLF (`\r\n`). The parser
   normalises both CR-only and CRLF. The writer always produces CRLF.
   Git may warn about line ending conversion — this is expected for
   `sample-data/sample.996`.

2. **PyQt6 signal/slot types** — `pyqtSignal(object)` is used for passing
   `ScannerConfig` across thread boundaries (e.g. `finished_ok`). Do not
   use `pyqtSignal(ScannerConfig)` — PyQt6 requires registered types.

3. **Serial read timing** — `protocol.py` uses a polling loop with 5 ms
   sleep. Increasing this risks timeouts on slower machines; decreasing it
   can cause high CPU usage during bulk upload/download.

4. **Group ID generation** — uses `uuid.uuid4().hex[:16].upper()` to match
   FreeSCAN's 16-char hex format. Do not change the length — it is part of
   the `.996` format contract.

5. **Modulation index vs string** — the `.996` file stores modulation as an
   index string (`"0"`=AUTO, `"1"`=AM, etc.). The scanner protocol uses the
   string name (`FM`, `NFM`, etc.). `scanner_model.py` handles translation
   in both directions.
