"""
Upload dialog — programs the scanner with the current channel list.
Runs the upload in a background QThread so the UI stays responsive.
"""
from __future__ import annotations

import logging
import uuid

from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QCheckBox,
)

import serial

from app.data.models import ScannerConfig, System, Group, Channel, TalkGroup, TrunkFrequency, SYS_TYPE_CONVENTIONAL
from app.serial.protocol import ScannerProtocol, ProtocolError
from app.serial.scanner_model import mod_mode_to_string, internal_to_sin_type, internal_to_csy_type

log = logging.getLogger(__name__)


def _para(payload: str, idx: int) -> str:
    """Extract the idx-th comma-separated field from a protocol response."""
    parts = payload.split(",")
    if idx < len(parts):
        return parts[idx]
    return ""


class _UploadWorker(QThread):
    """
    Background thread that performs the scanner upload.
    Emits progress/log signals for the dialog to display.
    """
    progress = pyqtSignal(int)          # 0-100
    log_line = pyqtSignal(str)          # text to append to log
    status = pyqtSignal(str)            # short status for the label
    finished_ok = pyqtSignal(int, int)  # systems_done, channels_done
    finished_err = pyqtSignal(str)      # error message

    def __init__(
        self,
        proto: ScannerProtocol,
        config: ScannerConfig,
        selected_systems: list[int],
        clear_first: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._proto = proto
        self._config = config
        self._selected = selected_systems
        self._clear_first = clear_first
        self._abort = False

    def abort(self) -> None:
        self._abort = True

    def run(self) -> None:
        try:
            self._do_upload()
        except Exception as exc:
            log.exception("Upload failed")
            self.finished_err.emit(str(exc))

    def _delete_all_systems(self, proto: ScannerProtocol) -> int:
        """
        CLR resets the scanner to factory state but restores factory-default
        channels (e.g. pre-programmed race tracks on the BCT15X).  DSY alone
        cannot delete factory systems because they have the protect bit set.

        The solution: CLR first (which clears user channels AND puts factory
        defaults back into the normal linked list), then walk that list with
        DSY to delete every system including the factory ones.

        CLR must be sent OUTSIDE program mode, so we exit PRG, send CLR,
        then re-enter PRG before the DSY loop.
        """
        self.log_line.emit("  Exiting program mode to send CLR…")
        try:
            proto.exit_program_mode()
        except ProtocolError as e:
            self.log_line.emit(f"  Warning: EPG before CLR failed: {e}")

        import time as _time
        _time.sleep(0.5)   # give scanner time to fully exit PRG

        self.log_line.emit("  Sending CLR to reset scanner to factory state…")
        try:
            proto.send_command("CLR")
            self.log_line.emit("  CLR complete.")
        except ProtocolError as e:
            self.log_line.emit(f"  Warning: CLR failed: {e} — continuing with DSY only.")

        _time.sleep(0.5)   # give scanner time to process CLR

        self.log_line.emit("  Re-entering program mode…")
        proto.enter_program_mode()

        try:
            first = proto.send_command("SIH")
            sys_index = int(first.strip())
        except (ProtocolError, ValueError):
            return 0

        deleted = 0
        seen = set()  # guard against corrupt linked lists
        while sys_index != -1 and sys_index not in seen:
            seen.add(sys_index)
            # Read next pointer BEFORE deleting (DSY removes the node)
            try:
                sin = proto.send_command(f"SIN,{sys_index}")
                next_index_str = sin.split(",")[12] if sin else "-1"
                try:
                    next_index = int(next_index_str)
                except ValueError:
                    next_index = -1
            except ProtocolError:
                next_index = -1

            try:
                proto.send_command(f"DSY,{sys_index}")
                deleted += 1
                self.log_line.emit(f"  Deleted system at index {sys_index}.")
            except ProtocolError as e:
                self.log_line.emit(f"  Warning: could not delete system {sys_index}: {e}")

            sys_index = next_index

        return deleted

    def _do_upload(self) -> None:
        proto = self._proto
        config = self._config
        systems = [config.systems[i] for i in self._selected]
        total_steps = max(1, sum(
            1 + len(s.groups) + sum(len(g.channels) for g in s.groups)
            for s in systems
        ))
        done = 0
        sys_count = 0
        ch_count = 0

        self.log_line.emit("Entering program mode…")
        proto.enter_program_mode()

        if self._clear_first:
            self.status.emit("Deleting existing systems…")
            self.log_line.emit("Deleting all existing systems from scanner…")
            try:
                deleted = self._delete_all_systems(proto)
                self.log_line.emit(f"Deleted {deleted} existing system(s).")
            except ProtocolError as e:
                self.log_line.emit(
                    f"ERROR: Failed to delete existing systems: {e}\n"
                    "Upload aborted."
                )
                proto.exit_program_mode()
                self.finished_err.emit(f"Failed to delete existing systems: {e}")
                return

        for sys in systems:
            if self._abort:
                break
            self.status.emit(f"Uploading system: {sys.name}")
            self.log_line.emit(f"\n[System] {sys.name} ({sys.type_name})")

            # EDACS, P25, and LTR trunked systems are not yet supported.
            # Motorola (MOT) is handled below.
            if sys.is_trunked and not sys.is_motorola:
                self.log_line.emit(
                    f"  Skipped — {sys.type_name} system upload not yet supported. "
                    "Only Motorola trunked systems can be uploaded."
                )
                continue

            # CSY uses a different (simpler) type code than SIN.
            csy_type = internal_to_csy_type(sys.system_type)

            # Create system slot on scanner.
            # CSY requires [SYS_TYPE],[PROTECT] — PROTECT=0 means unprotected.
            try:
                sys_index = proto.send_command("CSY", csy_type, "0")
                sys_index = sys_index.strip()
            except ProtocolError as e:
                self.log_line.emit(f"  ERROR creating system: {e}")
                continue

            if not sys_index or sys_index in ("-1", "ERR"):
                self.log_line.emit(
                    f"  ERROR: Scanner returned invalid system index ({sys_index!r}). "
                    "Scanner memory may be full — try clearing the scanner first."
                )
                continue

            # Configure system via SIN.
            # SET format: SIN,[INDEX],[NAME],[QUICK_KEY],[HLD],[LOUT],[DLY],
            #   [RSV]*5,[START_KEY],[RECORD],[RSV]*5,[NUMBER_TAG],
            #   [AGC_ANALOG],[AGC_DIGITAL],[P25WAITING]
            # Sanitise fields — any format error causes SIN to abort silently.
            # Strip characters invalid in scanner names: commas break the protocol;
            # parentheses and other special chars may cause ERR responses.
            _safe = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                        "0123456789 -_/&.'!")
            raw_name = (sys.name or "").strip()
            name = "".join(c for c in raw_name if c in _safe)[:16].strip()
            qk = (sys.quick_key or ".").strip() or "."
            try:
                hld = str(max(0, min(255, int(sys.hold_time or 2))))
            except ValueError:
                hld = "2"
            # DLY must be one of the accepted values; clamp to nearest valid value.
            _valid_dly = (-10, -5, -2, 0, 1, 2, 5, 10, 30)
            try:
                dly_int = int(sys.delay_time or 2)
                dly = str(min(_valid_dly, key=lambda v: abs(v - dly_int)))
            except ValueError:
                dly = "2"
            lout = 1 if sys.lockout else 0
            # BCT15X SIN 22-field SET format (differs from BCD996XT at positions 18-22).
            # Positions: 7-11=RSV(5), 12=START_KEY, 13=RECORD, 14-17=RSV(4),
            #            18=STATE("00"=none), 19=NUMBER_TAG, 20-22=trailing empty.
            cmd = (
                f"SIN,{sys_index},{name},{qk},{hld},{lout},{dly},"
                f",,,,,"    # pos 7-11: RSV (5 empty)
                f".,"       # pos 12: START_KEY ("." = none)
                f"{sys.record_mode or '0'},"  # pos 13: RECORD (0=off,1=marked,2=all)
                f",,,,"     # pos 14-17: RSV (4 empty)
                f"00,"      # pos 18: STATE ("00" = no state)
                f"NONE,,,"  # pos 19: NUMBER_TAG; 20-22: trailing empty
            )
            self.log_line.emit(f"  SIN cmd: {cmd!r}")
            try:
                sin_result = proto.send_command(cmd)
                if sin_result != "OK":
                    self.log_line.emit(f"  Warning: SIN returned {sin_result!r} — system name may not have been set.")
            except ProtocolError as e:
                self.log_line.emit(f"  Warning: SIN error: {e} — system name may not have been set.")

            done += 1
            self.progress.emit(int(done / total_steps * 100))

            if sys.is_conventional:
                for grp in sys.groups:
                    if self._abort:
                        break
                    self.log_line.emit(f"  [Group] {grp.name}")
                    # Add group
                    try:
                        grp_index = proto.send_command("AGC", sys_index)
                        grp_index = grp_index.strip()
                    except ProtocolError as e:
                        self.log_line.emit(f"    ERROR adding group: {e}")
                        done += 1 + len(grp.channels)
                        self.progress.emit(int(done / total_steps * 100))
                        continue

                    # Configure group via GIN.
                    # SET format: GIN,[GRP_INDEX],[NAME],[QUICK_KEY],[LOUT],
                    #   [LATITUDE],[LONGITUDE],[RANGE],[GPS_ENABLE]
                    grp_qk = grp.quick_key or "."
                    grp_lout = 1 if grp.lockout else 0
                    grp_name = "".join(c for c in (grp.name or "").strip() if c in _safe)[:16].strip()
                    try:
                        proto.send_command(
                            f"GIN,{grp_index},{grp_name},"
                            f"{grp_qk},{grp_lout},,,,"  # trailing empty geo fields
                        )
                    except ProtocolError as e:
                        self.log_line.emit(f"    Warning: GIN error: {e}")

                    done += 1
                    self.progress.emit(int(done / total_steps * 100))

                    for ch in grp.channels:
                        if self._abort:
                            break
                        if not isinstance(ch, Channel):
                            done += 1
                            continue
                        try:
                            freq_raw = float(ch.frequency)
                        except (ValueError, TypeError):
                            done += 1
                            continue
                        if freq_raw <= 0:
                            done += 1
                            continue

                        freq_int = int(freq_raw * 10000)
                        mod = mod_mode_to_string(
                            ch.modulation if ch.modulation else "0"
                        )
                        # Allocate channel slot
                        try:
                            ch_index = proto.send_command("ACC", grp_index)
                            ch_index = ch_index.strip()
                        except ProtocolError as e:
                            self.log_line.emit(f"    ERROR allocating channel: {e}")
                            done += 1
                            continue

                        # Upload channel via CIN.
                        # SET format: CIN,[INDEX],[NAME],[FRQ],[MOD],[CTCSS/DCS],
                        #   [TLOCK],[LOUT],[PRI],[ATT],[ALT],[ALTL],
                        #   [RECORD],[AUDIO_TYPE],[P25NAC],[NUMBER_TAG],
                        #   [ALT_COLOR],[ALT_PATTERN],[VOL_OFFSET]
                        tone = ch.tone or "0"
                        alt = ch.alert_tone or "0"
                        altl = ch.alert_level or "0"
                        ch_name = "".join(c for c in (ch.name or "").strip() if c in _safe)[:16].strip()
                        record = 1 if ch.output == "ON" else 0
                        try:
                            proto.send_command(
                                f"CIN,{ch_index},{ch_name},{freq_int},{mod},"
                                f"{tone},{1 if ch.tone_lockout else 0},"
                                f"{1 if ch.lockout else 0},{1 if ch.priority else 0},"
                                f"{1 if ch.attenuator else 0},{alt},{altl},"
                                f"{record},0,0,NONE,OFF,0,0"  # RECORD,AUDIO_TYPE,P25NAC,NUMBER_TAG,ALT_COLOR,ALT_PATTERN,VOL_OFFSET
                            )
                            self.log_line.emit(
                                f"    {ch_name}  {freq_raw:.4f} MHz  {mod}"
                            )
                            ch_count += 1
                        except ProtocolError as e:
                            self.log_line.emit(f"    ERROR on CIN: {e}")

                        done += 1
                        self.progress.emit(int(done / total_steps * 100))

                # Upload QGL (quick group lockout) for this system
                try:
                    qgl = sys.qgl or "1111111111"
                    proto.send_command(f"QGL,{sys_index},{qgl}")
                except ProtocolError:
                    pass

            elif sys.is_motorola:
                tgs = self._upload_motorola_system(
                    proto, sys, sys_index, _safe, done, total_steps
                )
                ch_count += tgs
                # QGL for trunked system
                try:
                    qgl = sys.qgl or "1111111111"
                    proto.send_command(f"QGL,{sys_index},{qgl}")
                except ProtocolError:
                    pass

            sys_count += 1

        try:
            proto.exit_program_mode()
            self.log_line.emit("\nExited program mode.")
        except ProtocolError as e:
            self.log_line.emit(f"\nWarning: EPG error: {e}")

        # After leaving program mode the scanner may sit on the last channel
        # it touched.  Wait for it to fully exit program mode then send SCAN.
        try:
            import time as _time
            _time.sleep(1.5)   # scanner needs time to finish EPG transition
            proto.send_key("S")
            self.log_line.emit("Sent SCAN key — scanner is now scanning.")
        except ProtocolError:
            self.log_line.emit(
                "Note: Could not send SCAN key. Press SCAN on the scanner to start."
            )

        self.progress.emit(100)
        self.finished_ok.emit(sys_count, ch_count)

    def _upload_motorola_system(
        self,
        proto: ScannerProtocol,
        sys: "System",
        sys_index: str,
        _safe: set,
        done: int,
        total_steps: int,
    ) -> int:
        """
        Upload TRN parameters, sites/trunk-freqs, and TGID groups/talk-groups
        for a Motorola trunked system.  Returns the number of talk groups uploaded.
        """
        tg_count = 0

        # --- TRN: trunking parameters ---
        # PDF TRN SET format (25 fields after index):
        #   ID_SEARCH, S_BIT, END_CODE, AFS, RSV, RSV,
        #   EMG, EMGL, FMAP, CTM_FMAP,
        #   RSV×10,
        #   MOT_ID, EMG_COLOR, EMG_PATTERN, P25NAC, PRI_ID_SCAN
        try:
            fmap = sys.fleet_map or "16"
            # Custom fleet map must be exactly 8 hex chars.
            # FreeSCAN's MakeFleetMap("") returns "00000000" — do the same.
            raw_ctm = (sys.custom_fleet_map or "").strip()
            ctm = raw_ctm if len(raw_ctm) == 8 else "00000000"
            emg_level = sys.emg_alert_level or "0"
            emg_type = "0"  # numeric index; NONE=0
            proto.set_trunking_params(
                int(sys_index),
                sys.id_search or "0",           # 1. ID_SEARCH
                "1" if sys.ignore_status_bit else "0",  # 2. S_BIT
                "1" if sys.end_code else "0",   # 3. END_CODE
                "0",                            # 4. AFS (EDACS format, 0 for Motorola)
                "0",                            # 5. RSV
                "0",                            # 6. RSV
                emg_type,                       # 7. EMG (alert type)
                emg_level,                      # 8. EMGL (alert level)
                fmap,                           # 9. FMAP (fleet map preset)
                ctm,                            # 10. CTM_FMAP (custom fleet map)
                "0", "0", "0",                  # 11-13. RSV
                "0", "0", "0",                  # 14-16. RSV
                "0", "0", "0",                  # 17-19. RSV
                "0",                            # 20. RSV (10th reserved field)
                "0",                            # 21. MOT_ID (0=Decimal)
                "OFF",                          # 22. EMG_COLOR
                "0",                            # 23. EMG_PATTERN
                "SRCH",                         # 24. P25NAC
                "0",                            # 25. PRI_ID_SCAN
            )
        except ProtocolError as e:
            self.log_line.emit(f"  Warning: TRN error: {e}")

        # --- Trunk frequencies ---
        # CSY,MOT does NOT auto-create a site on BCT15X; must call AST explicitly.
        # AST requires two params: AST,<sys_idx>,<site_type_str>
        # Site type string: M82S = Motorola Type II (SmartZone), M81S = Type I
        _site_type_map = {2: "M81S", 3: "M82S"}
        site_type_str = _site_type_map.get(sys.system_type, "M82S")
        site_index = -1
        if sys.trunk_frequencies:
            try:
                site_index = proto.append_site(int(sys_index), site_type_str)
                self.log_line.emit(f"  Created trunk site (AST) → index {site_index}")
            except ProtocolError as e:
                self.log_line.emit(f"  Warning: AST (create site) error: {e}")

        if site_index != -1 and sys.trunk_frequencies:
            # Configure site via SIF (sets name, modulation, etc.)
            # BCD996P2 SIF SET format (19 fields after index — superset of BCT15X):
            #   name, qk, hld, lout, mod, att, C-CH(always 1), rsv, rsv, start_key,
            #   lat, lon, range, gps, rsv, mot_type, edacs_type, p25waiting, rsv
            # C-CH is "always 1:ON" per BCD996P2 spec; the BCT15X had "0" here
            # and a BCT15X-specific STATE field ("00") where the spec now has rsv.
            site_name = "".join(c for c in (sys.name or "").strip() if c in _safe)[:16].strip()
            try:
                proto.set_site_info(
                    site_index,
                    site_name, ".", "0", "0",          # name, qk, hld, lout
                    "AUTO", "0", "1", "", "",           # mod, att, C-CH(1=always ON), rsv, rsv
                    ".",                                # start_key
                    "00000000N", "00000000E", "", "0",  # lat, lon, range, gps
                    "",                                 # rsv (BCT15X had STATE "00" here)
                    "STD", "", "", "",                  # mot_type, edacs_type, p25waiting, rsv
                )
            except ProtocolError as e:
                self.log_line.emit(f"  Warning: SIF error: {e}")

            self.log_line.emit(f"  Uploading {len(sys.trunk_frequencies)} trunk frequency(ies) to site {site_index}")
            auto_lcn = 0  # FreeSCAN-style auto-increment: starts at 0, increments before use
            for tf in sys.trunk_frequencies:
                if self._abort:
                    break
                try:
                    freq_raw = float(tf.frequency)
                    freq_int = int(freq_raw * 10000)
                except (ValueError, TypeError):
                    continue

                try:
                    freq_idx = proto.add_trunk_freq(site_index)
                except ProtocolError as e:
                    self.log_line.emit(f"    ERROR allocating trunk freq: {e}")
                    continue

                # TFQ SET format (PDF page 218, 7 fields after index):
                #   FRQ, LCN, LOUT, RECORD, NUMBER_TAG, VOL_OFFSET, RSV
                # LCN 0 is invalid — mirror FreeSCAN: auto-increment from 1, or
                # use the stored LCN if it is non-zero.
                auto_lcn += 1
                stored_lcn = int(tf.lcn) if tf.lcn and tf.lcn.strip().isdigit() else 0
                lcn = str(stored_lcn) if stored_lcn > 0 else str(auto_lcn)
                try:
                    tfq_resp = proto.set_trunk_freq(
                        freq_idx,
                        str(freq_int),           # FRQ
                        lcn,                     # LCN
                        "1" if tf.lockout else "0",  # LOUT
                        "0",                     # RECORD
                        "NONE",                  # NUMBER_TAG
                        "0",                     # VOL_OFFSET
                        "",                      # RSV
                    )
                    self.log_line.emit(f"    TF {freq_raw:.4f} MHz  LCN {lcn}  [TFQ idx={freq_idx} resp={tfq_resp!r}]")
                except ProtocolError as e:
                    self.log_line.emit(f"    Warning: TFQ error: {e}")

        # --- TGID groups (group_type == "2") → talk groups ---
        tgid_groups = [g for g in sys.groups if not g.is_site]
        for grp in tgid_groups:
            if self._abort:
                break
            grp_name = "".join(c for c in (grp.name or "").strip() if c in _safe)[:16].strip()
            self.log_line.emit(f"  [TGID Group] {grp_name}")

            try:
                grp_idx = proto.append_tgid_group(int(sys_index))
            except ProtocolError as e:
                self.log_line.emit(f"    ERROR creating TGID group: {e}")
                continue

            # Configure TGID group via GIN
            # SET format: GIN,[GRP_INDEX],[NAME],[QUICK_KEY],[LOUT],[LAT],[LON],[RANGE],[GPS]
            grp_lout = "1" if grp.lockout else "0"
            try:
                proto.set_group_info(
                    grp_idx,
                    f"{grp_name},{grp.quick_key or '.'},{ grp_lout},,,,",
                )
            except ProtocolError as e:
                self.log_line.emit(f"    Warning: GIN error: {e}")

            for tg in grp.channels:
                if self._abort:
                    break
                if not isinstance(tg, TalkGroup):
                    continue

                try:
                    tgid_idx = proto.append_tgid(grp_idx)
                except ProtocolError as e:
                    self.log_line.emit(f"    ERROR allocating TGID: {e}")
                    continue

                tg_name = "".join(c for c in (tg.name or "").strip() if c in _safe)[:16].strip()
                # TIN SET format (PDF page 221, 12 fields after index):
                #   NAME, TGID, LOUT, PRI, ALT, ALTL,
                #   RECORD, AUDIO_TYPE, NUMBER_TAG, ALT_COLOR, ALT_PATTERN, VOL_OFFSET
                try:
                    tin_resp = proto.set_tgid(
                        tgid_idx,
                        tg_name,                         # NAME
                        tg.tgid or "0",                  # TGID
                        "1" if tg.lockout else "0",      # LOUT
                        "1" if tg.priority else "0",     # PRI
                        tg.alert_tone or "0",            # ALT
                        tg.alert_level or "0",           # ALTL
                        "0",                             # RECORD
                        "0",                             # AUDIO_TYPE
                        "NONE",                          # NUMBER_TAG
                        "OFF",                           # ALT_COLOR
                        "0",                             # ALT_PATTERN
                        "0",                             # VOL_OFFSET
                    )
                    self.log_line.emit(f"    TGID {tg.tgid}  {tg_name}  [TIN idx={tgid_idx} resp={tin_resp!r}]")
                    tg_count += 1
                except ProtocolError as e:
                    self.log_line.emit(f"    Warning: TIN error: {e}")

        return tg_count


class UploadDialog(QDialog):
    """Dialog for uploading the channel list to the scanner."""

    def __init__(
        self,
        proto: ScannerProtocol,
        config: ScannerConfig,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._proto = proto
        self._config = config
        self._worker: _UploadWorker | None = None

        self.setWindowTitle("Upload to Scanner")
        self.setMinimumSize(560, 500)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # System selection
        sys_group = QGroupBox("Select Systems to Upload")
        sys_layout = QVBoxLayout(sys_group)
        self._sys_list = QListWidget()
        has_unsupported_trunked = False
        for i, sys in enumerate(self._config.systems):
            tg_count = sum(
                len(g.channels) for g in sys.groups if not g.is_site
            ) if sys.is_trunked else 0
            ch_count_label = (
                f"{tg_count} talk groups, {len(sys.trunk_frequencies)} trunk freqs"
                if sys.is_motorola else
                f"{sum(len(g.channels) for g in sys.groups)} channels"
            )
            label = f"[{sys.type_name}] {sys.name or f'System {i+1}'}  ({ch_count_label})"
            if sys.is_trunked and not sys.is_motorola:
                label += "  ⚠ not yet supported — will be skipped"
                has_unsupported_trunked = True
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, i)
            self._sys_list.addItem(item)
        self._sys_list.setMaximumHeight(140)
        sys_layout.addWidget(self._sys_list)

        if has_unsupported_trunked:
            from PyQt6.QtWidgets import QLabel as _QLabel
            warn = _QLabel(
                "⚠  EDACS, P25, and LTR systems will be skipped — only Motorola "
                "trunked systems are currently supported for upload."
            )
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #b05000; font-size: 11px;")
            sys_layout.addWidget(warn)

        layout.addWidget(sys_group)

        # Clear option
        self._clear_checkbox = QCheckBox(
            "Delete all existing systems before uploading  "
            "(removes all programmed systems, groups, and channels)"
        )
        self._clear_checkbox.setChecked(False)
        layout.addWidget(self._clear_checkbox)

        # Status
        self._status_label = QLabel("Ready to upload.")
        layout.addWidget(self._status_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        layout.addWidget(self._progress)

        # Log
        log_group = QGroupBox("Upload Log")
        log_layout = QVBoxLayout(log_group)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFontFamily("Courier")
        log_layout.addWidget(self._log)
        layout.addWidget(log_group)

        # Buttons
        btn_row = QHBoxLayout()
        self._upload_btn = QPushButton("Start Upload")
        self._upload_btn.clicked.connect(self._start_upload)
        self._abort_btn = QPushButton("Abort")
        self._abort_btn.setEnabled(False)
        self._abort_btn.clicked.connect(self._abort_upload)
        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._upload_btn)
        btn_row.addWidget(self._abort_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

    def _selected_systems(self) -> list[int]:
        result = []
        for i in range(self._sys_list.count()):
            item = self._sys_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(item.data(Qt.ItemDataRole.UserRole))
        return result

    def _start_upload(self) -> None:
        selected = self._selected_systems()
        if not selected:
            self._log.append("No systems selected.")
            return
        self._upload_btn.setEnabled(False)
        self._abort_btn.setEnabled(True)
        self._close_btn.setEnabled(False)
        self._log.clear()

        clear_first = self._clear_checkbox.isChecked()
        worker = _UploadWorker(
            self._proto, self._config, selected,
            clear_first=clear_first, parent=self,
        )
        worker.progress.connect(self._progress.setValue)
        worker.log_line.connect(self._log.append)
        worker.status.connect(self._status_label.setText)
        worker.finished_ok.connect(self._on_done)
        worker.finished_err.connect(self._on_error)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _abort_upload(self) -> None:
        if self._worker:
            self._worker.abort()
        self._status_label.setText("Aborting…")
        self._abort_btn.setEnabled(False)

    def _on_done(self, sys_count: int, ch_count: int) -> None:
        self._status_label.setText(
            f"Done. Uploaded {sys_count} system(s), {ch_count} channel(s)."
        )
        self._upload_btn.setEnabled(True)
        self._abort_btn.setEnabled(False)
        self._close_btn.setEnabled(True)
        self._log.append(f"\nUpload complete: {sys_count} systems, {ch_count} channels.")

    def _on_error(self, msg: str) -> None:
        self._status_label.setText("Upload failed.")
        self._log.append(f"\nERROR: {msg}")
        self._upload_btn.setEnabled(True)
        self._abort_btn.setEnabled(False)
        self._close_btn.setEnabled(True)

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.abort()
            self._worker.wait(3000)
        super().closeEvent(event)
