# -*- coding: utf-8 -*-
"""Async framed connection shared by server and client (frames: see protocol)."""
from __future__ import annotations
from asyncio import (
    IncompleteReadError, Lock, StreamReader, StreamWriter, TimeoutError as AsyncTimeout, wait_for,
)
import struct

from .protocol import DEFAULT_MAX_FRAME_BYTES, NetworkMessage, decode_frame


class LegacyPeerError(ValueError):
    """The peer speaks the JSON-lines protocol of versions before 3.0."""


class NetworkConnection:
    def __init__(
        self, reader: StreamReader, writer: StreamWriter, *, max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.max_frame_bytes = int(max_frame_bytes)
        self.is_active = True
        self._send_lock = Lock()

    async def send(self, message: NetworkMessage) -> None:
        if not self.is_active:
            raise ConnectionError("Connection is closed")
        frame = message.encode_frame()
        if len(frame) > self.max_frame_bytes:
            # Refused here rather than by the peer, which would drop the connection
            raise ValueError(f"Message too large ({len(frame)} bytes)")
        async with self._send_lock:
            self.writer.write(frame)
            await self.writer.drain()

    async def receive(self) -> NetworkMessage:
        if not self.is_active:
            raise ConnectionError("Connection is closed")
        try:
            header = await self.reader.readexactly(4)
            if header[:1] == b"{":
                raise LegacyPeerError("The peer uses the JSON-lines protocol (version before 3.0)")
            (length,) = struct.unpack(">I", header)
            if length <= 0 or length > self.max_frame_bytes:
                raise ValueError(f"Protocol error: frame of {length} bytes")
            payload = await self.reader.readexactly(length)
        except IncompleteReadError as exc:
            self.is_active = False
            raise ConnectionError("Connection closed by peer") from exc
        return decode_frame(payload)

    async def send_legacy_line(self, raw_json: bytes) -> None:
        """Answer a pre-3.0 peer in its own format (an error, before closing)."""
        async with self._send_lock:
            self.writer.write(raw_json + b"\n")
            await self.writer.drain()

    async def close(self) -> None:
        if not self.is_active:
            return
        self.is_active = False
        self.writer.close()
        try:
            # A peer that vanished never finishes the TLS close: do not hang on it
            await wait_for(self.writer.wait_closed(), 3.0)
        except (AsyncTimeout, ConnectionError, OSError, RuntimeError):
            pass
