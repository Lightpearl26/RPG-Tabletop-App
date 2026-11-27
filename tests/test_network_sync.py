# -*- coding: utf-8 -*-
"""Real TLS server and client on localhost: asset streaming, reconnection, syncs."""

from __future__ import annotations

import asyncio
import os
import socket
from types import SimpleNamespace

import pytest

pytest.importorskip("cryptography")

from rpg_tabletop.net.assets import AssetCatalog  # noqa: E402
from rpg_tabletop.net.client import RPGNetworkClient  # noqa: E402
from rpg_tabletop.net.commands import ServerCommand  # noqa: E402
from rpg_tabletop.net.config import ClientSettings, ServerSettings, TLSSettings  # noqa: E402
from rpg_tabletop.net.protocol import CommandMessage  # noqa: E402
from rpg_tabletop.net.server import RPGNetworkServer  # noqa: E402
from rpg_tabletop.net.sync_cache import compact_projection, expand_projection  # noqa: E402


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _settings(tmp_path, port: int) -> ServerSettings:
    return ServerSettings(
        host="127.0.0.1", port=port, password="pw",
        tls=TLSSettings(cert_file=str(tmp_path / "c.crt"), key_file=str(tmp_path / "c.key")),
        enable_firewall_setup=False, enable_upnp=False,
        heartbeat_interval=0.1, heartbeat_timeout=30.0,
    )


def _client(port: int, tmp_path, name="bob", **kwargs) -> RPGNetworkClient:
    settings = ClientSettings(host="127.0.0.1", port=port, username=name, password="pw",
                              trust_on_first_use=True)
    return RPGNetworkClient(settings, asset_cache=str(tmp_path / f"cache-{name}"), **kwargs)


class StubGateway:
    """Just enough of RuntimeNetworkGateway for the server."""

    def __init__(self, projection=None, fail=False):
        self.on_pending = None
        self.runtime = SimpleNamespace(entities={})
        self._bindings: dict[str, str] = {}
        self.projection = projection or {"map": None}
        self.fail = fail

    def bind(self, username, entity_id):
        self._bindings[username] = entity_id

    def unbind(self, username):
        return self._bindings.pop(username, None)

    def entity_for(self, username):
        return self._bindings.get(username)

    def projection_for(self, username):
        if self.fail:
            raise TypeError("broken projection")
        return self.projection

    def pending_for(self, username):
        return None

    def forget_initiative(self, username):
        return False

    def handle(self, username, message):
        raise AttributeError("unexpected bug in a handler")


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 60))


def test_big_asset_transfer_does_not_block_pings_or_commands(tmp_path):
    root = tmp_path / "maps"
    (root / "m" / "tiles").mkdir(parents=True)
    files = []
    for index in range(40):
        (root / "m" / "tiles" / f"{index}_0.webp").write_bytes(os.urandom(300_000))
        files.append({"namespace": "maps", "path": f"m/tiles/{index}_0.webp"})
    catalog = AssetCatalog()
    catalog.register_root("maps", root)

    async def scenario():
        port = _free_port()
        server = RPGNetworkServer(_settings(tmp_path, port), asset_catalog=catalog, gateway=StubGateway())
        await server.start()
        received = []
        client = _client(port, tmp_path, on_message=received.append)
        try:
            await client.connect()
            # Several requests in a row: queued, not counted by the rate limit
            for start in range(0, 40, 4):
                await client.request_assets(files[start:start + 4])
            pings = []
            for _ in range(60):
                await asyncio.sleep(0.05)
                ping = server.clients["bob"].ping_ms
                if ping is not None:
                    pings.append(ping)
                if sum(1 for m in received if getattr(m, "command", "") == "ASSET_DONE"):
                    break
            assert client.is_connected and "bob" in server.clients
            done = [m for m in received if getattr(m, "command", "") == ServerCommand.ASSET_CHUNK.value]
            assert len(done) == 40  # one interface message per complete file
            assert all(m.args.get("complete") for m in done)
            assert pings and max(pings) < 1500
        finally:
            await client.disconnect()
            await server.stop()

    run(scenario())
    cache = tmp_path / "cache-bob" / "maps" / "m" / "tiles"
    assert len(list(cache.iterdir())) == 40


def test_replace_drops_assets_that_left_the_screen(tmp_path):
    root = tmp_path / "maps"
    (root / "m").mkdir(parents=True)
    for index in range(200):
        (root / "m" / f"{index}.bin").write_bytes(os.urandom(80_000))
    catalog = AssetCatalog()
    catalog.register_root("maps", root)

    async def scenario():
        port = _free_port()
        server = RPGNetworkServer(_settings(tmp_path, port), asset_catalog=catalog)
        await server.start()
        client = _client(port, tmp_path)
        try:
            await client.connect()
            await client.request_assets([{"namespace": "maps", "path": f"m/{i}.bin"} for i in range(200)])
            await asyncio.sleep(0.05)
            await client.request_assets([{"namespace": "maps", "path": "m/199.bin"}], replace=True)
            for _ in range(100):
                await asyncio.sleep(0.05)
                if (tmp_path / "cache-bob" / "maps" / "m" / "199.bin").is_file():
                    break
            await asyncio.sleep(0.3)
        finally:
            await client.disconnect()
            await server.stop()

    run(scenario())
    received = list((tmp_path / "cache-bob" / "maps" / "m").iterdir())
    assert (tmp_path / "cache-bob" / "maps" / "m" / "199.bin") in received
    assert len(received) < 100


def test_reconnecting_player_replaces_the_stale_connection(tmp_path):
    async def scenario():
        port = _free_port()
        server = RPGNetworkServer(_settings(tmp_path, port), gateway=StubGateway(),
                                  player_bindings={"bob": "e1"})
        await server.start()
        first = _client(port, tmp_path)
        second = _client(port, tmp_path)
        try:
            await first.connect()
            stale = server.clients["bob"]
            await second.connect()  # used to fail with "Username already in use"
            await asyncio.sleep(0.2)
            assert server.clients["bob"] is not stale
            assert server.gateway.entity_for("bob") == "e1"
            assert not first.is_connected
            assert second.is_connected
        finally:
            await first.disconnect()
            await second.disconnect()
            await server.stop()

    run(scenario())


def test_a_broken_projection_does_not_cost_the_connection(tmp_path):
    async def scenario():
        port = _free_port()
        gateway = StubGateway(fail=True)
        server = RPGNetworkServer(_settings(tmp_path, port), gateway=gateway, player_bindings={"bob": "e1"})
        await server.start()
        client = _client(port, tmp_path)
        try:
            await client.connect()
            await server.broadcast_runtime_sync()
            await asyncio.sleep(0.2)
            assert client.is_connected and "bob" in server.clients
            gateway.fail = False
            await server.broadcast_runtime_sync()
            await asyncio.sleep(0.2)
            assert client.last_projection == {"map": None}
        finally:
            await client.disconnect()
            await server.stop()
        await asyncio.sleep(0.1)
        assert "bob" not in server.clients

    run(scenario())


def test_a_handler_bug_or_a_burst_does_not_disconnect(tmp_path):
    async def scenario():
        port = _free_port()
        server = RPGNetworkServer(_settings(tmp_path, port), gateway=StubGateway())
        await server.start()
        client = _client(port, tmp_path)
        try:
            await client.connect()
            for _ in range(12):  # over the limit of 5 per second, under the flood limit
                await client.send(CommandMessage("MOVE_REQUEST", {"target": [0, 0]}))
            await asyncio.sleep(0.3)
            assert client.is_connected and "bob" in server.clients
        finally:
            await client.disconnect()
            await server.stop()

    run(scenario())


def test_heavy_projection_parts_are_sent_once_per_connection():
    compiled = {"signature": "abc", "tiles": {"0_0": "h"}, "format": "webp"}
    token = {"id": "t", "image": "ff00", "image_id": "img1"}
    projection = {"map": {"asset": {"compiled": compiled}, "tokens": [dict(token)]}}
    images, compiled_seen = set(), set()

    first = compact_projection(projection, images, compiled_seen)
    second = compact_projection(projection, images, compiled_seen)
    assert first["map"]["tokens"][0]["image"] == "ff00"
    assert "image" not in second["map"]["tokens"][0]
    assert second["map"]["asset"]["compiled"] == {"signature": "abc", "cached": True}
    assert projection["map"]["asset"]["compiled"] is compiled  # source untouched

    client_images, client_compiled = {}, {}
    expand_projection(first, client_images, client_compiled)
    expanded = expand_projection(second, client_images, client_compiled)
    assert expanded["map"]["tokens"][0]["image"] == "ff00"
    assert expanded["map"]["asset"]["compiled"] == compiled


def test_player_without_token_on_the_gm_map_gets_no_map_content():
    pytest.importorskip("PySide6")
    from rpg_tabletop.assets.map import MapAsset, Vec2
    from rpg_tabletop.net import projection as projection_module
    from rpg_tabletop.session.models import SessionMapState, SessionToken

    asset = MapAsset(width=4096, height=4096)
    state = SessionMapState.from_asset("forest", asset)
    state.tokens.append(SessionToken(entity_id="other", pos=Vec2(10, 10)))
    runtime = SimpleNamespace(
        active_map_id="forest", active_map=state,
        _active_map_asset=lambda: asset,
    )
    view = projection_module._map_view(runtime, "hero")
    assert view["present"] is False
    assert view["asset"]["compiled"] is None and view["fov"] is None
    assert view["tokens"] == [] and view["elements"] == []
