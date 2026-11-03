# -*- coding: utf-8 -*-
"""Bridge between player network intents, local GM approval and SessionRuntime."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable

from ..assets.map import MapAsset, Vec2
from ..dice import DiceCheck
from ..runtime.rules import chebyshev_distance, grid_cell, STRIKE_STAMINA_COST, SHOOT_STAMINA_COST
from ..runtime.session import SessionEventType, SessionRuntime
from .commands import ActionIntent, ActionKind, ClientCommand, ServerCommand
from .models import DiceProposal, PendingKind, PendingRequest, PendingStatus
from .projection import build_player_runtime_projection
from .protocol import CommandMessage, ErrorMessage, ErrorType, NetworkMessage


ActionPreparer = Callable[[SessionRuntime, str, ActionIntent, PendingRequest], None]
ActionExecutor = Callable[[SessionRuntime, str, PendingRequest], dict[str, Any]]
PendingCallback = Callable[[PendingRequest], None]


@dataclass(slots=True)
class GatewayResult:
    replies: list[NetworkMessage] = field(default_factory=list)
    pending: PendingRequest | None = None
    broadcast_sync: bool = False


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

    def projection_for(self, username: str) -> dict[str, Any]:
        entity_id = self.entity_for(username)
        if entity_id is None:
            raise PermissionError("No Entity is bound to this player.")
        projection = build_player_runtime_projection(self.runtime, entity_id)
        actions = projection.setdefault("actions", {})
        actions["resolver_enabled"] = self.action_executor is not None
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

    def _remove_pending(self, pending: PendingRequest) -> None:
        self._pending.pop(pending.approval_id, None)
        self._pending_by_user.pop(pending.username, None)

    def cancel(self, username: str, approval_id: str) -> PendingRequest:
        pending = self.pending(approval_id)
        if pending.username != username:
            raise PermissionError("Cannot cancel another player's request")
        pending.status = PendingStatus.CANCELLED
        self._remove_pending(pending)
        return pending

    def reject(self, approval_id: str, *, reason: str = "") -> tuple[PendingRequest, NetworkMessage]:
        pending = self.pending(approval_id)
        pending.status = PendingStatus.REJECTED
        self._remove_pending(pending)
        return pending, CommandMessage(
            self._result_command(pending),
            {"success": False, "reason": "GM_REJECTED", "message": str(reason)},
            reply_to=pending.request_message_id,
        )

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
        if pending.kind not in {PendingKind.ACTION, PendingKind.STAT_CHECK}:
            raise ValueError("This pending request has no editable action parameters")
        if targets is not None:
            pending.replace_targets(targets)
        for key, change in (dice or {}).items():
            pending.set_die(
                str(key),
                expression=change.get("expression") if isinstance(change, dict) else None,
                values=change.get("values") if isinstance(change, dict) else None,
            )
        return pending

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
        elif pending.kind is PendingKind.ACTION:
            if self.action_executor is None:
                raise RuntimeError("No action_executor is connected to the network gateway")
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
        permissions = self.runtime.player_permissions(entity_id)
        if not permissions.can_combat_actions:
            raise PermissionError("Combat actions are only available during the player's turn")
        entity = self.runtime.entities[entity_id]
        stamina = self.runtime.action_stamina_remaining(entity_id)
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
        pending = PendingRequest(
            PendingKind.ACTION, username, entity_id, request.message_id,
            dict(request.args),
            preview={"stamina_cost": cost, "stamina_remaining": stamina},
            proposed_targets=list(intent.selection.token_ids),
            action_intent=intent,
        )
        if intent.kind in {ActionKind.STRIKE, ActionKind.SHOOT}:
            pending.proposed_dice["attacker"] = DiceProposal.roll("attacker", "1d100", label="Attacker")
            for token_id in pending.proposed_targets[:1]:
                pending.proposed_dice[f"defender:{token_id}"] = DiceProposal.roll(
                    f"defender:{token_id}", "1d100", label="Defender"
                )
        if self.action_preparer is not None:
            self.action_preparer(self.runtime, entity_id, intent, pending)
        return pending

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
                return GatewayResult(replies=[CommandMessage(
                    ServerCommand.REQUEST_CANCELLED.value,
                    {"approval_id": pending.approval_id},
                    reply_to=request.message_id,
                )])

            entity_id = self.entity_for(username)
            if entity_id is None:
                raise PermissionError("No Entity is bound to this player")

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
                return self._queue(pending) if self.require_gm_approval else self._execute_immediate(pending)
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
                return self._queue(pending) if self.require_gm_approval else self._execute_immediate(pending)
            raise ValueError(f"Command {command.value} belongs to the server/transport layer")
        except RuntimeError as exc:
            if str(exc) == "REQUEST_ALREADY_PENDING":
                return self._rejected(request, ErrorType.REQUEST_ALREADY_PENDING, "A request is already waiting for GM approval")
            return self._rejected(request, ErrorType.INVALID_STATE, str(exc))
        except PermissionError as exc:
            return self._rejected(request, ErrorType.NOT_AUTHORIZED, str(exc))
        except (KeyError, ValueError) as exc:
            return self._rejected(request, ErrorType.REQUEST_REJECTED, str(exc))

    def _execute_immediate(self, pending: PendingRequest) -> GatewayResult:
        self._pending[pending.approval_id] = pending
        self._pending_by_user[pending.username] = pending.approval_id
        _pending, result = self.approve(pending.approval_id)
        return result

    @staticmethod
    def _rejected(request: CommandMessage, error_type: ErrorType, reason: str) -> GatewayResult:
        return GatewayResult(replies=[CommandMessage(
            ServerCommand.REQUEST_REJECTED.value,
            {"error_type": error_type.value, "reason": reason, "request_command": request.command},
            reply_to=request.message_id,
        )])
