# -*- coding: utf-8 -*-

"""Map canvas tokens: initial token placements, selection, snapped moves, drawing and drag-and-drop."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPainter,
    QPen,
)

from rpg_tabletop.assets.map import (
    InitialTokenPlacement,
    Rect,
    Vec2,
)
from rpg_tabletop.ui.campaign_manager.editors.map.state import (
    TokenMoveSession,
)
from rpg_tabletop.ui.campaign_manager.editors.map.tokens import (
    TOKEN_SOURCE_MIME,
    TokenViewData,
)


class TokenMixin:
    """Initial token placements: selection, moves with snapping, drawing and drag-and-drop decoding. Mixed into MapCanvas."""

    # -------------------------------------------------------------------------
    # Tokens
    # -------------------------------------------------------------------------

    def _token_view(self, entity_id: str) -> TokenViewData:
        view = self.token_provider.token_view(entity_id)
        if view is not None:
            return view
        label = entity_id[:8] if entity_id else "Entity"
        return TokenViewData(entity_id=entity_id, name=label)

    def _token_size(self, placement: InitialTokenPlacement) -> float:
        cell = max(1.0, float(self.asset.grid.cell_size))
        return max(8.0, cell * max(0.1, float(placement.size_cells)))

    def _token_rect(self, placement: InitialTokenPlacement) -> Rect:
        size = self._token_size(placement)
        return Rect(
            placement.pos.x - size / 2.0,
            placement.pos.y - size / 2.0,
            size,
            size,
        )

    def token_at(self, point: Vec2) -> Optional[InitialTokenPlacement]:
        # Tokens are an overlay; the last placement is considered top-most.
        for placement in reversed(self.asset.initial_tokens):
            if self._contains(self._token_rect(placement), point):
                return placement
        return None

    def selected_tokens(self) -> list[InitialTokenPlacement]:
        """Return selected token placements in map order."""
        if not self.selected_token_ids:
            return []

        return [
            token
            for token in self.asset.initial_tokens
            if token.id in self.selected_token_ids
        ]

    def selected_token(self) -> Optional[InitialTokenPlacement]:
        if self.selected_token_id is None:
            return None

        return next(
            (
                token
                for token in self.asset.initial_tokens
                if token.id == self.selected_token_id
            ),
            None,
        )

    def _select_tokens(
        self,
        tokens: list[InitialTokenPlacement],
        *,
        primary: Optional[InitialTokenPlacement] = None,
    ) -> None:
        """
        Select token placements.

        Token selection and static MapElement selection are exclusive so their
        transformation semantics never conflict.
        """
        ids = {
            token.id
            for token in tokens
        }

        primary_id = (
            primary.id
            if primary is not None and primary.id in ids
            else None
        )
        if primary_id is None and tokens:
            primary_id = tokens[-1].id

        # Clear MapElement selection directly. Calling _select_elements() here
        # would also clear the token selection we are currently constructing.
        self.selected_element_ids.clear()
        self.selected_element_id = None
        self.camera_selected = False

        self.selected_token_ids = ids
        self.selected_token_id = primary_id

        self.selection_changed.emit(None)
        self._sync_text_format_bar()
        self._sync_image_format_bar()
        self.update()

    def _select_token(
        self,
        token: Optional[InitialTokenPlacement],
    ) -> None:
        self._select_tokens(
            [token] if token is not None else [],
            primary=token,
        )

    def _toggle_token_selection(
        self,
        token: InitialTokenPlacement,
    ) -> None:
        selected = self.selected_tokens()

        if token.id in self.selected_token_ids:
            selected = [
                item
                for item in selected
                if item.id != token.id
            ]
            self._select_tokens(
                selected,
                primary=selected[-1] if selected else None,
            )
            return

        selected.append(token)
        self._select_tokens(
            selected,
            primary=token,
        )

    def _tokens_in_marquee(
        self,
        rect: Rect,
    ) -> list[InitialTokenPlacement]:
        return [
            token
            for token in self.asset.initial_tokens
            if self._token_rect(token).intersects(rect)
        ]

    def _start_token_move(
        self,
        screen_pos: QPointF,
        *,
        primary: InitialTokenPlacement,
    ) -> None:
        selected = self.selected_tokens()

        if primary.id not in self.selected_token_ids:
            selected = [primary]
            self._select_tokens(
                selected,
                primary=primary,
            )

        if not selected:
            return

        world = self.screen_to_world(
            screen_pos
        )
        self._token_move = TokenMoveSession(
            token_ids=[
                token.id
                for token in selected
            ],
            primary_token_id=primary.id,
            start_world=Vec2(
                world.x,
                world.y,
            ),
            positions={
                token.id: Vec2(
                    token.pos.x,
                    token.pos.y,
                )
                for token in selected
            },
            history_before=self._capture_state(),
        )
        self.setCursor(
            Qt.CursorShape.SizeAllCursor
        )

    def _snap_token_move_delta(
        self,
        session: TokenMoveSession,
        delta: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> Vec2:
        """
        Tokens are strictly constrained to grid-cell centers.

        Unlike normal map elements, token movement is never free while a valid
        grid exists: the primary token is quantized to the nearest cell center
        for the whole drag, and every other selected token receives the same
        cell-aligned delta.

        ``Alt`` therefore does not disable snapping for token movement.
        """
        primary_start = session.positions.get(
            session.primary_token_id
        )
        if primary_start is None:
            return delta

        grid = self.asset.grid
        step = float(grid.cell_size)

        # No usable grid: keep the current position rather than allowing a
        # token to drift off-grid.
        if not grid.enabled or step <= 0:
            return Vec2(0.0, 0.0)

        center_x = float(grid.offset_x) + step / 2.0
        center_y = float(grid.offset_y) + step / 2.0

        candidate = primary_start + delta

        snapped = Vec2(
            center_x
            + round(
                (candidate.x - center_x)
                / step
            )
            * step,
            center_y
            + round(
                (candidate.y - center_y)
                / step
            )
            * step,
        )

        return snapped - primary_start

    def _update_token_move(
        self,
        screen_pos: QPointF,
        modifiers: Qt.KeyboardModifier,
    ) -> None:
        session = self._token_move
        if session is None:
            return

        current_ids = {
            token.id
            for token in self.selected_tokens()
        }
        if current_ids != set(session.token_ids):
            self._token_move = None
            return

        world = self.screen_to_world(
            screen_pos
        )
        delta = Vec2(
            world.x - session.start_world.x,
            world.y - session.start_world.y,
        )
        delta = self._snap_token_move_delta(
            session,
            delta,
            modifiers,
        )

        tokens_by_id = {
            token.id: token
            for token in self.asset.initial_tokens
            if token.id in session.positions
        }

        for token_id, start_pos in session.positions.items():
            token = tokens_by_id.get(
                token_id
            )
            if token is None:
                continue

            token.pos = Vec2(
                start_pos.x + delta.x,
                start_pos.y + delta.y,
            )

        self.update()

    def _finish_token_move(self) -> None:
        session = self._token_move
        if (
            session is not None
            and session.history_before is not None
        ):
            self._commit_history(
                session.history_before
            )

        self._token_move = None
        self.unsetCursor()
        self.update()

    def cancel_token_move(self) -> None:
        session = self._token_move
        if session is None:
            return

        tokens_by_id = {
            token.id: token
            for token in self.asset.initial_tokens
            if token.id in session.positions
        }

        for token_id, start_pos in session.positions.items():
            token = tokens_by_id.get(
                token_id
            )
            if token is None:
                continue

            token.pos = Vec2(
                start_pos.x,
                start_pos.y,
            )

        self._token_move = None
        self.unsetCursor()
        self.update()

    def _load_token_portrait(self, path: Optional[Path]) -> Optional[QImage]:
        if path is None:
            return None
        key = Path(path).expanduser()
        cached = self._token_image_cache.get(key)
        if cached is not None:
            return cached
        image = QImage(str(key))
        if image.isNull():
            return None
        self._token_image_cache[key] = image
        return image

    @staticmethod
    def _draw_cover_image(painter: QPainter, target: QRectF, image: QImage) -> None:
        if image.isNull() or target.width() <= 0 or target.height() <= 0:
            return
        source_w = float(image.width())
        source_h = float(image.height())
        target_ratio = target.width() / target.height()
        source_ratio = source_w / source_h if source_h else 1.0
        if source_ratio > target_ratio:
            crop_w = source_h * target_ratio
            source = QRectF((source_w - crop_w) / 2.0, 0.0, crop_w, source_h)
        else:
            crop_h = source_w / target_ratio if target_ratio else source_h
            source = QRectF(0.0, (source_h - crop_h) / 2.0, source_w, crop_h)
        painter.drawImage(target, image, source)

    @staticmethod
    def _resource_bar(
        painter: QPainter,
        rect: QRectF,
        value: int,
        maximum: int,
        color: QColor,
    ) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#313244"))
        painter.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
        ratio = 0.0 if maximum <= 0 else max(0.0, min(1.0, float(value) / float(maximum)))
        if ratio > 0:
            fill = QRectF(rect.x(), rect.y(), rect.width() * ratio, rect.height())
            painter.setBrush(color)
            painter.drawRoundedRect(fill, rect.height() / 2.0, rect.height() / 2.0)

    def _draw_token(
        self,
        painter: QPainter,
        placement: InitialTokenPlacement,
        *,
        ghost: bool = False,
        view: Optional[TokenViewData] = None,
    ) -> None:
        view = view or self._token_view(placement.entity_id)
        size = self._token_size(placement)
        half = size / 2.0
        opacity = max(0.0, min(1.0, float(placement.opacity)))
        if placement.hidden:
            opacity *= 0.40
        if ghost:
            opacity *= 0.60

        # Portrait: rotation belongs to the token image. Labels/resources stay upright.
        painter.save()
        painter.setOpacity(opacity)
        painter.translate(placement.pos.x, placement.pos.y)
        if placement.rotation:
            painter.rotate(placement.rotation)
        portrait_rect = QRectF(-half, -half, size, size)
        portrait = self._load_token_portrait(view.portrait)
        if portrait is not None:
            self._draw_cover_image(painter, portrait_rect, portrait)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#313244"))
            painter.drawRoundedRect(portrait_rect, size * 0.12, size * 0.12)
            initials = "".join(part[:1] for part in view.name.split()[:2]).upper() or "?"
            font = QFont()
            font.setBold(True)
            font.setPixelSize(max(8, round(size * 0.30)))
            painter.setFont(font)
            painter.setPen(QColor("#cdd6f4"))
            painter.drawText(portrait_rect, Qt.AlignmentFlag.AlignCenter, initials)
        border = QPen(QColor("#89b4fa" if not placement.hidden else "#6c7086"))
        border.setWidthF(max(1.0, size * 0.025))
        border.setStyle(Qt.PenStyle.DashLine if placement.hidden else Qt.PenStyle.SolidLine)
        painter.setPen(border)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(portrait_rect, size * 0.12, size * 0.12)
        painter.restore()

        painter.save()
        painter.setOpacity(opacity)
        # Name above the portrait.
        font = QFont()
        font.setBold(True)
        font.setPixelSize(max(7, round(size * 0.13)))
        painter.setFont(font)
        painter.setPen(QColor("#cdd6f4"))
        name_h = max(10.0, size * 0.20)
        painter.drawText(
            QRectF(placement.pos.x - size * 0.75, placement.pos.y - half - name_h - size * 0.04,
                   size * 1.5, name_h),
            Qt.AlignmentFlag.AlignCenter,
            view.name,
        )

        # Resource bars below the portrait.
        bar_h = max(3.0, size * 0.065)
        bar_gap = max(2.0, size * 0.025)
        bar_y = placement.pos.y + half + max(3.0, size * 0.05)
        hp_rect = QRectF(placement.pos.x - half, bar_y, size, bar_h)
        sta_rect = QRectF(placement.pos.x - half, bar_y + bar_h + bar_gap, size, bar_h)
        self._resource_bar(painter, hp_rect, view.hp, view.hp_max, QColor("#f38ba8"))
        self._resource_bar(painter, sta_rect, view.stamina, view.stamina_max, QColor("#fab387"))

        # Statuses: compact coloured badges under the bars.
        statuses = view.statuses[:8]
        if statuses:
            radius = max(4.0, size * 0.075)
            diameter = radius * 2.0
            gap = max(2.0, radius * 0.35)
            total_w = len(statuses) * diameter + max(0, len(statuses) - 1) * gap
            start_x = placement.pos.x - total_w / 2.0 + radius
            center_y = sta_rect.bottom() + max(3.0, size * 0.05) + radius
            status_font = QFont()
            status_font.setBold(True)
            status_font.setPixelSize(max(6, round(radius * 0.95)))
            painter.setFont(status_font)
            for index, status in enumerate(statuses):
                center_x = start_x + index * (diameter + gap)
                color = QColor(status.color)
                if not color.isValid():
                    color = QColor("#cba6f7")
                painter.setPen(QPen(QColor("#11111b"), max(1.0, radius * 0.12)))
                painter.setBrush(color)
                painter.drawEllipse(QPointF(center_x, center_y), radius, radius)
                label = (status.short_label or status.name[:1] or "?").upper()[:2]
                painter.setPen(QColor("#11111b"))
                painter.drawText(
                    QRectF(center_x - radius, center_y - radius, diameter, diameter),
                    Qt.AlignmentFlag.AlignCenter,
                    label,
                )
        painter.restore()

    def _draw_tokens(self, painter: QPainter, view: Rect) -> None:
        for placement in self.asset.initial_tokens:
            if not self._token_rect(placement).intersects(view):
                continue
            self._draw_token(painter, placement)

        if self.pending_token_entity_id is not None and self._hover_world is not None:
            pos = Vec2(self._hover_world.x, self._hover_world.y)
            if self.asset.grid.enabled and self.asset.grid.cell_size > 0:
                pos = self.asset.grid.cell_center(pos)
            ghost = InitialTokenPlacement(entity_id=self.pending_token_entity_id, pos=pos)
            self._draw_token(painter, ghost, ghost=True)

        if self._drag_token_preview is not None and self._hover_world is not None:
            pos = Vec2(self._hover_world.x, self._hover_world.y)
            if self.asset.grid.enabled and self.asset.grid.cell_size > 0:
                pos = self.asset.grid.cell_center(pos)

            ghost = InitialTokenPlacement(
                entity_id=self._drag_token_source_id or "",
                pos=pos,
            )
            self._draw_token(
                painter,
                ghost,
                ghost=True,
                view=self._drag_token_preview,
            )

    def place_token_at(
        self,
        entity_id: str,
        world: Vec2,
    ) -> InitialTokenPlacement:
        """Place or move one Entity token as a single undoable map edit."""
        before = self._capture_state()
        placement = self.asset.place_token(
            str(entity_id),
            world,
            snap=True,
        )
        self._select_token(
            placement
        )
        self._commit_history(before)
        self.token_placed.emit(placement.entity_id)
        self.update()
        return placement

    def _place_pending_token(self, world: Vec2) -> None:
        entity_id = self.pending_token_entity_id
        if entity_id is None:
            return

        self.pending_token_entity_id = None
        self.unsetCursor()
        self.place_token_at(entity_id, world)

    # -------------------------------------------------------------------------
    # Token drag & drop
    # -------------------------------------------------------------------------

    @staticmethod
    def _decode_token_drag(mime) -> Optional[tuple[str, str]]:
        if mime is None or not mime.hasFormat(TOKEN_SOURCE_MIME):
            return None

        try:
            raw = bytes(mime.data(TOKEN_SOURCE_MIME)).decode("utf-8")
            payload = json.loads(raw)
        except (UnicodeDecodeError, ValueError, TypeError):
            return None

        if not isinstance(payload, dict):
            return None

        kind = str(payload.get("kind") or "").strip().casefold()
        source_id = str(payload.get("id") or "").strip()

        if kind not in {"character", "entity"} or not source_id:
            return None

        return kind, source_id

    def _set_token_drag_preview(
        self,
        source_kind: str,
        source_id: str,
        position: QPointF,
    ) -> None:
        self._drag_token_source_kind = source_kind
        self._drag_token_source_id = source_id

        try:
            self._drag_token_preview = self.token_provider.source_view(
                source_kind,
                source_id,
            )
        except Exception:
            self._drag_token_preview = None

        world = self.screen_to_world(position)
        self._hover_world = Vec2(world.x, world.y)
        self.update()

    def _clear_token_drag_preview(self) -> None:
        self._drag_token_source_kind = None
        self._drag_token_source_id = None
        self._drag_token_preview = None
        self.update()
