# -*- coding: utf-8 -*-
"""Async JSON-line connection shared by server and client."""
from __future__ import annotations
from asyncio import Lock, StreamReader, StreamWriter
from .protocol import NetworkMessage, decode


class NetworkConnection:
    def __init__(self, reader: StreamReader, writer: StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.is_active = True
        self._send_lock = Lock()

    async def send(self, message: NetworkMessage) -> None:
        if not self.is_active:
            raise ConnectionError("Connection is closed")
        async with self._send_lock:
            self.writer.write(message.encode() + b"\n")
            await self.writer.drain()

    async def receive(self) -> NetworkMessage:
        if not self.is_active:
            raise ConnectionError("Connection is closed")
        raw = await self.reader.readline()
        if not raw:
            self.is_active = False
            raise ConnectionError("Connection closed by peer")
        return decode(raw.rstrip(b"\r\n"))

    async def close(self) -> None:
        if not self.is_active:
            return
        self.is_active = False
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (ConnectionError, OSError, RuntimeError):
            pass
