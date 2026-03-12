"""
Channel editor form — shows detail fields for a selected channel, group, or system.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QCheckBox,
    QGroupBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QFrame,
    QTextEdit,
    QSpacerItem,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)

from app.data.models import (
    ScannerConfig, System, Group, Channel, TalkGroup, TrunkFrequency,
    SYSTEM_TYPE_NAMES, SYS_TYPE_CONVENTIONAL, SYS_TYPE_MOTOROLA,
    SYS_TYPE_P25, SYS_TYPE_P25_EDACS,
)
from app.data.band_plan import is_frequency_valid
import uuid


# ---------------------------------------------------------------------------
# Help text for each field
# ---------------------------------------------------------------------------
HELP = {
    "freq": (
        "Frequency (MHz)\n\n"
        "Enter the frequency in MHz, e.g. 154.235 or 471.4250.\n\n"
        "Supported ranges vary by model. BCT15X/BCD996XT: 25–512, "
        "764–776, 794–824, 849–869, 894–956, 1240–1300 MHz. "
        "SDS200/SDS100: 25–512, 758–824, 849–869, 895–960, 1240–1300 MHz.\n"
        "An inline warning appears if the frequency is outside the connected "
        "scanner's range (SDS200 ranges are used when no scanner is connected).\n"
        "Step sizes are auto-selected based on band."
    ),
    "name": (
        "Channel Name\n\n"
        "Up to 16 characters. Displayed on the scanner's screen when "
        "this channel is active. Keep it short and descriptive."
    ),
    "modulation": (
        "Modulation Mode\n\n"
        "How the scanner decodes the signal:\n"
        "• AUTO — scanner picks the best mode for the frequency band\n"
        "• FM — standard FM (most VHF/UHF public safety)\n"
        "• NFM — narrow FM (newer systems)\n"
        "• AM — amplitude modulation (aviation)\n"
        "• WFM — wide FM (broadcast)\n"
        "• FMB — FM broadcast"
    ),
    "tone": (
        "CTCSS / DCS Tone\n\n"
        "Continuous Tone-Coded Squelch System (CTCSS) or Digital Coded "
        "Squelch (DCS). Set this if the channel uses a subaudible tone "
        "to control squelch. Leave as NONE to scan all traffic.\n\n"
        "Common values: 100.0, 127.3, 136.5 Hz (CTCSS) or Dxxx (DCS)."
    ),
    "delay": (
        "Scan Delay\n\n"
        "How long (in seconds) the scanner stays on a channel after the "
        "transmission ends before resuming scanning.\n\n"
        "Typical values: 1–5 seconds. Use a higher delay on busy channels."
    ),
    "lockout": (
        "Lockout\n\n"
        "When checked, the scanner skips this channel during scanning. "
        "Useful to temporarily silence a noisy or unimportant channel "
        "without deleting it."
    ),
    "priority": (
        "Priority\n\n"
        "When checked, the scanner periodically checks this channel even "
        "while scanning other channels, ensuring you never miss activity "
        "on an important frequency."
    ),
    "attenuator": (
        "Attenuator\n\n"
        "Reduces the receiver gain by 20 dB. Use this if a nearby "
        "transmitter is overwhelming the scanner and causing interference."
    ),
    "tone_lockout": (
        "Tone Lockout\n\n"
        "When checked, the scanner ignores the CTCSS/DCS tone on this "
        "channel (opens squelch regardless of tone). Useful if you know "
        "a channel uses a tone but you want to hear all traffic."
    ),
    "number_tag": (
        "Number Tag\n\n"
        "An optional numeric label (NONE or 0–999) you can use to jump "
        "directly to this channel using the number keys and [.No] button."
    ),
    "volume_offset": (
        "Volume Offset\n\n"
        "Adjusts the audio volume for this channel relative to the global "
        "setting. Range: -3 to +3. Use positive values for quiet channels, "
        "negative for loud ones."
    ),
    "output": (
        "Record Output\n\n"
        "When checked, this channel's audio is routed to the scanner's "
        "REC (line-level) output jack on the back of the unit.\n\n"
        "Enable this on any channel you want captured by NeoSCAN's audio "
        "transcription feature or an external recorder. Channels with this "
        "unchecked will not produce audio on the record port."
    ),
    "sys_record_mode": (
        "Record Mode\n\n"
        "Controls which channels send audio to the scanner's RECORD OUT jack:\n"
        "• Off — no audio is sent to the RECORD OUT jack for this system\n"
        "• Marked — only channels with 'Record Output' enabled send audio\n"
        "• All — every channel in this system sends audio to RECORD OUT\n\n"
        "This system-level setting must be 'Marked' or 'All' for any channel-level "
        "recording to work. If this is set to 'Off', no audio reaches the jack "
        "regardless of channel settings."
    ),
    "sys_name": (
        "System Name\n\n"
        "Up to 16 characters displayed when this system is active. "
        "Choose something descriptive like 'Public Safety' or 'Airport'."
    ),
    "sys_type": (
        "System Type\n\n"
        "The type of radio system:\n"
        "• Conventional — fixed-frequency channels (most common)\n"
        "• Motorola, EDACS, LTR, P25 — trunked radio systems that "
        "dynamically assign frequencies to calls"
    ),
    "quick_key": (
        "Quick Key\n\n"
        "A number (0–99) that lets you quickly enable or disable this "
        "system/group by pressing the corresponding key while scanning. "
        "Use '.' for no quick key."
    ),
    "hold_time": (
        "Hold Time\n\n"
        "Minimum time (in tenths of a second) the scanner stays on this "
        "channel even if no signal is present. Prevents the scanner from "
        "skipping past brief transmissions."
    ),
    "grp_name": (
        "Group Name\n\n"
        "Up to 16 characters. Displayed on the scanner screen to identify "
        "which group of channels is being scanned."
    ),
    # Motorola trunked system fields
    "mot_id_search": (
        "ID Search Mode\n\n"
        "Controls how the scanner finds talk groups on this system:\n"
        "• ID Scan — only monitors talk groups you have programmed\n"
        "• ID Search — scans all talk group IDs it hears, even unrecognised ones\n\n"
        "Use ID Search to discover unknown talk groups on a new system."
    ),
    "mot_status_bit": (
        "Status Bit\n\n"
        "Motorola Type I systems encode a status bit in each transmission that "
        "distinguishes between different sub-fleets.\n\n"
        "• Ignore — treat all transmissions on a TGID the same regardless of status bit\n"
        "• Yes — treat different status-bit values as separate talk groups\n\n"
        "Leave as Ignore unless you know the system uses status bits."
    ),
    "mot_end_code": (
        "End Code\n\n"
        "How the scanner recognises the end of a transmission:\n"
        "• Ignore — scanner uses its own timing to detect end of transmission\n"
        "• Analog — honours the Motorola analog end-of-transmission code\n"
        "• Analog + Digital — honours both analog and digital end codes\n\n"
        "For most systems, Ignore works well."
    ),
    "mot_fleet_map": (
        "Fleet Map\n\n"
        "Motorola Type I systems divide their talk group space into blocks "
        "called fleets using a fleet map. This determines how TGID numbers "
        "are interpreted.\n\n"
        "Choose the preset that matches the system you are monitoring. "
        "Select 'Custom' only if you have the exact fleet map from a "
        "reliable source (e.g. RadioReference)."
    ),
    "mot_custom_fleet_map": (
        "Custom Fleet Map\n\n"
        "An 8-digit hex string defining the fleet block sizes for a "
        "Motorola Type I system. Each digit (0–F) encodes the size of one "
        "of the 8 fleet blocks.\n\n"
        "Only used when Fleet Map is set to 'Custom'. Incorrect values will "
        "produce wrong talk group IDs. Leave as 00000000 if unsure."
    ),
    "mot_id_display": (
        "ID Display Format\n\n"
        "Controls how talk group IDs are displayed on the scanner screen:\n"
        "• Decimal — shows TGID as a plain decimal number (e.g. 4096)\n"
        "• HEX — shows TGID in hexadecimal (e.g. 1000)\n\n"
        "Match this to how TGIDs are listed in your reference source."
    ),
    "p25_nac": (
        "P25 NAC (Network Access Code)\n\n"
        "Filters P25 traffic by Network Access Code (a 12-bit value, 0x000–0xFFF):\n"
        "• SRCH — search mode: receive all transmissions and display the NAC found\n"
        "• 0–4095 (decimal) — only open squelch for transmissions matching this NAC\n"
        "• FFFF — receive all NACs without filtering\n\n"
        "Use SRCH when setting up a new system; switch to the specific NAC once "
        "you have identified it from the display."
    ),
    "tgid": (
        "Talk Group ID (TGID)\n\n"
        "The numeric identifier for this talk group on the Motorola system. "
        "Enter the decimal value (e.g. 4096). You can find TGIDs in the "
        "RadioReference database for your area."
    ),
}


# ---------------------------------------------------------------------------
# Modulation options
# ---------------------------------------------------------------------------
MODULATION_OPTIONS = ["AUTO", "AM", "FM", "NFM", "WFM", "FMB"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_label(text: str) -> QLabel:
    lbl = QLabel(text)
    return lbl


def _help_label(field_key: str) -> QLabel:
    text = HELP.get(field_key, "")
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(
        "background: #f5f5dc; border: 1px solid #ccc; border-radius: 4px; "
        "padding: 6px; font-size: 11px; color: #333;"
    )
    lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    return lbl


# ---------------------------------------------------------------------------
# Duplicate frequency detection
# ---------------------------------------------------------------------------
def find_duplicate_channels(
    config: ScannerConfig, freq_str: str, exclude_channel: Channel
) -> list[tuple[str, str, str]]:
    """Return (sys_name, grp_name, ch_name) for every Channel sharing freq_str."""
    try:
        target = float(freq_str)
    except (ValueError, TypeError):
        return []
    results = []
    for sys in config.systems:
        for grp in sys.groups:
            for ch in grp.channels:
                if not isinstance(ch, Channel) or ch is exclude_channel:
                    continue
                try:
                    if float(ch.frequency) == target:
                        results.append((sys.name or "?", grp.name or "?", ch.name or "(unnamed)"))
                except (ValueError, TypeError):
                    pass
    return results


def check_frequency_in_band(freq_str: str, model: str) -> str | None:
    """Return a warning string if freq_str is outside the model's band plan,
    or None if the frequency is valid or unparseable (don't warn while typing)."""
    if not freq_str.strip():
        return None
    try:
        freq = float(freq_str)
    except (ValueError, TypeError):
        return None
    if is_frequency_valid(freq, model or "DEFAULT"):
        return None
    model_display = model if model else "SDS200 (default)"
    return f"{freq:.4f} MHz is outside the supported range for {model_display}."


# ---------------------------------------------------------------------------
# Channel editor form
# ---------------------------------------------------------------------------
class ChannelEditorPanel(QWidget):
    """
    Detail editor panel.  Shows a form for the currently selected
    System, Group, or Channel.  Changes are written back to the model
    immediately (no Apply button needed).
    """

    modified = pyqtSignal()           # emitted whenever a field changes
    structure_changed = pyqtSignal()  # emitted when items are added/removed

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._config: ScannerConfig | None = None
        self._scanner_model: str = ""
        self._context: tuple | None = None  # (type, s_idx, g_idx, c_idx)
        self._updating = False
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        self._content = QWidget()
        scroll.setWidget(self._content)

        self._main_layout = QVBoxLayout(self._content)
        self._main_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._title = QLabel("Select an item from the tree to edit it.")
        self._title.setStyleSheet("font-size: 14px; font-weight: bold; padding: 8px 4px 4px 4px;")
        self._main_layout.addWidget(self._title)

        self._form_container = QWidget()
        self._main_layout.addWidget(self._form_container)

    def set_config(self, config: ScannerConfig | None) -> None:
        self._config = config

    def set_scanner_model(self, model: str) -> None:
        """Update the scanner model used for frequency range validation."""
        self._scanner_model = model

    def show_channel(self, config: ScannerConfig, s_idx: int, g_idx: int, c_idx: int) -> None:
        self._config = config
        self._context = ("channel", s_idx, g_idx, c_idx)
        ch = config.systems[s_idx].groups[g_idx].channels[c_idx]
        if isinstance(ch, Channel):
            self._build_channel_form(ch, s_idx, g_idx, c_idx)
        else:
            self._build_talkgroup_form(ch, s_idx, g_idx, c_idx)

    def show_group(self, config: ScannerConfig, s_idx: int, g_idx: int) -> None:
        self._config = config
        self._context = ("group", s_idx, g_idx, None)
        sys_obj = config.systems[s_idx]
        grp = sys_obj.groups[g_idx]
        if (sys_obj.is_motorola or sys_obj.is_p25) and not grp.is_site:
            self._build_tgid_group_form(grp, s_idx, g_idx)
        else:
            self._build_group_form(grp, s_idx, g_idx)

    def show_system(self, config: ScannerConfig, s_idx: int) -> None:
        self._config = config
        self._context = ("system", s_idx, None, None)
        sys = config.systems[s_idx]
        if sys.is_motorola or sys.is_p25:
            self._build_trunked_system_form(sys, s_idx)
        else:
            self._build_system_form(sys, s_idx)

    def clear(self) -> None:
        self._config = None
        self._context = None
        self._clear_form()
        self._title.setText("Select an item from the tree to edit it.")

    # ------------------------------------------------------------------
    # Form builders
    # ------------------------------------------------------------------

    def _clear_form(self) -> None:
        old = self._form_container
        self._form_container = QWidget()
        self._main_layout.replaceWidget(old, self._form_container)
        old.deleteLater()

    def _build_channel_form(
        self, ch: Channel, s_idx: int, g_idx: int, c_idx: int
    ) -> None:
        self._clear_form()
        self._title.setText(f"Channel: {ch.name or '(unnamed)'}")
        layout = QVBoxLayout(self._form_container)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        group = QGroupBox("Channel Settings")
        form = QFormLayout(group)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        # Name
        e_name = QLineEdit(ch.name)
        e_name.setMaxLength(16)
        e_name.setPlaceholderText("Up to 16 characters")
        e_name.setToolTip(HELP["name"])
        e_name.textChanged.connect(lambda v: self._set_channel_field(s_idx, g_idx, c_idx, "name", v))
        form.addRow("Name:", e_name)
        form.addRow("", _help_label("name"))

        # Frequency
        e_freq = QLineEdit(ch.frequency)
        e_freq.setPlaceholderText("e.g. 154.2350")
        e_freq.setToolTip(HELP["freq"])
        _dup_warning = QLabel()
        _dup_warning.setStyleSheet("color: #cc6600; font-size: 11px;")
        _dup_warning.setWordWrap(True)
        _dup_warning.setVisible(False)

        _range_warning = QLabel()
        _range_warning.setStyleSheet("color: #cc6600; font-size: 11px;")
        _range_warning.setWordWrap(True)
        _range_warning.setVisible(False)

        def _update_dup_warning(freq_str: str) -> None:
            if self._config:
                dups = find_duplicate_channels(self._config, freq_str, ch)
                if dups:
                    lines = "; ".join(f"{s} / {g} / {c}" for s, g, c in dups[:3])
                    suffix = f" (+{len(dups) - 3} more)" if len(dups) > 3 else ""
                    _dup_warning.setText(f"Duplicate frequency: {lines}{suffix}")
                    _dup_warning.setVisible(True)
                else:
                    _dup_warning.setVisible(False)

        def _update_range_warning(freq_str: str) -> None:
            msg = check_frequency_in_band(freq_str, self._scanner_model)
            if msg:
                _range_warning.setText(msg)
                _range_warning.setVisible(True)
            else:
                _range_warning.setVisible(False)

        def _on_freq_changed(v: str) -> None:
            self._set_channel_field(s_idx, g_idx, c_idx, "frequency", v)
            _update_dup_warning(v)
            _update_range_warning(v)

        e_freq.textChanged.connect(_on_freq_changed)
        form.addRow("Frequency (MHz):", e_freq)
        form.addRow("", _dup_warning)
        form.addRow("", _range_warning)
        form.addRow("", _help_label("freq"))
        _update_dup_warning(ch.frequency)
        _update_range_warning(ch.frequency)

        # Modulation
        c_mod = QComboBox()
        c_mod.addItems(MODULATION_OPTIONS)
        if ch.modulation in MODULATION_OPTIONS:
            c_mod.setCurrentText(ch.modulation)
        c_mod.setToolTip(HELP["modulation"])
        c_mod.currentTextChanged.connect(
            lambda v: self._set_channel_field(s_idx, g_idx, c_idx, "modulation", v)
        )
        form.addRow("Modulation:", c_mod)
        form.addRow("", _help_label("modulation"))

        # Tone
        e_tone = QLineEdit(ch.tone)
        e_tone.setPlaceholderText("0 = NONE, or tone index")
        e_tone.setToolTip(HELP["tone"])
        e_tone.textChanged.connect(lambda v: self._set_channel_field(s_idx, g_idx, c_idx, "tone", v))
        form.addRow("CTCSS/DCS Tone:", e_tone)
        form.addRow("", _help_label("tone"))

        # Delay
        e_delay = QLineEdit(ch.delay)
        e_delay.setPlaceholderText("seconds (e.g. 2)")
        e_delay.setToolTip(HELP["delay"])
        e_delay.textChanged.connect(lambda v: self._set_channel_field(s_idx, g_idx, c_idx, "delay", v))
        form.addRow("Scan Delay:", e_delay)
        form.addRow("", _help_label("delay"))

        # Number tag
        e_tag = QLineEdit(ch.number_tag if ch.number_tag != "NONE" else "")
        e_tag.setPlaceholderText("NONE or 0-999")
        e_tag.setToolTip(HELP["number_tag"])
        e_tag.textChanged.connect(
            lambda v: self._set_channel_field(s_idx, g_idx, c_idx, "number_tag", v or "NONE")
        )
        form.addRow("Number Tag:", e_tag)

        # Volume offset
        e_vol = QLineEdit(ch.volume_offset)
        e_vol.setPlaceholderText("-3 to +3")
        e_vol.setToolTip(HELP["volume_offset"])
        e_vol.textChanged.connect(
            lambda v: self._set_channel_field(s_idx, g_idx, c_idx, "volume_offset", v)
        )
        form.addRow("Volume Offset:", e_vol)

        layout.addWidget(group)

        # Flags group
        flags_group = QGroupBox("Options")
        flags_layout = QVBoxLayout(flags_group)
        for label, attr, help_key in [
            ("Locked out (skip during scan)", "lockout", "lockout"),
            ("Priority channel", "priority", "priority"),
            ("Attenuator", "attenuator", "attenuator"),
            ("Tone lockout", "tone_lockout", "tone_lockout"),
        ]:
            cb = QCheckBox(label)
            cb.setChecked(getattr(ch, attr))
            cb.setToolTip(HELP[help_key])
            cb.toggled.connect(
                lambda v, a=attr: self._set_channel_field(s_idx, g_idx, c_idx, a, v)
            )
            flags_layout.addWidget(cb)
            flags_layout.addWidget(_help_label(help_key))

        # Record output — stored as "ON"/"OFF" string in the model
        cb_record = QCheckBox("Record Output (send audio to REC jack)")
        cb_record.setChecked(ch.output == "ON")
        cb_record.setToolTip(HELP["output"])
        cb_record.toggled.connect(
            lambda v: self._set_channel_field(s_idx, g_idx, c_idx, "output", "ON" if v else "OFF")
        )
        flags_layout.addWidget(cb_record)
        flags_layout.addWidget(_help_label("output"))

        layout.addWidget(flags_group)

    def _build_talkgroup_form(
        self, tg: TalkGroup, s_idx: int, g_idx: int, c_idx: int
    ) -> None:
        self._clear_form()
        self._title.setText(f"Talk Group: {tg.name or '(unnamed)'}")
        layout = QVBoxLayout(self._form_container)
        group = QGroupBox("Talk Group Settings")
        form = QFormLayout(group)

        e_name = QLineEdit(tg.name)
        e_name.setMaxLength(16)
        e_name.textChanged.connect(lambda v: self._set_channel_field(s_idx, g_idx, c_idx, "name", v))
        form.addRow("Name:", e_name)
        form.addRow("", _help_label("name"))

        e_tgid = QLineEdit(tg.tgid)
        e_tgid.setPlaceholderText("Talk group ID (decimal)")
        e_tgid.setToolTip(HELP["tgid"])
        e_tgid.textChanged.connect(lambda v: self._set_channel_field(s_idx, g_idx, c_idx, "tgid", v))
        form.addRow("TGID:", e_tgid)
        form.addRow("", _help_label("tgid"))

        cb_lockout = QCheckBox("Locked out")
        cb_lockout.setChecked(tg.lockout)
        cb_lockout.toggled.connect(lambda v: self._set_channel_field(s_idx, g_idx, c_idx, "lockout", v))
        form.addRow("", cb_lockout)

        layout.addWidget(group)

    def _build_group_form(self, grp: Group, s_idx: int, g_idx: int) -> None:
        self._clear_form()
        self._title.setText(f"Group: {grp.name or '(unnamed)'}")
        layout = QVBoxLayout(self._form_container)
        group = QGroupBox("Group Settings")
        form = QFormLayout(group)

        e_name = QLineEdit(grp.name)
        e_name.setMaxLength(16)
        e_name.setToolTip(HELP["grp_name"])
        e_name.textChanged.connect(lambda v: self._set_group_field(s_idx, g_idx, "name", v))
        e_name.textChanged.connect(lambda v: self._title.setText(f"Group: {v or '(unnamed)'}"))
        form.addRow("Group Name:", e_name)
        form.addRow("", _help_label("grp_name"))

        qk_row, _ = self._qk_row(
            grp.quick_key,
            lambda v: self._set_group_field(s_idx, g_idx, "quick_key", v or "."),
            self._used_group_qks,
        )
        form.addRow("Quick Key:", qk_row)
        form.addRow("", _help_label("quick_key"))

        cb_lockout = QCheckBox("Locked out (skip this group)")
        cb_lockout.setChecked(grp.lockout)
        cb_lockout.setToolTip(HELP["lockout"])
        cb_lockout.toggled.connect(lambda v: self._set_group_field(s_idx, g_idx, "lockout", v))
        form.addRow("", cb_lockout)

        layout.addWidget(group)

        info = QLabel(f"Group ID: {grp.group_id}\nChannels: {len(grp.channels)}")
        info.setStyleSheet("color: gray; font-size: 11px; padding: 4px;")
        layout.addWidget(info)

    def _build_trunked_system_form(self, sys: System, s_idx: int) -> None:
        """Form for a Motorola or P25 trunked system: parameters + trunk frequency table."""
        self._clear_form()
        type_label = sys.type_name
        self._title.setText(f"System: {sys.name or '(unnamed)'}  [{type_label}]")
        layout = QVBoxLayout(self._form_container)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ---- Trunking parameters ----
        params_box = QGroupBox("Trunking Parameters")
        form = QFormLayout(params_box)

        e_name = QLineEdit(sys.name)
        e_name.setMaxLength(16)
        e_name.setToolTip(HELP["sys_name"])
        e_name.textChanged.connect(lambda v: self._set_system_field(s_idx, "name", v))
        e_name.textChanged.connect(
            lambda v: self._title.setText(f"System: {v or '(unnamed)'}  [{type_label}]")
        )
        form.addRow("System Name:", e_name)
        form.addRow("", _help_label("sys_name"))

        qk_row, _ = self._qk_row(
            sys.quick_key,
            lambda v: self._set_system_field(s_idx, "quick_key", v or "."),
            self._used_system_qks,
        )
        form.addRow("Quick Key:", qk_row)
        form.addRow("", _help_label("quick_key"))

        e_hold = QLineEdit(sys.hold_time)
        e_hold.setPlaceholderText("tenths of a second")
        e_hold.setToolTip(HELP["hold_time"])
        e_hold.textChanged.connect(lambda v: self._set_system_field(s_idx, "hold_time", v))
        form.addRow("Hold Time:", e_hold)
        form.addRow("", _help_label("hold_time"))

        e_delay = QLineEdit(sys.delay_time)
        e_delay.setPlaceholderText("seconds")
        e_delay.setToolTip(HELP["delay"])
        e_delay.textChanged.connect(lambda v: self._set_system_field(s_idx, "delay_time", v))
        form.addRow("Delay Time:", e_delay)
        form.addRow("", _help_label("delay"))

        c_id_search = QComboBox()
        c_id_search.addItem("ID Scan", userData="0")
        c_id_search.addItem("ID Search", userData="1")
        idx = c_id_search.findData(sys.id_search or "0")
        c_id_search.setCurrentIndex(idx if idx >= 0 else 0)
        c_id_search.setToolTip(HELP["mot_id_search"])
        c_id_search.currentIndexChanged.connect(
            lambda _: self._set_system_field(s_idx, "id_search", c_id_search.currentData())
        )
        form.addRow("ID Search Mode:", c_id_search)
        form.addRow("", _help_label("mot_id_search"))

        if sys.is_motorola:
            c_sbit = QComboBox()
            c_sbit.addItem("Ignore", userData=False)
            c_sbit.addItem("Yes", userData=True)
            c_sbit.setCurrentIndex(1 if sys.ignore_status_bit else 0)
            c_sbit.setToolTip(HELP["mot_status_bit"])
            c_sbit.currentIndexChanged.connect(
                lambda _: self._set_system_field(s_idx, "ignore_status_bit", c_sbit.currentData())
            )
            form.addRow("Status Bit:", c_sbit)
            form.addRow("", _help_label("mot_status_bit"))

            c_end = QComboBox()
            c_end.addItem("Ignore", userData=False)
            c_end.addItem("Analog", userData=True)
            c_end.setCurrentIndex(1 if sys.end_code else 0)
            c_end.setToolTip(HELP["mot_end_code"])
            c_end.currentIndexChanged.connect(
                lambda _: self._set_system_field(s_idx, "end_code", c_end.currentData())
            )
            form.addRow("End Code:", c_end)
            form.addRow("", _help_label("mot_end_code"))

            # Fleet map: preset numbers 1–16 or Custom
            c_fmap = QComboBox()
            for i in range(1, 17):
                c_fmap.addItem(f"Preset {i}", userData=str(i))
            c_fmap.addItem("Custom", userData="0")
            fmap_idx = c_fmap.findData(sys.fleet_map or "16")
            c_fmap.setCurrentIndex(fmap_idx if fmap_idx >= 0 else c_fmap.count() - 1)
            c_fmap.setToolTip(HELP["mot_fleet_map"])

            e_ctm_fmap = QLineEdit(sys.custom_fleet_map or "")
            e_ctm_fmap.setPlaceholderText("8 hex digits (e.g. 00FFFFFF)")
            e_ctm_fmap.setEnabled(sys.fleet_map == "0")
            e_ctm_fmap.setToolTip(HELP["mot_custom_fleet_map"])
            e_ctm_fmap.textChanged.connect(
                lambda v: self._set_system_field(s_idx, "custom_fleet_map", v)
            )

            c_fmap.currentIndexChanged.connect(
                lambda _: (
                    self._set_system_field(s_idx, "fleet_map", c_fmap.currentData()),
                    e_ctm_fmap.setEnabled(c_fmap.currentData() == "0"),
                )
            )
            form.addRow("Fleet Map:", c_fmap)
            form.addRow("", _help_label("mot_fleet_map"))
            form.addRow("Custom Fleet Map:", e_ctm_fmap)
            form.addRow("", _help_label("mot_custom_fleet_map"))

        if sys.is_p25:
            e_nac = QLineEdit(sys.p25_nac or "SRCH")
            e_nac.setPlaceholderText("SRCH or 0–4095")
            e_nac.setToolTip(HELP["p25_nac"])
            e_nac.textChanged.connect(lambda v: self._set_system_field(s_idx, "p25_nac", v))
            form.addRow("P25 NAC:", e_nac)
            form.addRow("", _help_label("p25_nac"))

        c_mot_id = QComboBox()
        c_mot_id.addItem("Decimal", userData="0")
        c_mot_id.addItem("HEX", userData="1")
        idx = c_mot_id.findData(sys.mot_id or "0")
        c_mot_id.setCurrentIndex(idx if idx >= 0 else 0)
        c_mot_id.setToolTip(HELP["mot_id_display"])
        c_mot_id.currentIndexChanged.connect(
            lambda _: self._set_system_field(s_idx, "mot_id", c_mot_id.currentData())
        )
        form.addRow("ID Display:", c_mot_id)
        form.addRow("", _help_label("mot_id_display"))

        cb_lockout = QCheckBox("Locked out (skip this system)")
        cb_lockout.setChecked(sys.lockout)
        cb_lockout.setToolTip(HELP["lockout"])
        cb_lockout.toggled.connect(lambda v: self._set_system_field(s_idx, "lockout", v))
        form.addRow("", cb_lockout)

        layout.addWidget(params_box)

        # ---- Trunk Frequencies table (P25F has a single embedded frequency — no site) ----
        if sys.is_p25f:
            return
        tf_box = QGroupBox("Trunk Frequencies")
        tf_vbox = QVBoxLayout(tf_box)

        # LCN is auto-assigned at upload time (1, 2, 3, … per FreeSCAN convention).
        # It is not exposed here to avoid confusion.
        tf_table = QTableWidget(0, 2)
        tf_table.setHorizontalHeaderLabels(["Frequency (MHz)", "Lockout"])
        tf_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tf_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        tf_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tf_table.verticalHeader().setVisible(False)

        # Populate rows
        tf_table.blockSignals(True)
        for tf in sys.trunk_frequencies:
            row = tf_table.rowCount()
            tf_table.insertRow(row)
            tf_table.setItem(row, 0, QTableWidgetItem(tf.frequency))
            cb_item = QTableWidgetItem()
            cb_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            cb_item.setCheckState(Qt.CheckState.Checked if tf.lockout else Qt.CheckState.Unchecked)
            tf_table.setItem(row, 1, cb_item)
        tf_table.blockSignals(False)

        def _tf_changed(row: int, col: int) -> None:
            if not self._config or row >= len(sys.trunk_frequencies):
                return
            tf = sys.trunk_frequencies[row]
            if col == 0:
                tf.frequency = tf_table.item(row, 0).text().strip()
            elif col == 1:
                tf.lockout = tf_table.item(row, 1).checkState() == Qt.CheckState.Checked
            self._config.modified = True
            self.modified.emit()

        tf_table.cellChanged.connect(_tf_changed)

        def _add_tf() -> None:
            if not self._config:
                return
            tf = TrunkFrequency()
            # Link trunk freq to the system via its group_id.
            # The upload dialog creates the actual site on the scanner via AST;
            # the .996 file loader accepts both system and site group_ids.
            tf.group_id = sys.group_id
            sys.trunk_frequencies.append(tf)
            row = tf_table.rowCount()
            tf_table.blockSignals(True)
            tf_table.insertRow(row)
            tf_table.setItem(row, 0, QTableWidgetItem(""))
            cb_item = QTableWidgetItem()
            cb_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            cb_item.setCheckState(Qt.CheckState.Unchecked)
            tf_table.setItem(row, 1, cb_item)
            tf_table.blockSignals(False)
            tf_table.setCurrentCell(row, 0)
            tf_table.editItem(tf_table.item(row, 0))
            self._config.modified = True
            self.modified.emit()

        def _del_tf() -> None:
            if not self._config:
                return
            rows = sorted(set(i.row() for i in tf_table.selectedItems()), reverse=True)
            for r in rows:
                if r < len(sys.trunk_frequencies):
                    sys.trunk_frequencies.pop(r)
                tf_table.removeRow(r)
            self._config.modified = True
            self.modified.emit()

        tf_vbox.addWidget(tf_table)
        btn_row = QHBoxLayout()
        btn_add = QPushButton("Add Frequency")
        btn_del = QPushButton("Delete Selected")
        btn_add.clicked.connect(_add_tf)
        btn_del.clicked.connect(_del_tf)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_del)
        btn_row.addStretch()
        tf_vbox.addLayout(btn_row)
        layout.addWidget(tf_box)

    def _build_tgid_group_form(self, grp: Group, s_idx: int, g_idx: int) -> None:
        """Form for a TGID group in a Motorola system: name/QK/lockout + talk group table."""
        self._clear_form()
        self._title.setText(f"TGID Group: {grp.name or '(unnamed)'}")
        layout = QVBoxLayout(self._form_container)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ---- Group settings ----
        grp_box = QGroupBox("Group Settings")
        form = QFormLayout(grp_box)

        e_name = QLineEdit(grp.name)
        e_name.setMaxLength(16)
        e_name.textChanged.connect(lambda v: self._set_group_field(s_idx, g_idx, "name", v))
        e_name.textChanged.connect(lambda v: self._title.setText(f"TGID Group: {v or '(unnamed)'}"))
        form.addRow("Group Name:", e_name)

        qk_row, _ = self._qk_row(
            grp.quick_key,
            lambda v: self._set_group_field(s_idx, g_idx, "quick_key", v or "."),
            self._used_group_qks,
        )
        form.addRow("Quick Key:", qk_row)

        cb_lockout = QCheckBox("Locked out")
        cb_lockout.setChecked(grp.lockout)
        cb_lockout.toggled.connect(lambda v: self._set_group_field(s_idx, g_idx, "lockout", v))
        form.addRow("", cb_lockout)

        layout.addWidget(grp_box)

        # ---- Talk groups table ----
        tg_box = QGroupBox("Talk Groups")
        tg_vbox = QVBoxLayout(tg_box)

        tg_table = QTableWidget(0, 5)
        tg_table.setHorizontalHeaderLabels(["TGID", "Name", "Priority", "Lockout", "Audio Type"])
        tg_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        tg_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        tg_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        tg_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        tg_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        tg_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tg_table.verticalHeader().setVisible(False)

        _audio_type_labels = ["All", "Analog Only", "Digital Only"]

        def _tg_audio_combo(current: str) -> QComboBox:
            c = QComboBox()
            for i, label in enumerate(_audio_type_labels):
                c.addItem(label, userData=str(i))
            idx = c.findData(current or "0")
            c.setCurrentIndex(idx if idx >= 0 else 0)
            return c

        def _populate_tg_table() -> None:
            tg_table.blockSignals(True)
            tg_table.setRowCount(0)
            for row, tg in enumerate(grp.channels):
                if not isinstance(tg, TalkGroup):
                    continue
                tg_table.insertRow(row)
                tg_table.setItem(row, 0, QTableWidgetItem(tg.tgid))
                tg_table.setItem(row, 1, QTableWidgetItem(tg.name))
                pri_item = QTableWidgetItem()
                pri_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                pri_item.setCheckState(Qt.CheckState.Checked if tg.priority else Qt.CheckState.Unchecked)
                tg_table.setItem(row, 2, pri_item)
                lout_item = QTableWidgetItem()
                lout_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                lout_item.setCheckState(Qt.CheckState.Checked if tg.lockout else Qt.CheckState.Unchecked)
                tg_table.setItem(row, 3, lout_item)
                combo = _tg_audio_combo(tg.audio_type)
                combo.currentIndexChanged.connect(
                    lambda _, r=row: _tg_audio_changed(r)
                )
                tg_table.setCellWidget(row, 4, combo)
            tg_table.blockSignals(False)

        def _tg_audio_changed(row: int) -> None:
            if not self._config or row >= len(grp.channels):
                return
            tg = grp.channels[row]
            if not isinstance(tg, TalkGroup):
                return
            combo = tg_table.cellWidget(row, 4)
            if combo:
                tg.audio_type = combo.currentData()
            self._config.modified = True
            self.modified.emit()

        def _tg_cell_changed(row: int, col: int) -> None:
            if not self._config or row >= len(grp.channels):
                return
            tg = grp.channels[row]
            if not isinstance(tg, TalkGroup):
                return
            if col == 0:
                tg.tgid = tg_table.item(row, 0).text().strip()
            elif col == 1:
                tg.name = tg_table.item(row, 1).text().strip()
            elif col == 2:
                tg.priority = tg_table.item(row, 2).checkState() == Qt.CheckState.Checked
            elif col == 3:
                tg.lockout = tg_table.item(row, 3).checkState() == Qt.CheckState.Checked
            self._config.modified = True
            self.modified.emit()

        tg_table.cellChanged.connect(_tg_cell_changed)

        def _add_tg() -> None:
            if not self._config:
                return
            tg = TalkGroup()
            tg.group_id = grp.group_id
            grp.channels.append(tg)
            row = tg_table.rowCount()
            tg_table.blockSignals(True)
            tg_table.insertRow(row)
            tg_table.setItem(row, 0, QTableWidgetItem(""))
            tg_table.setItem(row, 1, QTableWidgetItem(""))
            pri_item = QTableWidgetItem()
            pri_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            pri_item.setCheckState(Qt.CheckState.Unchecked)
            tg_table.setItem(row, 2, pri_item)
            lout_item = QTableWidgetItem()
            lout_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            lout_item.setCheckState(Qt.CheckState.Unchecked)
            tg_table.setItem(row, 3, lout_item)
            combo = _tg_audio_combo("0")
            combo.currentIndexChanged.connect(lambda _, r=row: _tg_audio_changed(r))
            tg_table.setCellWidget(row, 4, combo)
            tg_table.blockSignals(False)
            tg_table.setCurrentCell(row, 0)
            tg_table.editItem(tg_table.item(row, 0))
            self._config.modified = True
            self.modified.emit()
            self.structure_changed.emit()

        def _del_tg() -> None:
            if not self._config:
                return
            rows = sorted(set(i.row() for i in tg_table.selectedItems()), reverse=True)
            for r in rows:
                if r < len(grp.channels):
                    grp.channels.pop(r)
                tg_table.removeRow(r)
            self._config.modified = True
            self.modified.emit()
            self.structure_changed.emit()

        _populate_tg_table()
        tg_vbox.addWidget(tg_table)
        btn_row = QHBoxLayout()
        btn_add = QPushButton("Add Talk Group")
        btn_del = QPushButton("Delete Selected")
        btn_add.clicked.connect(_add_tg)
        btn_del.clicked.connect(_del_tg)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_del)
        btn_row.addStretch()
        tg_vbox.addLayout(btn_row)
        layout.addWidget(tg_box)

    def _build_system_form(self, sys: System, s_idx: int) -> None:
        self._clear_form()
        self._title.setText(f"System: {sys.name or '(unnamed)'}")
        layout = QVBoxLayout(self._form_container)
        group = QGroupBox("System Settings")
        form = QFormLayout(group)

        e_name = QLineEdit(sys.name)
        e_name.setMaxLength(16)
        e_name.setToolTip(HELP["sys_name"])
        e_name.textChanged.connect(lambda v: self._set_system_field(s_idx, "name", v))
        e_name.textChanged.connect(lambda v: self._title.setText(f"System: {v or '(unnamed)'}"))
        form.addRow("System Name:", e_name)
        form.addRow("", _help_label("sys_name"))

        IMPLEMENTED_SYSTEM_TYPES = {SYS_TYPE_CONVENTIONAL, SYS_TYPE_MOTOROLA, SYS_TYPE_P25, SYS_TYPE_P25_EDACS}
        c_type = QComboBox()
        for i, (type_id, type_name) in enumerate(SYSTEM_TYPE_NAMES.items()):
            c_type.addItem(type_name, userData=type_id)
            if type_id not in IMPLEMENTED_SYSTEM_TYPES:
                item = c_type.model().item(i)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        cur = c_type.findData(sys.system_type)
        if cur >= 0:
            c_type.setCurrentIndex(cur)
        c_type.setToolTip(HELP["sys_type"])
        def _on_type_changed(_):
            self._set_system_field(s_idx, "system_type", c_type.currentData())
            # Rebuild the form so the correct fields are shown for the new type
            if self._config:
                self.show_system(self._config, s_idx)
        c_type.currentIndexChanged.connect(_on_type_changed)
        form.addRow("System Type:", c_type)
        form.addRow("", _help_label("sys_type"))

        qk_row, _ = self._qk_row(
            sys.quick_key,
            lambda v: self._set_system_field(s_idx, "quick_key", v or "."),
            self._used_system_qks,
        )
        form.addRow("Quick Key:", qk_row)
        form.addRow("", _help_label("quick_key"))

        e_hold = QLineEdit(sys.hold_time)
        e_hold.setPlaceholderText("tenths of a second")
        e_hold.setToolTip(HELP["hold_time"])
        e_hold.textChanged.connect(lambda v: self._set_system_field(s_idx, "hold_time", v))
        form.addRow("Hold Time:", e_hold)
        form.addRow("", _help_label("hold_time"))

        e_delay = QLineEdit(sys.delay_time)
        e_delay.setPlaceholderText("seconds")
        e_delay.textChanged.connect(lambda v: self._set_system_field(s_idx, "delay_time", v))
        form.addRow("Delay Time:", e_delay)

        cb_lockout = QCheckBox("Locked out (skip this system)")
        cb_lockout.setChecked(sys.lockout)
        cb_lockout.toggled.connect(lambda v: self._set_system_field(s_idx, "lockout", v))
        form.addRow("", cb_lockout)

        c_rec = QComboBox()
        c_rec.addItem("Off (no recording)", userData="0")
        c_rec.addItem("Marked channels only", userData="1")
        c_rec.addItem("All channels", userData="2")
        cur_rec = c_rec.findData(sys.record_mode or "0")
        c_rec.setCurrentIndex(cur_rec if cur_rec >= 0 else 0)
        c_rec.setToolTip(HELP["sys_record_mode"])
        c_rec.currentIndexChanged.connect(
            lambda _: self._set_system_field(s_idx, "record_mode", c_rec.currentData())
        )
        form.addRow("Record Mode:", c_rec)
        form.addRow("", _help_label("sys_record_mode"))

        layout.addWidget(group)

        info = QLabel(
            f"System ID: {sys.group_id}\n"
            f"Groups: {len(sys.groups)}\n"
            f"Trunk Frequencies: {len(sys.trunk_frequencies)}"
        )
        info.setStyleSheet("color: gray; font-size: 11px; padding: 4px;")
        layout.addWidget(info)

    # ------------------------------------------------------------------
    # Quick key helpers
    # ------------------------------------------------------------------

    def _used_system_qks(self) -> set[int]:
        """Return the set of numeric quick keys already assigned to systems."""
        used = set()
        if not self._config:
            return used
        for sys in self._config.systems:
            try:
                used.add(int(sys.quick_key))
            except (ValueError, TypeError):
                pass
        return used

    def _used_group_qks(self) -> set[int]:
        """Return the set of numeric quick keys already assigned to any group."""
        used = set()
        if not self._config:
            return used
        for sys in self._config.systems:
            for grp in sys.groups:
                try:
                    used.add(int(grp.quick_key))
                except (ValueError, TypeError):
                    pass
        return used

    def _next_available_qk(self, used: set[int]) -> str:
        """Return the lowest quick key 0-99 not in *used*, or '' if all taken."""
        for k in range(100):
            if k not in used:
                return str(k)
        return ""

    def _qk_row(self, current_qk: str, on_change, used_fn) -> tuple[QWidget, QLineEdit]:
        """
        Build a widget containing a QLineEdit for a quick key plus a
        'Next Available' button.  Returns (row_widget, line_edit).
        """
        row = QWidget()
        hbox = QHBoxLayout(row)
        hbox.setContentsMargins(0, 0, 0, 0)

        e_qk = QLineEdit(current_qk if current_qk != "." else "")
        e_qk.setPlaceholderText(". = none, or 0-99")
        e_qk.setToolTip(HELP["quick_key"])
        e_qk.textChanged.connect(on_change)
        hbox.addWidget(e_qk)

        btn = QPushButton("Next Available")
        btn.setFixedWidth(110)
        btn.setToolTip("Fill in the lowest quick key (0–99) not already used")
        btn.clicked.connect(lambda: self._fill_next_qk(e_qk, used_fn()))
        hbox.addWidget(btn)

        return row, e_qk

    def _fill_next_qk(self, field: QLineEdit, used: set[int]) -> None:
        nxt = self._next_available_qk(used)
        if nxt:
            field.setText(nxt)

    # ------------------------------------------------------------------
    # Model write-back helpers
    # ------------------------------------------------------------------

    def _set_channel_field(
        self, s_idx: int, g_idx: int, c_idx: int, attr: str, value
    ) -> None:
        if not self._config:
            return
        ch = self._config.systems[s_idx].groups[g_idx].channels[c_idx]
        setattr(ch, attr, value)
        self._config.modified = True
        self.modified.emit()

    def _set_group_field(self, s_idx: int, g_idx: int, attr: str, value) -> None:
        if not self._config:
            return
        setattr(self._config.systems[s_idx].groups[g_idx], attr, value)
        self._config.modified = True
        self.modified.emit()

    def _set_system_field(self, s_idx: int, attr: str, value) -> None:
        if not self._config:
            return
        setattr(self._config.systems[s_idx], attr, value)
        self._config.modified = True
        self.modified.emit()
