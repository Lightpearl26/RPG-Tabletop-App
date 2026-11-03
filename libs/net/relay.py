# -*- coding: utf-8 -*-
"""VPS relay: reverse SSH tunnel + remote socat TCP passthrough.

TLS is never terminated on the VPS; encrypted bytes pass through socat and the
SSH reverse tunnel until they reach the GM-hosted TLS server.
"""
from __future__ import annotations
import asyncio
from asyncio.subprocess import Process
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from .config import RelaySettings

LogCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class RelayEndpoint:
    host: str
    port: int


class VPSRelay:
    def __init__(
        self,
        settings: RelaySettings,
        *,
        local_port: int,
        on_log: LogCallback | None = None,
    ) -> None:
        self.settings = settings
        self.local_port = int(local_port)
        self.on_log = on_log or (lambda _msg: None)
        self.process: Process | None = None
        self._log_task: asyncio.Task[None] | None = None

    @property
    def endpoint(self) -> RelayEndpoint | None:
        if self.process is None or self.process.returncode is not None:
            return None
        return RelayEndpoint(self.settings.host, int(self.settings.public_port))

    def _base_ssh(self) -> list[str]:
        s = self.settings
        if not s.host.strip() or not s.user.strip():
            raise ValueError("Relay host and user are required")
        if any(ch.isspace() for ch in s.host + s.user):
            raise ValueError("Relay host/user must not contain whitespace")
        cmd = [
            "ssh", "-o", "BatchMode=yes",
            "-o", f"StrictHostKeyChecking={s.strict_host_key_checking}",
            "-p", str(int(s.ssh_port)),
        ]
        if s.key_file:
            key = Path(s.key_file).expanduser()
            if not key.is_file():
                raise FileNotFoundError(f"Relay SSH key not found: {key}")
            cmd.extend(["-i", str(key)])
        return cmd

    async def _remote_cleanup(self) -> None:
        s = self.settings
        cmd = self._base_ssh() + [
            f"{s.user}@{s.host}",
            f"pkill -f 'socat.*TCP-LISTEN:{int(s.public_port)}' 2>/dev/null || true",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
        except (OSError, RuntimeError) as exc:
            self.on_log(f"Relay cleanup warning: {exc}")

    async def _read_logs(self) -> None:
        if self.process is None or self.process.stdout is None:
            return
        while True:
            line = await self.process.stdout.readline()
            if not line:
                break
            text = line.decode(errors="replace").rstrip()
            if text:
                self.on_log(text)

    async def start(self) -> RelayEndpoint | None:
        if not self.settings.enabled or not self.settings.host:
            return None
        await self._remote_cleanup()
        s = self.settings
        remote_command = (
            "sh -lc \"command -v socat >/dev/null 2>&1 || "
            "{ echo 'socat missing on VPS'; exit 127; }; "
            f"exec socat TCP-LISTEN:{int(s.public_port)},fork,reuseaddr "
            f"TCP:127.0.0.1:{int(s.tunnel_port)}\""
        )
        cmd = self._base_ssh()
        cmd[1:1] = [
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
        ]
        cmd += [
            "-R", f"127.0.0.1:{int(s.tunnel_port)}:127.0.0.1:{self.local_port}",
            f"{s.user}@{s.host}", remote_command,
        ]
        self.on_log(
            f"Starting relay {s.host}:{int(s.public_port)} -> local:{self.local_port}"
        )
        self.process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        self._log_task = asyncio.create_task(self._read_logs())
        await asyncio.sleep(2.0)
        if self.process.returncode is not None:
            if self._log_task is not None:
                await self._log_task
            raise RuntimeError("VPS relay failed to start (SSH key/socat/remote port)")
        return self.endpoint

    async def stop(self) -> None:
        if self.settings.enabled and self.settings.host:
            await self._remote_cleanup()
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self._log_task is not None:
            try:
                await self._log_task
            except asyncio.CancelledError:
                pass
        self.process = None
        self._log_task = None
