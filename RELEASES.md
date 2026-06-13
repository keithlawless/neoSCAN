# NeoSCAN Release History

---

## v1.2.2 — 2026-06-13

### Windows ADS-B connect fix

- **Fixed ADS-B being unusable on the Windows binary**: the Connect SDR dialog disabled the Connect button whenever automatic device enumeration found nothing, which it always did in the packaged build (pyrtlsdr is not bundled and `rtl_test.exe` is usually absent). Device enumeration is now treated as best-effort — dump1090 opens the dongle itself — so when no devices are listed the dialog offers a manual index picker (Device 0–3, default 0) and Connect stays enabled as long as dump1090 is installed
- **Clearer messaging in packaged builds**: the "detection unavailable" notice no longer suggests `pip install pyrtlsdr` (which cannot affect the frozen interpreter) and explains that you can still connect by index

---

## v1.2.1 — 2026-06-13

### Windows ADS-B / SDR fixes

- **Fixed dump1090 version check timing out on Windows**: the Connect SDR dialog failed when the dump1090 build prints its version then keeps running (e.g. the Mongoose-based [gvanem/Dump1090](https://github.com/gvanem/Dump1090) build) instead of exiting; the version is now read from the first line of output without waiting for the process to exit
- **Fixed stale-process cleanup on Windows**: replaced the Unix-only `pkill` with `taskkill` on Windows so leftover dump1090 instances no longer hold network ports on reconnect
- **Suppressed console windows** that briefly flashed when launching dump1090 and enumerating RTL-SDR devices on Windows
- **Documentation**: README now points Windows users to gvanem/Dump1090 for a prebuilt `dump1090.exe`

---

## v1.2.0 — 2026-05-25

### ADS-B Reception & ATC Radar

- **ADS-B support** via RTL-SDR dongle + dump1090: receive live aircraft transponder data on 1090 MHz
- **Live aircraft grid** (ADS-B tab): ICAO, callsign, altitude, vertical trend, speed, heading, position, squawk, alert flags, last-seen age; stale aircraft greyed after 60 s, removed after 5 min
- **Vertical trend** (Climbing / Descending / Level) derived from OLS linear regression over altitude history; requires ≥ 5 observations and ≥ 500 ft range before classifying
- **ATC Mode radar tab**: real-time QPainter radar display with aircraft icons oriented by heading, colour-coded by trend, range rings, compass labels, and callsign/FL labels; zoom (scroll or +/− keys), pan (drag), reset (↺ button or double-click)
- **Home-location resolution**: manual coordinates in Preferences → ADS-B, then system GPS (QtPositioning), then IP geolocation fallback
- **ADS-B traffic logging** to daily CSV files (rolls over at midnight); records final aircraft statistics when a contact leaves the grid or on disconnect
- **Windows support**: pyrtlsdr device enumeration, common-path search for dump1090.exe, Zadig/WinUSB error guidance, custom executable path in Preferences
- **SDR connection UX**: ADS-B and ATC Mode tabs disabled until an SDR is connected; toolbar shows live connection status; auto-kills stale dump1090 processes on reconnect

---

## v1.1.0 — 2025-11-01

### AI Transcription & Multi-Scanner

- **Audio transcription** via external [whisper-wrapper-api-server](https://github.com/keithlawless/whisper-wrapper-api-server); no local GPU required
- **Daily AI summaries** using map-reduce over per-hour transcripts to handle long recordings
- **Silero VAD** pre-filter before transcription to eliminate hallucinations on noise-only clips
- **Three simultaneous scanner connections**: remote-control panel supports up to three independent Uniden scanner sessions at once
- **Transcript CSV export** and scrolling marquee display in the remote-control view

---

## v1.0.0 — 2025-06-15

### Initial Release

- **Serial connection** to Uniden BCT15-X and BCD996XT scanners over USB (FTDI, Prolific, Silicon Labs, CH340 adapters); auto-detect on macOS, Windows, and Linux
- **.996 file import/export**: full round-trip fidelity for conventional and Motorola trunked systems
- **Channel editor**: hierarchical Systems → Groups → Channels tree; scrollable detail form with per-field help text
- **CSV import wizard**: 4-step import with fuzzy field mapping
- **Upload/download**: full scanner programming over USB with progress dialog; QThread-based so the UI stays responsive
- **Remote control panel**: virtual 24-key keypad, live scrolling display, 150 ms transmission log with CSV export
- **Preferences**: auto-connect, dark/light theme toggle, COM port selection
- **macOS** (.dmg), **Windows** (.exe), **Linux** (tarball) distributions via PyInstaller
