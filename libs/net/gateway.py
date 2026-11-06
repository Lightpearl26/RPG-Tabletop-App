# -*- coding: utf-8 -*-
"""Bridge between player network intents, local GM approval and SessionRuntime."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable

from ..assets.map import MapAsset, Vec2
from ..dice import DiceCheck
from ..runtime.actions import ActionError, CombatResolver, check_dice_values, spell_dice_keys
from ..runtime.initiative import check_roll
from ..runtime.targeting import TargetingError, resolve_targets
from ..runtime.rules import (
    PRIVATE_STATS, chebyshev_distance, grid_cell, STRIKE_STAMINA_COST, SHOOT_STAMINA_COST,
)
from ..runtime.session import SessionEventType, SessionRuntime
from .commands import ActionIntent, ActionKind, ClientCommand, ServerCommand
from .models import DiceProposal, PendingKind, PendingRequest, PendingStatus
from .projection import build_player_runtime_projection
from .protocol import CommandMessage, ErrorType, NetworkMessage


ActionPreparer = Callable[[SessionRuntime, str, ActionIntent, PendingRequest], None]
ActionExecutor = Callable[[SessionRuntime, str, PendingRequest], dict[str, Any]]
PendingCallback = Callable[[PendingRequest], None]
# username, entity id, d20 result
InitiativeCallback = Callable[[str, str, int], None]

# Requester name of the actions the GM starts for the entities it controls
GM_USER = "MJ"


@dataclass(slots=True)
class GatewayResult:
    replies: list[NetworkMessage] = field(default_factory=list)
    pending: PendingRequest | None = None
    broadcast_sync: bool = False
    # Messages for other players than the requester: (username, message)
    outbox: list[tuple[str, NetworkMessage]] = field(default_factory=list)


class RuntimeNetworkGateway:
    """The GM process owns this object and the SessionRuntime it wraps.

    Player requests never talk to a separate GM endpoint.  Requests requiring
    arbitration become :class:`PendingRequest` objects and are emitted locally
    to the GM UI through ``on_pending``.
    """

    def __init__(
        self,
        runtime: SessionRuntime,
        *,
        on_pending: PendingCallback | None = None,
        action_preparer: ActionPreparer | None = None,
        action_executor: ActionExecutor | None = None,
        require_gm_approval: bool = True,
    ) -> None:
        self.runtime = runtime
        self.on_pending = on_pending
        self.action_preparer = action_preparer
        self.action_executor = action_executor
        self.require_gm_approval = bool(require_gm_approval)
        self._bindings: dict[str, str] = {}
        self._pending: dict[str, PendingRequest] = {}
        self._pending_by_user: dict[str, str] = {}
        # Initiative asked to a player and not answered yet: username -> entity id
        self._initiative_requests: dict[str, str] = {}
        self.on_initiative: InitiativeCallback | None = None
        # Built-in resolution of strike, shoot and spells
        self.resolver = CombatResolver(runtime)
        # Set by the server: whether a player is connected right now
        self.is_connected: Callable[[str], bool] = lambda _username: False
        # Called when a player sent a die of a pending request
        self.on_pending_updated: PendingCallback | None = None

    # ---------------------------------------------------------------- binding
    def bind(self, username: str, entity_id: str) -> None:
        username, entity_id = str(username).strip(), str(entity_id).strip()
        if not username:
            raise ValueError("username is required")
        if entity_id not in self.runtime.entities:
            raise KeyError(f"Unknown entity: {entity_id}")
        self._bindings[username] = entity_id

    def unbind(self, username: str) -> str | None:
        return self._bindings.pop(str(username).strip(), None)

    def entity_for(self, username: str) -> str | None:
        return self._bindings.get(str(username).strip())

    def username_for(self, entity_id: str) -> str | None:
        """The player currently bound to an entity, if any."""
        return next(
            (username for username, bound in self._bindings.items() if bound == str(entity_id)),
            None,
        )

    # ------------------------------------------------------------- initiative
    def expect_initiative(self, username: str, entity_id: str) -> None:
        """Accept one INITIATIVE_ROLL from this player for this entity."""
        if self.entity_for(username) != str(entity_id):
            raise PermissionError("This player does not control this entity.")
        self._initiative_requests[str(username)] = str(entity_id)

    def forget_initiative(self, username: str) -> bool:
        return self._initiative_requests.pop(str(username), None) is not None

    def _receive_initiative(self, username: str, request: CommandMessage) -> GatewayResult:
        entity_id = self._initiative_requests.get(username)
        if entity_id is None:
            raise RuntimeError("No initiative roll was asked.")
        value = check_roll(request.args.get("value"))
        del self._initiative_requests[username]
        if self.on_initiative is not None:
            self.on_initiative(username, entity_id, value)
        return GatewayResult(replies=[CommandMessage(
            ServerCommand.INITIATIVE_RESULT.value,
            {"entity_id": entity_id, "value": value},
            reply_to=request.message_id,
        )])

    def projection_for(self, username: str) -> dict[str, Any]:
        entity_id = self.entity_for(username)
        if entity_id is None:
            raise PermissionError("No Entity is bound to this player.")
        projection = build_player_runtime_projection(self.runtime, entity_id)
        actions = projection.setdefault("actions", {})
        actions["resolver_enabled"] = True  # built-in resolver, or the custom executor
        return projection

    # ------------------------------------------------------------ pending API
    def pending(self, approval_id: str) -> PendingRequest:
        try:
            return self._pending[str(approval_id)]
        except KeyError as exc:
            raise KeyError(f"Unknown pending request: {approval_id}") from exc

    def pending_for(self, username: str) -> PendingRequest | None:
        approval_id = self._pending_by_user.get(str(username))
        return self._pending.get(approval_id) if approval_id else None

    def _queue(self, pending: PendingRequest) -> GatewayResult:
        if pending.username in self._pending_by_user:
            raise RuntimeError("REQUEST_ALREADY_PENDING")
        self._pending[pending.approval_id] = pending
        self._pending_by_user[pending.username] = pending.approval_id
        if self.on_pending is not None:
            self.on_pending(pending)
        command = (
            ServerCommand.ACTION_PENDING.value
            if pending.kind is PendingKind.ACTION else ServerCommand.REQUEST_PENDING.value
        )
        return GatewayResult(
            replies=[CommandMessage(
                command,
                {
                    "approval_id": pending.approval_id,
                    "kind": pending.kind.value,
                    "status": "waiting_for_gm",
                },
                reply_to=pending.request_message_id,
            )],
            pending=pending,
        )

    # ------------------------------------------------------------ player dice
    def _player_of(self, entity_id: str | None) -> str:
        """Connected player who controls an entity, "" when the app rolls for it."""
        if not entity_id:
            return ""
        username = self.username_for(entity_id)
        return username if username and self.is_connected(username) else ""

    def _token_entity(self, token_id: str) -> str | None:
        state = self.runtime.active_map
        token = state.token_by_id(str(token_id)) if state is not None else None
        return token.entity_id if token is not None else None

    def _player_label(self, pending: PendingRequest, die: DiceProposal) -> str:
        """What the player is asked to roll, without naming hidden attackers."""
        kind = str(pending.preview.get("kind", ""))
        action = {"strike": "un strike", "shoot": "un shoot"}.get(kind, "un sort")
        if die.key == "attacker":
            return f"Ton d100 d'attaque ({kind})"
        if die.key.startswith("defender:"):
            return f"Tu es visé par {action} : ton d100 de défense"
        if die.key == "damage":
            return f"Tes dés de dégâts ({kind}) : {die.expression}"
        parts = die.key.split(":")
        stat = parts[-1].upper()
        if parts[0] == "user":
            return f"{pending.preview.get('spell', 'Sort')} : ton d100 de {stat}"
        return f"Tu es visé par un sort : ton d100 de {stat}"

    def _ask_owners(self, pending: PendingRequest, result: GatewayResult, *, retry: bool = False) -> None:
        """Ask every player who owns a die of `pending` to roll it."""
        for die in pending.proposed_dice.values():
            if not die.owner or die.owner == GM_USER:
                continue
            die.awaiting = True
            result.outbox.append((die.owner, CommandMessage(
                ServerCommand.DICE_ROLL_REQUEST.value,
                {
                    "approval_id": pending.approval_id,
                    "key": die.key,
                    "expression": die.expression,
                    "label": self._player_label(pending, die),
                    "retry": retry,
                },
            )))

    def _cancel_awaiting(self, pending: PendingRequest, result: GatewayResult, *, reason: str = "rolled") -> None:
        """
        Close the dice dialogs still open: the GM went on without these dice
        ("rolled": the app's values count) or refused the request ("rejected").
        """
        for die in pending.proposed_dice.values():
            if die.awaiting and die.owner:
                die.awaiting = False
                result.outbox.append((die.owner, CommandMessage(
                    ServerCommand.DICE_ROLL_CANCELLED.value,
                    {"approval_id": pending.approval_id, "key": die.key, "reason": reason},
                )))

    def _receive_dice(self, username: str, request: CommandMessage) -> GatewayResult:
        pending = self.pending(str(request.args.get("approval_id", "")))
        key = str(request.args.get("key", ""))
        die = pending.proposed_dice.get(key)
        if die is None or die.owner != username or not die.awaiting:
            raise RuntimeError("This die is not waiting for you.")
        try:
            die.values = check_dice_values(die.expression, request.args.get("values", []))
        except ActionError as error:
            raise ValueError(str(error)) from error
        die.awaiting = False
        if self.on_pending_updated is not None:
            self.on_pending_updated(pending)
        return GatewayResult(replies=[CommandMessage(
            ServerCommand.DICE_ROLL_ACCEPTED.value,
            {"approval_id": pending.approval_id, "key": key, "values": list(die.values), "total": die.total},
            reply_to=request.message_id,
        )])

    def _attack_dice(self, pending: PendingRequest, attacker_id: str) -> None:
        """d100 of the attacker and of the first target, each rolled by its player."""
        existing = pending.proposed_dice
        dice: dict[str, DiceProposal] = {}
        attacker = existing.get("attacker") or DiceProposal.roll("attacker", "1d100", label="Attaque")
        attacker.owner = self._player_of(attacker_id)
        dice["attacker"] = attacker
        for token_id in pending.proposed_targets[:1]:
            key = f"defender:{token_id}"
            defender = existing.get(key) or DiceProposal.roll(key, "1d100", label="Défense")
            defender.owner = self._player_of(self._token_entity(token_id))
            dice[key] = defender
        pending.proposed_dice = dice

    def _spell_dice(self, pending: PendingRequest, caster_id: str, spell, target_ids: list[str]) -> None:
        """d100 of the spell formulas: the caster's and each target's, rolled by their player."""
        existing = pending.proposed_dice
        dice: dict[str, DiceProposal] = {}
        for key, entity_id, stat in spell_dice_keys(spell, caster_id, target_ids):
            entity = self.runtime.entities.get(entity_id)
            label = f"{entity.name if entity else entity_id} · {stat.upper()}"
            die = existing.get(key) or DiceProposal.roll(key, "1d100", label=label)
            die.owner = self._player_of(entity_id)
            dice[key] = die
        pending.proposed_dice = dice

    def _remove_pending(self, pending: PendingRequest) -> None:
        self._pending.pop(pending.approval_id, None)
        if self._pending_by_user.get(pending.username) == pending.approval_id:
            self._pending_by_user.pop(pending.username, None)

    def cancel(self, username: str, approval_id: str) -> PendingRequest:
        pending = self.pending(approval_id)
        if pending.username != username:
            raise PermissionError("Cannot cancel another player's request")
        if pending.kind is PendingKind.DAMAGE:
            raise RuntimeError("The attack already hit: its damage cannot be cancelled.")
        pending.status = PendingStatus.CANCELLED
        self._remove_pending(pending)
        return pending

    def reject(self, approval_id: str, *, reason: str = "") -> tuple[PendingRequest, GatewayResult]:
        """
        Refuse a request. Damage dice a player rolled are asked again instead:
        the request then stays waiting.
        """
        pending = self.pending(approval_id)
        result = GatewayResult()
        damage = pending.proposed_dice.get("damage")
        if pending.kind is PendingKind.DAMAGE and damage is not None and damage.owner:
            damage.reroll()  # the app's stand-in until the player answers
            self._ask_owners(pending, result, retry=True)
            return pending, result
        self._cancel_awaiting(pending, result, reason="rejected")
        pending.status = PendingStatus.REJECTED
        self._remove_pending(pending)
        result.replies.append(CommandMessage(
            self._result_command(pending),
            {"success": False, "reason": "GM_REJECTED", "message": str(reason)},
            reply_to=pending.request_message_id,
        ))
        return pending, result

    def reroll(self, approval_id: str, dice_key: str) -> PendingRequest:
        pending = self.pending(approval_id)
        pending.reroll(dice_key)
        return pending

    def edit_action(
        self,
        approval_id: str,
        *,
        targets: list[str] | None = None,
        dice: dict[str, dict[str, Any]] | None = None,
    ) -> PendingRequest:
        pending = self.pending(approval_id)
        if pending.kind not in {PendingKind.ACTION, PendingKind.STAT_CHECK, PendingKind.DAMAGE}:
            raise ValueError("This pending request has no editable action parameters")
        if targets is not None and pending.kind is not PendingKind.ACTION:
            targets = None
        if targets is not None and list(targets) != list(pending.proposed_targets):
            pending.replace_targets(targets)
            self._retarget(pending)
        for key, change in (dice or {}).items():
            die = pending.proposed_dice.get(str(key))
            if die is None or not isinstance(change, dict):
                continue
            expression, values = change.get("expression"), change.get("values")
            unchanged = (expression is None or str(expression) == die.expression) and (
                values is None or [int(value) for value in values] == die.values
            )
            if not unchanged:
                pending.set_die(str(key), expression=expression, values=values)
        return pending

    def _retarget(self, pending: PendingRequest) -> None:
        """The GM changed the targets: follow with the target list and the dice."""
        entity_ids = [
            entity_id for entity_id in (self._token_entity(t) for t in pending.proposed_targets) if entity_id
        ]
        pending.preview["target_entities"] = list(dict.fromkeys(entity_ids))
        intent = pending.action_intent
        if intent is not None and intent.kind is ActionKind.SPELL:
            caster = self.runtime.entities.get(pending.entity_id)
            spell = caster.get_spell(intent.action_id) if caster else None
            if spell is not None:
                self._spell_dice(pending, pending.entity_id, spell, pending.preview["target_entities"])
                for die in pending.proposed_dice.values():
                    die.owner = ""  # new targets chosen by the GM: the app rolls
                    die.awaiting = False

    def approve(self, approval_id: str) -> tuple[PendingRequest, GatewayResult]:
        pending = self.pending(approval_id)
        # Revalidation happens inside the actual runtime operation.
        if pending.kind is PendingKind.MOVE:
            raw = pending.payload["target"]
            event = self.runtime.move_player_token(
                pending.entity_id, Vec2(float(raw[0]), float(raw[1]))
            )
            result = GatewayResult(
                replies=[CommandMessage(
                    ServerCommand.MOVE_RESULT.value,
                    {"success": True, "event": event.to_dict()},
                    reply_to=pending.request_message_id,
                )],
                broadcast_sync=True,
            )
        elif pending.kind is PendingKind.END_TURN:
            if self.runtime.combat is None or self.runtime.combat.current_entity_id != pending.entity_id:
                raise PermissionError("This is no longer the player's turn")
            ended, started = self.runtime.next_turn()
            result = GatewayResult(
                replies=[CommandMessage(
                    ServerCommand.END_TURN_RESULT.value,
                    {"success": True, "ended": ended.to_dict(), "started": started.to_dict()},
                    reply_to=pending.request_message_id,
                )],
                broadcast_sync=True,
            )
        elif pending.kind is PendingKind.STAT_CHECK:
            proposal = pending.proposed_dice["check"]
            dice = proposal.to_dice()
            entity = self.runtime.entities[pending.entity_id]
            stat = str(pending.payload["stat"])
            check = DiceCheck(entity, stat, dice)
            event = self.runtime._dispatch(  # isolated compatibility adapter for approved dice
                SessionEventType.STAT_CHECK,
                {
                    "stat": stat,
                    "stat_value": entity.get_stat(stat),
                    "dice": dice.cmd,
                    "values": list(dice.dice_values),
                    "total": dice.total,
                    "success": check.success,
                    "critical_success": dice.critical_success,
                    "critical_failure": dice.critical_failure,
                    "gm_modified": pending.gm_modified,
                },
                message=(
                    f"{entity.name} tests {stat.upper()}: {dice.total} "
                    f"({'success' if check.success else 'failure'})"
                ),
                source_id=pending.entity_id,
                target_ids=(pending.entity_id,),
            )
            result = GatewayResult(
                replies=[CommandMessage(
                    ServerCommand.STAT_CHECK_RESULT.value,
                    {"success": True, "event": event.to_dict()},
                    reply_to=pending.request_message_id,
                )]
            )
        elif pending.kind is PendingKind.ACTION and self.action_executor is None:
            result = self._resolve_action(pending)
        elif pending.kind is PendingKind.DAMAGE:
            result = self._resolve_damage(pending)
        elif pending.kind is PendingKind.ACTION:
            payload = dict(self.action_executor(self.runtime, pending.entity_id, pending))
            payload.setdefault("success", True)
            result = GatewayResult(
                replies=[CommandMessage(
                    ServerCommand.ACTION_RESULT.value,
                    payload,
                    reply_to=pending.request_message_id,
                )],
                broadcast_sync=True,
            )
        else:
            raise RuntimeError(f"Unhandled pending kind: {pending.kind.value}")
        pending.status = PendingStatus.APPROVED
        self._remove_pending(pending)
        return pending, result

    @staticmethod
    def _result_command(pending: PendingRequest) -> str:
        return {
            PendingKind.MOVE: ServerCommand.MOVE_RESULT.value,
            PendingKind.STAT_CHECK: ServerCommand.STAT_CHECK_RESULT.value,
            PendingKind.ACTION: ServerCommand.ACTION_RESULT.value,
            PendingKind.END_TURN: ServerCommand.END_TURN_RESULT.value,
            PendingKind.DAMAGE: ServerCommand.ACTION_RESULT.value,
        }[pending.kind]

    # --------------------------------------------------------------- prepare
    def _prepare_move(self, username: str, entity_id: str, request: CommandMessage) -> PendingRequest:
        raw = request.args.get("target")
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise ValueError("target must be [x, y]")
        permissions = self.runtime.player_permissions(entity_id)
        if not permissions.can_move:
            raise PermissionError("The player cannot move in the current context")
        preview: dict[str, Any] = {"target": [float(raw[0]), float(raw[1])]}
        state = self.runtime.active_map
        if state is not None:
            token = state.token_for_entity(entity_id)
            if token is not None and self.runtime.active_map_id is not None:
                asset = MapAsset.load(self.runtime.campaign.map_path(self.runtime.active_map_id))
                target = asset.grid.cell_center(Vec2(float(raw[0]), float(raw[1]))) if asset.grid.enabled else Vec2(float(raw[0]), float(raw[1]))
                if asset.grid.enabled:
                    first = grid_cell(token.pos, cell_size=asset.grid.cell_size, offset_x=asset.grid.offset_x, offset_y=asset.grid.offset_y)
                    second = grid_cell(target, cell_size=asset.grid.cell_size, offset_x=asset.grid.offset_x, offset_y=asset.grid.offset_y)
                    preview["distance_cells"] = chebyshev_distance(first, second)
                    preview["movement_remaining"] = self.runtime.movement_remaining(entity_id)
        return PendingRequest(
            PendingKind.MOVE, username, entity_id, request.message_id,
            {"target": [float(raw[0]), float(raw[1])]}, preview=preview,
        )

    def _prepare_stat_check(self, username: str, entity_id: str, request: CommandMessage) -> PendingRequest:
        stat = str(request.args.get("stat", "")).strip().lower()
        if stat in PRIVATE_STATS:
            raise ValueError("Cette caractéristique est réservée au MJ")
        entity = self.runtime.entities[entity_id]
        if not stat or not hasattr(entity.character.stats, stat):
            raise ValueError(f"Unknown stat: {stat}")
        pending = PendingRequest(
            PendingKind.STAT_CHECK, username, entity_id, request.message_id,
            {"stat": stat},
            preview={"stat": stat, "stat_value": entity.get_stat(stat)},
        )
        pending.proposed_dice["check"] = DiceProposal.roll("check", "1d100", label="Stat check")
        return pending

    def _prepare_action(self, username: str, entity_id: str, request: CommandMessage) -> PendingRequest:
        intent = ActionIntent.from_dict(request.args)
        return self._build_action_pending(username, entity_id, intent, request.message_id, dict(request.args))

    def _build_action_pending(
        self,
        username: str,
        entity_id: str,
        intent: ActionIntent,
        message_id: str,
        payload: dict[str, Any],
        *,
        gm: bool = False,
    ) -> PendingRequest:
        permissions = self.runtime.player_permissions(entity_id)
        if not permissions.can_combat_actions:
            raise PermissionError("Combat actions are only available during the entity's turn")
        entity = self.runtime.entities[entity_id]
        stamina = self.runtime.action_stamina_remaining(entity_id)
        spell = None
        if intent.kind is ActionKind.STRIKE:
            cost = STRIKE_STAMINA_COST
        elif intent.kind is ActionKind.SHOOT:
            cost = SHOOT_STAMINA_COST
        else:
            spell = entity.get_spell(intent.action_id)
            if spell is None:
                raise KeyError(f"Unknown spell: {intent.action_id}")
            cost = int(spell.cost)
        if stamina < cost:
            raise ValueError(f"Not enough stamina: {cost} required, {stamina} remaining")
        # Melee, line of sight, area and line rules; the GM may still change the
        # targets of the pending request afterwards.
        try:
            targets = resolve_targets(
                self.runtime, entity_id, intent.kind.value, spell=spell,
                token_ids=intent.selection.token_ids, point=intent.selection.point, gm=gm,
            )
        except TargetingError as error:
            raise ValueError(str(error)) from error
        pending = PendingRequest(
            PendingKind.ACTION, username, entity_id, message_id,
            payload,
            preview={
                "kind": intent.kind.value,
                "spell": spell.name if spell is not None else "",
                "stamina_cost": cost,
                "stamina_remaining": stamina,
                "cells": [list(cell) for cell in targets.cells],
                "target_entities": list(targets.entity_ids),
            },
            proposed_targets=list(targets.token_ids),
            action_intent=intent,
        )
        if intent.kind in {ActionKind.STRIKE, ActionKind.SHOOT}:
            self._attack_dice(pending, entity_id)
        elif spell is not None:
            self._spell_dice(pending, entity_id, spell, list(targets.entity_ids))
        if self.action_preparer is not None:
            self.action_preparer(self.runtime, entity_id, intent, pending)
        return pending

    def submit_gm_action(self, entity_id: str, intent: ActionIntent) -> tuple[PendingRequest, GatewayResult]:
        """
        The GM makes the entity whose turn it is act. The request waits in the
        GM's list like a player's; players whose entity defends roll their dice.

        Raises:
            ValueError / KeyError / PermissionError: If the action is not possible now.
        """
        pending = self._build_action_pending(GM_USER, str(entity_id), intent, "", intent.to_dict(), gm=True)
        result = GatewayResult(pending=pending)
        self._ask_owners(pending, result)
        self._pending[pending.approval_id] = pending
        if self.on_pending is not None:
            self.on_pending(pending)
        return pending, result

    def _action_reply(self, pending: PendingRequest, payload: dict[str, Any]) -> CommandMessage:
        return CommandMessage(
            ServerCommand.ACTION_RESULT.value,
            {"success": True, **payload},
            reply_to=pending.request_message_id,
        )

    def _resolve_action(self, pending: PendingRequest) -> GatewayResult:
        """Step 1 of an attack (then the damage request), or a whole spell."""
        intent = pending.action_intent
        if intent is None:
            raise ValueError("This request has no action.")
        result = GatewayResult(broadcast_sync=True)
        if intent.kind in {ActionKind.STRIKE, ActionKind.SHOOT}:
            if not pending.proposed_targets:
                raise ValueError("Choose a target.")
            token_id = pending.proposed_targets[0]
            target_id = self._token_entity(token_id)
            if target_id is None:
                raise ValueError("The target is no longer on the map.")
            attack = pending.proposed_dice["attacker"].total
            defense_die = pending.proposed_dice.get(f"defender:{token_id}")
            defense = defense_die.total if defense_die else DiceProposal.roll("defender", "1d100").total
            outcome = self.resolver.roll_attack(
                pending.entity_id, intent.kind.value, target_id, attack, defense
            )
            self._cancel_awaiting(pending, result)  # the GM rolled in their place
            text = f"d100 {attack} contre {defense} : " + (
                f"touché, {outcome.expression} de dégâts" if outcome.hit else "raté"
            )
            result.replies.append(self._action_reply(pending, {
                "stage": "attack", "hit": outcome.hit, "damage": outcome.expression, "message": text,
            }))
            if outcome.hit:
                self._start_damage(pending, target_id, outcome.expression, result)
            return result

        caster = self.runtime.entities[pending.entity_id]
        spell = caster.get_spell(intent.action_id)
        if spell is None:
            raise KeyError(f"Unknown spell: {intent.action_id}")
        targets = [
            entity_id for entity_id in (self._token_entity(t) for t in pending.proposed_targets) if entity_id
        ] or list(pending.preview.get("target_entities", []))
        dice = {
            key: die.total for key, die in pending.proposed_dice.items()
            if key.startswith(("user:", "target:"))
        }
        self.resolver.cast_spell(pending.entity_id, spell.name, targets, dice)
        self._cancel_awaiting(pending, result)
        result.replies.append(self._action_reply(pending, {"stage": "spell", "message": f"{spell.name} lancé."}))
        return result

    def _start_damage(self, action: PendingRequest, target_id: str, expression: str, result: GatewayResult) -> None:
        """Step 2: the attacker's player rolls the damage dice, then the GM checks them."""
        kind = str(action.preview.get("kind", ""))
        damage = PendingRequest(
            PendingKind.DAMAGE, action.username, action.entity_id, action.request_message_id,
            {"kind": kind, "target_id": target_id, "expression": expression},
            preview={"kind": kind, "target_entities": [target_id], "damage": expression},
        )
        die = DiceProposal.roll("damage", expression, label=f"Dégâts ({expression})")
        die.owner = self._player_of(action.entity_id)
        damage.proposed_dice["damage"] = die
        self._pending[damage.approval_id] = damage
        if damage.username != GM_USER:
            self._pending_by_user[damage.username] = damage.approval_id
        self._ask_owners(damage, result)
        if self.on_pending is not None:
            self.on_pending(damage)
        result.pending = damage

    def _resolve_damage(self, pending: PendingRequest) -> GatewayResult:
        result = GatewayResult(broadcast_sync=True)
        die = pending.proposed_dice["damage"]
        event = self.resolver.apply_attack_damage(
            pending.entity_id,
            str(pending.payload.get("kind", "")),
            str(pending.payload["target_id"]),
            die.expression,
            die.values,
        )
        self._cancel_awaiting(pending, result)
        dealt = event.data.get("action", {}).get("dealt", 0)
        result.replies.append(self._action_reply(pending, {
            "stage": "damage", "dealt": dealt, "message": f"Dégâts infligés : {dealt}.",
        }))
        return result

    # --------------------------------------------------------------- dispatch
    def handle(self, username: str, request: CommandMessage) -> GatewayResult:
        try:
            command = ClientCommand(request.command)
        except ValueError:
            return self._rejected(request, ErrorType.INVALID_COMMAND, f"Unknown command: {request.command}")
        try:
            if command is ClientCommand.RUNTIME_SYNC_REQUEST:
                return GatewayResult(replies=[CommandMessage(
                    ServerCommand.RUNTIME_SYNC.value,
                    {"projection": self.projection_for(username)},
                    reply_to=request.message_id,
                )])
            if command is ClientCommand.ACTION_CANCEL_REQUEST:
                approval_id = str(request.args.get("approval_id", ""))
                pending = self.cancel(username, approval_id)
                cancelled = GatewayResult(replies=[CommandMessage(
                    ServerCommand.REQUEST_CANCELLED.value,
                    {"approval_id": pending.approval_id},
                    reply_to=request.message_id,
                )])
                self._cancel_awaiting(pending, cancelled)
                return cancelled
            if command is ClientCommand.DICE_ROLL:
                return self._receive_dice(username, request)

            entity_id = self.entity_for(username)
            if entity_id is None:
                raise PermissionError("No Entity is bound to this player")

            if command is ClientCommand.INITIATIVE_ROLL:
                return self._receive_initiative(username, request)

            if command is ClientCommand.TOKEN_INSPECT_REQUEST:
                token_id = str(request.args.get("token_id", "")).strip()
                if not token_id:
                    raise ValueError("token_id is required")
                view = self.runtime.public_entity_view(entity_id, token_id)
                return GatewayResult(replies=[CommandMessage(
                    ServerCommand.TOKEN_INSPECT_RESULT.value,
                    {"token_id": token_id, "entity": view},
                    reply_to=request.message_id,
                )])

            if command is ClientCommand.MOVE_REQUEST:
                pending = self._prepare_move(username, entity_id, request)
                if self._requires_gm(pending.kind):
                    return self._queue(pending)
                return self._execute_immediate(pending)
            if command is ClientCommand.STAT_CHECK_REQUEST:
                pending = self._prepare_stat_check(username, entity_id, request)
                return self._queue(pending) if self.require_gm_approval else self._execute_immediate(pending)
            if command is ClientCommand.END_TURN_REQUEST:
                if self.runtime.combat is None or self.runtime.combat.current_entity_id != entity_id:
                    raise PermissionError("This is not the player's turn")
                pending = PendingRequest(PendingKind.END_TURN, username, entity_id, request.message_id, {})
                return self._queue(pending) if self.require_gm_approval else self._execute_immediate(pending)
            if command is ClientCommand.ACTION_REQUEST:
                pending = self._prepare_action(username, entity_id, request)
                if not self.require_gm_approval:
                    return self._execute_immediate(pending)
                asked = GatewayResult()
                self._ask_owners(pending, asked)
                queued = self._queue(pending)
                queued.outbox.extend(asked.outbox)
                return queued
            raise ValueError(f"Command {command.value} belongs to the server/transport layer")
        except RuntimeError as exc:
            if str(exc) == "REQUEST_ALREADY_PENDING":
                return self._rejected(request, ErrorType.REQUEST_ALREADY_PENDING, "A request is already waiting for GM approval")
            return self._rejected(request, ErrorType.INVALID_STATE, str(exc))
        except PermissionError as exc:
            return self._rejected(request, ErrorType.NOT_AUTHORIZED, str(exc))
        except (KeyError, ValueError) as exc:
            return self._rejected(request, ErrorType.REQUEST_REJECTED, str(exc))

    def _requires_gm(self, kind: PendingKind) -> bool:
        if not self.require_gm_approval:
            return False
        # Out of combat, a player moves freely: no GM confirmation needed
        if kind is PendingKind.MOVE and self.runtime.combat is None:
            return False
        return True

    def _execute_immediate(self, pending: PendingRequest) -> GatewayResult:
        # Not registered per user: an immediate request never blocks a waiting one
        self._pending[pending.approval_id] = pending
        try:
            _pending, result = self.approve(pending.approval_id)
        finally:
            self._pending.pop(pending.approval_id, None)
        return result

    @staticmethod
    def _rejected(request: CommandMessage, error_type: ErrorType, reason: str) -> GatewayResult:
        return GatewayResult(replies=[CommandMessage(
            ServerCommand.REQUEST_REJECTED.value,
            {"error_type": error_type.value, "reason": reason, "request_command": request.command},
            reply_to=request.message_id,
        )])
