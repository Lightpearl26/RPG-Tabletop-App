# -*- coding: utf-8 -*-
"""Session Entity palette; drag an entry onto the map to place its token."""
from __future__ import annotations

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from ...runtime.session import SessionRuntime


TOKEN_ENTITY_MIME = "application/x-rpg-tabletop-session-entity"


class _DraggableEntityList(QListWidget):
    def startDrag(self, supported_actions) -> None:  # noqa: N802 - Qt API
        item = self.currentItem()
        if item is None:
            return
        entity_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not entity_id:
            return
        mime = QMimeData()
        mime.setData(TOKEN_ENTITY_MIME, entity_id.encode("utf-8"))
        mime.setText(entity_id)
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class TokenManagerPanel(QWidget):
    """Lists only Entities loaded in the current SessionRuntime."""

    def __init__(self, runtime: SessionRuntime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.setObjectName("RuntimePanel")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(7)

        title = QLabel("TOKENS DE SESSION")
        title.setObjectName("RuntimeHeading")
        root.addWidget(title)
        hint = QLabel("Fais glisser une entité sur la map pour placer ou déplacer son token.")
        hint.setObjectName("RuntimeMuted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher une entité…")
        self.search.textChanged.connect(self._apply_filter)
        root.addWidget(self.search)

        self.list = _DraggableEntityList()
        self.list.setDragEnabled(True)
        self.list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list.setDragDropMode(QListWidget.DragDropMode.DragOnly)
        self.list.setDefaultDropAction(Qt.DropAction.CopyAction)
        root.addWidget(self.list, 1)

        self.summary = QLabel("0 entité")
        self.summary.setObjectName("RuntimeMuted")
        root.addWidget(self.summary)
        self.refresh()

    def refresh(self) -> None:
        selected_id = None
        if self.list.currentItem() is not None:
            selected_id = self.list.currentItem().data(Qt.ItemDataRole.UserRole)
        self.list.clear()
        tokens = self.runtime.active_map.tokens if self.runtime.active_map is not None else []
        token_by_entity = {token.entity_id: token for token in tokens}
        entities = sorted(
            self.runtime.entities.items(),
            key=lambda pair: pair[1].name.casefold(),
        )
        for entity_id, entity in entities:
            token = token_by_entity.get(entity_id)
            status = "Sur la map" if token is not None else "À placer"
            item = QListWidgetItem(f"{entity.name}   ·   {status}")
            item.setData(Qt.ItemDataRole.UserRole, entity_id)
            item.setToolTip(
                f"{entity.name}\n{'Token présent sur la map' if token else 'Glisser sur la map pour créer un token'}"
            )
            self.list.addItem(item)
            if entity_id == selected_id:
                self.list.setCurrentItem(item)
        count = len(entities)
        placed = len(token_by_entity)
        self.summary.setText(f"{count} entité(s) dans la session · {placed} token(s) sur la map")
        self._apply_filter(self.search.text())

    def _apply_filter(self, text: str) -> None:
        query = str(text or "").strip().casefold()
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setHidden(bool(query) and query not in item.text().casefold())
