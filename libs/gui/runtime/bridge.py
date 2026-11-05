# -*- coding: utf-8 -*-
"""Qt-safe bridge for callbacks coming from the background asyncio network loop."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class RuntimeNetworkBridge(QObject):
    log_received = Signal(str)
    pending_created = Signal(object)
    pending_updated = Signal(object)
    client_connected = Signal(str)
    client_disconnected = Signal(str)
    chat_received = Signal(str, str)
    async_done = Signal(str, object, object)
    # username, entity id, d20 result sent by a player
    initiative_received = Signal(str, str, int)

    def on_log(self, message: str) -> None:
        self.log_received.emit(str(message))

    def on_pending(self, pending) -> None:
        self.pending_created.emit(pending)

    def on_pending_updated(self, pending) -> None:
        self.pending_updated.emit(pending)

    def on_client_connected(self, username: str) -> None:
        self.client_connected.emit(str(username))

    def on_client_disconnected(self, username: str) -> None:
        self.client_disconnected.emit(str(username))

    def on_chat(self, sender: str, message: str) -> None:
        self.chat_received.emit(str(sender), str(message))

    def on_initiative(self, username: str, entity_id: str, value: int) -> None:
        self.initiative_received.emit(str(username), str(entity_id), int(value))
