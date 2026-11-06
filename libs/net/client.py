# -*- coding: utf-8 -*-
"""Player-side asynchronous TLS client."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import ssl
from typing import Any, Callable, Iterable

from .assets import AssetReceiver
from .commands import ActionIntent, ClientCommand, ServerCommand
from .config import ClientSettings
from .connection import NetworkConnection
from .protocol import (
    PROTOCOL_VERSION, ChatMessage, CommandMessage, ErrorMessage, NetworkMessage, PingMessage,
)
from .tls import build_client_ssl_context, peer_certificate_fingerprint, verify_peer_fingerprint

MessageCallback = Callable[[NetworkMessage], None]


class RPGNetworkClient:
    def __init__(
        self,
        settings: ClientSettings,
        *,
        asset_cache: str | None = None,
        on_message: MessageCallback | None = None,
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        self.settings = settings
        self.on_message = on_message
        self.on_disconnect = on_disconnect
        self.asset_receiver = AssetReceiver(asset_cache) if asset_cache else None
        self.connection: NetworkConnection | None = None
        self.is_connected = False
        self.is_authenticated = False
        self.bound_entity_id: str | None = None
        self.peer_fingerprint: str = ""
        self.last_projection: dict[str, Any] = {}
        self.players: dict[str, int | None] = {}
        self._read_task: asyncio.Task[None] | None = None
        self._reply_queues: dict[str, asyncio.Queue[NetworkMessage]] = {}

    async def connect(self, *, auth_timeout: float = 15.0) -> CommandMessage:
        if self.is_connected:
            raise RuntimeError("Client is already connected")
        pinning = bool(
            not self.settings.ca_file
            and (self.settings.expected_fingerprint or self.settings.trust_on_first_use)
        )
        context = build_client_ssl_context(ca_file=self.settings.ca_file, pinning=pinning)
        reader, writer = await asyncio.open_connection(
            self.settings.host,
            int(self.settings.port),
            ssl=context,
            server_hostname=(self.settings.server_hostname if not pinning else None),
            limit=self.settings.stream_line_limit_bytes,
        )
        self.peer_fingerprint = peer_certificate_fingerprint(writer)
        try:
            if self.settings.expected_fingerprint:
                verify_peer_fingerprint(writer, self.settings.expected_fingerprint)
        except Exception:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            raise
        self.connection = NetworkConnection(reader, writer)
        self.is_connected = True
        self._read_task = asyncio.create_task(self._read_loop())

        auth = CommandMessage(ClientCommand.AUTH.value, {
            "username": self.settings.username,
            "password": self.settings.password,
            "entity_id": self.settings.requested_entity_id,
        })
        self._reply_queues[auth.message_id] = asyncio.Queue()
        await self.connection.send(auth)
        reply = await self.wait_for_reply(auth.message_id, timeout=auth_timeout)
        if isinstance(reply, ErrorMessage):
            await self.disconnect()
            raise PermissionError(reply.reason)
        if not isinstance(reply, CommandMessage) or reply.command != ServerCommand.AUTH_OK.value:
            await self.disconnect()
            raise RuntimeError("Unexpected authentication response")
        self.is_authenticated = True
        raw_entity = reply.args.get("entity_id")
        self.bound_entity_id = str(raw_entity) if raw_entity else None
        return reply

    async def disconnect(self) -> None:
        if self.connection is not None and self.connection.is_active:
            with suppress(Exception):
                await self.connection.send(CommandMessage(ClientCommand.QUIT.value, {}))
        if self._read_task is not None:
            self._read_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._read_task
        if self.connection is not None:
            await self.connection.close()
        self.connection = None
        self.is_connected = False
        self.is_authenticated = False
        self._read_task = None

    async def _read_loop(self) -> None:
        assert self.connection is not None
        try:
            while self.connection.is_active:
                message = await self.connection.receive()
                if message.protocol_version != PROTOCOL_VERSION:
                    raise RuntimeError(
                        f"Protocol mismatch: server={message.protocol_version}, client={PROTOCOL_VERSION}"
                    )
                if isinstance(message, PingMessage) and not message.pong:
                    await self.connection.send(PingMessage(
                        message.ping_id, pong=True, reply_to=message.message_id
                    ))
                    continue
                if message.reply_to and message.reply_to in self._reply_queues:
                    await self._reply_queues[message.reply_to].put(message)
                self._apply_local_state(message)
                if self.on_message is not None:
                    self.on_message(message)
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError, RuntimeError, ValueError, ssl.SSLError):
            pass
        finally:
            self.is_connected = False
            self.is_authenticated = False
            if self.on_disconnect is not None:
                self.on_disconnect()

    def _apply_local_state(self, message: NetworkMessage) -> None:
        if not isinstance(message, CommandMessage):
            return
        if message.command == ServerCommand.ENTITY_BOUND.value:
            self.bound_entity_id = str(message.args.get("entity_id", "")) or None
        elif message.command == ServerCommand.ENTITY_UNBOUND.value:
            self.bound_entity_id = None
        elif message.command == ServerCommand.RUNTIME_SYNC.value:
            projection = message.args.get("projection", {})
            if isinstance(projection, dict):
                self.last_projection = projection
        elif message.command == ServerCommand.PLAYER_LIST.value:
            for name in message.args.get("players", []):
                self.players[str(name)] = None
        elif message.command == ServerCommand.PLAYER_JOINED.value:
            self.players[str(message.args.get("username", ""))] = None
        elif message.command == ServerCommand.PLAYER_LEFT.value:
            self.players.pop(str(message.args.get("username", "")), None)
        elif message.command == ServerCommand.PLAYER_PINGS.value:
            for item in message.args.get("players", []):
                if isinstance(item, dict):
                    name = str(item.get("username", ""))
                    self.players[name] = item.get("ping_ms")
        elif message.command == ServerCommand.ASSET_CHUNK.value and self.asset_receiver is not None:
            self.asset_receiver.receive_chunk(message.args)

    async def send(self, message: NetworkMessage) -> str:
        if self.connection is None or not self.connection.is_active:
            raise RuntimeError("Client is not connected")
        await self.connection.send(message)
        return message.message_id

    async def send_command(self, command: str, args: dict[str, Any] | None = None) -> str:
        message = CommandMessage(command, args)
        self._reply_queues.setdefault(message.message_id, asyncio.Queue())
        return await self.send(message)

    async def wait_for_reply(
        self,
        request_id: str,
        *,
        commands: Iterable[str] | None = None,
        timeout: float | None = 30.0,
    ) -> NetworkMessage:
        queue = self._reply_queues.setdefault(request_id, asyncio.Queue())
        accepted = set(commands or [])
        while True:
            awaitable = queue.get()
            message = await asyncio.wait_for(awaitable, timeout) if timeout is not None else await awaitable
            if not accepted:
                return message
            if isinstance(message, CommandMessage) and message.command in accepted:
                return message
            if isinstance(message, ErrorMessage):
                return message

    async def request_once(
        self, command: str, args: dict[str, Any] | None = None, *, timeout: float = 30.0
    ) -> NetworkMessage:
        request_id = await self.send_command(command, args)
        return await self.wait_for_reply(request_id, timeout=timeout)

    async def send_chat(self, text: str) -> str:
        return await self.send(ChatMessage(self.settings.username, str(text)))

    async def request_runtime_sync(self) -> str:
        return await self.send_command(ClientCommand.RUNTIME_SYNC_REQUEST.value, {})

    async def request_stat_check(self, stat: str) -> str:
        return await self.send_command(ClientCommand.STAT_CHECK_REQUEST.value, {"stat": str(stat)})

    async def request_move(self, x: float, y: float) -> str:
        return await self.send_command(ClientCommand.MOVE_REQUEST.value, {"target": [float(x), float(y)]})

    async def request_action(self, intent: ActionIntent) -> str:
        return await self.send_command(ClientCommand.ACTION_REQUEST.value, intent.to_dict())

    async def cancel_pending(self, approval_id: str) -> str:
        return await self.send_command(ClientCommand.ACTION_CANCEL_REQUEST.value, {"approval_id": approval_id})

    async def inspect_token(self, token_id: str) -> str:
        return await self.send_command(ClientCommand.TOKEN_INSPECT_REQUEST.value, {"token_id": str(token_id)})

    async def request_end_turn(self) -> str:
        return await self.send_command(ClientCommand.END_TURN_REQUEST.value, {})

    async def request_asset_manifest(self, namespaces: list[str] | None = None) -> str:
        return await self.send_command(
            ClientCommand.ASSET_MANIFEST_REQUEST.value,
            {"namespaces": namespaces} if namespaces is not None else {},
        )

    async def request_assets(self, items: list[dict[str, str]]) -> str:
        return await self.send_command(ClientCommand.ASSET_REQUEST.value, {"items": items})
