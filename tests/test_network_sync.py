# -*- coding: utf-8 -*-
"""Real TLS server and client on localhost: asset streaming, reconnection, syncs."""

from __future__ import annotations

import asyncio
import os
import socket
from types import SimpleNamespace

import pytest

pytest.importorskip("cryptography")

from rpg_tabletop.net.asset_service import AssetAnswer  # noqa: E402
from rpg_tabletop.net.assets import AssetStatus  # noqa: E402
from rpg_tabletop.net.client import RPGNetworkClient  # noqa: E402
from rpg_tabletop.net.commands import ServerCommand  # noqa: E402
from rpg_tabletop.net.config import ClientSettings, ServerSettings, TLSSettings  # noqa: E402
from rpg_tabletop.net.protocol import CommandMessage  # noqa: E402
from rpg_tabletop.net.server import RPGNetworkServer  # noqa: E402


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

    def __init__(self, projection=None, fail=False, files=None):
        self.files = files or {}  # tile key -> file the player may have
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

    def asset_answer(self, username, ref, have=""):
        path = self.files.get(ref.name) if ref.kind == "tile" and ref.map_id == "m" else None
        if path is None:
            return AssetAnswer(ref, AssetStatus.DENIED)
        return AssetAnswer(ref, path=path, extra={"hash": ref.name, "version": "v1"})

    def handle(self, username, message):
        raise AttributeError("unexpected bug in a handler")


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 60))


def _tile_files(tmp_path, count, size):
    root = tmp_path / "maps" / "m" / "tiles"
    root.mkdir(parents=True)
    files = {}
    for index in range(count):
        path = root / f"{index}_0.webp"
        path.write_bytes(os.urandom(size))
        files[f"{index}_0"] = path
    return files


def test_big_asset_transfer_does_not_block_pings_or_commands(tmp_path):
    files = _tile_files(tmp_path, 40, 300_000)

    async def scenario():
        port = _free_port()
        server = RPGNetworkServer(_settings(tmp_path, port), gateway=StubGateway(files=files))
        await server.start()
        received = []
        client = _client(port, tmp_path, on_message=received.append)
        try:
            await client.connect()
            refs = [{"ref": f"tile:m:{index}_0"} for index in range(40)]
            # Several requests in a row: queued, not counted by the rate limit
            for start in range(0, 40, 4):
                await client.request_assets(refs[start:start + 4])
            pings = []
            for _ in range(200):
                await asyncio.sleep(0.05)
                ping = server.clients["bob"].ping_ms
                if ping is not None:
                    pings.append(ping)
                if any(getattr(m, "command", "") == "ASSET_DONE" for m in received) and len(pings) >= 3:
                    break
            assert client.is_connected and "bob" in server.clients
            done = [m for m in received if getattr(m, "command", "") == ServerCommand.ASSET_CHUNK.value]
            assert len(done) == 40  # one interface message per complete file
            assert all(m.args.get("complete") and m.args.get("version") == "v1" for m in done)
            assert pings and max(pings) < 1500
        finally:
            await client.disconnect()
            await server.stop()

    run(scenario())
    cache = tmp_path / "cache-bob" / "maps" / "m" / "tiles"
    assert len(list(cache.iterdir())) == 40
    assert (cache / "7_0").read_bytes() == files["7_0"].read_bytes()


def test_replace_drops_assets_that_left_the_screen(tmp_path):
    files = _tile_files(tmp_path, 200, 80_000)

    async def scenario():
        port = _free_port()
        server = RPGNetworkServer(_settings(tmp_path, port), gateway=StubGateway(files=files))
        await server.start()
        statuses = []
        client = _client(port, tmp_path, on_message=statuses.append)
        try:
            await client.connect()
            await client.request_assets([{"ref": f"tile:m:{i}_0"} for i in range(200)])
            await asyncio.sleep(0.05)
            await client.request_assets([{"ref": "tile:m:199_0"}, {"ref": "tile:other:0_0"}], replace=True)
            for _ in range(100):
                await asyncio.sleep(0.05)
                if (tmp_path / "cache-bob" / "maps" / "m" / "tiles" / "199_0").is_file():
                    break
            await asyncio.sleep(0.3)
        finally:
            await client.disconnect()
            await server.stop()
        denied = [m.args for m in statuses if getattr(m, "command", "") == "ASSET_STATUS"]
        assert {"ref": "tile:other:0_0", "status": "denied"} in denied

    run(scenario())
    received = list((tmp_path / "cache-bob" / "maps" / "m" / "tiles").iterdir())
    assert (tmp_path / "cache-bob" / "maps" / "m" / "tiles" / "199_0") in received
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


@pytest.fixture
def compiled_map(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QGuiApplication
    from rpg_tabletop.assets.map import DrawingElement, DrawTool, MapAsset, Vec2, supported_tile_formats
    from rpg_tabletop.session.models import SessionMapState, SessionToken

    QGuiApplication.instance() or QGuiApplication([])
    if not supported_tile_formats():
        pytest.skip("No tile format available")
    map_dir = tmp_path / "campaign-maps" / "m"
    asset = MapAsset(width=1024, height=1024)
    asset.elements.append(DrawingElement(
        tool=DrawTool.RECT, points=[Vec2(300, 300), Vec2(400, 400)], stroke_width=0, fill_color="#ff0000",
    ))
    asset.save(map_dir, tile_size=256, tile_format=supported_tile_formats()[-1])
    state = SessionMapState.from_asset("m", asset)
    state.tokens.append(SessionToken(entity_id="hero", pos=Vec2(330, 330)))
    runtime = SimpleNamespace(
        active_map_id="m", active_map=state, entities={},
        _active_map_asset=lambda: MapAsset.load(map_dir),
        vision_radius_cells=lambda entity_id: 2,
        visible_tokens_for=lambda entity_id: [],
        campaign=SimpleNamespace(map_path=lambda map_id: map_dir.parent / map_id),
    )
    return runtime


def test_the_server_only_gives_the_tiles_of_the_field_of_view(compiled_map):
    from rpg_tabletop.net.asset_service import answer_asset
    from rpg_tabletop.net.assets import parse_ref
    from rpg_tabletop.net.projection import _map_view

    runtime = compiled_map
    structure = _map_view(runtime, "hero")["asset"]["compiled"]
    assert set(structure) == {"version", "size", "origin", "tile_size", "format"}  # no tile list

    def ask(ref, have="", who="hero"):
        return answer_asset(runtime, who, parse_ref(ref), have)

    drawn = ask("tile:m:1_1")
    assert drawn.path is not None and drawn.path.is_file()
    assert drawn.extra["version"] == structure["version"]
    assert ask("tile:m:1_1", have=drawn.extra["hash"]).status is AssetStatus.UNCHANGED
    assert ask("tile:m:0_0").status is AssetStatus.UNIFORM
    assert ask("tile:m:3_3").status is AssetStatus.DENIED       # outside the field of view
    assert ask("tile:other:1_1").status is AssetStatus.DENIED   # not the GM's map
    assert ask("tile:m:1_1", who="ghost").status is AssetStatus.DENIED  # no token on the map
    assert ask("media:m:map.json").status is AssetStatus.DENIED  # GM files are not reachable


# ---------------------------------------------------------------- protocol 3

def test_only_changed_sections_are_synced(tmp_path):
    projection = {"entity": {"name": "Hero", "hp": 10}, "map": {"tokens": [1, 2]}, "combat": {"active": False}}
    gateway = StubGateway(projection=projection)

    async def scenario():
        port = _free_port()
        server = RPGNetworkServer(_settings(tmp_path, port), gateway=gateway, player_bindings={"bob": "e1"})
        await server.start()
        syncs = []
        client = _client(port, tmp_path, on_message=lambda m: syncs.append(m) if getattr(
            m, "command", "") == "RUNTIME_SYNC" else None)
        try:
            await client.connect()
            await asyncio.sleep(0.1)
            projection["map"] = {"tokens": [1, 2, 3]}
            await server.broadcast_runtime_sync()
            await asyncio.sleep(0.1)
            await server.broadcast_runtime_sync()  # nothing changed: nothing sent
            await asyncio.sleep(0.1)
        finally:
            await client.disconnect()
            await server.stop()
        return syncs, client

    syncs, client = run(scenario())
    assert len(syncs) == 2
    assert client.last_projection == projection
    # The interface always gets the whole projection, the wire only the change
    assert syncs[1].args["projection"]["map"] == {"tokens": [1, 2, 3]}
    assert syncs[1].args["projection"]["entity"] == {"name": "Hero", "hp": 10}


def test_runtime_calls_go_through_the_executor(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    calls = []
    gateway = StubGateway()
    original = gateway.handle

    def handle(username, message):
        calls.append(threading.current_thread().name)
        return original(username, message)

    gateway.handle = handle
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qt-like")

    async def scenario():
        port = _free_port()
        server = RPGNetworkServer(_settings(tmp_path, port), gateway=gateway, runtime_executor=pool.submit)
        await server.start()
        client = _client(port, tmp_path)
        try:
            await client.connect()
            await client.send(CommandMessage("MOVE_REQUEST", {"target": [0, 0]}))
            await asyncio.sleep(0.2)
        finally:
            await client.disconnect()
            await server.stop()

    run(scenario())
    pool.shutdown()
    assert calls and all(name.startswith("qt-like") for name in calls)


def test_an_old_player_app_is_told_to_update(tmp_path):
    import json
    import ssl

    async def scenario():
        port = _free_port()
        server = RPGNetworkServer(_settings(tmp_path, port), gateway=StubGateway())
        await server.start()
        try:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=context)
            writer.write(b'{"version":"2.3","id":"1","type":"CMD","command":"AUTH","args":{}}\n')
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), 5)
            writer.close()
            return json.loads(line)
        finally:
            await server.stop()

    answer = run(scenario())
    assert answer["type"] == "ERROR" and answer["error_type"] == "VERSION_MISMATCH"


def test_frames_compress_json_and_carry_raw_bytes():
    from rpg_tabletop.net.protocol import FLAG_BLOB, FLAG_ZLIB, decode_frame

    message = CommandMessage("RUNTIME_SYNC", {"projection": {"text": "abc" * 2000}}, blob=b"\x00\xff" * 10)
    frame = message.encode_frame()
    assert frame[4] & FLAG_ZLIB and frame[4] & FLAG_BLOB
    assert len(frame) < 1000  # 6 KB of JSON compressed, 20 raw bytes
    back = decode_frame(frame[4:])
    assert back.args == message.args and back.blob == message.blob


def test_freehand_simplification_keeps_the_shape():
    from rpg_tabletop.core.geometry import simplify_polyline

    line = [(float(x), 0.0) for x in range(1000)] + [(999.0, float(y)) for y in range(1, 500)]
    kept = simplify_polyline(line, 0.5)
    assert [line[i] for i in kept] == [(0.0, 0.0), (999.0, 0.0), (999.0, 499.0)]


def test_preview_only_without_field_of_view_and_thumbnails_for_tokens(compiled_map, tmp_path):
    from PySide6.QtGui import QColor, QImage
    from rpg_tabletop.assets.map import MapAsset
    from rpg_tabletop.net.asset_service import answer_asset
    from rpg_tabletop.net.assets import parse_ref
    from rpg_tabletop.net.projection import token_image_id

    runtime = compiled_map
    assert answer_asset(runtime, "hero", parse_ref("preview:m")).status is AssetStatus.DENIED

    map_dir = runtime.campaign.map_path("m")
    asset = MapAsset.load(map_dir)
    asset.grid.enabled = False
    asset.save(map_dir, tile_size=256, tile_format=asset.compiled.tile_format)
    preview = answer_asset(runtime, "hero", parse_ref("preview:m"))
    assert preview.path is not None and preview.read()[:4] == b"\x89PNG"
    # Without a grid every tile may be asked for
    assert answer_asset(runtime, "hero", parse_ref("tile:m:3_3")).status is not AssetStatus.DENIED

    picture = tmp_path / "hero.png"
    image = QImage(1200, 900, QImage.Format.Format_ARGB32)
    image.fill(QColor("blue"))
    image.save(str(picture))
    token = runtime.active_map.tokens[0]
    runtime.entities["hero"] = SimpleNamespace(token=picture)
    runtime.visible_tokens_for = lambda entity_id: [token]
    answer = answer_asset(runtime, "hero", parse_ref(f"token:{token_image_id(picture)}"))
    thumbnail = QImage.fromData(answer.read())
    assert max(thumbnail.width(), thumbnail.height()) == 256
