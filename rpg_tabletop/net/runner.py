# -*- coding: utf-8 -*-
"""Small background asyncio loop for Qt/desktop applications.

The networking core has no Qt dependency.  A PySide6 application may keep this
runner in its main window and submit ``server.start()``, approvals, broadcasts,
etc. without blocking the GUI thread.
"""
from __future__ import annotations
import asyncio
from concurrent.futures import Future
import threading
from typing import Coroutine, Any


class AsyncioLoopThread:
    def __init__(self, *, name: str = "RPGTabletop-Network") -> None:
        self.name = name
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self._ready = threading.Event()

    @property
    def is_running(self) -> bool:
        return self.loop is not None and self.thread is not None and self.thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._ready.clear()
        self.thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self.thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("Network asyncio thread failed to start")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self.loop = loop
        asyncio.set_event_loop(loop)
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            self.loop = None

    def submit(self, coroutine: Coroutine[Any, Any, Any]) -> Future[Any]:
        if not self.is_running or self.loop is None:
            raise RuntimeError("Network asyncio thread is not running")
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def call_soon(self, callback, *args) -> None:
        if not self.is_running or self.loop is None:
            raise RuntimeError("Network asyncio thread is not running")
        self.loop.call_soon_threadsafe(callback, *args)

    def stop(self, *, timeout: float = 5.0) -> None:
        if not self.is_running or self.loop is None or self.thread is None:
            return
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=timeout)
        self.thread = None
