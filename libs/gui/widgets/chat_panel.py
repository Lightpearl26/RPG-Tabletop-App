# -*- coding: utf-8 -*-
"""Small shared text chat panel for runtime windows."""
from __future__ import annotations

from html import escape

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget


class ChatPanel(QWidget):
    send_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RuntimePanel")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(7)
        self.history = QTextBrowser()
        self.history.setOpenExternalLinks(False)
        root.addWidget(self.history, 1)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Écrire un message aux joueurs…")
        self.send_button = QPushButton("Envoyer")
        self.input.returnPressed.connect(self._send)
        self.send_button.clicked.connect(self._send)
        row.addWidget(self.input, 1)
        row.addWidget(self.send_button)
        root.addLayout(row)
        self.set_connected(False)

    def set_connected(self, connected: bool) -> None:
        self.input.setEnabled(bool(connected))
        self.send_button.setEnabled(bool(connected))

    def _send(self) -> None:
        message = self.input.text().strip()
        if not message:
            return
        self.input.clear()
        self.send_requested.emit(message)

    def append_message(self, sender: str, message: str, *, system: bool = False) -> None:
        if system:
            color = "#98a2b1"
        elif sender.casefold() in {"mj", "gm"}:
            color = "#cba6f7"
        else:
            color = "#8ec5ff"
        self.history.append(
            f'<span style="color:{color};"><b>{escape(str(sender))}</b></span> '
            f'<span style="color:#e5e9ef;">{escape(str(message))}</span>'
        )
