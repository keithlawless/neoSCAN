"""
Download dialog — reads the channel list from the scanner into a new ScannerConfig.
"""
from __future__ import annotations

import logging
import time
import uuid

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from app.data.models import (
    ScannerConfig, System, Group, Channel, TalkGroup, TrunkFrequency,
    SYS_TYPE_CONVENTIONAL, SYS_TYPE_P25_EDACS,
)
from app.serial.protocol import ScannerProtocol, ProtocolError
from app.serial.scanner_model import sin_type_to_internal, mod_mode_to_string

log = logging.getLogger(__name__)


def _para(payload: str, idx: int) -> str:
    """Extract the 0-based idx-th field from a comma-delimited payload."""
    parts = payload.split(",")
    if idx < len(parts):
        return parts[idx].strip()
    return ""


class _DownloadWorker(QThread):
    """Background thread for scanner download."""

    progress = pyqtSignal(int)
    log_line = pyqtSignal(str)
    status = pyqtSignal(str)
    finished_ok = pyqtSignal(object)   # ScannerConfig
    finished_err = pyqtSignal(str)

    def __init__(self, proto: ScannerProtocol, scanner_model: str = "", parent=None) -> None:
        super().__init__(parent)
        self._proto = proto
        self._scanner_model = scanner_model
        self._abort = False

    def abort(self) -> None:
        self._abort = True

    def run(self) -> None:
        try:
            config = self._do_download()
            self.finished_ok.emit(config)
        except Exception as exc:
            log.exception("Download failed")
            self.finished_err.emit(str(exc))

    def _do_download(self) -> ScannerConfig:
        proto = self._proto
        config = ScannerConfig()

        # Check whether the scanner is in normal scan/monitor mode before we
        # interrupt it.  STS succeeds in normal operation; it fails (or returns
        # ERR) inside program mode, so a successful response here confirms the
        # scanner was scanning/monitoring and should be restored afterward.
        resume_scan = False
        try:
            proto.get_status()
            resume_scan = True
            self.log_line.emit("Scanner is in scan mode — will resume after download.")
        except ProtocolError:
            pass

        self.log_line.emit("Entering program mode…")
        proto.enter_program_mode()

        # SIH = System Index Head — returns the first system's memory index,
        # or -1 if no systems are programmed.  (FreeSCAN frmCommsDownload.vb:2344)
        self.log_line.emit("Querying system list…")
        try:
            sih = proto.send_command("SIH")
        except ProtocolError as e:
            proto.exit_program_mode()
            raise RuntimeError(f"SIH failed: {e}") from e

        try:
            sys_index = int(sih)
        except ValueError:
            sys_index = -1

        if sys_index == -1:
            proto.exit_program_mode()
            self.log_line.emit("Scanner reports no systems programmed.")
            return config

        self.log_line.emit(f"First system index: {sys_index}")
        sys_count = 0
        ch_count = 0

        # The linked list ends when the next-system field == -1.
        # next-system field is at position 13 (1-indexed) = index 12 (0-indexed)
        # for BCT15-X / BCD996XT.  (FreeSCAN: intPos=13, ParaParse 1-indexed)
        while sys_index != -1 and not self._abort:
            self.status.emit(f"Downloading system {sys_count + 1}…")
            try:
                sin = proto.send_command(f"SIN,{sys_index}")
            except ProtocolError as e:
                self.log_line.emit(f"  SIN error at index {sys_index}: {e}")
                break

            if not sin or sin == "ERR":
                break

            sys_type_str = _para(sin, 0)
            sys_name = _para(sin, 1)
            sys_qk = _para(sin, 2)
            sys_hold = _para(sin, 3)
            sys_lockout = _para(sin, 4)
            sys_delay = _para(sin, 5)
            sys_data_skip = _para(sin, 6)
            # field 13 (0-indexed) = first group index for conventional
            first_grp_index_field = _para(sin, 13)

            self.log_line.emit(f"\n[System] {sys_name} ({sys_type_str})")

            sys_obj = System()
            sys_obj.name = sys_name
            sys_obj.system_type = sin_type_to_internal(sys_type_str)
            sys_obj.quick_key = sys_qk or "."
            sys_obj.hold_time = sys_hold
            sys_obj.lockout = sys_lockout == "1"
            sys_obj.delay_time = sys_delay
            sys_obj.data_skip = sys_data_skip == "1"
            sys_obj.group_id = uuid.uuid4().hex[:16].upper()
            sys_obj.apco_mode = _para(sin, 9) or "AUTO"
            sys_obj.apco_threshold = _para(sin, 10) or "8"
            sys_obj.record_mode = _para(sin, 17) or "0"

            if sys_obj.is_conventional:
                try:
                    first_grp_index = int(first_grp_index_field)
                except ValueError:
                    first_grp_index = 0

                grp_index = first_grp_index
                while grp_index not in (-1, 0) and not self._abort:
                    try:
                        gin = proto.send_command(f"GIN,{grp_index}")
                    except ProtocolError as e:
                        self.log_line.emit(f"  GIN error: {e}")
                        break
                    if not gin or gin == "ERR":
                        break

                    grp_name = _para(gin, 1)
                    grp_qk = _para(gin, 2)
                    grp_lockout = _para(gin, 3)
                    first_chan_index_str = _para(gin, 7)
                    next_grp_index_str = _para(gin, 5)

                    self.log_line.emit(f"  [Group] {grp_name}")
                    grp_obj = Group()
                    grp_obj.name = grp_name
                    grp_obj.quick_key = grp_qk or "."
                    grp_obj.lockout = grp_lockout == "1"
                    grp_obj.group_id = uuid.uuid4().hex[:16].upper()

                    try:
                        chan_index = int(first_chan_index_str)
                    except ValueError:
                        chan_index = 0

                    while chan_index not in (-1, 0) and not self._abort:
                        try:
                            cin = proto.send_command(f"CIN,{chan_index}")
                        except ProtocolError as e:
                            self.log_line.emit(f"    CIN error: {e}")
                            break
                        if not cin or cin == "ERR":
                            break

                        ch_name = _para(cin, 0)
                        ch_freq_raw = _para(cin, 1)
                        ch_mod_str = _para(cin, 2)
                        ch_tone = _para(cin, 3)
                        ch_tone_lock = _para(cin, 4)
                        ch_lockout = _para(cin, 5)
                        ch_priority = _para(cin, 6)
                        ch_att = _para(cin, 7)
                        ch_alert = _para(cin, 8)
                        ch_alert_lvl = _para(cin, 9)
                        next_chan_index_str = _para(cin, 11)   # FWD_INDEX
                        ch_record = _para(cin, 14)             # RECORD (after 4 index fields)

                        try:
                            freq_mhz = float(ch_freq_raw) / 10000.0
                            freq_str = f"{freq_mhz:.4f}"
                        except ValueError:
                            freq_str = ch_freq_raw

                        ch_obj = Channel()
                        ch_obj.name = ch_name
                        ch_obj.frequency = freq_str
                        ch_obj.modulation = ch_mod_str or "AUTO"
                        ch_obj.tone = ch_tone
                        ch_obj.tone_lockout = ch_tone_lock == "1"
                        ch_obj.lockout = ch_lockout == "1"
                        ch_obj.priority = ch_priority == "1"
                        ch_obj.attenuator = ch_att == "1"
                        ch_obj.alert_tone = ch_alert
                        ch_obj.alert_level = ch_alert_lvl
                        ch_obj.output = "ON" if ch_record == "1" else "OFF"
                        ch_obj.group_id = grp_obj.group_id
                        grp_obj.channels.append(ch_obj)

                        self.log_line.emit(f"    {ch_name}  {freq_str} MHz")
                        ch_count += 1

                        try:
                            chan_index = int(next_chan_index_str)
                        except ValueError:
                            chan_index = -1

                    sys_obj.groups.append(grp_obj)
                    try:
                        grp_index = int(next_grp_index_str)
                    except ValueError:
                        grp_index = -1

            elif sys_obj.is_motorola:
                ch_count += self._download_motorola_system(
                    proto, sys_obj, sys_index, first_grp_index_field, config
                )

            elif sys_obj.is_p25:
                ch_count += self._download_p25_system(
                    proto, sys_obj, sys_index, first_grp_index_field, config
                )

            config.systems.append(sys_obj)
            sys_count += 1
            self.progress.emit(min(90, sys_count * 10))

            # Advance to next system — field index 12 (0-based) = position 13 (1-based)
            next_sys_str = _para(sin, 12)
            try:
                sys_index = int(next_sys_str)
            except ValueError:
                sys_index = -1

        try:
            proto.exit_program_mode()
            self.log_line.emit("\nExited program mode.")
        except ProtocolError as e:
            self.log_line.emit(f"Warning: EPG error: {e}")

        if resume_scan:
            time.sleep(1.5)
            try:
                proto.send_key("S")
                self.log_line.emit("Scan mode restored.")
            except ProtocolError as e:
                self.log_line.emit(f"Warning: could not resume scan: {e}")

        self.progress.emit(100)
        self.log_line.emit(
            f"\nDownload complete: {sys_count} system(s), {ch_count} channel(s)."
        )
        return config

    def _download_motorola_system(
        self,
        proto: ScannerProtocol,
        sys_obj: System,
        sys_index: int,
        first_site_field: str,
        config: "ScannerConfig",
    ) -> int:
        """
        Download sites/trunk-freqs and TGID groups/talk-groups for a Motorola system.
        Populates sys_obj.groups and sys_obj.trunk_frequencies in place.
        Also appends trunk freqs to config.trunk_frequencies for .996 file save.
        Returns the number of talk groups downloaded.
        """
        tg_count = 0

        # --- Trunking parameters (TRN) ---
        try:
            trn = proto.get_trunking_params(sys_index)
        except ProtocolError as e:
            self.log_line.emit(f"  TRN error: {e}")
            return 0

        self.log_line.emit(f"  TRN raw ({len(trn)} fields): {','.join(trn[:25])}")
        self.log_line.emit(f"  SIN field[13] (first site/group head): {first_site_field}")
        self.log_line.emit(f"  TRN[20] (TGID_GRP_HEAD candidate): {trn[20] if len(trn) > 20 else '<missing>'}")

        # TRN GET response field indices (0-based, after command prefix stripped):
        # [0]=ID_SEARCH [1]=S_BIT [2]=END_CODE [3]=AFS [4-5]=RSV [6]=EMG [7]=EMGL
        # [8]=FMAP [9]=CTM_FMAP [10-18]=band plan (base/step/offset × 3 groups) [19]=DIG_END_CODE
        # [20]=TGID_GRP_HEAD [21]=TGID_GRP_TAIL [22]=ID_LOUT_GRP_HEAD [23]=ID_LOUT_GRP_TAIL
        # [24..28]=MOT_ID/EMG_COLOR/EMG_PATTERN/P25NAC/PRI_ID_SCAN (approx; not all verified)
        # BCT15X has band plan at [10-18], pushing TGID_GRP_HEAD to [20].
        # Confirmed: FreeSCAN ParaParse(TRN,21) = 1-indexed pos 21 = 0-indexed [20].
        def _f(fields: list[str], i: int) -> str:
            return fields[i].strip() if i < len(fields) else ""

        sys_obj.id_search = _f(trn, 0)
        sys_obj.ignore_status_bit = _f(trn, 1) == "1"
        sys_obj.end_code = _f(trn, 2) == "1"
        sys_obj.fleet_map = _f(trn, 8) or "16"
        sys_obj.custom_fleet_map = _f(trn, 9)
        sys_obj.mot_id = _f(trn, 22)
        sys_obj.p25_nac = _f(trn, 25) or "SRCH"
        sys_obj.pri_id_scan = _f(trn, 26)

        # --- Sites → trunk frequencies ---
        try:
            site_idx = int(first_site_field)
        except (ValueError, TypeError):
            site_idx = -1

        while site_idx not in (-1, 0) and not self._abort:
            try:
                sif = proto.get_site_info(site_idx)
            except ProtocolError as e:
                self.log_line.emit(f"  SIF error at {site_idx}: {e}")
                break

            site_name = _f(sif, 1)
            site_qk = _f(sif, 2)
            site_lockout = _f(sif, 4)
            # SIF GET response (0-based): [0]=RSV [1]=NAME [2]=QK [3]=HLD [4]=LOUT
            # [5]=MOD [6]=ATT [7]=C-CH [8-9]=RSV [10]=REV_INDEX [11]=FWD_INDEX
            # [12]=SYS_INDEX [13]=CHN_HEAD [14]=CHN_TAIL ...
            try:
                next_site_idx = int(_f(sif, 11))   # FWD_INDEX = next site
            except ValueError:
                next_site_idx = -1
            try:
                tfq_idx = int(_f(sif, 13))          # CHN_HEAD = first trunk freq
            except ValueError:
                tfq_idx = -1

            self.log_line.emit(f"  [Site] {site_name}")

            site_grp = Group()
            site_grp.name = site_name
            site_grp.quick_key = site_qk or "."
            site_grp.lockout = site_lockout == "1"
            site_grp.group_type = "3"   # site
            site_grp.group_id = uuid.uuid4().hex[:16].upper()

            while tfq_idx not in (-1, 0) and not self._abort:
                try:
                    tfq = proto.get_trunk_freq(tfq_idx)
                except ProtocolError as e:
                    self.log_line.emit(f"    TFQ error at {tfq_idx}: {e}")
                    break

                # TFQ fields (0-based): [0]=FREQ, [1]=LCN, [2]=LOUT,
                #   [3]=REV_INDEX, [4]=FWD_INDEX
                self.log_line.emit(f"    TFQ raw ({len(tfq)} fields): {','.join(tfq[:8])}")
                freq_raw = _f(tfq, 0)
                lcn = _f(tfq, 1)
                tf_lockout = _f(tfq, 2)
                try:
                    next_tfq_idx = int(_f(tfq, 4))
                except ValueError:
                    next_tfq_idx = -1

                try:
                    freq_mhz = float(freq_raw) / 10000.0
                    freq_str = f"{freq_mhz:.4f}"
                except ValueError:
                    freq_str = freq_raw

                tf = TrunkFrequency()
                tf.frequency = freq_str
                tf.lcn = lcn
                tf.lockout = tf_lockout == "1"
                # Use sys_obj.group_id so the .996 TrunkSection can link back to system
                tf.group_id = sys_obj.group_id
                sys_obj.trunk_frequencies.append(tf)
                config.trunk_frequencies.append(tf)

                self.log_line.emit(f"    TF {freq_str} MHz  LCN {lcn}")
                tfq_idx = next_tfq_idx

            sys_obj.groups.append(site_grp)
            site_idx = next_site_idx

        # --- TGID groups → talk groups ---
        # TRN TGID_GRP_HEAD: field[17] on BCD996P2, field[20] on BCT15X/BCD996XT
        tgid_head_field = 17 if self._scanner_model.upper() == "BCD996P2" else 20
        try:
            tgid_grp_idx = int(_f(trn, tgid_head_field))
        except ValueError:
            tgid_grp_idx = -1

        while tgid_grp_idx not in (-1, 0) and not self._abort:
            try:
                gin = proto.get_group_info(tgid_grp_idx)
            except ProtocolError as e:
                self.log_line.emit(f"  GIN error at {tgid_grp_idx}: {e}")
                break

            grp_name = _f(gin, 1)
            grp_qk = _f(gin, 2)
            grp_lockout = _f(gin, 3)
            try:
                next_grp_idx = int(_f(gin, 5))
            except ValueError:
                next_grp_idx = -1
            try:
                tgid_idx = int(_f(gin, 7))
            except ValueError:
                tgid_idx = -1

            self.log_line.emit(f"  [TGID Group] {grp_name}")

            tg_grp = Group()
            tg_grp.name = grp_name
            tg_grp.quick_key = grp_qk or "."
            tg_grp.lockout = grp_lockout == "1"
            tg_grp.group_type = "2"  # TGID group
            tg_grp.group_id = uuid.uuid4().hex[:16].upper()

            while tgid_idx not in (-1, 0) and not self._abort:
                try:
                    tin = proto.get_tgid(tgid_idx)
                except ProtocolError as e:
                    self.log_line.emit(f"    TIN error at {tgid_idx}: {e}")
                    break

                # TIN GET response (0-based): [0]=NAME [1]=TGID [2]=LOUT [3]=PRI
                #   [4]=ALT [5]=ALTL [6]=REV_INDEX [7]=FWD_INDEX [8]=SYS_INDEX
                #   [9]=GRP_INDEX [10]=RECORD [11]=AUDIO_TYPE [12]=NUMBER_TAG ...
                self.log_line.emit(f"    TIN raw ({len(tin)} fields): {','.join(tin[:12])}")
                tg_name = _f(tin, 0)
                tgid = _f(tin, 1)
                tg_lockout = _f(tin, 2)
                tg_priority = _f(tin, 3)
                tg_alert = _f(tin, 4)
                tg_alert_lvl = _f(tin, 5)
                tg_audio = _f(tin, 11)
                try:
                    next_tgid_idx = int(_f(tin, 7))   # FWD_INDEX
                except ValueError:
                    next_tgid_idx = -1

                tg = TalkGroup()
                tg.name = tg_name
                tg.tgid = tgid
                tg.lockout = tg_lockout == "1"
                tg.priority = tg_priority == "1"
                tg.alert_tone = tg_alert
                tg.alert_level = tg_alert_lvl
                tg.audio_type = tg_audio
                tg.group_id = tg_grp.group_id
                tg_grp.channels.append(tg)

                self.log_line.emit(f"    TGID {tgid}  {tg_name}")
                tg_count += 1
                tgid_idx = next_tgid_idx

            sys_obj.groups.append(tg_grp)
            tgid_grp_idx = next_grp_idx

        return tg_count

    def _download_p25_system(
        self,
        proto: ScannerProtocol,
        sys_obj: System,
        sys_index: int,
        first_site_field: str,
        config: "ScannerConfig",
    ) -> int:
        """
        Download sites/trunk-freqs and TGID groups/talk-groups for a P25 system.
        P25S has sites like Motorola. P25F (one-frequency) has no sites.
        Returns number of talk groups downloaded.
        """
        tg_count = 0

        try:
            trn = proto.get_trunking_params(sys_index)
        except ProtocolError as e:
            self.log_line.emit(f"  TRN error: {e}")
            return 0

        self.log_line.emit(f"  TRN raw ({len(trn)} fields): {','.join(trn[:20])}")

        def _f(fields: list[str], i: int) -> str:
            return fields[i].strip() if i < len(fields) else ""

        # P25 TRN GET response (0-based, INDEX excluded):
        # [0]=ID_SEARCH [1]=S_BIT [2]=END_CODE [3]=AFS [4-5]=RSV
        # [6]=EMG [7]=EMGL [8]=FMAP [9]=CTM_FMAP [10-18]=RSV (9 fields)
        # [19]=TGID_GRP_HEAD (P25 has no DIG_END_CODE at [19] unlike BCT15X Motorola)
        # [20]=TGID_GRP_TAIL [21-22]=ID_LOUT heads [23]=MOT_ID
        # [24]=EMG_COLOR [25]=EMG_PATTERN [26]=RSV [27]=P25NAC [28]=PRI_ID_SCAN
        sys_obj.id_search = _f(trn, 0)
        sys_obj.p25_nac = _f(trn, 27) or "SRCH"

        # --- Sites → trunk frequencies (P25S only; P25F has no sites) ---
        is_p25f = sys_obj.is_p25f
        if not is_p25f:
            try:
                site_idx = int(first_site_field)
            except (ValueError, TypeError):
                site_idx = -1

            while site_idx not in (-1, 0) and not self._abort:
                try:
                    sif = proto.get_site_info(site_idx)
                except ProtocolError as e:
                    self.log_line.emit(f"  SIF error at {site_idx}: {e}")
                    break

                site_name = _f(sif, 1)
                site_qk = _f(sif, 2)
                site_lockout = _f(sif, 4)
                try:
                    next_site_idx = int(_f(sif, 11))
                except ValueError:
                    next_site_idx = -1
                try:
                    tfq_idx = int(_f(sif, 13))
                except ValueError:
                    tfq_idx = -1

                self.log_line.emit(f"  [Site] {site_name}")

                site_grp = Group()
                site_grp.name = site_name
                site_grp.quick_key = site_qk or "."
                site_grp.lockout = site_lockout == "1"
                site_grp.group_type = "3"
                site_grp.group_id = uuid.uuid4().hex[:16].upper()

                while tfq_idx not in (-1, 0) and not self._abort:
                    try:
                        tfq = proto.get_trunk_freq(tfq_idx)
                    except ProtocolError as e:
                        self.log_line.emit(f"    TFQ error at {tfq_idx}: {e}")
                        break

                    freq_raw = _f(tfq, 0)
                    lcn = _f(tfq, 1)
                    tf_lockout = _f(tfq, 2)
                    try:
                        next_tfq_idx = int(_f(tfq, 4))
                    except ValueError:
                        next_tfq_idx = -1

                    try:
                        freq_mhz = float(freq_raw) / 10000.0
                        freq_str = f"{freq_mhz:.4f}"
                    except ValueError:
                        freq_str = freq_raw

                    tf = TrunkFrequency()
                    tf.frequency = freq_str
                    tf.lcn = lcn
                    tf.lockout = tf_lockout == "1"
                    tf.group_id = sys_obj.group_id
                    sys_obj.trunk_frequencies.append(tf)
                    config.trunk_frequencies.append(tf)

                    self.log_line.emit(f"    TF {freq_str} MHz  LCN {lcn}")
                    tfq_idx = next_tfq_idx

                sys_obj.groups.append(site_grp)
                site_idx = next_site_idx

        # --- TGID groups → talk groups ---
        # P25 TGID_GRP_HEAD: field[19] (one earlier than BCT15X Motorola's [20])
        try:
            tgid_grp_idx = int(_f(trn, 19))
        except ValueError:
            tgid_grp_idx = -1

        while tgid_grp_idx not in (-1, 0) and not self._abort:
            try:
                gin = proto.get_group_info(tgid_grp_idx)
            except ProtocolError as e:
                self.log_line.emit(f"  GIN error at {tgid_grp_idx}: {e}")
                break

            grp_name = _f(gin, 1)
            grp_qk = _f(gin, 2)
            grp_lockout = _f(gin, 3)
            try:
                next_grp_idx = int(_f(gin, 5))
            except ValueError:
                next_grp_idx = -1
            try:
                tgid_idx = int(_f(gin, 7))
            except ValueError:
                tgid_idx = -1

            self.log_line.emit(f"  [TGID Group] {grp_name}")

            tg_grp = Group()
            tg_grp.name = grp_name
            tg_grp.quick_key = grp_qk or "."
            tg_grp.lockout = grp_lockout == "1"
            tg_grp.group_type = "2"
            tg_grp.group_id = uuid.uuid4().hex[:16].upper()

            while tgid_idx not in (-1, 0) and not self._abort:
                try:
                    tin = proto.get_tgid(tgid_idx)
                except ProtocolError as e:
                    self.log_line.emit(f"    TIN error at {tgid_idx}: {e}")
                    break

                tg_name = _f(tin, 0)
                tgid = _f(tin, 1)
                tg_lockout = _f(tin, 2)
                tg_priority = _f(tin, 3)
                tg_alert = _f(tin, 4)
                tg_alert_lvl = _f(tin, 5)
                tg_audio = _f(tin, 11)
                try:
                    next_tgid_idx = int(_f(tin, 7))
                except ValueError:
                    next_tgid_idx = -1

                tg = TalkGroup()
                tg.name = tg_name
                tg.tgid = tgid
                tg.lockout = tg_lockout == "1"
                tg.priority = tg_priority == "1"
                tg.alert_tone = tg_alert
                tg.alert_level = tg_alert_lvl
                tg.audio_type = tg_audio
                tg.group_id = tg_grp.group_id
                tg_grp.channels.append(tg)

                self.log_line.emit(f"    TGID {tgid}  {tg_name}")
                tg_count += 1
                tgid_idx = next_tgid_idx

            sys_obj.groups.append(tg_grp)
            tgid_grp_idx = next_grp_idx

        return tg_count


class DownloadDialog(QDialog):
    """Dialog for downloading channel data from the scanner."""

    downloaded_config: ScannerConfig | None = None

    def __init__(self, proto: ScannerProtocol, scanner_model: str = "", parent=None) -> None:
        super().__init__(parent)
        self._proto = proto
        self._scanner_model = scanner_model
        self._worker: _DownloadWorker | None = None
        self.downloaded_config = None

        self.setWindowTitle("Download from Scanner")
        self.setMinimumSize(520, 420)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        info = QLabel(
            "This will download all systems, groups, and channels from the scanner.\n"
            "Any unsaved changes in the editor will not be overwritten until you "
            "accept the downloaded configuration."
        )
        info.setWordWrap(True)
        info.setStyleSheet("font-size: 11px; color: #444;")
        layout.addWidget(info)

        self._status_label = QLabel("Ready.")
        layout.addWidget(self._status_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        layout.addWidget(self._progress)

        log_group = QGroupBox("Download Log")
        log_layout = QVBoxLayout(log_group)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFontFamily("Courier")
        log_layout.addWidget(self._log)
        layout.addWidget(log_group)

        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("Start Download")
        self._start_btn.clicked.connect(self._start_download)
        self._abort_btn = QPushButton("Abort")
        self._abort_btn.setEnabled(False)
        self._abort_btn.clicked.connect(self._abort)
        self._close_btn = QPushButton("Cancel")
        self._close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._start_btn)
        btn_row.addWidget(self._abort_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

    def _start_download(self) -> None:
        self._start_btn.setEnabled(False)
        self._abort_btn.setEnabled(True)
        self._close_btn.setEnabled(False)
        self._log.clear()

        worker = _DownloadWorker(self._proto, scanner_model=self._scanner_model, parent=self)
        worker.progress.connect(self._progress.setValue)
        worker.log_line.connect(self._log.append)
        worker.status.connect(self._status_label.setText)
        worker.finished_ok.connect(self._on_done)
        worker.finished_err.connect(self._on_error)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _abort(self) -> None:
        if self._worker:
            self._worker.abort()
        self._abort_btn.setEnabled(False)

    def _on_done(self, config: ScannerConfig) -> None:
        self.downloaded_config = config
        self._status_label.setText(
            f"Downloaded {len(config.systems)} system(s). "
            "Click 'Load into Editor' to use this data."
        )
        self._start_btn.setEnabled(True)
        self._abort_btn.setEnabled(False)
        self._close_btn.setText("Load into Editor")
        self._close_btn.setEnabled(True)
        self._close_btn.clicked.disconnect()
        self._close_btn.clicked.connect(self.accept)

    def _on_error(self, msg: str) -> None:
        self._status_label.setText("Download failed.")
        self._log.append(f"\nERROR: {msg}")
        self._start_btn.setEnabled(True)
        self._abort_btn.setEnabled(False)
        self._close_btn.setEnabled(True)

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.abort()
            self._worker.wait(3000)
        super().closeEvent(event)
