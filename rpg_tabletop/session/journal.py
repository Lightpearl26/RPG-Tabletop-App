# -*- coding: utf-8 -*-

"""
Persistent history of a runtime (the Session).

A Session is:
    - immutable ordered events (the journal),
    - periodic full snapshots of the runtime state,
    - no permanent in-memory cache for inactive maps.

Only the active map exists as a SessionMapState in SessionRuntime.  When leaving
that map, MAP_CHANGED stores its complete runtime state in the event.  Loading a
map later reconstructs it from the campaign MapAsset plus the most recent map
checkpoint and the following map-local events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from json import dumps, loads
from os import replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Optional
from uuid import uuid4

from rpg_tabletop.assets.campaign import Campaign
from rpg_tabletop.assets.map import (
    CameraState,
    MapAsset,
    MapElement,
    Vec2,
)

from .models import (
    SessionEvent,
    SessionEventType,
    SessionMapState,
    SessionToken,
    _datetime_from_str,
    _datetime_to_str,
    _utc_now,
)
from .snapshot import SessionSnapshot

SESSION_FORMAT_VERSION = 2
SESSION_FILE = "session.json"
DEFAULT_SNAPSHOT_INTERVAL = 250


# =============================================================================
# Session persistence
# =============================================================================


@dataclass(slots=True)
class Session:
    name: str
    id: str = field(default_factory=lambda: uuid4().hex)
    events: list[SessionEvent] = field(default_factory=list)
    snapshots: list[SessionSnapshot] = field(default_factory=list)
    current_seq: int = 0
    snapshot_interval: int = DEFAULT_SNAPSHOT_INTERVAL
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    version: int = SESSION_FORMAT_VERSION

    @property
    def initial_snapshot(self) -> SessionSnapshot:
        if not self.snapshots:
            raise RuntimeError("The session has no initial snapshot.")
        return min(self.snapshots, key=lambda snapshot: snapshot.seq)

    def event(self, seq: int) -> Optional[SessionEvent]:
        return next((event for event in self.events if event.seq == seq), None)

    def events_between(self, start_exclusive: int, end_inclusive: int) -> list[SessionEvent]:
        return [
            event
            for event in self.events
            if start_exclusive < event.seq <= end_inclusive
        ]

    def snapshot_before_or_at(self, seq: int) -> SessionSnapshot:
        candidates = [snapshot for snapshot in self.snapshots if snapshot.seq <= seq]
        if not candidates:
            raise RuntimeError(f"No snapshot exists before event #{seq}.")
        return max(candidates, key=lambda snapshot: snapshot.seq)

    def seq_at(self, timestamp: datetime) -> int:
        """Return the last event sequence at or before a timestamp."""
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        timestamp = timestamp.astimezone(timezone.utc)
        seq = 0
        for event in self.events:
            if event.timestamp <= timestamp:
                seq = event.seq
            else:
                break
        return seq

    def latest_map_checkpoint(
        self,
        map_id: str,
        before_or_at_seq: int,
    ) -> Optional[SessionEvent]:
        """Latest MAP_CHANGED event that stored the state of `map_id`."""
        for event in reversed(self.events):
            if event.seq > before_or_at_seq:
                continue
            if event.type is not SessionEventType.MAP_CHANGED:
                continue
            if event.data.get("from_map_id") == map_id and event.data.get("from_map_state"):
                return event
        return None

    def map_state_at(
        self,
        map_id: str,
        seq: int,
        campaign: Campaign,
    ) -> SessionMapState:
        """
        Reconstruct one map at a sequence without keeping inactive maps in memory.
        """
        checkpoint = self.latest_map_checkpoint(map_id, seq)
        if checkpoint is not None:
            state = SessionMapState.from_dict(checkpoint.data["from_map_state"])
            start_seq = checkpoint.seq
        else:
            asset = MapAsset.load(campaign.map_path(map_id))
            state = SessionMapState.from_asset(map_id, asset)
            start_seq = 0

        for event in self.events_between(start_seq, seq):
            _apply_map_local_event(state, event)
        return state

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "id": self.id,
            "name": self.name,
            "created_at": _datetime_to_str(self.created_at),
            "updated_at": _datetime_to_str(self.updated_at),
            "current_seq": self.current_seq,
            "snapshot_interval": self.snapshot_interval,
            "snapshots": [snapshot.to_dict() for snapshot in self.snapshots],
            "events": [event.to_dict() for event in self.events],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        version = int(data.get("version", 1))
        if version > SESSION_FORMAT_VERSION:
            raise ValueError(
                f"Session format {version} is newer than this app understands."
            )
        session = cls(
            version=version,
            id=str(data.get("id") or uuid4().hex),
            name=str(data.get("name", "New Session")),
            created_at=_datetime_from_str(str(data.get("created_at") or _datetime_to_str(_utc_now()))),
            updated_at=_datetime_from_str(str(data.get("updated_at") or _datetime_to_str(_utc_now()))),
            current_seq=int(data.get("current_seq", 0)),
            snapshot_interval=max(1, int(data.get("snapshot_interval", DEFAULT_SNAPSHOT_INTERVAL))),
            snapshots=[SessionSnapshot.from_dict(item) for item in data.get("snapshots", [])],
            events=[SessionEvent.from_dict(item) for item in data.get("events", [])],
        )
        session.events.sort(key=lambda event: event.seq)
        session.snapshots.sort(key=lambda snapshot: snapshot.seq)
        return session

    def save(self, session_dir: Path) -> Path:
        session_dir = Path(session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
        path = session_dir / SESSION_FILE
        self.updated_at = _utc_now()

        tmp_name: Optional[str] = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                tmp_name = stream.name
                stream.write(dumps(self.to_dict(), ensure_ascii=False, indent=4))
                stream.write("\n")
            replace(tmp_name, path)
        finally:
            if tmp_name is not None:
                Path(tmp_name).unlink(missing_ok=True)
        return path

    @classmethod
    def load(cls, session_dir: Path) -> Session:
        path = Path(session_dir) / SESSION_FILE
        if not path.is_file():
            raise FileNotFoundError(f"Session file not found: {path}")
        return cls.from_dict(loads(path.read_text(encoding="utf-8")))


# =============================================================================
# Map-local replay
# =============================================================================


def _apply_map_local_event(state: SessionMapState, event: SessionEvent) -> None:
    data = event.data
    if data.get("map_id") != state.map_id:
        return

    if event.type is SessionEventType.TOKEN_ADDED:
        token = SessionToken.from_dict(data["token"])
        if state.token_by_id(token.id) is None:
            state.tokens.append(token)

    elif event.type is SessionEventType.TOKEN_MOVED:
        token = state.token_by_id(str(data["token_id"]))
        if token is not None:
            token.pos = Vec2.from_value(data["to"])

    elif event.type is SessionEventType.TOKEN_REMOVED:
        token_id = str(data["token"]["id"])
        state.tokens = [token for token in state.tokens if token.id != token_id]

    elif event.type is SessionEventType.MAP_ELEMENT_ADDED:
        element = MapElement.from_dict(data["element"])
        if not any(current.id == element.id for current in state.elements):
            state.elements.append(element)

    elif event.type is SessionEventType.MAP_ELEMENT_UPDATED:
        updated = MapElement.from_dict(data["after"])
        state.elements = [
            updated if current.id == updated.id else current
            for current in state.elements
        ]

    elif event.type is SessionEventType.MAP_ELEMENT_REMOVED:
        element_id = str(data["element"]["id"])
        state.elements = [
            element for element in state.elements if element.id != element_id
        ]

    elif event.type is SessionEventType.CAMERA_CHANGED:
        state.gm_camera = CameraState.from_dict(data["after"])

    elif event.type is SessionEventType.TOKEN_DISCLOSURE_CHANGED:
        token = state.token_by_id(str(data["token_id"]))
        if token is not None:
            token.disclosed_fields = [str(item) for item in data.get("after", [])]
