# -*- coding: utf-8 -*-
"""Qt-safe controller around the asyncio Player network client."""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Coroutine

from PySide6.QtCore import QObject, Signal

from rpg_tabletop.net import AsyncioLoopThread, ClientCommand, ClientSettings, RPGNetworkClient
from rpg_tabletop.net.protocol import CommandMessage


class PlayerNetworkController(QObject):
    """Owns the network client and keeps all asyncio work off the Qt thread."""

    message_received = Signal(object)
    connection_changed = Signal(bool, str)
    operation_finished = Signal(str, object, object)

    def __init__(self, asset_cache: str | Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cache_root = Path(asset_cache)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.loop_thread: AsyncioLoopThread | None = None
        self.client: RPGNetworkClient | None = None
        self._intentional_disconnect = False
        self._asset_request_lock: asyncio.Lock | None = None
        self._generation = 0

    @property
    def is_connected(self) -> bool:
        return bool(self.client and self.client.is_connected and self.client.is_authenticated)

    @property
    def bound_entity_id(self) -> str | None:
        return self.client.bound_entity_id if self.client is not None else None

    def connect(self, settings: ClientSettings) -> None:
        if self.loop_thread is not None:
            raise RuntimeError("A network connection attempt is already active")
        self._generation += 1
        generation = self._generation
        self._intentional_disconnect = False
        self.loop_thread = AsyncioLoopThread(name="RPGTabletop-Player-Network")
        self.loop_thread.start()
        self.client = RPGNetworkClient(
            settings,
            asset_cache=str(self.cache_root),
            on_message=self.message_received.emit,
            on_disconnect=lambda: self._on_disconnect(generation),
        )
        self._submit("connect", self._connect_with_timeout())

    async def _connect_with_timeout(self):
        if self.client is None:
            raise RuntimeError("Client non initialisé.")
        self._asset_request_lock = asyncio.Lock()
        try:
            return await asyncio.wait_for(self.client.connect(), timeout=25.0)
        except Exception:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            raise

    def _on_disconnect(self, generation: int) -> None:
        if generation == self._generation and not self._intentional_disconnect:
            self.connection_changed.emit(False, "Connexion interrompue")

    def _submit(self, label: str, coroutine: Coroutine[Any, Any, Any]) -> Future[Any] | None:
        if self.loop_thread is None or not self.loop_thread.is_running:
            coroutine.close()
            self.operation_finished.emit(label, None, RuntimeError("Le client réseau n'est pas connecté."))
            return None
        generation = self._generation
        future = self.loop_thread.submit(coroutine)

        def _done(completed: Future[Any]) -> None:
            if generation != self._generation:
                return
            try:
                result = completed.result()
                error = None
            except BaseException as exc:  # forwarded to the GUI thread, including task cancellation
                result, error = None, exc
            self.operation_finished.emit(label, result, error)

        future.add_done_callback(_done)
        return future

    def _client_call(self, label: str, method_name: str, *args: Any) -> None:
        if self.client is None:
            self.operation_finished.emit(label, None, RuntimeError("Client non initialisé."))
            return
        method = getattr(self.client, method_name)
        self._submit(label, method(*args))

    def _send_command(self, label: str, command: ClientCommand, args: dict[str, Any] | None = None) -> None:
        if self.client is None:
            self.operation_finished.emit(label, None, RuntimeError("Client non initialisé."))
            return
        # Use send() rather than send_command(): the GUI consumes replies through
        # message_received, so no per-request asyncio reply queue is needed.
        self._submit(label, self.client.send(CommandMessage(command.value, args or {})))

    def request_runtime_sync(self) -> None:
        if self.client is not None and self.client.bound_entity_id:
            self._send_command("runtime_sync", ClientCommand.RUNTIME_SYNC_REQUEST)

    def request_move(self, x: float, y: float) -> None:
        self._send_command("move", ClientCommand.MOVE_REQUEST, {"target": [float(x), float(y)]})

    def request_stat_check(self, stat: str) -> None:
        self._send_command("stat_check", ClientCommand.STAT_CHECK_REQUEST, {"stat": str(stat)})

    def request_action(self, intent) -> None:
        self._send_command("action", ClientCommand.ACTION_REQUEST, intent.to_dict())

    def send_initiative(self, value: int) -> None:
        self._send_command("initiative", ClientCommand.INITIATIVE_ROLL, {"value": int(value)})

    def send_dice(self, approval_id: str, key: str, values: list[int]) -> None:
        self._send_command("dice", ClientCommand.DICE_ROLL, {
            "approval_id": str(approval_id), "key": str(key), "values": [int(value) for value in values],
        })

    def request_end_turn(self) -> None:
        self._send_command("end_turn", ClientCommand.END_TURN_REQUEST)

    def inspect_token(self, token_id: str) -> None:
        self._send_command("inspect", ClientCommand.TOKEN_INSPECT_REQUEST, {"token_id": str(token_id)})

    def cancel_pending(self, approval_id: str) -> None:
        self._send_command("cancel_pending", ClientCommand.ACTION_CANCEL_REQUEST, {"approval_id": str(approval_id)})

    def send_chat(self, message: str) -> None:
        self._client_call("chat", "send_chat", str(message))

    async def _request_assets_batched(self, items: list[dict[str, str]]) -> None:
        if self.client is None:
            raise RuntimeError("Client non initialisé.")
        lock = self._asset_request_lock
        if lock is None:
            raise RuntimeError("Le client n'a pas terminé sa connexion.")
        # The server accepts at most 64 assets per request and rate-limits
        # incoming commands to five per second. Serialize and space batches.
        async with lock:
            for index in range(0, len(items), 64):
                batch = items[index:index + 64]
                await self.client.send(CommandMessage(
                    ClientCommand.ASSET_REQUEST.value,
                    {"items": batch},
                ))
                if index + 64 < len(items):
                    await asyncio.sleep(0.30)

    def request_assets(self, items: list[dict[str, str]]) -> None:
        if items:
            self._submit("assets", self._request_assets_batched(items))

    def shutdown(self) -> None:
        """Close the client cleanly; called from the Qt thread during window close."""
        self._generation += 1
        self._intentional_disconnect = True
        loop_thread = self.loop_thread
        client = self.client
        if loop_thread is not None and loop_thread.is_running and client is not None:
            try:
                future = loop_thread.submit(client.disconnect())
                future.result(timeout=3.0)
            except Exception:
                pass
            loop_thread.stop(timeout=3.0)
        elif loop_thread is not None:
            loop_thread.stop(timeout=1.0)
        self.client = None
        self.loop_thread = None
        self._asset_request_lock = None
        self.connection_changed.emit(False, "Déconnecté")
