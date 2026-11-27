# -*- coding: utf-8 -*-
"""Network configuration independent from any campaign file."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from json import dump, load
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class TLSSettings:
    cert_file: str = "certs/rpg_tabletop_server.crt"
    key_file: str = "certs/rpg_tabletop_server.key"
    common_name: str = "rpg-tabletop-server"


@dataclass(slots=True)
class RelaySettings:
    enabled: bool = False
    host: str = ""
    user: str = "root"
    ssh_port: int = 22
    public_port: int = 7799
    tunnel_port: int = 17799
    key_file: str = ""
    strict_host_key_checking: str = "accept-new"


@dataclass(slots=True)
class ServerSettings:
    host: str = "0.0.0.0"
    port: int = 7799
    password: str = "ROOM42"
    tls: TLSSettings = field(default_factory=TLSSettings)
    relay: RelaySettings = field(default_factory=RelaySettings)
    enable_firewall_setup: bool = True
    enable_upnp: bool = True
    # A ping every 5 s; a player silent for 20 s is considered gone (the
    # client gives up on a silent server after ClientSettings.idle_timeout)
    heartbeat_interval: float = 5.0
    heartbeat_timeout: float = 20.0
    rate_limit_messages: int = 5
    rate_limit_window: float = 1.0
    stream_line_limit_bytes: int = 4 * 1024 * 1024  # unused since protocol 3.0 (kept for saved settings)
    max_message_bytes: int = 8 * 1024 * 1024
    max_pending_per_player: int = 1

    def validate(self) -> None:
        if not (1 <= int(self.port) <= 65535):
            raise ValueError("Server port must be between 1 and 65535")
        if not self.password:
            raise ValueError("Server password must not be empty")
        if self.heartbeat_interval <= 0 or self.heartbeat_timeout <= 0:
            raise ValueError("Heartbeat values must be positive")
        if self.rate_limit_messages <= 0 or self.rate_limit_window <= 0:
            raise ValueError("Rate-limit values must be positive")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ServerSettings":
        raw = dict(data or {})
        tls = TLSSettings(**dict(raw.pop("tls", {}) or {}))
        relay = RelaySettings(**dict(raw.pop("relay", {}) or {}))
        known = cls.__dataclass_fields__.keys()
        result = cls(tls=tls, relay=relay, **{k: v for k, v in raw.items() if k in known})
        result.validate()
        return result

    @classmethod
    def load(cls, path: str | Path) -> "ServerSettings":
        path = Path(path)
        if not path.is_file():
            return cls()
        try:
            with path.open("r", encoding="utf-8") as stream:
                data = load(stream)
        except (OSError, ValueError):
            return cls()
        return cls.from_dict(data if isinstance(data, dict) else {})

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as stream:
            dump(self.to_dict(), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        return path


@dataclass(slots=True)
class ClientSettings:
    host: str
    port: int = 7799
    username: str = "Player"
    password: str = "ROOM42"
    requested_entity_id: str = ""
    server_hostname: str = "rpg-tabletop-server"
    ca_file: str = ""
    expected_fingerprint: str = ""
    stream_line_limit_bytes: int = 4 * 1024 * 1024  # unused since protocol 3.0
    trust_on_first_use: bool = False
    max_message_bytes: int = 8 * 1024 * 1024
    # No message at all from the server for this long: the connection is dead
    # (the server sends a heartbeat every few seconds)
    idle_timeout: float = 25.0
