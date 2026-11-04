# -*- coding: utf-8 -*-

"""
Session management on disk: preload, duplicate, rename and time travel.

Nothing here depends on a Qt widget. A session lives in `sessions/<name>/` and
`SessionRuntime.save` writes to `campaign.session_path(session.name)`, so the
folder name and `Session.name` must always be the same string: every name goes
through `normalize_name`.

Time travel never rewrites history in memory: a point of the past is a prefix of
the journal (events and snapshots with `seq <= point`). Restoring truncates the
session in place (after a backup copy); branching saves that prefix as a new
session.
"""

from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional, Sequence
from uuid import uuid4

from ..assets.campaign import Campaign
from .entity import Entity
from .session import SESSION_FILE, Session, SessionRuntime


INVALID_NAME_CHARACTERS = '<>:"/\\|?*'
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


# =============================================================================
# Names and listings
# =============================================================================


def normalize_name(name: str) -> str:
    """
    Return the folder-safe session name.

    Raises:
        ValueError: If nothing usable is left.
    """
    cleaned = "".join("_" if c in INVALID_NAME_CHARACTERS else c for c in str(name))
    cleaned = " ".join(cleaned.strip().rstrip(". ").split())
    if not cleaned:
        raise ValueError("The session name cannot be empty.")
    if cleaned.upper() in RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def unique_name(campaign: Campaign, base: str) -> str:
    """
    Return `base`, or `base 2`, `base 3`... the first one no session uses.
    """
    base = normalize_name(base)
    candidate, index = base, 2
    while campaign.session_path(candidate).exists():
        candidate = f"{base} {index}"
        index += 1
    return candidate


def entity_names(campaign: Campaign) -> list[str]:
    """
    Names of the entities of the campaign, as `Entity.from_name` expects them.
    """
    names: set[str] = set()
    if campaign.entity_path.is_dir():
        for path in campaign.entity_path.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                name = raw.get("name") if isinstance(raw, dict) else None
            except (OSError, ValueError):
                name = None
            names.add(str(name).strip() if name else path.stem.replace("_", " "))
    return sorted(names, key=str.casefold)


def map_names(campaign: Campaign) -> list[str]:
    """
    Folder names of the maps that can be loaded.
    """
    if not campaign.maps_path.is_dir():
        return []
    return sorted(
        (
            path.name
            for path in campaign.maps_path.iterdir()
            if path.is_dir() and (path / "map.json").is_file()
        ),
        key=str.casefold,
    )


@dataclass(frozen=True)
class SessionInfo:
    """Light description of a saved session, read without rebuilding it."""

    name: str
    path: Path
    current_seq: int
    updated_at: str
    event_count: int
    snapshot_count: int


def list_sessions(campaign: Campaign) -> list[SessionInfo]:
    """
    List the saved sessions of the campaign, most recently updated first.
    """
    root = campaign.sessions_path
    result: list[SessionInfo] = []
    if not root.is_dir():
        return result
    for path in root.iterdir():
        file = path / SESSION_FILE
        if path.name.startswith(".") or not file.is_file():
            continue
        try:
            raw = json.loads(file.read_text(encoding="utf-8"))
            result.append(
                SessionInfo(
                    name=path.name,
                    path=path,
                    current_seq=int(raw.get("current_seq", 0)),
                    updated_at=str(raw.get("updated_at", "")),
                    event_count=len(raw.get("events", [])),
                    snapshot_count=len(raw.get("snapshots", [])),
                )
            )
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return sorted(result, key=lambda info: info.updated_at, reverse=True)


# =============================================================================
# State preview
# =============================================================================


@dataclass(frozen=True)
class EntityState:
    name: str
    hp: int
    stamina: int


@dataclass(frozen=True)
class StateSummary:
    """What a session looked like right after one event."""

    seq: int
    timestamp: datetime
    active_map_id: Optional[str]
    token_count: int
    combat_round: Optional[int]
    current_entity: Optional[str]
    entities: tuple[EntityState, ...]

    @property
    def entity_names(self) -> frozenset[str]:
        return frozenset(entity.name for entity in self.entities)

    def describe(self) -> str:
        """Human-readable multi-line text."""
        lines = [
            f"State after event #{self.seq}" if self.seq else "Initial state",
            f"Map: {self.active_map_id or 'none'} ({self.token_count} token(s))",
        ]
        if self.combat_round is None:
            lines.append("Combat: none")
        else:
            lines.append(
                f"Combat: round {self.combat_round}, turn of {self.current_entity or '?'}"
            )
        lines.append(f"Entities ({len(self.entities)}):")
        lines.extend(
            f"  {e.name} - HP {e.hp} / STA {e.stamina}" for e in self.entities
        )
        return "\n".join(lines)


@contextmanager
def preserved_registry(campaign: Campaign) -> Iterator[None]:
    """
    Rebuilding a runtime clears `campaign.entities`; put it back afterwards so a
    preview never disturbs a runtime window that is open on the same campaign.
    """
    entities, spells = campaign.entities.items(), campaign.spells.items()
    try:
        yield
    finally:
        campaign.entities.clear()
        campaign.spells.clear()
        for key, value in entities:
            campaign.entities.register(key, value)
        for key, value in spells:
            campaign.spells.register(key, value)


def summarize(campaign: Campaign, session: Session, seq: Optional[int] = None) -> StateSummary:
    """
    Rebuild the session at `seq` (default: the end) and describe it. Read-only.
    """
    seq = session.current_seq if seq is None else max(0, min(int(seq), session.current_seq))
    with preserved_registry(campaign):
        runtime = SessionRuntime.reconstruct(session, campaign, seq, recording=False)
        event = session.event(seq)
        combat = runtime.combat
        current = (
            runtime.entities.get(combat.current_entity_id or "") if combat is not None else None
        )
        return StateSummary(
            seq=seq,
            timestamp=event.timestamp if event is not None else session.initial_snapshot.timestamp,
            active_map_id=runtime.active_map_id,
            token_count=len(runtime.active_map.tokens) if runtime.active_map is not None else 0,
            combat_round=combat.round if combat is not None else None,
            current_entity=current.name if current is not None else None,
            entities=tuple(
                EntityState(e.name, int(e.get_stat("hp")), int(e.get_stat("stamina")))
                for e in sorted(runtime.entities.values(), key=lambda e: e.name.casefold())
            ),
        )


# =============================================================================
# Preload
# =============================================================================


def _load_entities(names: Sequence[str]) -> list[Entity]:
    entities: list[Entity] = []
    for name in dict.fromkeys(names):
        try:
            entity = Entity.from_name(name)
        except (TypeError, ValueError):
            entity = None
        if entity is None:
            raise ValueError(f"Entity not found, or without id or base character: {name}")
        entities.append(entity)
    return entities


def create(
    campaign: Campaign,
    name: str,
    entities: Sequence[str],
    map_id: Optional[str],
) -> Path:
    """
    Create a session whose initial snapshot holds `entities` and `map_id`.

    Returns:
        Path: The session folder.

    Raises:
        FileExistsError: If a session already uses this name.
    """
    name = normalize_name(name)
    target = campaign.session_path(name)
    if target.exists():
        raise FileExistsError(f"A session already uses this folder: {target}")
    runtime = SessionRuntime.start_new(
        name, campaign, entities=_load_entities(entities), initial_map_id=map_id
    )
    runtime.save(target)
    return target


def apply_setup(
    campaign: Campaign,
    name: str,
    entities: Sequence[str],
    map_id: Optional[str],
) -> Path:
    """
    Apply an edited preload to a saved session. The session must not be open.

    - Not started (`current_seq == 0`): the initial snapshot is rebuilt.
    - Started: entities are added or removed and the map is changed through
      recorded events. Removing an entity first removes its tokens on the active
      map; an entity in the active combat cannot be removed.
    """
    directory = campaign.session_path(name)
    session = Session.load(directory)

    if session.current_seq == 0:
        runtime = SessionRuntime.start_new(
            session.name,
            campaign,
            entities=_load_entities(entities),
            initial_map_id=map_id,
            snapshot_interval=session.snapshot_interval,
        )
        runtime.session.id = session.id
        runtime.session.created_at = session.created_at
        runtime.save(directory)
        return directory

    runtime = SessionRuntime.load(session, campaign)
    wanted = set(entities)
    by_name = {entity.name: entity_id for entity_id, entity in runtime.entities.items()}
    for name in sorted(set(by_name) - wanted, key=str.casefold):
        entity_id = by_name[name]
        if runtime.combat is not None and entity_id in runtime.combat.participant_ids:
            raise ValueError(f"{name} takes part in the active combat: end it first.")
        # Tokens of the active map go first so none points at a missing entity.
        # Tokens kept in the checkpoints of other maps are not touched.
        if runtime.active_map is not None:
            for token in [t for t in runtime.active_map.tokens if t.entity_id == entity_id]:
                runtime.remove_token(token.id)
        runtime.remove_entity(entity_id)
    present = set(by_name) & wanted
    for entity in _load_entities([n for n in entities if n not in present]):
        runtime.add_entity(entity)
    if map_id != runtime.active_map_id:
        runtime.change_map(map_id)
    runtime.save(directory)
    return directory


# =============================================================================
# Time travel, duplicate, rename
# =============================================================================


def truncate(session: Session, seq: int) -> Session:
    """
    Return a copy of `session` as it was after event `seq`.

    Only the journal is cut: events and snapshots up to `seq` are kept, so the
    state is rebuilt by the usual replay from the latest earlier snapshot.

    Raises:
        ValueError: If `seq` is outside the session.
    """
    seq = int(seq)
    if not 0 <= seq <= session.current_seq:
        raise ValueError(f"Event #{seq} does not exist in this session.")
    return Session(
        name=session.name,
        id=session.id,
        events=[event for event in session.events if event.seq <= seq],
        snapshots=[snapshot for snapshot in session.snapshots if snapshot.seq <= seq],
        current_seq=seq,
        snapshot_interval=session.snapshot_interval,
        created_at=session.created_at,
    )


def _save_as_new(campaign: Campaign, session: Session, new_name: str) -> Path:
    new_name = normalize_name(new_name)
    target = campaign.session_path(new_name)
    if target.exists():
        raise FileExistsError(f"A session already uses this folder: {target}")
    # A new identity: players cache downloaded map files per session id.
    session.name = new_name
    session.id = uuid4().hex
    session.created_at = datetime.now(timezone.utc)
    staging = target.with_name(f".{target.name}.tmp")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        session.save(staging)
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def branch(
    campaign: Campaign,
    name: str,
    new_name: str,
    seq: Optional[int] = None,
) -> Path:
    """
    Save the session as it was after event `seq` (default: now) under a new name.
    """
    session = Session.load(campaign.session_path(name))
    return _save_as_new(
        campaign,
        truncate(session, session.current_seq if seq is None else seq),
        new_name,
    )


def duplicate(campaign: Campaign, name: str, new_name: str) -> Path:
    """
    Copy a session under a new name and a new id.
    """
    return branch(campaign, name, new_name)


def restore(
    campaign: Campaign,
    name: str,
    seq: int,
    *,
    keep_backup: bool = True,
) -> Optional[Path]:
    """
    Go back to event `seq` in place; every later event is removed. The session
    must not be open in a runtime window.

    Returns:
        Optional[Path]: The backup copy (the session as it was), if one was made.
    """
    directory = campaign.session_path(name)
    session = Session.load(directory)
    if not 0 <= int(seq) < session.current_seq:
        raise ValueError("Choose an event earlier than the current state.")
    backup = (
        branch(campaign, name, unique_name(campaign, f"{name} (before restore)"))
        if keep_backup
        else None
    )
    truncate(session, seq).save(directory)
    return backup


def rename(campaign: Campaign, name: str, new_name: str) -> Path:
    """
    Rename the session folder and `Session.name`; the id is kept. The session must
    not be open in a runtime window.
    """
    new_name = normalize_name(new_name)
    source = campaign.session_path(name)
    target = campaign.session_path(new_name)
    if new_name == name:
        return source
    if target.exists() and new_name.casefold() != name.casefold():
        raise FileExistsError(f"A session already uses this folder: {target}")
    source.rename(target)
    try:
        session = Session.load(target)
        session.name = new_name
        session.save(target)
    except BaseException:
        target.rename(source)
        raise
    return target