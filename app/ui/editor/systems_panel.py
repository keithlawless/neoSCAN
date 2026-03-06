"""
Systems panel — tree view of Systems > Groups > Channels.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QModelIndex
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QFont, QColor
from PyQt6.QtWidgets import (
    QTreeView,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QAbstractItemView,
    QHeaderView,
    QSizePolicy,
)

from app.data.models import ScannerConfig, System, Group, Channel, TalkGroup


# Custom item roles
ROLE_ITEM_TYPE = Qt.ItemDataRole.UserRole + 1   # "system" | "group" | "channel"
ROLE_SYS_IDX = Qt.ItemDataRole.UserRole + 2
ROLE_GRP_IDX = Qt.ItemDataRole.UserRole + 3
ROLE_CH_IDX = Qt.ItemDataRole.UserRole + 4


class SystemsPanel(QWidget):
    """
    Left-side panel containing a tree of all systems, groups and channels.
    Emits selection signals so the editor panel can update its form.
    """

    system_selected = pyqtSignal(int)           # sys_idx
    group_selected = pyqtSignal(int, int)       # sys_idx, grp_idx
    channel_selected = pyqtSignal(int, int, int)  # sys_idx, grp_idx, ch_idx

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._config: ScannerConfig | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Toolbar
        btn_bar = QHBoxLayout()
        self._btn_add_sys = QPushButton("+ System")
        self._btn_add_sys.setFixedHeight(24)
        self._btn_add_sys.clicked.connect(self._on_add_system)
        self._btn_add_grp = QPushButton("+ Group")
        self._btn_add_grp.setFixedHeight(24)
        self._btn_add_grp.clicked.connect(self._on_add_group)
        self._btn_add_ch = QPushButton("+ Channel")
        self._btn_add_ch.setFixedHeight(24)
        self._btn_add_ch.clicked.connect(self._on_add_channel)
        self._btn_del = QPushButton("Delete")
        self._btn_del.setFixedHeight(24)
        self._btn_del.clicked.connect(self._on_delete)

        btn_bar.addWidget(self._btn_add_sys)
        btn_bar.addWidget(self._btn_add_grp)
        btn_bar.addWidget(self._btn_add_ch)
        btn_bar.addStretch()
        btn_bar.addWidget(self._btn_del)
        layout.addLayout(btn_bar)

        # Tree
        self._model = QStandardItemModel()
        self._model.setHorizontalHeaderLabels(["Name", "Type / Frequency"])

        self._tree = QTreeView()
        self._tree.setModel(self._model)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tree.setUniformRowHeights(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.selectionModel().currentChanged.connect(self._on_selection_changed)

        layout.addWidget(self._tree)
        self._update_toolbar()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_config(self, config: ScannerConfig) -> None:
        self._config = config
        self._rebuild_tree()

    def clear(self) -> None:
        self._config = None
        self._model.clear()
        self._model.setHorizontalHeaderLabels(["Name", "Type / Frequency"])
        self._update_toolbar()

    def refresh_selected_item(self) -> None:
        """
        Update the display text of the currently selected tree item to match
        the current model state.  Called after the detail editor modifies a field.
        """
        if not self._config:
            return
        idx = self._tree.currentIndex()
        item = self._model.itemFromIndex(idx)
        if not item:
            return

        item_type = item.data(ROLE_ITEM_TYPE)
        s_idx = item.data(ROLE_SYS_IDX)
        g_idx = item.data(ROLE_GRP_IDX)
        c_idx = item.data(ROLE_CH_IDX)

        # Grab the name-column item (column 0) for the selected row.
        # The selection may be on column 1 so always resolve to column 0.
        name_index = self._model.index(idx.row(), 0, idx.parent())
        name_item = self._model.itemFromIndex(name_index)
        info_index = self._model.index(idx.row(), 1, idx.parent())
        info_item = self._model.itemFromIndex(info_index)
        if not name_item:
            return

        if item_type == "system" and s_idx is not None:
            sys = self._config.systems[s_idx]
            name_item.setText(sys.name or f"System {s_idx + 1}")
            if info_item:
                info_item.setText(sys.type_name)

        elif item_type == "group" and s_idx is not None and g_idx is not None:
            grp = self._config.systems[s_idx].groups[g_idx]
            name_item.setText(grp.name or f"Group {g_idx + 1}")
            if info_item:
                ch_count = len(grp.channels)
                info_item.setText(f"{ch_count} channel{'s' if ch_count != 1 else ''}")

        elif item_type == "channel" and all(x is not None for x in (s_idx, g_idx, c_idx)):
            ch = self._config.systems[s_idx].groups[g_idx].channels[c_idx]
            name_item.setText(ch.name or "(unnamed)")
            if info_item:
                if isinstance(ch, Channel):
                    info_item.setText(f"{ch.display_frequency()} MHz")
                else:
                    info_item.setText(f"TGID {ch.tgid}")

    # ------------------------------------------------------------------
    # Tree building
    # ------------------------------------------------------------------

    def _rebuild_tree(self) -> None:
        self._model.clear()
        self._model.setHorizontalHeaderLabels(["Name", "Type / Frequency"])
        if not self._config:
            return
        for s_idx, sys in enumerate(self._config.systems):
            sys_item = self._make_system_item(sys, s_idx)
            self._model.appendRow(sys_item)
        self._tree.expandToDepth(0)
        self._update_toolbar()

    def _make_system_item(self, sys: System, s_idx: int) -> list[QStandardItem]:
        name_item = QStandardItem(sys.name or f"System {s_idx + 1}")
        type_item = QStandardItem(sys.type_name)

        font = QFont()
        font.setBold(True)
        name_item.setFont(font)
        if sys.lockout:
            name_item.setForeground(QColor("#999"))

        name_item.setData("system", ROLE_ITEM_TYPE)
        name_item.setData(s_idx, ROLE_SYS_IDX)
        type_item.setData("system", ROLE_ITEM_TYPE)
        type_item.setData(s_idx, ROLE_SYS_IDX)

        for g_idx, grp in enumerate(sys.groups):
            grp_row = self._make_group_item(grp, s_idx, g_idx)
            name_item.appendRow(grp_row)

        return [name_item, type_item]

    def _make_group_item(
        self, grp: Group, s_idx: int, g_idx: int
    ) -> list[QStandardItem]:
        ch_count = len(grp.channels)
        name_item = QStandardItem(grp.name or f"Group {g_idx + 1}")
        info_item = QStandardItem(f"{ch_count} channel{'s' if ch_count != 1 else ''}")

        if grp.lockout:
            name_item.setForeground(QColor("#999"))

        name_item.setData("group", ROLE_ITEM_TYPE)
        name_item.setData(s_idx, ROLE_SYS_IDX)
        name_item.setData(g_idx, ROLE_GRP_IDX)
        info_item.setData("group", ROLE_ITEM_TYPE)
        info_item.setData(s_idx, ROLE_SYS_IDX)
        info_item.setData(g_idx, ROLE_GRP_IDX)

        for c_idx, ch in enumerate(grp.channels):
            ch_row = self._make_channel_item(ch, s_idx, g_idx, c_idx)
            name_item.appendRow(ch_row)

        return [name_item, info_item]

    def _make_channel_item(
        self,
        ch: Channel | TalkGroup,
        s_idx: int,
        g_idx: int,
        c_idx: int,
    ) -> list[QStandardItem]:
        name_item = QStandardItem(ch.name or "(unnamed)")
        if isinstance(ch, Channel):
            freq_str = ch.display_frequency()
            freq_item = QStandardItem(f"{freq_str} MHz")
        else:
            freq_item = QStandardItem(f"TGID {ch.tgid}")

        if getattr(ch, "lockout", False):
            name_item.setForeground(QColor("#999"))
            freq_item.setForeground(QColor("#999"))

        for item in (name_item, freq_item):
            item.setData("channel", ROLE_ITEM_TYPE)
            item.setData(s_idx, ROLE_SYS_IDX)
            item.setData(g_idx, ROLE_GRP_IDX)
            item.setData(c_idx, ROLE_CH_IDX)

        return [name_item, freq_item]

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_selection_changed(self, current: QModelIndex, _prev: QModelIndex) -> None:
        item = self._model.itemFromIndex(current)
        if not item:
            self._update_toolbar()
            return
        item_type = item.data(ROLE_ITEM_TYPE)
        s_idx = item.data(ROLE_SYS_IDX)
        g_idx = item.data(ROLE_GRP_IDX)
        c_idx = item.data(ROLE_CH_IDX)

        if item_type == "system" and s_idx is not None:
            self.system_selected.emit(s_idx)
        elif item_type == "group" and s_idx is not None and g_idx is not None:
            self.group_selected.emit(s_idx, g_idx)
        elif item_type == "channel" and all(x is not None for x in (s_idx, g_idx, c_idx)):
            self.channel_selected.emit(s_idx, g_idx, c_idx)
        self._update_toolbar()

    def _current_indices(self) -> tuple[int | None, int | None, int | None]:
        idx = self._tree.currentIndex()
        item = self._model.itemFromIndex(idx)
        if not item:
            return None, None, None
        return item.data(ROLE_SYS_IDX), item.data(ROLE_GRP_IDX), item.data(ROLE_CH_IDX)

    def _current_type(self) -> str | None:
        idx = self._tree.currentIndex()
        item = self._model.itemFromIndex(idx)
        if not item:
            return None
        return item.data(ROLE_ITEM_TYPE)

    # ------------------------------------------------------------------
    # Toolbar button actions
    # ------------------------------------------------------------------

    def _update_toolbar(self) -> None:
        has_config = self._config is not None
        t = self._current_type()
        self._btn_add_sys.setEnabled(has_config)
        self._btn_add_grp.setEnabled(has_config and t in ("system", "group", "channel"))
        self._btn_add_ch.setEnabled(has_config and t in ("group", "channel"))
        self._btn_del.setEnabled(has_config and t is not None)

    def _on_add_system(self) -> None:
        if not self._config:
            return
        from app.data.models import System
        import uuid
        sys = System()
        sys.name = f"New System {len(self._config.systems) + 1}"
        sys.group_id = uuid.uuid4().hex[:16].upper()
        self._config.systems.append(sys)
        self._config.modified = True
        self._rebuild_tree()
        # Select the new system
        new_row = self._model.rowCount() - 1
        self._tree.setCurrentIndex(self._model.index(new_row, 0))

    def _on_add_group(self) -> None:
        if not self._config:
            return
        s_idx, _, _ = self._current_indices()
        if s_idx is None:
            return
        import uuid
        from app.data.models import Group
        grp = Group()
        grp.name = "New Group"
        grp.group_id = uuid.uuid4().hex[:16].upper()
        self._config.systems[s_idx].groups.append(grp)
        self._config.modified = True
        self._rebuild_tree()

    def _on_add_channel(self) -> None:
        if not self._config:
            return
        s_idx, g_idx, _ = self._current_indices()
        if s_idx is None or g_idx is None:
            return
        from app.data.models import Channel
        ch = Channel()
        ch.name = "New Channel"
        ch.frequency = "0.0"
        ch.modulation = "FM"
        ch.group_id = self._config.systems[s_idx].groups[g_idx].group_id
        self._config.systems[s_idx].groups[g_idx].channels.append(ch)
        self._config.modified = True
        self._rebuild_tree()

    def _on_delete(self) -> None:
        if not self._config:
            return
        t = self._current_type()
        s_idx, g_idx, c_idx = self._current_indices()
        if t == "channel" and all(x is not None for x in (s_idx, g_idx, c_idx)):
            del self._config.systems[s_idx].groups[g_idx].channels[c_idx]
        elif t == "group" and s_idx is not None and g_idx is not None:
            del self._config.systems[s_idx].groups[g_idx]
        elif t == "system" and s_idx is not None:
            del self._config.systems[s_idx]
        self._config.modified = True
        self._rebuild_tree()
