# -*- coding: utf-8 -*-
from __future__ import annotations

from PySide6.QtWidgets import QAbstractItemView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from ...runtime.session import SessionRuntime


class EntityRegistryPanel(QWidget):
    def __init__(self, runtime: SessionRuntime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.setObjectName("RuntimePanel")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        title = QLabel("ENTITÉS DE LA SESSION")
        title.setObjectName("RuntimeHeading")
        root.addWidget(title)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Nom", "HP", "STA", "AGI", "PER", "ID"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)
        self.refresh()

    def refresh(self) -> None:
        entities = sorted(self.runtime.entities.items(), key=lambda item: item[1].name.casefold())
        self.table.setRowCount(len(entities))
        for row, (entity_id, entity) in enumerate(entities):
            values = [entity.name, entity.hp, entity.stamina, entity.agi, entity.per, entity_id]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(str(value)))
