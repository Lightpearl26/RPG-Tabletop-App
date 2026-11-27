# -*- coding: utf-8 -*-
"""GM-hosted authoritative TCP/TLS server."""
from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import Future
from contextlib import nullcontext, suppress
from dataclasses import dataclass, field
from hashlib import sha1
import hmac
import json
import socket
from time import monotonic
from typing import Any, Callable, TypeVar

from .assets import file_chunks, parse_ref
from .commands import ClientCommand, ServerCommand
from .config import ServerSettings
from .connection import LegacyPeerError, NetworkConnection
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
T = TypeVar("T")
# Runs a function where the SessionRuntime lives (the GM's Qt thread) and
# returns a concurrent Future of its result.
RuntimeExecutor = Callable[[Callable[[], Any]], Future]

# Asset requests are queued and streamed by a background task per player, so a
# big map never blocks the reading of that player's messages (pongs included).
ASSET_COMMANDS = frozenset({ClientCommand.ASSET_REQUEST.value})
MAX_ASSET_ITEMS_PER_REQUEST = 1024
MAX_ASSET_QUEUE = 4096
# A message to one player may not hold the others back longer than this
SEND_TIMEOUT = 15.0
# Over the rate limit a message is refused; this many times over it, the
# connection is considered abusive and closed.
RATE_LIMIT_DISCONNECT_FACTOR = 4


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
    # Assets waiting to be answered: reference -> hash of the copy the player has
    asset_queue: dict[str, str] = field(default_factory=dict)
    asset_wakeup: asyncio.Event = field(default_factory=asyncio.Event)
    asset_task: asyncio.Task[None] | None = None
    assets_sent: int = 0
    # Digest of each projection section last sent: only changed sections go out
    sent_sections: dict[str, str] = field(default_factory=dict)


class RPGNetworkServer:
    def __init__(
        self,
        settings: ServerSettings,
        *,
        gateway: RuntimeNetworkGateway | None = None,
        player_bindings: dict[str, str] | None = None,
        on_log: LogCallback | None = None,
        on_pending: PendingCallback | None = None,
        on_pending_updated: PendingCallback | None = None,
        on_client_connected: ClientCallback | None = None,
        on_client_disconnected: ClientCallback | None = None,
        on_chat: ChatCallback | None = None,
        on_initiative=None,
        runtime_executor: RuntimeExecutor | None = None,
    ) -> None:
        settings.validate()
        # Every access to the gateway and the SessionRuntime goes through
        # run_on_runtime: the GM interface changes them on its own thread.
        self.runtime_executor = runtime_executor
        self.settings = settings
        self.gateway = gateway
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
            if on_initiative is not None:
                self.gateway.on_initiative = on_initiative
            if on_pending_updated is not None:
                self.gateway.on_pending_updated = on_pending_updated
            # Dice go to the players who are here; the app rolls for the others
            self.gateway.is_connected = lambda username: username in self.clients

    def _log(self, message: str) -> None:
        self.on_log(str(message))

    async def run_on_runtime(self, function: Callable[[], T]) -> T:
        """
        Call `function` where the SessionRuntime lives.

        The runtime is not thread-safe and the GM interface changes it on the Qt
        thread: player requests, projections and asset checks run there too,
        one after the other, instead of racing with the GM on the network thread.
        """
        if self.runtime_executor is None:
            return function()
        return await asyncio.wrap_future(self.runtime_executor(function))

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
            if session.asset_task is not None:
                session.asset_task.cancel()
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
        connection = NetworkConnection(reader, writer, max_frame_bytes=self.settings.max_message_bytes)
        peer = writer.get_extra_info("peername") or ("?", 0)
        session: ServerClientSession | None = None
        try:
            try:
                auth = await asyncio.wait_for(connection.receive(), timeout=15.0)
            except LegacyPeerError:
                # A player app older than 3.0: tell it in its own format to update
                reason = f"Protocol {PROTOCOL_VERSION} required: update the player app"
                await connection.send_legacy_line(json.dumps({
                    "version": PROTOCOL_VERSION, "id": "legacy", "type": "ERROR",
                    "error_type": ErrorType.VERSION_MISMATCH.value, "reason": reason,
                }).encode("utf-8"))
                self._log(f"[connect] peer={peer} uses an older player app")
                return
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
            session = await self._authenticate(connection, peer, auth)
            if session is None:
                return
            await self._client_loop(session)
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, ConnectionError, OSError, RuntimeError, ValueError) as exc:
            self._log(f"[connect] peer={peer} error={exc}")
        except Exception as exc:  # noqa: BLE001 - never leave a ghost session behind
            self._log(f"[connect] peer={peer} unexpected {type(exc).__name__}: {exc}")
        finally:
            if session is not None:
                await self._drop_session(session, announce=True)
            else:
                await connection.close()
            if task is not None:
                self._client_tasks.discard(task)

    async def _authenticate(
        self, connection: NetworkConnection, peer, request: CommandMessage
    ) -> ServerClientSession | None:
        username = str(request.args.get("username", "")).strip()
        password = str(request.args.get("password", ""))
        requested_entity = str(request.args.get("entity_id", "")).strip()
        if not username or len(username) > 64:
            await connection.send(ErrorMessage(ErrorType.AUTH_REJECTED, "Invalid username", reply_to=request.message_id))
            return None
        if username.lower() in {"admin", "gm", "server"}:
            await connection.send(ErrorMessage(ErrorType.AUTH_REJECTED, "Reserved username", reply_to=request.message_id))
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

        # A player who comes back while the server still holds their previous
        # connection (it died on their side, or the heartbeat has not noticed
        # yet) replaces it instead of being refused for minutes.
        previous = self.clients.get(username)
        if previous is not None:
            self._log(f"[auth] username={username!r} reconnects: closing the previous connection")
            await self._safe_send(previous, ErrorMessage(ErrorType.CLOSED, "Replaced by a new connection"), timeout=2.0)
            await self._drop_session(previous, announce=False)

        session = ServerClientSession(username, tuple(peer), connection, bound_entity_id=bound)
        self.clients[username] = session
        try:
            if bound and self.gateway is not None:
                gateway = self.gateway
                try:
                    await self.run_on_runtime(lambda: gateway.bind(username, bound))
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
            if previous is None:
                await self.broadcast(
                    CommandMessage(ServerCommand.PLAYER_JOINED.value, {"username": username}),
                    exclude={username},
                )
            if bound and self.gateway is not None:
                try:
                    message = await self._full_sync_message(session)
                except Exception as exc:  # noqa: BLE001 - the next sync will catch up
                    self._log(f"[auth] username={username!r} first sync failed: {type(exc).__name__}: {exc}")
                else:
                    await connection.send(message)
        except BaseException:
            # The session must not outlive a failed authentication ("Username
            # already in use" until the heartbeat timeout otherwise).
            if self.clients.get(username) is session:
                self.clients.pop(username, None)
                gateway = self.gateway
                if gateway is not None and bound:
                    with suppress(Exception):
                        await self.run_on_runtime(
                            lambda: gateway.entity_for(username) == bound and gateway.unbind(username)
                        )
            raise
        self._log(f"[auth] accepted username={username!r} peer={peer} entity={bound!r}")
        if self.on_client_connected is not None:
            self.on_client_connected(username)
        return session

    def _sync_message(
        self,
        session: ServerClientSession,
        projection: dict[str, Any],
        *,
        full: bool = False,
        reply_to: str | None = None,
    ) -> CommandMessage | None:
        """
        RUNTIME_SYNC with the sections of the projection that changed since the
        last one sent on this connection, or None when nothing changed.

        {"projection": {<changed sections>}, "partial": true}: the client keeps
        the previous value of every section that is absent (RPGNetworkClient).
        A full sync ("partial": false) restarts from scratch.
        """
        digests = {
            key: sha1(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
            for key, value in projection.items()
        }
        if full:
            changed = list(projection)
        else:
            changed = [key for key, digest in digests.items() if session.sent_sections.get(key) != digest]
            if not changed and set(session.sent_sections) == set(digests):
                return None
        session.sent_sections = digests
        return CommandMessage(
            ServerCommand.RUNTIME_SYNC.value,
            {"projection": {key: projection[key] for key in changed}, "partial": not full},
            reply_to=reply_to,
        )

    async def _full_sync_message(self, session: ServerClientSession) -> CommandMessage:
        gateway = self.gateway
        if gateway is None:
            raise RuntimeError("No RuntimeNetworkGateway is connected")
        projection = await self.run_on_runtime(lambda: gateway.projection_for(session.username))
        message = self._sync_message(session, projection, full=True)
        assert message is not None
        return message

    def _for_session(self, session: ServerClientSession, message: NetworkMessage) -> NetworkMessage | None:
        """A RUNTIME_SYNC built by the gateway (reply to a request): sent in full."""
        if (
            isinstance(message, CommandMessage)
            and message.command == ServerCommand.RUNTIME_SYNC.value
            and isinstance(message.args.get("projection"), dict)
        ):
            return self._sync_message(session, message.args["projection"], full=True, reply_to=message.reply_to)
        return message

    async def bind_player(self, username: str, entity_id: str) -> None:
        if username not in self.clients:
            raise KeyError(f"Player is not connected: {username}")
        gateway = self.gateway
        if gateway is None:
            raise RuntimeError("No RuntimeNetworkGateway is connected")
        await self.run_on_runtime(lambda: gateway.bind(username, entity_id))
        self.player_bindings[username] = entity_id
        session = self.clients[username]
        session.bound_entity_id = entity_id
        await session.connection.send(CommandMessage(ServerCommand.ENTITY_BOUND.value, {"entity_id": entity_id}))
        await session.connection.send(await self._full_sync_message(session))

    async def request_initiative(self, username: str, entity_id: str) -> None:
        """Ask a player for the d20 initiative of their entity."""
        gateway = self.gateway
        if gateway is None:
            raise RuntimeError("No RuntimeNetworkGateway is connected")
        session = self.clients.get(username)
        if session is None:
            raise KeyError(f"Player is not connected: {username}")

        def expect() -> str:
            gateway.expect_initiative(username, entity_id)
            entity = gateway.runtime.entities.get(str(entity_id))
            return entity.name if entity is not None else ""

        name = await self.run_on_runtime(expect)
        await session.connection.send(CommandMessage(
            ServerCommand.INITIATIVE_REQUEST.value, {"entity_id": str(entity_id), "name": name},
        ))

    async def cancel_initiative(self, username: str) -> None:
        """Withdraw an initiative request the player has not answered."""
        gateway = self.gateway
        if gateway is None or not await self.run_on_runtime(lambda: gateway.forget_initiative(username)):
            return
        session = self.clients.get(username)
        if session is not None:
            await session.connection.send(CommandMessage(ServerCommand.INITIATIVE_CANCELLED.value, {}))

    async def unbind_player(self, username: str) -> None:
        gateway = self.gateway
        if gateway is not None:
            await self.run_on_runtime(lambda: gateway.unbind(username))
        self.player_bindings.pop(username, None)
        session = self.clients.get(username)
        if session is not None:
            session.bound_entity_id = None
            session.sent_sections = {}
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
            if isinstance(message, CommandMessage) and message.command in ASSET_COMMANDS:
                # Cheap (only queued) and bounded: not counted by the rate limit,
                # which a viewport scrolled over a big map would otherwise trip.
                try:
                    await self._handle_asset_command(session, message)
                except (KeyError, ValueError, OSError) as exc:
                    await session.connection.send(ErrorMessage(
                        ErrorType.REQUEST_REJECTED, str(exc), reply_to=message.message_id
                    ))
                continue
            if self._rate_limited(session):
                if len(session.recent_messages) > self.settings.rate_limit_messages * RATE_LIMIT_DISCONNECT_FACTOR:
                    await session.connection.send(ErrorMessage(
                        ErrorType.RATE_LIMIT, "Too many messages; disconnected", reply_to=message.message_id
                    ))
                    self._log(f"[session] username={session.username!r} flooded the server: disconnected")
                    break
                await session.connection.send(ErrorMessage(
                    ErrorType.RATE_LIMIT, "Too many messages; slow down", reply_to=message.message_id
                ))
                continue
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
            if self.gateway is None:
                await session.connection.send(ErrorMessage(
                    ErrorType.INVALID_STATE, "No game runtime is connected", reply_to=message.message_id
                ))
                continue
            gateway = self.gateway
            try:
                result = await self.run_on_runtime(
                    lambda message=message: gateway.handle(session.username, message)
                )
            except Exception as exc:  # noqa: BLE001 - a bad request must not cut the player off
                self._log(
                    f"[session] username={session.username!r} {message.command} failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                await session.connection.send(ErrorMessage(
                    ErrorType.INVALID_STATE, f"Server error: {exc}", reply_to=message.message_id
                ))
                continue
            await self._apply_gateway_result(session.username, result)

    async def _handle_asset_command(self, session: ServerClientSession, request: CommandMessage) -> None:
        """
        ASSET_REQUEST {"items": [{"ref": "...", "have": "<hash>"}], "replace": bool}

        Only queued here; each reference is checked and answered by the
        player's asset task when its turn comes.
        """
        if self.gateway is None:
            await session.connection.send(ErrorMessage(
                ErrorType.INVALID_STATE, "No game runtime is connected", reply_to=request.message_id
            ))
            return
        items = request.args.get("items", [])
        if not isinstance(items, list) or len(items) > MAX_ASSET_ITEMS_PER_REQUEST:
            raise ValueError(f"items must be a list of at most {MAX_ASSET_ITEMS_PER_REQUEST} assets")
        # `replace`: the client sends what it needs now (what is on screen); the
        # files it no longer needs are dropped instead of being sent anyway.
        if request.args.get("replace"):
            session.asset_queue.clear()
        for item in items:
            if not isinstance(item, dict):
                continue
            ref = str(item.get("ref", ""))
            if not ref or ref in session.asset_queue:
                continue
            if len(session.asset_queue) >= MAX_ASSET_QUEUE:
                break
            session.asset_queue[ref] = str(item.get("have") or "")[:128]
        if session.asset_task is None or session.asset_task.done():
            session.asset_task = asyncio.create_task(self._asset_worker(session))
        session.asset_wakeup.set()

    async def _asset_worker(self, session: ServerClientSession) -> None:
        """
        Answer the queued references of one player, in order.

        A file goes as one ASSET_CHUNK per 64 KB, each taking the connection's
        send lock on its own: heartbeats, syncs and replies go out between two
        chunks, and the player's own messages keep being read meanwhile.
        """
        try:
            while self.is_running and session.connection.is_active:
                if not session.asset_queue:
                    if session.assets_sent:
                        count, session.assets_sent = session.assets_sent, 0
                        await session.connection.send(CommandMessage(
                            ServerCommand.ASSET_DONE.value, {"count": count}
                        ))
                    session.asset_wakeup.clear()
                    if not session.asset_queue:
                        await session.asset_wakeup.wait()
                    continue
                raw_ref = next(iter(session.asset_queue))
                have = session.asset_queue.pop(raw_ref)
                gateway = self.gateway
                if gateway is None:
                    return
                try:
                    ref = parse_ref(raw_ref)
                except ValueError as exc:
                    await session.connection.send(CommandMessage(
                        ServerCommand.ASSET_STATUS.value,
                        {"ref": raw_ref, "status": "denied", "reason": str(exc)},
                    ))
                    continue
                # Checked now, against the session as it is when the file leaves
                answer = await self.run_on_runtime(
                    lambda ref=ref, have=have: gateway.asset_answer(session.username, ref, have)
                )
                if answer.status is not None:
                    await session.connection.send(CommandMessage(
                        ServerCommand.ASSET_STATUS.value, answer.status_args()
                    ))
                    continue
                try:
                    raw = await asyncio.to_thread(answer.read)
                except (OSError, ValueError):
                    await session.connection.send(CommandMessage(
                        ServerCommand.ASSET_STATUS.value, {"ref": raw_ref, "status": "missing"}
                    ))
                    continue
                for args, data in file_chunks(ref, raw, answer.extra):
                    await session.connection.send(CommandMessage(ServerCommand.ASSET_CHUNK.value, args, blob=data))
                session.assets_sent += 1
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError, RuntimeError):
            return

    async def _apply_gateway_result(self, username: str, result: GatewayResult) -> None:
        """Replies to the requester (absent for GM requests), messages to other players, sync."""
        session = self.clients.get(username)
        if session is not None:
            for reply in result.replies:
                outgoing = self._for_session(session, reply)
                if outgoing is not None:
                    await session.connection.send(outgoing)
        for recipient, message in result.outbox:
            target = self.clients.get(recipient)
            if target is None:
                continue
            await self._safe_send(target, message)
        if result.broadcast_sync:
            await self.broadcast_runtime_sync()

    async def deliver(self, result: GatewayResult, username: str = "") -> None:
        """
        Send what the GM's handling of a request produced: replies to `username`
        (none for the GM's own requests), dice asked to players, sync.
        """
        await self._apply_gateway_result(username, result)

    async def cancel_die(self, owner: str, approval_id: str, key: str) -> None:
        """The GM rolled a die in a player's place: close the player's dialog."""
        session = self.clients.get(owner)
        if session is not None:
            await session.connection.send(CommandMessage(
                ServerCommand.DICE_ROLL_CANCELLED.value,
                {"approval_id": approval_id, "key": key, "reason": "rolled"},
            ))

    async def approve_pending(self, approval_id: str) -> PendingRequest:
        gateway = self.gateway
        if gateway is None:
            raise RuntimeError("No RuntimeNetworkGateway is connected")
        pending, result = await self.run_on_runtime(lambda: gateway.approve(approval_id))
        await self._apply_gateway_result(pending.username, result)
        return pending

    async def reject_pending(self, approval_id: str, *, reason: str = "") -> PendingRequest:
        gateway = self.gateway
        if gateway is None:
            raise RuntimeError("No RuntimeNetworkGateway is connected")
        pending, result = await self.run_on_runtime(lambda: gateway.reject(approval_id, reason=reason))
        await self._apply_gateway_result(pending.username, result)
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
        """Send each bound player what changed in their projection (nothing if nothing did)."""
        gateway = self.gateway
        if gateway is None:
            return
        sessions = [
            session for session in list(self.clients.values())
            if session.bound_entity_id is not None or gateway.entity_for(session.username) is not None
        ]
        if not sessions:
            return

        def build() -> list[tuple[ServerClientSession, dict[str, Any] | None]]:
            built = []
            scope = getattr(getattr(gateway, "runtime", None), "visibility_scope", None)
            with scope() if scope is not None else nullcontext():
                built = _build_all()
            return built

        def _build_all() -> list[tuple[ServerClientSession, dict[str, Any] | None]]:
            built = []
            for session in sessions:
                if gateway.entity_for(session.username) is None:
                    continue
                try:
                    built.append((session, gateway.projection_for(session.username)))
                except (KeyError, PermissionError, RuntimeError):
                    continue
                except Exception as exc:  # noqa: BLE001 - one bad projection must not stop the others
                    self._log(
                        f"[sync] username={session.username!r} projection failed: {type(exc).__name__}: {exc}"
                    )
            return built

        sends = []
        for session, projection in await self.run_on_runtime(build):
            if self.clients.get(session.username) is not session or projection is None:
                continue
            message = self._sync_message(session, projection)
            if message is not None:
                sends.append(self._safe_send(session, message))
        if sends:
            await asyncio.gather(*sends)

    async def broadcast(self, message: NetworkMessage, *, exclude: set[str] | None = None) -> None:
        excluded = exclude or set()
        targets = [session for username, session in list(self.clients.items()) if username not in excluded]
        if targets:
            # Concurrently: a slow player must not delay everybody else
            await asyncio.gather(*(self._safe_send(session, message) for session in targets))

    async def _safe_send(
        self, session: ServerClientSession, message: NetworkMessage, *, timeout: float = SEND_TIMEOUT
    ) -> bool:
        try:
            await asyncio.wait_for(session.connection.send(message), timeout)
            return True
        except (asyncio.TimeoutError, ConnectionError, OSError, RuntimeError):
            return False
        except ValueError as exc:  # too large to be sent: the player keeps the connection
            self._log(f"[send] username={session.username!r} {exc}")
            return False

    async def _heartbeat_loop(self) -> None:
        while self.is_running:
            try:
                await self._heartbeat_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the heartbeat must never die
                self._log(f"[heartbeat] {type(exc).__name__}: {exc}")
            await asyncio.sleep(self.settings.heartbeat_interval)

    async def _heartbeat_once(self) -> None:
        now = monotonic()
        pings = []
        for session in list(self.clients.values()):
            if now - session.last_pong_time > self.settings.heartbeat_timeout:
                await self._safe_send(session, ErrorMessage(ErrorType.TIMEOUT, "Heartbeat timeout"), timeout=2.0)
                await self._drop_session(session, announce=True)
                continue
            if session.last_ping_time <= session.last_pong_time:
                ping_id = int(now * 1000)
                session.last_ping_id = ping_id
                session.last_ping_time = now
                pings.append(self._safe_send(session, PingMessage(ping_id)))
        if pings:
            await asyncio.gather(*pings)
        await self.broadcast(CommandMessage(
            ServerCommand.PLAYER_PINGS.value,
            {"players": [
                {"username": name, "ping_ms": client.ping_ms}
                for name, client in self.clients.items()
            ]},
        ))

    async def _drop_client(self, username: str, *, announce: bool) -> None:
        session = self.clients.get(username)
        if session is not None:
            await self._drop_session(session, announce=announce)

    async def _drop_session(self, session: ServerClientSession, *, announce: bool) -> None:
        """Forget one connection; a newer connection of the same player is left alone."""
        task = session.asset_task
        session.asset_task = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        username = session.username
        if self.clients.get(username) is not session:
            await session.connection.close()
            return
        self.clients.pop(username, None)
        gateway = self.gateway
        if gateway is not None:
            def forget() -> None:
                if username in self.clients:
                    return  # reconnected meanwhile: the new connection keeps its binding
                pending = gateway.pending_for(username)
                if pending is not None:
                    with suppress(Exception):
                        gateway.cancel(username, pending.approval_id)
                gateway.forget_initiative(username)
                gateway.unbind(username)
            with suppress(Exception):
                await self.run_on_runtime(forget)
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
    player_bindings: dict[str, str] | None = None,
    gateway: RuntimeNetworkGateway | None = None,
    **server_callbacks,
) -> RPGNetworkServer:
    """
    Convenience factory used by the GM application.

    Pass the GM window's `gateway` so that requests started without a server
    (NPC actions) and the ones from players share one list.
    """
    if gateway is None:
        gateway = RuntimeNetworkGateway(
            runtime,
            action_preparer=action_preparer,
            action_executor=action_executor,
            require_gm_approval=True,
        )
    server = RPGNetworkServer(
        settings,
        gateway=gateway,
        player_bindings=player_bindings,
        **server_callbacks,
    )
    return server
