# -*- coding: utf-8 -*-
"""Versioned JSON-lines protocol used by RPG Tabletop App.

Transport: TCP + TLS 1.3.  Each application message is one UTF-8 JSON object
terminated by ``\n``.  This intentionally keeps the framing from the legacy
runtime while adding correlation IDs and explicit protocol versioning.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from json import dumps, loads
from typing import Any
from uuid import uuid4

PROTOCOL_VERSION = "2.1"


class MessageType(str, Enum):
    LOG = "LOG"
    PING = "PING"
    MSG = "MSG"
    CMD = "CMD"
    ERROR = "ERROR"


class ErrorType(str, Enum):
    RATE_LIMIT = "RATE_LIMIT"
    INVALID_COMMAND = "INVALID_COMMAND"
    INVALID_PAYLOAD = "INVALID_PAYLOAD"
    ENCODING = "ENCODING"
    TIMEOUT = "TIMEOUT"
    CLOSED = "CLOSED"
    AUTH_REJECTED = "AUTH_REJECTED"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    INVALID_STATE = "INVALID_STATE"
    REQUEST_REJECTED = "REQUEST_REJECTED"
    REQUEST_ALREADY_PENDING = "REQUEST_ALREADY_PENDING"
    NOT_FOUND = "NOT_FOUND"


@dataclass(slots=True)
class NetworkMessage:
    type: MessageType
    content: dict[str, Any] = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: uuid4().hex)
    reply_to: str | None = None
    protocol_version: str = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "version": self.protocol_version,
            "id": self.message_id,
            "type": self.type.value,
        }
        if self.reply_to is not None:
            payload["reply_to"] = self.reply_to
        payload.update(self.content)
        return payload

    def encode(self) -> bytes:
        return dumps(
            self.to_dict(), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")


class LogMessage(NetworkMessage):
    def __init__(self, log: str, **meta: Any) -> None:
        super().__init__(MessageType.LOG, {"log": str(log)}, **_meta(meta))

    @property
    def log(self) -> str:
        return str(self.content.get("log", ""))


class PingMessage(NetworkMessage):
    """Heartbeat message.

    The server sends ``pong=False`` and the client echoes the same ``ping_id``
    with ``pong=True``.
    """

    def __init__(self, ping_id: int, *, pong: bool = False, **meta: Any) -> None:
        super().__init__(
            MessageType.PING,
            {"ping_id": int(ping_id), "pong": bool(pong)},
            **_meta(meta),
        )

    @property
    def ping_id(self) -> int:
        return int(self.content.get("ping_id", 0))

    @property
    def pong(self) -> bool:
        return bool(self.content.get("pong", False))


class ChatMessage(NetworkMessage):
    def __init__(self, sender: str, message: str, **meta: Any) -> None:
        super().__init__(
            MessageType.MSG,
            {"sender": str(sender), "message": str(message)},
            **_meta(meta),
        )

    @property
    def sender(self) -> str:
        return str(self.content.get("sender", ""))

    @property
    def message(self) -> str:
        return str(self.content.get("message", ""))


class CommandMessage(NetworkMessage):
    def __init__(
        self,
        command: str,
        args: dict[str, Any] | None = None,
        **meta: Any,
    ) -> None:
        super().__init__(
            MessageType.CMD,
            {"command": str(command), "args": dict(args or {})},
            **_meta(meta),
        )

    @property
    def command(self) -> str:
        return str(self.content.get("command", ""))

    @property
    def args(self) -> dict[str, Any]:
        raw = self.content.get("args", {})
        return raw if isinstance(raw, dict) else {}

    def reply(self, command: str, args: dict[str, Any] | None = None) -> "CommandMessage":
        return CommandMessage(command, args, reply_to=self.message_id)


class ErrorMessage(NetworkMessage):
    def __init__(
        self,
        error_type: ErrorType,
        reason: str,
        *,
        details: dict[str, Any] | None = None,
        **meta: Any,
    ) -> None:
        content: dict[str, Any] = {
            "error_type": error_type.value,
            "reason": str(reason),
        }
        if details:
            content["details"] = dict(details)
        super().__init__(MessageType.ERROR, content, **_meta(meta))

    @property
    def error_type(self) -> ErrorType:
        try:
            return ErrorType(str(self.content.get("error_type", "")))
        except ValueError:
            return ErrorType.ENCODING

    @property
    def reason(self) -> str:
        return str(self.content.get("reason", ""))

    @property
    def details(self) -> dict[str, Any]:
        raw = self.content.get("details", {})
        return raw if isinstance(raw, dict) else {}


def _meta(meta: dict[str, Any]) -> dict[str, Any]:
    allowed = {"message_id", "reply_to", "protocol_version"}
    return {key: value for key, value in meta.items() if key in allowed}


def _common(data: dict[str, Any]) -> dict[str, Any]:
    raw_reply = data.get("reply_to")
    return {
        "message_id": str(data.get("id") or uuid4().hex),
        "reply_to": str(raw_reply) if raw_reply is not None else None,
        "protocol_version": str(data.get("version") or "1.0"),
    }


def decode(raw: bytes) -> NetworkMessage:
    try:
        data = loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"Invalid message payload: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Protocol error: payload must be a JSON object")

    try:
        msg_type = MessageType(str(data["type"]))
    except KeyError as exc:
        raise ValueError("Protocol error: missing 'type' field") from exc
    except ValueError as exc:
        raise ValueError(f"Protocol error: unknown message type {data.get('type')!r}") from exc

    meta = _common(data)
    if msg_type is MessageType.LOG:
        if not isinstance(data.get("log"), str):
            raise ValueError("Protocol error: 'log' must be a string")
        return LogMessage(data["log"], **meta)
    if msg_type is MessageType.PING:
        if not isinstance(data.get("ping_id"), int):
            raise ValueError("Protocol error: 'ping_id' must be an integer")
        return PingMessage(data["ping_id"], pong=bool(data.get("pong", False)), **meta)
    if msg_type is MessageType.MSG:
        if not isinstance(data.get("sender"), str) or not isinstance(data.get("message"), str):
            raise ValueError("Protocol error: invalid chat payload")
        return ChatMessage(data["sender"], data["message"], **meta)
    if msg_type is MessageType.CMD:
        command = data.get("command")
        args = data.get("args", {})
        if not isinstance(command, str) or not isinstance(args, dict):
            raise ValueError("Protocol error: invalid command payload")
        return CommandMessage(command, args, **meta)
    if msg_type is MessageType.ERROR:
        raw_error, reason = data.get("error_type"), data.get("reason")
        if not isinstance(raw_error, str) or not isinstance(reason, str):
            raise ValueError("Protocol error: invalid error payload")
        try:
            error_type = ErrorType(raw_error)
        except ValueError as exc:
            raise ValueError(f"Protocol error: unknown error type {raw_error!r}") from exc
        details = data.get("details", {})
        if not isinstance(details, dict):
            raise ValueError("Protocol error: 'details' must be an object")
        return ErrorMessage(error_type, reason, details=details, **meta)
    raise ValueError(f"Protocol error: unhandled message type {msg_type!r}")
