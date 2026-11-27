# -*- coding: utf-8 -*-
"""
IDE widgets of the campaign history (assets.versioning).

    VersionBar      context selector (campaign or a session) and operations:
                    new session, send to the campaign, take the campaign,
                    merge a session, history
    ConflictDialog  one version to keep for each asset two sessions changed
    HistoryDialog   the points of a session: start a session from one, or go
                    back to one
"""
from __future__ import annotations

import json
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
    QHeaderView, QLabel, QPlainTextEdit, QPushButton, QRadioButton, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from rpg_tabletop.assets.versioning import CAMPAIGN, Conflict, Point, VersionStore

CAMPAIGN_LABEL = "Campagne"


def context_label(session: str) -> str:
    return CAMPAIGN_LABEL if session == CAMPAIGN else f"Session : {session}"


def asset_label(asset: str) -> str:
    """entities/hero.json -> « hero » (entité)."""
    kinds = {"entities": "entité", "characters": "personnage", "items": "objet",
             "spells": "sort", "maps": "carte"}
    parts = asset.split("/")
    name = parts[-1].rsplit(".", 1)[0] if len(parts) > 1 else asset
    return f"« {name} » ({kinds.get(parts[0], parts[0])})"


# =============================================================================
# Toolbar
# =============================================================================
class VersionBar(QWidget):
    """Which version of the assets the IDE shows, and what to do with it."""

    context_requested = Signal(str)
    new_session_requested = Signal()
    push_requested = Signal()
    pull_requested = Signal()
    merge_requested = Signal()
    history_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QLabel("Contexte :"))
        self.combo = QComboBox()
        self.combo.setMinimumWidth(200)
        self.combo.setToolTip(
            "Version des assets affichée et modifiée par l'IDE : la campagne, ou une session.\n"
            "Changer de contexte réécrit les fichiers de la campagne avec cette version."
        )
        layout.addWidget(self.combo)
        self.new_button = QPushButton("Nouvelle session…")
        self.new_button.setToolTip("Créer une session à partir du contexte actuel.")
        self.push_button = QPushButton("Envoyer dans la campagne")
        self.push_button.setToolTip("La campagne devient exactement cette session.")
        self.pull_button = QPushButton("Récupérer la campagne")
        self.pull_button.setToolTip(
            "Reprendre dans cette session ce qui a changé dans la campagne.\n"
            "En cas de conflit, la version de la campagne l'emporte."
        )
        self.merge_button = QPushButton("Fusionner…")
        self.merge_button.setToolTip("Reprendre dans cette session les changements d'une autre session.")
        self.history_button = QPushButton("Historique…")
        self.history_button.setToolTip("Les points enregistrés : revenir en arrière ou repartir d'un point.")
        for button in (self.new_button, self.push_button, self.pull_button, self.merge_button,
                       self.history_button):
            layout.addWidget(button)
        self.combo.activated.connect(self._on_activated)
        self.new_button.clicked.connect(self.new_session_requested)
        self.push_button.clicked.connect(self.push_requested)
        self.pull_button.clicked.connect(self.pull_requested)
        self.merge_button.clicked.connect(self.merge_requested)
        self.history_button.clicked.connect(self.history_requested)

    def refresh(self, store: VersionStore, running: Optional[str] = None) -> None:
        """Show the contexts; `running`: the session open in a runtime window (locks the others)."""
        current = store.current()
        self.combo.blockSignals(True)
        try:
            self.combo.clear()
            self.combo.addItem(CAMPAIGN_LABEL, CAMPAIGN)
            for name in store.sessions():
                self.combo.addItem(f"Session : {name}", name)
            self.combo.setCurrentIndex(max(0, self.combo.findData(current)))
        finally:
            self.combo.blockSignals(False)
        in_session = current != CAMPAIGN
        self.combo.setEnabled(running is None)
        self.combo.setToolTip(
            self.combo.toolTip() if running is None
            else f"La session « {running} » est en cours : ferme-la pour changer de contexte."
        )
        self.push_button.setEnabled(in_session)
        self.pull_button.setEnabled(in_session)
        self.merge_button.setEnabled(in_session and len(store.sessions()) > 1)

    def _on_activated(self, index: int) -> None:
        self.context_requested.emit(str(self.combo.itemData(index) or ""))


# =============================================================================
# Conflicts
# =============================================================================
class ConflictDialog(QDialog):
    """
    For every asset both sessions changed incompatibly, keep one whole version:
    the one of the session receiving the merge, or the one of the incoming session.
    """

    def __init__(self, store: VersionStore, conflicts: list[Conflict], ours_name: str,
                 theirs_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.conflicts = conflicts
        self.setWindowTitle("Résoudre les conflits de la fusion")
        self.resize(1100, 680)
        layout = QVBoxLayout(self)
        intro = QLabel(
            f"Ces assets ont été modifiés à la fois dans « {ours_name} » et dans « {theirs_name} ».\n"
            "Choisis pour chacun la version à garder (l'autre sera abandonnée pour cet asset)."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.table = QTableWidget(len(conflicts), 4)
        self.table.setHorizontalHeaderLabels(["Asset", "Champs en conflit", ours_name, theirs_name])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._groups: list[tuple[QRadioButton, QRadioButton]] = []
        for row, conflict in enumerate(conflicts):
            self.table.setItem(row, 0, QTableWidgetItem(asset_label(conflict.asset)))
            fields = ", ".join(conflict.fields) or ("supprimé d'un côté" if not (conflict.ours and conflict.theirs)
                                                     else "contenu différent")
            self.table.setItem(row, 1, QTableWidgetItem(fields))
            ours, theirs = QRadioButton("Garder"), QRadioButton("Garder")
            ours.setChecked(True)
            group = QButtonGroup(self)
            group.addButton(ours)
            group.addButton(theirs)
            self.table.setCellWidget(row, 2, ours)
            self.table.setCellWidget(row, 3, theirs)
            self._groups.append((ours, theirs))
        self.table.currentCellChanged.connect(lambda row, *_: self._show(row))

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.table)
        compare = QWidget()
        compare_layout = QHBoxLayout(compare)
        compare_layout.setContentsMargins(0, 0, 0, 0)
        self.ours_view = _VersionView(ours_name)
        self.theirs_view = _VersionView(theirs_name)
        compare_layout.addWidget(self.ours_view)
        compare_layout.addWidget(self.theirs_view)
        splitter.addWidget(compare)
        splitter.setSizes([260, 420])
        layout.addWidget(splitter, 1)

        row_buttons = QHBoxLayout()
        all_ours = QPushButton(f"Tout garder de « {ours_name} »")
        all_theirs = QPushButton(f"Tout garder de « {theirs_name} »")
        all_ours.clicked.connect(lambda: [pair[0].setChecked(True) for pair in self._groups])
        all_theirs.clicked.connect(lambda: [pair[1].setChecked(True) for pair in self._groups])
        row_buttons.addWidget(all_ours)
        row_buttons.addWidget(all_theirs)
        row_buttons.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Fusionner")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row_buttons.addWidget(buttons)
        layout.addLayout(row_buttons)
        if conflicts:
            self.table.setCurrentCell(0, 0)

    def choices(self) -> dict[str, str]:
        return {
            conflict.asset: "theirs" if pair[1].isChecked() else "ours"
            for conflict, pair in zip(self.conflicts, self._groups)
        }

    def _show(self, row: int) -> None:
        if not 0 <= row < len(self.conflicts):
            return
        conflict = self.conflicts[row]
        self.ours_view.show_version(self.store, conflict.asset, conflict.ours)
        self.theirs_view.show_version(self.store, conflict.asset, conflict.theirs)


class _VersionView(QWidget):
    """One side of a conflict: the JSON of an asset, or the preview of a map."""

    def __init__(self, title: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        heading = QLabel(title)
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.picture.setVisible(False)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(self.picture)
        layout.addWidget(self.text, 1)

    def show_version(self, store: VersionStore, asset: str, files: dict[str, str]) -> None:
        self.picture.clear()
        self.picture.setVisible(False)
        if not files:
            self.text.setPlainText("(supprimé dans cette version)")
            return
        main = files.get(asset) or files.get(f"{asset}/map.json") or next(iter(files.values()))
        preview = files.get(f"{asset}/preview.png")
        if preview:
            pixmap = QPixmap()
            if pixmap.loadFromData(store.read_object(preview)):
                self.picture.setPixmap(pixmap.scaled(
                    360, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                ))
                self.picture.setVisible(True)
        try:
            data = json.loads(store.read_object(main).decode("utf-8"))
            self.text.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))
        except (ValueError, UnicodeDecodeError):
            self.text.setPlainText(f"{len(files)} fichier(s)")


# =============================================================================
# History
# =============================================================================
class HistoryDialog(QDialog):
    """The points of one session (or of the campaign), newest first."""

    branch_requested = Signal(str)  # point id
    reset_requested = Signal(str)   # point id

    def __init__(self, store: VersionStore, session: str, *, can_reset: bool = True,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.session = session
        self.setWindowTitle(f"Historique — {context_label(session)}")
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        self.points: list[Point] = store.history(session)
        self.table = QTableWidget(len(self.points), 3)
        self.table.setHorizontalHeaderLabels(["Date", "Point", "Événement de session"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for row, point in enumerate(self.points):
            self.table.setItem(row, 0, QTableWidgetItem(point.time.replace("T", " ")[:19]))
            self.table.setItem(row, 1, QTableWidgetItem(point.message))
            seq = point.meta.get("session_seq")
            self.table.setItem(row, 2, QTableWidgetItem("" if seq is None else f"#{seq}"))
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.branch_button = QPushButton("Nouvelle session depuis ce point…")
        self.reset_button = QPushButton("Revenir à ce point…")
        self.reset_button.setToolTip(
            "La session revient à ce point : tout ce qui a suivi est abandonné dans cette session."
            if can_reset else "Ferme la session en cours pour revenir en arrière."
        )
        self.reset_button.setEnabled(False)
        self._can_reset = can_reset
        close = QPushButton("Fermer")
        buttons.addWidget(self.branch_button)
        buttons.addWidget(self.reset_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        close.clicked.connect(self.reject)
        self.branch_button.clicked.connect(lambda: self._emit(self.branch_requested))
        self.reset_button.clicked.connect(lambda: self._emit(self.reset_requested))
        self.table.currentCellChanged.connect(lambda row, *_: self._sync(row))
        if self.points:
            self.table.setCurrentCell(0, 0)

    def selected_point(self) -> Optional[Point]:
        row = self.table.currentRow()
        return self.points[row] if 0 <= row < len(self.points) else None

    def _sync(self, row: int) -> None:
        self.branch_button.setEnabled(0 <= row < len(self.points))
        self.reset_button.setEnabled(self._can_reset and 0 < row < len(self.points))

    def _emit(self, signal) -> None:
        point = self.selected_point()
        if point is not None:
            signal.emit(point.id)
            self.accept()
