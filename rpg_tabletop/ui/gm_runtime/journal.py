# -*- coding: utf-8 -*-
from __future__ import annotations

from PySide6.QtWidgets import QAbstractItemView, QComboBox, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from rpg_tabletop.session.session import SessionEventType, SessionRuntime


class JournalPanel(QWidget):
    def __init__(self, runtime: SessionRuntime, parent=None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.setObjectName("RuntimePanel")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        top = QHBoxLayout()
        title = QLabel("JOURNAL DE SESSION")
        title.setObjectName("RuntimeHeading")
        top.addWidget(title)
        top.addStretch(1)
        self.filter = QComboBox()
        self.filter.addItem("Tous", "")
        for event_type in SessionEventType:
            self.filter.addItem(event_type.value, event_type.value)
        self.filter.currentIndexChanged.connect(self.refresh)
        top.addWidget(self.filter)
        root.addLayout(top)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["#", "Time", "Type", "Message"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 170)
        root.addWidget(self.table, 1)
        self.refresh()

    def refresh(self) -> None:
        selected = str(self.filter.currentData() or "")
        events = [e for e in self.runtime.session.events if not selected or e.type.value == selected]
        self.table.setRowCount(len(events))
        for row, event in enumerate(events):
            values = [str(event.seq), event.timestamp.astimezone().strftime("%H:%M:%S"), event.type.value, event.message]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
        if events:
            self.table.scrollToBottom()
