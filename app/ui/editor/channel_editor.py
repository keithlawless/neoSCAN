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
)

from app.data.models import (
    ScannerConfig, System, Group, Channel, TalkGroup,
    SYSTEM_TYPE_NAMES, SYS_TYPE_CONVENTIONAL,
)


# ---------------------------------------------------------------------------
# Help text for each field
# ---------------------------------------------------------------------------
HELP = {
    "freq": (
        "Frequency (MHz)\n\n"
        "Enter the frequency in MHz, e.g. 154.235 or 471.4250.\n\n"
        "The BCT15-X supports: 25–512 MHz and 764–956 MHz.\n"
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
# Channel editor form
# ---------------------------------------------------------------------------
class ChannelEditorPanel(QWidget):
    """
    Detail editor panel.  Shows a form for the currently selected
    System, Group, or Channel.  Changes are written back to the model
    immediately (no Apply button needed).
    """

    modified = pyqtSignal()  # emitted whenever a field changes

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._config: ScannerConfig | None = None
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
        grp = config.systems[s_idx].groups[g_idx]
        self._build_group_form(grp, s_idx, g_idx)

    def show_system(self, config: ScannerConfig, s_idx: int) -> None:
        self._config = config
        self._context = ("system", s_idx, None, None)
        sys = config.systems[s_idx]
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
        e_freq.textChanged.connect(lambda v: self._set_channel_field(s_idx, g_idx, c_idx, "frequency", v))
        form.addRow("Frequency (MHz):", e_freq)
        form.addRow("", _help_label("freq"))

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
        e_tgid.textChanged.connect(lambda v: self._set_channel_field(s_idx, g_idx, c_idx, "tgid", v))
        form.addRow("TGID:", e_tgid)

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

        c_type = QComboBox()
        for type_id, type_name in SYSTEM_TYPE_NAMES.items():
            c_type.addItem(type_name, userData=type_id)
        cur = c_type.findData(sys.system_type)
        if cur >= 0:
            c_type.setCurrentIndex(cur)
        c_type.setToolTip(HELP["sys_type"])
        c_type.currentIndexChanged.connect(
            lambda _: self._set_system_field(s_idx, "system_type", c_type.currentData())
        )
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
