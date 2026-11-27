# -*- coding: utf-8 -*-
"""Qt-safe bridge for callbacks coming from the background asyncio network loop."""
from __future__ import annotations

from concurrent.futures import Future
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot


class RuntimeExecutor(QObject):
    """
    Runs functions on the Qt thread for the network server.

    The SessionRuntime and the gateway are changed by the GM interface on the
    Qt thread; the server sends every player request, projection and asset
    check here (RPGNetworkServer.run_on_runtime) so that nothing races.
    """

    _call = Signal(object, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def submit(self, function: Callable[[], Any]) -> Future:
        future: Future = Future()
        if QThread.currentThread() is self.thread():
            self._run(function, future)
        else:
            self._call.emit(function, future)
        return future

    @Slot(object, object)
    def _run(self, function: Callable[[], Any], future: Future) -> None:
        if not future.set_running_or_notify_cancel():
            return  # the network side gave up (connection closed)
        try:
            future.set_result(function())
        except BaseException as exc:  # noqa: BLE001 - handed to the network side
            future.set_exception(exc)


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
