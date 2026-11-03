# -*- coding: utf-8 -*-
"""GM-hosted authoritative TCP/TLS server."""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
import hmac
import socket
from time import monotonic
from typing import Any, Callable

from .assets import AssetCatalog
from .commands import ClientCommand, ServerCommand
from .config import ServerSettings
from .connection import NetworkConnection
from .firewall import FirewallResult, ensure_windows_firewall_rule
from .gateway import GatewayResult, RuntimeNetworkGateway
from .models import PendingRequest
from .protocol import (
    PROTOCOL_VERSION, ChatMessage, CommandMessage, ErrorMessage, ErrorType,
    NetworkMessage, PingMessage,
)
from .relay import RelayEndpoint, VPSRelay
from .tls import build_server_ssl_context, certificate_fingerprint, ensure_server_certificate
from .upnp import UPnPPortMapping, UPnPStatus

LogCallback = Callable[[str], None]
PendingCallback = Callable[[PendingRequest], None]
ClientCallback = Callable[[str], None]
ChatCallback = Callable[[str, str], None]


@dataclass(slots=True)
class ServerClientSession:
    username: str
    peer: tuple[Any, ...]
    connection: NetworkConnection
    bound_entity_id: str | None = None
    connected_at: float = field(default_factory=monotonic)
    last_pong_time: float = field(default_factory=monotonic)
    last_ping_time: float = 0.0
    last_ping_id: int | None = None
    ping_ms: int | None = None
    recent_messages: deque[float] = field(default_factory=deque)


class RPGNetworkServer:
    def __init__(
        self,
        settings: ServerSettings,
        *,
        gateway: RuntimeNetworkGateway | None = None,
        asset_catalog: AssetCatalog | None = None,
        player_bindings: dict[str, str] | None = None,
        on_log: LogCallback | None = None,
        on_pending: PendingCallback | None = None,
        on_pending_updated: PendingCallback | None = None,
        on_client_connected: ClientCallback | None = None,
        on_client_disconnected: ClientCallback | None = None,
        on_chat: ChatCallback | None = None,
    ) -> None:
        settings.validate()
        self.settings = settings
        self.gateway = gateway
        self.asset_catalog = asset_catalog
        self.player_bindings = dict(player_bindings or {})
        self.on_log = on_log or (lambda _message: None)
        self.on_pending = on_pending
        self.on_pending_updated = on_pending_updated
        self.on_client_connected = on_client_connected
        self.on_client_disconnected = on_client_disconnected
        self.on_chat = on_chat

        self.clients: dict[str, ServerClientSession] = {}
        self.server: asyncio.AbstractServer | None = None
        self.is_running = False
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._client_tasks: set[asyncio.Task[Any]] = set()
        self._upnp: UPnPPortMapping | None = None
        self.upnp_status: UPnPStatus | None = None
        self.firewall_status: FirewallResult | None = None
        self.relay = VPSRelay(
            settings.relay, local_port=settings.port,
            on_log=lambda message: self._log(f"[relay] {message}"),
        )
        self.relay_endpoint: RelayEndpoint | None = None
        cert, _key = ensure_server_certificate(settings.tls)
        self.certificate_fingerprint = certificate_fingerprint(cert)

        if self.gateway is not None:
            previous = self.gateway.on_pending
            def pending_hook(pending: PendingRequest) -> None:
                if previous is not None:
                    previous(pending)
                if self.on_pending is not None:
                    self.on_pending(pending)
            self.gateway.on_pending = pending_hook

    def _log(self, message: str) -> None:
        self.on_log(str(message))

    @staticmethod
    def local_ip() -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
        except OSError:
            return "127.0.0.1"
        finally:
            sock.close()

    def connection_info(self) -> dict[str, Any]:
        return {
            "lan": {"host": self.local_ip(), "port": self.settings.port},
            "upnp": None if self.upnp_status is None else {
                "mapped": self.upnp_status.mapped,
                "external_address": self.upnp_status.external_address,
                "port": self.settings.port,
                "behind_cgnat": self.upnp_status.behind_cgnat,
            },
            "relay": None if self.relay_endpoint is None else {
                "host": self.relay_endpoint.host,
                "port": self.relay_endpoint.port,
            },
            "tls_fingerprint_sha256": self.certificate_fingerprint,
            "protocol_version": PROTOCOL_VERSION,
        }

    async def start(self) -> None:
        if self.is_running:
            return
        if self.settings.enable_firewall_setup:
            self.firewall_status = await asyncio.to_thread(
                ensure_windows_firewall_rule, self.settings.port
            )
            self._log(f"[firewall] {self.firewall_status.message}")
            if self.firewall_status.manual_command:
                self._log(f"[firewall] manual: {self.firewall_status.manual_command}")
        if self.settings.enable_upnp:
            self._upnp = UPnPPortMapping(self.settings.port)
            self.upnp_status = await asyncio.to_thread(self._upnp.start)
            self._log(f"[upnp] {self.upnp_status.message}")
            if self.upnp_status.mapped and self.upnp_status.behind_cgnat:
                self._log("[upnp] CGNAT/private WAN detected; external players should use the VPS relay")

        context = build_server_ssl_context(self.settings.tls)
        self.server = await asyncio.start_server(
            self._accept_connection,
            self.settings.host,
            self.settings.port,
            ssl=context,
            limit=self.settings.stream_line_limit_bytes,
        )
        self.is_running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._log(
            f"[server] TLS 1.3 listening on {self.settings.host}:{self.settings.port} "
            f"fingerprint={self.certificate_fingerprint}"
        )
        if self.settings.relay.enabled and self.settings.relay.host:
            try:
                self.relay_endpoint = await self.relay.start()
            except (OSError, RuntimeError, ValueError) as exc:
                self._log(f"[relay] unavailable: {exc}")

    async def stop(self) -> None:
        if not self.is_running and self.server is None:
            return
        self.is_running = False
        await self.relay.stop()
        self.relay_endpoint = None
        if self._upnp is not None:
            await asyncio.to_thread(self._upnp.stop)
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        for session in list(self.clients.values()):
            await session.connection.close()
        self.clients.clear()
        for task in list(self._client_tasks):
            task.cancel()
        if self._client_tasks:
            await asyncio.gather(*self._client_tasks, return_exceptions=True)
        self._client_tasks.clear()
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        self._log("[server] stopped")

    async def _accept_connection(self, reader, writer) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._client_tasks.add(task)
        connection = NetworkConnection(reader, writer)
        peer = writer.get_extra_info("peername") or ("?", 0)
        username: str | None = None
        try:
            auth = await asyncio.wait_for(connection.receive(), timeout=15.0)
            if auth.protocol_version != PROTOCOL_VERSION:
                await connection.send(ErrorMessage(
                    ErrorType.VERSION_MISMATCH,
                    f"Protocol {PROTOCOL_VERSION} required; client sent {auth.protocol_version}",
                    reply_to=auth.message_id,
                ))
                return
            if not isinstance(auth, CommandMessage) or auth.command != ClientCommand.AUTH.value:
                await connection.send(ErrorMessage(
                    ErrorType.AUTH_REJECTED, "AUTH must be the first message", reply_to=auth.message_id
                ))
                return
            username = await self._authenticate(connection, peer, auth)
            if username is None:
                return
            await self._client_loop(self.clients[username])
        except (asyncio.TimeoutError, ConnectionError, OSError, RuntimeError, ValueError) as exc:
            self._log(f"[connect] peer={peer} error={exc}")
        finally:
            if username is not None:
                await self._drop_client(username, announce=True)
            else:
                await connection.close()
            if task is not None:
                self._client_tasks.discard(task)

    async def _authenticate(self, connection: NetworkConnection, peer, request: CommandMessage) -> str | None:
        username = str(request.args.get("username", "")).strip()
        password = str(request.args.get("password", ""))
        requested_entity = str(request.args.get("entity_id", "")).strip()
        if not username or len(username) > 64:
            await connection.send(ErrorMessage(ErrorType.AUTH_REJECTED, "Invalid username", reply_to=request.message_id))
            return None
        if username.lower() in {"admin", "gm", "server"}:
            await connection.send(ErrorMessage(ErrorType.AUTH_REJECTED, "Reserved username", reply_to=request.message_id))
            return None
        if username in self.clients:
            await connection.send(ErrorMessage(ErrorType.AUTH_REJECTED, "Username already in use", reply_to=request.message_id))
            return None
        if not hmac.compare_digest(password.encode("utf-8"), self.settings.password.encode("utf-8")):
            await connection.send(ErrorMessage(ErrorType.AUTH_REJECTED, "Invalid password", reply_to=request.message_id))
            return None

        bound = self.player_bindings.get(username)
        if bound and requested_entity and requested_entity != bound:
            await connection.send(ErrorMessage(
                ErrorType.AUTH_REJECTED, "Requested Entity does not match the GM binding", reply_to=request.message_id
            ))
            return None
        session = ServerClientSession(username, tuple(peer), connection, bound_entity_id=bound)
        self.clients[username] = session
        if bound and self.gateway is not None:
            try:
                self.gateway.bind(username, bound)
            except (KeyError, ValueError) as exc:
                self.clients.pop(username, None)
                await connection.send(ErrorMessage(ErrorType.AUTH_REJECTED, str(exc), reply_to=request.message_id))
                return None

        await connection.send(CommandMessage(
            ServerCommand.AUTH_OK.value,
            {
                "username": username,
                "entity_id": bound,
                "protocol_version": PROTOCOL_VERSION,
                "server_fingerprint": self.certificate_fingerprint,
            },
            reply_to=request.message_id,
        ))
        already = [name for name in self.clients if name != username]
        await connection.send(CommandMessage(ServerCommand.PLAYER_LIST.value, {"players": already}))
        await self.broadcast(
            CommandMessage(ServerCommand.PLAYER_JOINED.value, {"username": username}),
            exclude={username},
        )
        if bound and self.gateway is not None:
            await connection.send(CommandMessage(
                ServerCommand.RUNTIME_SYNC.value,
                {"projection": self.gateway.projection_for(username)},
            ))
        self._log(f"[auth] accepted username={username!r} peer={peer} entity={bound!r}")
        if self.on_client_connected is not None:
            self.on_client_connected(username)
        return username

    async def bind_player(self, username: str, entity_id: str) -> None:
        if username not in self.clients:
            raise KeyError(f"Player is not connected: {username}")
        if self.gateway is None:
            raise RuntimeError("No RuntimeNetworkGateway is connected")
        self.gateway.bind(username, entity_id)
        self.player_bindings[username] = entity_id
        session = self.clients[username]
        session.bound_entity_id = entity_id
        await session.connection.send(CommandMessage(ServerCommand.ENTITY_BOUND.value, {"entity_id": entity_id}))
        await session.connection.send(CommandMessage(
            ServerCommand.RUNTIME_SYNC.value, {"projection": self.gateway.projection_for(username)}
        ))

    async def unbind_player(self, username: str) -> None:
        if self.gateway is not None:
            self.gateway.unbind(username)
        self.player_bindings.pop(username, None)
        session = self.clients.get(username)
        if session is not None:
            session.bound_entity_id = None
            await session.connection.send(CommandMessage(ServerCommand.ENTITY_UNBOUND.value, {}))

    def _rate_limited(self, session: ServerClientSession) -> bool:
        now = monotonic()
        cutoff = now - self.settings.rate_limit_window
        while session.recent_messages and session.recent_messages[0] < cutoff:
            session.recent_messages.popleft()
        session.recent_messages.append(now)
        return len(session.recent_messages) > self.settings.rate_limit_messages

    async def _client_loop(self, session: ServerClientSession) -> None:
        while self.is_running and session.connection.is_active:
            message = await session.connection.receive()
            if message.protocol_version != PROTOCOL_VERSION:
                await session.connection.send(ErrorMessage(
                    ErrorType.VERSION_MISMATCH, f"Protocol {PROTOCOL_VERSION} required",
                    reply_to=message.message_id,
                ))
                break
            if isinstance(message, PingMessage):
                if message.pong and session.last_ping_id == message.ping_id:
                    session.last_pong_time = monotonic()
                    session.ping_ms = max(0, int((session.last_pong_time - session.last_ping_time) * 1000))
                continue
            if self._rate_limited(session):
                await session.connection.send(ErrorMessage(
                    ErrorType.RATE_LIMIT, "Too many messages; slow down", reply_to=message.message_id
                ))
                break
            if isinstance(message, ChatMessage):
                clean = ChatMessage(session.username, message.message[:4000], reply_to=message.message_id)
                if self.on_chat is not None:
                    self.on_chat(clean.sender, clean.message)
                await self.broadcast(clean)
                continue
            if not isinstance(message, CommandMessage):
                continue
            if message.command == ClientCommand.QUIT.value:
                break
            if message.command in {ClientCommand.ASSET_MANIFEST_REQUEST.value, ClientCommand.ASSET_REQUEST.value}:
                try:
                    await self._handle_asset_command(session, message)
                except (KeyError, ValueError, FileNotFoundError) as exc:
                    await session.connection.send(ErrorMessage(
                        ErrorType.REQUEST_REJECTED, str(exc), reply_to=message.message_id
                    ))
                continue
            if self.gateway is None:
                await session.connection.send(ErrorMessage(
                    ErrorType.INVALID_STATE, "No game runtime is connected", reply_to=message.message_id
                ))
                continue
            result = self.gateway.handle(session.username, message)
            await self._apply_gateway_result(session.username, result)

    async def _handle_asset_command(self, session: ServerClientSession, request: CommandMessage) -> None:
        if self.asset_catalog is None:
            await session.connection.send(ErrorMessage(
                ErrorType.INVALID_STATE, "Asset service is disabled", reply_to=request.message_id
            ))
            return
        if request.command == ClientCommand.ASSET_MANIFEST_REQUEST.value:
            namespaces = request.args.get("namespaces")
            if namespaces is not None and not isinstance(namespaces, list):
                raise ValueError("namespaces must be a list")
            manifest = [entry.to_dict() for entry in self.asset_catalog.manifest(namespaces)]
            await session.connection.send(CommandMessage(
                ServerCommand.ASSET_MANIFEST.value, {"manifest": manifest}, reply_to=request.message_id
            ))
            return
        items = request.args.get("items", [])
        if not isinstance(items, list) or len(items) > 64:
            raise ValueError("items must be a list of at most 64 assets")
        for item in items:
            if not isinstance(item, dict):
                continue
            namespace, path = str(item.get("namespace", "")), str(item.get("path", ""))
            for chunk in self.asset_catalog.chunks(namespace, path):
                await session.connection.send(CommandMessage(ServerCommand.ASSET_CHUNK.value, chunk))
        await session.connection.send(CommandMessage(
            ServerCommand.ASSET_DONE.value, {"count": len(items)}, reply_to=request.message_id
        ))

    async def _apply_gateway_result(self, username: str, result: GatewayResult) -> None:
        session = self.clients.get(username)
        if session is None:
            return
        for reply in result.replies:
            await session.connection.send(reply)
        if result.broadcast_sync:
            await self.broadcast_runtime_sync()

    async def approve_pending(self, approval_id: str) -> PendingRequest:
        if self.gateway is None:
            raise RuntimeError("No RuntimeNetworkGateway is connected")
        pending, result = self.gateway.approve(approval_id)
        await self._apply_gateway_result(pending.username, result)
        return pending

    async def reject_pending(self, approval_id: str, *, reason: str = "") -> PendingRequest:
        if self.gateway is None:
            raise RuntimeError("No RuntimeNetworkGateway is connected")
        pending, message = self.gateway.reject(approval_id, reason=reason)
        session = self.clients.get(pending.username)
        if session is not None:
            await session.connection.send(message)
        return pending

    def edit_pending_action(
        self, approval_id: str, *, targets: list[str] | None = None,
        dice: dict[str, dict[str, Any]] | None = None,
    ) -> PendingRequest:
        if self.gateway is None:
            raise RuntimeError("No RuntimeNetworkGateway is connected")
        pending = self.gateway.edit_action(approval_id, targets=targets, dice=dice)
        if self.on_pending_updated is not None:
            self.on_pending_updated(pending)
        return pending

    def reroll_pending_die(self, approval_id: str, dice_key: str) -> PendingRequest:
        if self.gateway is None:
            raise RuntimeError("No RuntimeNetworkGateway is connected")
        pending = self.gateway.reroll(approval_id, dice_key)
        if self.on_pending_updated is not None:
            self.on_pending_updated(pending)
        return pending

    async def broadcast_runtime_sync(self) -> None:
        if self.gateway is None:
            return
        for username, session in list(self.clients.items()):
            if self.gateway.entity_for(username) is None:
                continue
            try:
                projection = self.gateway.projection_for(username)
                await session.connection.send(CommandMessage(
                    ServerCommand.RUNTIME_SYNC.value, {"projection": projection}
                ))
            except (ConnectionError, KeyError, PermissionError, RuntimeError):
                continue

    async def broadcast(self, message: NetworkMessage, *, exclude: set[str] | None = None) -> None:
        excluded = exclude or set()
        for username, session in list(self.clients.items()):
            if username in excluded:
                continue
            try:
                await session.connection.send(message)
            except (ConnectionError, OSError, RuntimeError):
                pass

    async def _heartbeat_loop(self) -> None:
        while self.is_running:
            now = monotonic()
            for username, session in list(self.clients.items()):
                if now - session.last_pong_time > self.settings.heartbeat_timeout:
                    with suppress(ConnectionError, OSError, RuntimeError):
                        await session.connection.send(ErrorMessage(ErrorType.TIMEOUT, "Heartbeat timeout"))
                    await self._drop_client(username, announce=True)
                    continue
                if session.last_ping_time <= session.last_pong_time:
                    ping_id = int(now * 1000)
                    session.last_ping_id = ping_id
                    session.last_ping_time = now
                    with suppress(ConnectionError, OSError, RuntimeError):
                        await session.connection.send(PingMessage(ping_id))
            await self.broadcast(CommandMessage(
                ServerCommand.PLAYER_PINGS.value,
                {"players": [
                    {"username": name, "ping_ms": client.ping_ms}
                    for name, client in self.clients.items()
                ]},
            ))
            await asyncio.sleep(self.settings.heartbeat_interval)

    async def _drop_client(self, username: str, *, announce: bool) -> None:
        session = self.clients.pop(username, None)
        if session is None:
            return
        if self.gateway is not None:
            pending = self.gateway.pending_for(username)
            if pending is not None:
                with suppress(Exception):
                    self.gateway.cancel(username, pending.approval_id)
            self.gateway.unbind(username)
        await session.connection.close()
        if announce:
            await self.broadcast(CommandMessage(ServerCommand.PLAYER_LEFT.value, {"username": username}))
        if self.on_client_disconnected is not None:
            self.on_client_disconnected(username)
        self._log(f"[session] disconnected username={username!r}")


def create_runtime_server(
    runtime,
    settings: ServerSettings,
    *,
    action_preparer=None,
    action_executor=None,
    asset_catalog: AssetCatalog | None = None,
    player_bindings: dict[str, str] | None = None,
    **server_callbacks,
) -> RPGNetworkServer:
    """Convenience factory used by the GM application."""
    pending_callback = server_callbacks.get("on_pending")
    gateway = RuntimeNetworkGateway(
        runtime,
        action_preparer=action_preparer,
        action_executor=action_executor,
        require_gm_approval=True,
    )
    server = RPGNetworkServer(
        settings,
        gateway=gateway,
        asset_catalog=asset_catalog,
        player_bindings=player_bindings,
        **server_callbacks,
    )
    return server
