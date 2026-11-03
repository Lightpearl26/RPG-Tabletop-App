# -*- coding: utf-8 -*-
"""Local-only network models used by the GM-hosted server."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from ..dice import Dice
from .commands import ActionIntent, ActionKind


class PendingKind(str, Enum):
    MOVE = "move"
    STAT_CHECK = "stat_check"
    ACTION = "action"
    END_TURN = "end_turn"


class PendingStatus(str, Enum):
    WAITING = "waiting"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class DiceProposal:
    key: str
    expression: str
    values: list[int]
    label: str = ""

    @property
    def total(self) -> int:
        return sum(int(v) for v in self.values)

    @classmethod
    def roll(cls, key: str, expression: str, *, label: str = "") -> "DiceProposal":
        dice = Dice.roll(expression)
        return cls(key, expression, list(dice.dice_values), label)

    def reroll(self) -> None:
        dice = Dice.roll(self.expression)
        self.values = list(dice.dice_values)

    def to_dice(self) -> Dice:
        return Dice(self.expression, [int(v) for v in self.values])

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "expression": self.expression,
            "values": list(self.values),
            "total": self.total,
            "label": self.label,
        }


@dataclass(slots=True)
class PendingRequest:
    kind: PendingKind
    username: str
    entity_id: str
    request_message_id: str
    payload: dict[str, Any]
    approval_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: PendingStatus = PendingStatus.WAITING
    preview: dict[str, Any] = field(default_factory=dict)
    proposed_targets: list[str] = field(default_factory=list)
    proposed_dice: dict[str, DiceProposal] = field(default_factory=dict)
    action_intent: ActionIntent | None = None
    gm_modified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "kind": self.kind.value,
            "username": self.username,
            "entity_id": self.entity_id,
            "request_message_id": self.request_message_id,
            "created_at": self.created_at,
            "status": self.status.value,
            "payload": dict(self.payload),
            "preview": dict(self.preview),
            "proposed_targets": list(self.proposed_targets),
            "proposed_dice": {key: value.to_dict() for key, value in self.proposed_dice.items()},
            "action_intent": self.action_intent.to_dict() if self.action_intent else None,
            "gm_modified": self.gm_modified,
        }

    def replace_targets(self, targets: list[str]) -> None:
        new_targets = [str(value).strip() for value in targets if str(value).strip()]
        self.proposed_targets = new_targets
        if self.action_intent is not None and self.action_intent.kind in {ActionKind.STRIKE, ActionKind.SHOOT}:
            kept = {
                key: value for key, value in self.proposed_dice.items()
                if not key.startswith("defender:")
            }
            for token_id in new_targets[:1]:
                key = f"defender:{token_id}"
                existing = self.proposed_dice.get(key)
                kept[key] = existing or DiceProposal.roll(key, "1d100", label="Defender")
            self.proposed_dice = kept
        self.gm_modified = True

    def set_die(self, key: str, *, expression: str | None = None, values: list[int] | None = None) -> None:
        if key not in self.proposed_dice:
            raise KeyError(f"Unknown pending die: {key}")
        proposal = self.proposed_dice[key]
        if expression is not None:
            # Validate command through Dice parsing.
            Dice(str(expression), [1] * max(1, int(str(expression).split('d')[0])))
            proposal.expression = str(expression)
        if values is not None:
            checked = [int(v) for v in values]
            dice_type = int(proposal.expression.split('d')[1])
            expected = int(proposal.expression.split('d')[0])
            if len(checked) != expected or any(v < 1 or v > dice_type for v in checked):
                raise ValueError("Dice values do not match the dice expression")
            proposal.values = checked
        self.gm_modified = True

    def reroll(self, key: str) -> None:
        if key not in self.proposed_dice:
            raise KeyError(f"Unknown pending die: {key}")
        self.proposed_dice[key].reroll()
        self.gm_modified = True
