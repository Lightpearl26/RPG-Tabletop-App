# -*- coding: utf-8 -*-
"""
Session editor: preload, launch, time travel, duplicate and rename.

A session is a folder (like a map), so this editor overrides the persistence of
`ResourceEditor` while keeping its signals and modified-state contract.

New session:
    the form is the preload (name, initial map, entities). Save creates the
    session with that initial state (event #0).

Existing session:
    - Setup: add entities / change map (recorded as events once started).
    - Timeline: preview any past state, restore to it or branch from it.
    - Buttons: launch from the last snapshot, duplicate, rename.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rpg_tabletop.assets.campaign import Campaign
from rpg_tabletop.session import store
from rpg_tabletop.session.session import SESSION_FILE, Session
from .base import ResourceEditor


NO_MAP = "\u2014 no map \u2014"
TIMELINE_COLUMNS = ("#", "Time", "Type", "Message", "Snapshot")


class SessionEditor(ResourceEditor):
    """Session manager backed by a `sessions/<name>/session.json` folder."""

    resource_label = "Session"
    resource_type = "sessions"

    # Folder name of the session to open in a RuntimeWindow
    launch_requested = Signal(str)
    # A session was created, duplicated or restored: browsers should refresh
    sessions_changed = Signal()

    def __init__(
        self,
        resource_id: str = "",
        campaign: Campaign | None = None,
        *,
        is_new: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        self.session: Optional[Session] = None
        self.summary: Optional[store.StateSummary] = None
        # Set by the IDE: whether a runtime window currently owns a session name.
        self.session_in_use: Callable[[str], bool] = lambda _name: False
        super().__init__(resource_id, campaign, is_new=is_new, parent=parent)
        self._build_ui()
        self._populate_fields()
        self._refresh_timeline()
        self._update_info()
        self._sync_buttons()
        self._mark_clean()

    @property
    def directory(self) -> Path:
        return self.campaign.sessions_path

    # ------------------------------------------------------------------
    # ResourceEditor data contract
    # ------------------------------------------------------------------

    def _new_data(self) -> dict:
        return {"name": "", "map": None, "entities": []}

    def _setup_data(self) -> dict:
        assert self.session is not None and self.summary is not None
        return {
            "name": self.session.name,
            "map": self.summary.active_map_id,
            "entities": sorted(self.summary.entity_names),
        }

    def _load(self, resource_id: str) -> None:
        if not resource_id or Path(resource_id).name != resource_id:
            raise ValueError("Session resource ID must be a folder name.")
        directory = self.campaign.session_path(resource_id)
        self.session = Session.load(directory)
        self.file_path = directory / SESSION_FILE
        try:
            self.summary = store.summarize(self.campaign, self.session)
        except Exception as error:  # the rebuild touches maps, entities and spells
            raise ValueError(f"Cannot rebuild the session state: {error}") from error
        self._data = self._setup_data()

    def _collect_data(self) -> dict:
        return {
            "name": self.name_input.text().strip(),
            "map": self.map_input.currentData(),
            "entities": sorted(
                self.entity_list.item(row).text()
                for row in range(self.entity_list.count())
                if self.entity_list.item(row).checkState() == Qt.CheckState.Checked
            ),
        }

    def _populate_fields(self) -> None:
        data = self._data
        self.name_input.setText(str(data.get("name", "")))
        self.name_input.setReadOnly(not self.is_new)

        current_map = data.get("map")
        maps = store.map_names(self.campaign)
        if current_map and current_map not in maps:
            maps.append(current_map)
        self.map_input.clear()
        self.map_input.addItem(NO_MAP, None)
        for name in maps:
            self.map_input.addItem(name, name)
        self.map_input.setCurrentIndex(max(0, self.map_input.findData(current_map)))

        checked = set(data.get("entities", []))
        self.entity_list.clear()
        for name in sorted({*store.entity_names(self.campaign), *checked}, key=str.casefold):
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if name in checked else Qt.CheckState.Unchecked
            )
            self.entity_list.addItem(item)

        started = not self.is_new and self.session is not None and self.session.current_seq > 0
        self.setup_hint.setText(
            "This session has started: added/removed entities (with their tokens on the "
            "active map) and map changes are recorded as events, so time travel replays "
            "them. An entity in the active combat cannot be removed."
            if started
            else "Checked entities and the map form the initial state (event #0)."
        )

    def _reload_session(self) -> None:
        self.session = Session.load(self.campaign.session_path(self.resource_id))
        self.summary = store.summarize(self.campaign, self.session)
        self._data = self._setup_data()
        self._reload_fields()
        self._mark_clean()
        self._refresh_timeline()
        self._update_info()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 24)
        root.setSpacing(12)

        header = QHBoxLayout()
        self.name_input = QLineEdit()
        self.name_input.setObjectName("SessionNameField")
        self.name_input.setPlaceholderText("Session name")
        self.launch_button = QPushButton("Launch from last snapshot")
        self.duplicate_button = QPushButton("Duplicate\u2026")
        self.rename_button = QPushButton("Rename\u2026")
        header.addWidget(QLabel("Name"))
        header.addWidget(self.name_input, 1)
        header.addWidget(self.launch_button)
        header.addWidget(self.duplicate_button)
        header.addWidget(self.rename_button)
        root.addLayout(header)

        self.info_label = QLabel()
        self.info_label.setObjectName("MutedText")
        root.addWidget(self.info_label)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_setup_tab(), "Setup")
        self.tabs.addTab(self._build_timeline_tab(), "Timeline")
        root.addWidget(self.tabs, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.save_button = QPushButton("Save Session")
        self.save_button.setObjectName("SessionEditorSaveButton")
        footer.addWidget(self.save_button)
        root.addLayout(footer)

        self.save_button.clicked.connect(self.save_requested)
        self.launch_button.clicked.connect(self._launch)
        self.duplicate_button.clicked.connect(self._duplicate)
        self.rename_button.clicked.connect(self._rename)
        self.name_input.textChanged.connect(self._refresh_modified)
        self.map_input.currentIndexChanged.connect(self._refresh_modified)
        self.entity_list.itemChanged.connect(self._refresh_modified)
        self.modified_changed.connect(lambda _modified: self._sync_buttons())
        self.timeline.itemSelectionChanged.connect(self._on_timeline_selection)
        self.restore_button.clicked.connect(self._restore)
        self.branch_button.clicked.connect(self._branch)

    def _build_setup_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        self.setup_hint = QLabel()
        self.setup_hint.setObjectName("MutedText")
        self.setup_hint.setWordWrap(True)
        layout.addWidget(self.setup_hint)

        row = QHBoxLayout()
        row.addWidget(QLabel("Map"))
        self.map_input = QComboBox()
        self.map_input.setMinimumWidth(240)
        row.addWidget(self.map_input)
        row.addStretch(1)
        layout.addLayout(row)

        layout.addWidget(QLabel("Entities"))
        self.entity_list = QListWidget()
        layout.addWidget(self.entity_list, 1)
        return page

    def _build_timeline_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.timeline = QTableWidget(0, len(TIMELINE_COLUMNS))
        self.timeline.setHorizontalHeaderLabels(TIMELINE_COLUMNS)
        self.timeline.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.timeline.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.timeline.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.timeline.verticalHeader().setVisible(False)
        self.timeline.horizontalHeader().setStretchLastSection(True)
        self.timeline.setColumnWidth(0, 60)
        self.timeline.setColumnWidth(1, 110)
        self.timeline.setColumnWidth(2, 170)
        self.timeline.setColumnWidth(3, 420)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        splitter.addWidget(self.timeline)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        hint = QLabel(
            "Restoring removes every later event (a backup copy is kept). "
            "Branching leaves this session untouched."
        )
        hint.setObjectName("MutedText")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.addStretch(1)
        self.restore_button = QPushButton("Go back to this point\u2026")
        self.branch_button = QPushButton("Branch from this point\u2026")
        row.addWidget(self.restore_button)
        row.addWidget(self.branch_button)
        layout.addLayout(row)
        return page

    def _update_info(self) -> None:
        session = self.session
        if session is None:
            self.info_label.setText(
                "New session: it is created with this initial state (event #0)."
            )
            return
        self.info_label.setText(
            f"Event #{session.current_seq} \u00b7 {len(session.events)} event(s) \u00b7 "
            f"{len(session.snapshots)} snapshot(s) \u00b7 saved "
            f"{session.updated_at.astimezone():%d/%m/%Y %H:%M}"
        )

    def _sync_buttons(self) -> None:
        saved = not self.is_new
        self.launch_button.setEnabled(saved)
        self.duplicate_button.setEnabled(saved)
        self.rename_button.setEnabled(saved)
        self.tabs.setTabEnabled(1, saved)
        self._sync_timeline_buttons()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> Path:
        data = self._collect_data()
        if self.is_new:
            path = store.create(self.campaign, data["name"], data["entities"], data["map"])
        else:
            if self.session_in_use(self.resource_id):
                raise ValueError("This session is open in a runtime window: close it first.")
            if not self.has_changes:
                return self.campaign.session_path(self.resource_id)
            path = store.apply_setup(
                self.campaign, self.resource_id, data["entities"], data["map"]
            )
        self.is_new = False
        self.resource_id = path.name
        self.title = path.name
        self.file_path = path / SESSION_FILE
        self._reload_session()
        self._sync_buttons()
        self.saved.emit(str(path), path.name)
        return path

    def _ready_for(self, action: str) -> bool:
        """Guard shared by the actions that rewrite the session on disk."""
        if self.is_new or self.session is None:
            return False
        if self.session_in_use(self.resource_id):
            QMessageBox.warning(
                self, action, "This session is open in a runtime window. Close it first."
            )
            return False
        if self.has_changes:
            QMessageBox.warning(
                self, action, "Save or discard the unsaved setup changes first."
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _launch(self) -> None:
        if not self.is_new and not self.has_changes:
            self.launch_requested.emit(self.resource_id)

    def _duplicate(self) -> None:
        if self.session is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            "Duplicate session",
            "Name of the copy:",
            text=store.unique_name(self.campaign, f"{self.resource_id} (copy)"),
        )
        if not accepted:
            return
        try:
            path = store.duplicate(self.campaign, self.resource_id, name)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Duplicate session", str(error))
            return
        self.sessions_changed.emit()
        QMessageBox.information(
            self,
            "Duplicate session",
            f"Created \"{path.name}\" from the last saved state of this session.",
        )

    def _rename(self) -> None:
        if not self._ready_for("Rename session"):
            return
        name, accepted = QInputDialog.getText(
            self, "Rename session", "New name:", text=self.resource_id
        )
        if not accepted or name.strip() == self.resource_id:
            return
        try:
            path = store.rename(self.campaign, self.resource_id, name)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Rename session", str(error))
            return
        self.resource_id = path.name
        self.title = path.name
        self.file_path = path / SESSION_FILE
        self._reload_session()
        self.saved.emit(str(path), path.name)

    def _restore(self) -> None:
        seq = self._selected_seq()
        if seq is None or self.session is None or seq >= self.session.current_seq:
            return
        if not self._ready_for("Restore session"):
            return
        removed = self.session.current_seq - seq
        answer = QMessageBox.question(
            self,
            "Restore session",
            f"Go back to event #{seq}?\n\n{removed} later event(s) will be removed "
            "from this session. A backup copy is kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            backup = store.restore(self.campaign, self.resource_id, seq)
            self._reload_session()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Restore session", str(error))
            return
        self.sessions_changed.emit()
        QMessageBox.information(
            self,
            "Restore session",
            f"Back to event #{seq}."
            + (f"\nBackup: \"{backup.name}\"." if backup is not None else ""),
        )

    def _branch(self) -> None:
        seq = self._selected_seq()
        if seq is None or self.session is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            "Branch session",
            "Name of the new session:",
            text=store.unique_name(self.campaign, f"{self.resource_id} (from #{seq})"),
        )
        if not accepted:
            return
        try:
            path = store.branch(self.campaign, self.resource_id, name, seq)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Branch session", str(error))
            return
        self.sessions_changed.emit()
        QMessageBox.information(
            self, "Branch session", f"Created \"{path.name}\" at event #{seq}."
        )

    # ------------------------------------------------------------------
    # Timeline
    # ------------------------------------------------------------------

    def _refresh_timeline(self) -> None:
        self.timeline.blockSignals(True)
        self.timeline.setRowCount(0)
        session = self.session
        if session is None:
            self.timeline.blockSignals(False)
            self.preview.clear()
            self._sync_timeline_buttons()
            return

        snapshot_seqs = {snapshot.seq for snapshot in session.snapshots}
        rows = [(0, session.initial_snapshot.timestamp, "start", "Initial state")]
        rows += [(e.seq, e.timestamp, e.type.value, e.message) for e in session.events]
        self.timeline.setRowCount(len(rows))
        for row, (seq, stamp, kind, message) in enumerate(rows):
            values = (
                str(seq),
                stamp.astimezone().strftime("%d/%m %H:%M:%S"),
                kind,
                message,
                "\u25cf" if seq in snapshot_seqs else "",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, seq)
                self.timeline.setItem(row, column, item)
        self.timeline.blockSignals(False)
        self.timeline.selectRow(len(rows) - 1)
        self.timeline.scrollToBottom()

    def _selected_seq(self) -> Optional[int]:
        rows = self.timeline.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.timeline.item(rows[0].row(), 0)
        return int(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def _sync_timeline_buttons(self) -> None:
        seq = self._selected_seq()
        current = self.session.current_seq if self.session is not None else 0
        self.restore_button.setEnabled(seq is not None and seq < current)
        self.branch_button.setEnabled(seq is not None)

    def _on_timeline_selection(self) -> None:
        self._sync_timeline_buttons()
        seq = self._selected_seq()
        if seq is None or self.session is None:
            self.preview.clear()
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            text = store.summarize(self.campaign, self.session, seq).describe()
        except Exception as error:  # a missing map or entity must not break the editor
            text = f"Preview unavailable: {error}"
        finally:
            QApplication.restoreOverrideCursor()
        self.preview.setPlainText(text)
