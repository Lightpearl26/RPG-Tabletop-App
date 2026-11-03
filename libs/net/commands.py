# -*- coding: utf-8 -*-
"""Command names and JSON-safe action intents."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ClientCommand(str, Enum):
    AUTH = "AUTH"
    QUIT = "QUIT"
    RUNTIME_SYNC_REQUEST = "RUNTIME_SYNC_REQUEST"
    STAT_CHECK_REQUEST = "STAT_CHECK_REQUEST"
    MOVE_REQUEST = "MOVE_REQUEST"
    ACTION_REQUEST = "ACTION_REQUEST"
    ACTION_CANCEL_REQUEST = "ACTION_CANCEL_REQUEST"
    TOKEN_INSPECT_REQUEST = "TOKEN_INSPECT_REQUEST"
    END_TURN_REQUEST = "END_TURN_REQUEST"
    ASSET_MANIFEST_REQUEST = "ASSET_MANIFEST_REQUEST"
    ASSET_REQUEST = "ASSET_REQUEST"


class ServerCommand(str, Enum):
    AUTH_OK = "AUTH_OK"
    ENTITY_BOUND = "ENTITY_BOUND"
    ENTITY_UNBOUND = "ENTITY_UNBOUND"
    RUNTIME_SYNC = "RUNTIME_SYNC"
    REQUEST_PENDING = "REQUEST_PENDING"
    ACTION_PENDING = "ACTION_PENDING"
    STAT_CHECK_RESULT = "STAT_CHECK_RESULT"
    MOVE_RESULT = "MOVE_RESULT"
    ACTION_RESULT = "ACTION_RESULT"
    TOKEN_INSPECT_RESULT = "TOKEN_INSPECT_RESULT"
    END_TURN_RESULT = "END_TURN_RESULT"
    REQUEST_REJECTED = "REQUEST_REJECTED"
    REQUEST_CANCELLED = "REQUEST_CANCELLED"
    PLAYER_LIST = "PLAYER_LIST"
    PLAYER_JOINED = "PLAYER_JOINED"
    PLAYER_LEFT = "PLAYER_LEFT"
    PLAYER_PINGS = "PLAYER_PINGS"
    ASSET_MANIFEST = "ASSET_MANIFEST"
    ASSET_CHUNK = "ASSET_CHUNK"
    ASSET_DONE = "ASSET_DONE"


class ActionKind(str, Enum):
    STRIKE = "strike"
    SHOOT = "shoot"
    SPELL = "spell"


@dataclass(frozen=True, slots=True)
class TargetSelection:
    token_ids: tuple[str, ...] = ()
    point: tuple[float, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_ids": list(self.token_ids),
            "point": list(self.point) if self.point is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "TargetSelection":
        data = data or {}
        raw_tokens = data.get("token_ids", [])
        if not isinstance(raw_tokens, list):
            raise ValueError("selection.token_ids must be a list")
        token_ids = tuple(str(item).strip() for item in raw_tokens if str(item).strip())
        point = None
        raw_point = data.get("point")
        if raw_point is not None:
            if not isinstance(raw_point, (list, tuple)) or len(raw_point) != 2:
                raise ValueError("selection.point must be [x, y]")
            point = (float(raw_point[0]), float(raw_point[1]))
        return cls(token_ids=token_ids, point=point)


@dataclass(frozen=True, slots=True)
class ActionIntent:
    kind: ActionKind
    action_id: str = ""
    selection: TargetSelection = field(default_factory=TargetSelection)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "action_id": self.action_id,
            "selection": self.selection.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ActionIntent":
        if not isinstance(data, dict):
            raise ValueError("action payload must be an object")
        try:
            kind = ActionKind(str(data.get("kind", "")))
        except ValueError as exc:
            raise ValueError("action.kind must be strike, shoot or spell") from exc
        action_id = str(data.get("action_id", "")).strip()
        if kind is ActionKind.SPELL and not action_id:
            raise ValueError("spell action requires action_id")
        return cls(kind, action_id, TargetSelection.from_dict(data.get("selection")))
