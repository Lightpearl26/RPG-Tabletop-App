# -*- coding: utf-8 -*-

"""
Who an action can target, on the active map.

    strike   one token in melee: a neighbouring cell (diagonals included), no
             wall in between.
    shoot    one token the shooter sees (PER radius, no wall) with a clear line
             of sight: no wall and no other token on the way.
    spells   self    the caster only.
             single  one token seen with a clear line of sight (caster included).
             multi   several such tokens, up to the spell's max_targets if set.
             area    a cell the caster sees; every token in the square of
                     radius area_size around it, except behind a wall from
                     the center (the caster too, if inside).
             line    a cell giving the direction; every token on the one-cell
                     line from the caster (excluded) up to the PER radius,
                     stopping at the first wall.

Range is always the vision radius (PER). Tokens hidden by the GM never block a
line of sight and players cannot target them; areas and lines still hit them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Optional, Sequence

from rpg_tabletop.assets.spell import Spell
from rpg_tabletop.core.geometry import (
    Cell,
    GridGeometry,
    Segment,
    area_cells,
    chebyshev,
    footprint_distance,
    line_cells,
    line_of_sight,
    wall_between,
)

if TYPE_CHECKING:
    from .models import SessionToken
    from .runtime import SessionRuntime


STRIKE_REACH = 1


class TargetingError(ValueError):
    """The chosen targets break the targeting rules of the action."""


@dataclass(frozen=True)
class MapContext:
    grid: GridGeometry
    walls: tuple[Segment, ...]


@dataclass(frozen=True)
class ActionTargets:
    """Who an action reaches."""

    token_ids: tuple[str, ...] = ()
    entity_ids: tuple[str, ...] = ()
    # Cells of an area or a line, to show what was covered
    cells: tuple[Cell, ...] = ()


@dataclass
class TargetingOptions:
    """What a player may pick right now, sent with the player projection."""

    range: int = 0
    strike: list[str] = field(default_factory=list)
    shoot: list[str] = field(default_factory=list)
    # Tokens a single / multi spell may target
    spell_tokens: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "range": self.range,
            "strike": list(self.strike),
            "shoot": list(self.shoot),
            "spell_tokens": list(self.spell_tokens),
        }


# =============================================================================
# Map
# =============================================================================


def map_context(runtime: SessionRuntime) -> MapContext:
    """
    Grid and walls of the active map.

    Raises:
        RuntimeError: Without an active map or an enabled grid.
    """
    asset = runtime._active_map_asset()
    grid = asset.grid
    if not grid.enabled or grid.cell_size <= 0:
        raise RuntimeError("Combat targeting requires an enabled map grid.")
    return MapContext(
        GridGeometry(float(grid.cell_size), float(grid.offset_x), float(grid.offset_y)),
        tuple(asset.wall_segments()),
    )


def token_cell(context: MapContext, token: SessionToken) -> Cell:
    return context.grid.cell_of(token.pos.x, token.pos.y)


def token_cells(context: MapContext, token: SessionToken) -> frozenset[Cell]:
    return context.grid.footprint(token.pos.x, token.pos.y, token.size_cells)


def _blockers(context: MapContext, tokens: Iterable[SessionToken], *exclude: str) -> set[Cell]:
    """Cells of the tokens that stand in the way of a shot (hidden ones excluded)."""
    cells: set[Cell] = set()
    for token in tokens:
        if token.hidden or token.id in exclude:
            continue
        cells |= token_cells(context, token)
    return cells


# =============================================================================
# Sight
# =============================================================================


def sees(runtime: SessionRuntime, context: MapContext, observer: SessionToken, target: SessionToken) -> bool:
    """Within the observer's PER radius and not behind a wall."""
    if observer.id == target.id:
        return True
    origin, cell = token_cell(context, observer), token_cell(context, target)
    if chebyshev(origin, cell) > runtime.vision_radius_cells(observer.entity_id):
        return False
    return not wall_between(context.grid, context.walls, origin, cell)


def clear_shot(
    runtime: SessionRuntime,
    context: MapContext,
    shooter: SessionToken,
    target: SessionToken,
) -> bool:
    """Seen, and no wall or other token between the two centers."""
    if shooter.id == target.id:
        return True
    if not sees(runtime, context, shooter, target):
        return False
    state = runtime.active_map
    blockers = _blockers(context, state.tokens if state else (), shooter.id, target.id)
    return line_of_sight(
        context.grid, context.walls, token_cell(context, shooter), token_cell(context, target), blockers
    )


def in_melee(context: MapContext, attacker: SessionToken, target: SessionToken) -> bool:
    """Neighbouring footprints with no wall between the closest cells."""
    if attacker.id == target.id:
        return False
    mine, theirs = token_cells(context, attacker), token_cells(context, target)
    if footprint_distance(mine, theirs) > STRIKE_REACH:
        return False
    return any(
        chebyshev(a, b) <= STRIKE_REACH and not wall_between(context.grid, context.walls, a, b)
        for a in mine
        for b in theirs
    )


# =============================================================================
# Options for a player
# =============================================================================


def _own_token(runtime: SessionRuntime, entity_id: str) -> SessionToken:
    state = runtime.active_map
    token = state.token_for_entity(str(entity_id)) if state is not None else None
    if token is None:
        raise TargetingError("Your token is not on the active map.")
    return token


def _candidates(runtime: SessionRuntime, *, gm: bool) -> list[SessionToken]:
    state = runtime.active_map
    tokens = list(state.tokens) if state is not None else []
    return tokens if gm else [token for token in tokens if not token.hidden]


def targeting_options(runtime: SessionRuntime, entity_id: str, *, gm: bool = False) -> TargetingOptions:
    """Tokens each kind of action may target now; empty when nothing applies."""
    options = TargetingOptions()
    try:
        context = map_context(runtime)
        own = _own_token(runtime, entity_id)
    except (RuntimeError, TargetingError):
        return options
    options.range = runtime.vision_radius_cells(entity_id)
    for token in _candidates(runtime, gm=gm):
        if in_melee(context, own, token):
            options.strike.append(token.id)
        if clear_shot(runtime, context, own, token):
            options.spell_tokens.append(token.id)
            if token.id != own.id:
                options.shoot.append(token.id)
    return options


# =============================================================================
# Validation of a chosen target
# =============================================================================


def _tokens_on(context: MapContext, tokens: Iterable[SessionToken], cells: Iterable[Cell]) -> list[SessionToken]:
    """Tokens standing on the cells, in the order of the cells (along a line)."""
    order = {cell: index for index, cell in reversed(list(enumerate(cells)))}
    hits = []
    for token in tokens:
        covered = [order[cell] for cell in token_cells(context, token) if cell in order]
        if covered:
            hits.append((min(covered), token))
    return [token for _index, token in sorted(hits, key=lambda pair: pair[0])]


def _result(tokens: Sequence[SessionToken], cells: Iterable[Cell] = ()) -> ActionTargets:
    unique = list({token.id: token for token in tokens}.values())
    return ActionTargets(
        token_ids=tuple(token.id for token in unique),
        entity_ids=tuple(dict.fromkeys(token.entity_id for token in unique)),
        cells=tuple(cells),
    )


def _picked(runtime: SessionRuntime, token_ids: Sequence[str]) -> list[SessionToken]:
    state = runtime.active_map
    picked: list[SessionToken] = []
    for token_id in dict.fromkeys(str(token_id) for token_id in token_ids):
        token = state.token_by_id(token_id) if state is not None else None
        if token is None:
            raise TargetingError("A chosen target is not on the map.")
        picked.append(token)
    return picked


def resolve_targets(
    runtime: SessionRuntime,
    entity_id: str,
    kind: str,
    *,
    spell: Optional[Spell] = None,
    token_ids: Sequence[str] = (),
    point: Optional[tuple[float, float]] = None,
    gm: bool = False,
) -> ActionTargets:
    """
    Check the targets of an action and return everyone it reaches.

    Args:
        kind: "strike", "shoot" or "spell".
        token_ids: The tokens picked (strike, shoot, single, multi).
        point: The map point picked (area center, line direction).
        gm: The GM acts: tokens hidden from players can be picked.

    Raises:
        TargetingError: If the targets break the rules.
    """
    entity_id = str(entity_id)
    if kind == "spell" and spell is not None and str(spell.targeting) == "self":
        state = runtime.active_map
        token = state.token_for_entity(entity_id) if state is not None else None
        return ActionTargets(
            token_ids=(token.id,) if token is not None else (),
            entity_ids=(entity_id,),
        )

    try:
        context = map_context(runtime)
    except RuntimeError as error:
        raise TargetingError(str(error)) from error
    own = _own_token(runtime, entity_id)
    allowed = {token.id for token in _candidates(runtime, gm=gm)}

    if kind in ("strike", "shoot") or (spell is not None and str(spell.targeting) in ("single", "multi")):
        picked = _picked(runtime, token_ids)
        if any(token.id not in allowed for token in picked):
            raise TargetingError("A chosen target cannot be seen.")
        if not picked:
            raise TargetingError("Choose a target.")

        if kind == "strike":
            if len(picked) != 1:
                raise TargetingError("A strike hits one target.")
            if not in_melee(context, own, picked[0]):
                raise TargetingError("The target is not in melee range (one cell around, no wall).")
            return _result(picked)
        if kind == "shoot":
            if len(picked) != 1:
                raise TargetingError("A shot hits one target.")
            if picked[0].id == own.id:
                raise TargetingError("You cannot shoot yourself.")
            if not clear_shot(runtime, context, own, picked[0]):
                raise TargetingError("No line of sight to the target (out of sight, wall or token in the way).")
            return _result(picked)

        assert spell is not None
        if str(spell.targeting) == "single" and len(picked) != 1:
            raise TargetingError("This spell targets one token.")
        if spell.max_targets and len(picked) > int(spell.max_targets):
            raise TargetingError(f"This spell targets at most {int(spell.max_targets)} tokens.")
        for token in picked:
            if not clear_shot(runtime, context, own, token):
                raise TargetingError("No line of sight to a target (out of sight, wall or token in the way).")
        return _result(picked)

    if kind != "spell" or spell is None:
        raise TargetingError(f"Unknown action: {kind}")
    if point is None:
        raise TargetingError("Choose a cell on the map.")

    origin = token_cell(context, own)
    aim = context.grid.cell_of(float(point[0]), float(point[1]))
    radius = runtime.vision_radius_cells(entity_id)
    every_token = list(runtime.active_map.tokens) if runtime.active_map is not None else []

    if str(spell.targeting) == "area":
        if chebyshev(origin, aim) > radius:
            raise TargetingError("The area center is out of sight range.")
        if wall_between(context.grid, context.walls, origin, aim):
            raise TargetingError("A wall hides the area center.")
        cells = area_cells(context.grid, context.walls, aim, int(spell.area_size))
        return _result(_tokens_on(context, every_token, cells), cells)

    if str(spell.targeting) == "line":
        if aim == origin:
            raise TargetingError("Choose a direction: a cell other than your own.")
        cells = line_cells(context.grid, context.walls, origin, aim, radius)
        hit = [token for token in _tokens_on(context, every_token, cells) if token.id != own.id]
        return _result(hit, cells)

    raise TargetingError(f"Unknown targeting: {spell.targeting}")


def targeting_spec(
    runtime: SessionRuntime,
    entity_id: str,
    kind: str,
    spell: Optional[Spell] = None,
    *,
    gm: bool = False,
) -> dict:
    """
    How a map view lets someone pick the targets of an action.

    Returns:
        dict: {"mode": strike | shoot | self | single | multi | area | line,
               "allowed": token ids that may be picked (None for a cell pick),
               "max": most tokens picked (0 = no limit), "area_size", "range"}.
    """
    options = targeting_options(runtime, entity_id, gm=gm)
    spec: dict = {"mode": kind, "allowed": None, "max": 1, "area_size": 0, "range": options.range}
    if kind == "strike":
        spec["allowed"] = list(options.strike)
    elif kind == "shoot":
        spec["allowed"] = list(options.shoot)
    elif spell is None:
        raise TargetingError(f"Unknown action: {kind}")
    else:
        targeting = str(spell.targeting)
        spec["mode"] = targeting
        if targeting in ("single", "multi"):
            spec["allowed"] = list(options.spell_tokens)
            spec["max"] = 1 if targeting == "single" else int(spell.max_targets or 0)
        else:
            spec["max"] = 0
            if targeting == "area":
                spec["area_size"] = int(spell.area_size)
    return spec
