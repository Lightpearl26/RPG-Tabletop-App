# -*- coding: utf-8 -*-

"""SessionRuntime: construction, reconstruction from the journal, snapshots, notes and save.

The other concerns live in the mixin modules of this package: events (how journal
events change the state), player_rules, maps, entities, sheet_edits and combat."""

from __future__ import annotations

from datetime import datetime
from json import loads
from pathlib import Path
from typing import Iterable, Optional

from rpg_tabletop.assets.campaign import Campaign
from rpg_tabletop.assets.map import MapAsset
from rpg_tabletop.session.entity import Entity
from rpg_tabletop.session.journal import DEFAULT_SNAPSHOT_INTERVAL, Session
from rpg_tabletop.session.models import (
    CombatSession,
    SessionEvent,
    SessionEventType,
    SessionMapState,
    _utc_now,
)
from rpg_tabletop.session.snapshot import (
    SessionSnapshot,
    _restore_entities,
    entity_to_snapshot,
)
from rpg_tabletop.session.spell_event import share_effects

from .combat import CombatMixin
from .entities import EntityMixin
from .events import EventMixin
from .maps import MapMixin
from .player_rules import PlayerRulesMixin
from .sheet_edits import SheetEditMixin


class SessionRuntime(
    EventMixin,
    PlayerRulesMixin,
    MapMixin,
    EntityMixin,
    SheetEditMixin,
    CombatMixin,
):
    """
    Mutable live state of one Session.

    Only active_map is loaded.  All changes should enter through this object so
    that the event log remains sufficient to reconstruct the runtime.
    """

    def __init__(
        self,
        session: Session,
        campaign: Campaign,
        *,
        entities: Optional[dict[str, Entity]] = None,
        active_map_id: Optional[str] = None,
        active_map: Optional[SessionMapState] = None,
        combat: Optional[CombatSession] = None,
        seq: Optional[int] = None,
        recording: bool = True,
    ) -> None:
        self.session = session
        self.campaign = campaign
        self.entities: dict[str, Entity] = entities or {}
        self.active_map_id = active_map_id
        self.active_map = active_map
        self.combat = combat
        self.seq = session.current_seq if seq is None else seq
        self.recording = recording
        # Entity fields edited from the GM character sheet and not yet written to
        # the campaign files: {entity_id: {"items", "stats_modifier.str", ...}}.
        # Kept in memory only: reopening a session never rewrites the campaign.
        self.asset_changes: dict[str, set[str]] = {}

        # V1 sessions did not persist per-turn budgets. When one is loaded,
        # initialize the current turn from the Entity's current AGI/Stamina.
        if (
            self.combat is not None
            and self.combat.turn_state is None
            and self.combat.current_entity_id in self.entities
        ):
            self.combat.turn_state = self._turn_state_for(self.combat.current_entity_id)

    # ------------------------------------------------------------------ setup

    @classmethod
    def start_new(
        cls,
        name: str,
        campaign: Campaign,
        *,
        entities: Optional[Iterable[Entity]] = None,
        initial_map_id: Optional[str] = None,
        snapshot_interval: int = DEFAULT_SNAPSHOT_INTERVAL,
    ) -> SessionRuntime:
        """
        Start a session from the current campaign state.

        If `entities` is omitted, every entity JSON in the campaign is loaded.
        """
        if entities is None:
            loaded: list[Entity] = []
            campaign.entities.clear()
            for path in sorted(campaign.entity_path.glob("*.json")):
                raw = loads(path.read_text(encoding="utf-8"))
                entity_name = str(raw.get("name") or path.stem)
                entity = Entity.from_name(entity_name)
                if entity:
                    loaded.append(entity)
            entities = loaded

        entity_dict = {str(entity.id): entity for entity in entities}
        # Entities loaded one by one from their files hold separate effect copies
        share_effects(entity_dict.values())
        campaign.entities.clear()
        for entity_id, entity in entity_dict.items():
            campaign.entities.register(entity_id, entity)
            for spell in entity.spells.values():
                campaign.spells.register(spell.name, spell)

        active_map = None
        if initial_map_id is not None:
            asset = MapAsset.load(campaign.map_path(initial_map_id))
            active_map = SessionMapState.from_asset(initial_map_id, asset)

        session = Session(
            name=name,
            snapshot_interval=max(1, int(snapshot_interval)),
        )
        runtime = cls(
            session,
            campaign,
            entities=entity_dict,
            active_map_id=initial_map_id,
            active_map=active_map,
            combat=None,
            seq=0,
        )
        session.snapshots.append(runtime._capture_snapshot(seq=0))
        return runtime

    @classmethod
    def load(cls, session: Session, campaign: Campaign) -> SessionRuntime:
        return cls.reconstruct(session, campaign, session.current_seq, recording=True)

    @classmethod
    def reconstruct(
        cls,
        session: Session,
        campaign: Campaign,
        seq: int,
        *,
        recording: bool = False,
    ) -> SessionRuntime:
        """Rebuild a runtime at any historical sequence."""
        seq = max(0, min(int(seq), session.current_seq))
        snapshot = session.snapshot_before_or_at(seq)
        entities = _restore_entities(snapshot.entities, campaign)
        active_map = (
            SessionMapState.from_dict(snapshot.active_map_state.to_dict())
            if snapshot.active_map_state is not None
            else None
        )
        combat = (
            CombatSession.from_dict(snapshot.combat.to_dict())
            if snapshot.combat is not None
            else None
        )

        runtime = cls(
            session,
            campaign,
            entities=entities,
            active_map_id=snapshot.active_map_id,
            active_map=active_map,
            combat=combat,
            seq=snapshot.seq,
            recording=recording,
        )
        for event in session.events_between(snapshot.seq, seq):
            runtime._apply_event(event)
            runtime.seq = event.seq
        return runtime

    @classmethod
    def reconstruct_at(
        cls,
        session: Session,
        campaign: Campaign,
        timestamp: datetime,
    ) -> SessionRuntime:
        return cls.reconstruct(session, campaign, session.seq_at(timestamp), recording=False)

    # --------------------------------------------------------------- snapshots

    def _capture_snapshot(self, *, seq: Optional[int] = None) -> SessionSnapshot:
        return SessionSnapshot(
            seq=self.seq if seq is None else seq,
            timestamp=_utc_now(),
            entities={
                entity_id: entity_to_snapshot(entity)
                for entity_id, entity in self.entities.items()
            },
            active_map_id=self.active_map_id,
            active_map_state=(
                SessionMapState.from_dict(self.active_map.to_dict())
                if self.active_map is not None
                else None
            ),
            combat=(
                CombatSession.from_dict(self.combat.to_dict())
                if self.combat is not None
                else None
            ),
        )

    def create_snapshot(self, *, force: bool = False) -> SessionSnapshot:
        existing = next(
            (snapshot for snapshot in self.session.snapshots if snapshot.seq == self.seq),
            None,
        )
        if existing is not None:
            return existing

        if not force and self.seq % self.session.snapshot_interval != 0:
            raise ValueError("Current sequence is not a snapshot boundary.")

        snapshot = self._capture_snapshot()
        self.session.snapshots.append(snapshot)
        self.session.snapshots.sort(key=lambda item: item.seq)
        return snapshot

    def add_note(self, message: str, *, source_id: Optional[str] = None) -> SessionEvent:
        return self._dispatch(
            SessionEventType.NOTE,
            {},
            message=message,
            source_id=source_id,
        )

    # --------------------------------------------------------------- save/load

    def save(self, session_dir: Optional[Path] = None) -> Path:
        """Force a current checkpoint, then atomically persist the Session."""
        self.create_snapshot(force=True)
        if session_dir is None:
            session_dir = self.campaign.session_path(self.session.name)
        return self.session.save(session_dir)
