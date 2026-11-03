# -*- coding: utf-8 -*-
"""Player-safe interactive map view driven only by server projections."""
from __future__ import annotations

from math import floor, ceil
from pathlib import Path, PurePosixPath
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QBrush, QImage, QMouseEvent, QPainter, QPen, QWheelEvent, QResizeEvent
from PySide6.QtWidgets import QWidget

from ...assets.map import MapElement, MediaCache, Vec2


class PlayerMapView(QWidget):
    move_requested = Signal(float, float)
    token_inspect_requested = Signal(str)
    target_selection_changed = Signal(object)
    status_message = Signal(str)
    viewport_changed = Signal()

    MIN_ZOOM = 0.05
    MAX_ZOOM = 6.0

    def __init__(self, cache_root: str | Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cache_root = Path(cache_root)
        self.projection: dict[str, Any] = {}
        self.map_data: dict[str, Any] | None = None
        self.own_entity: dict[str, Any] = {}
        self.permissions: dict[str, Any] = {}
        self.combat: dict[str, Any] = {}
        self.center = Vec2()
        self.zoom = 1.0
        self._tile_cache: dict[Path, QImage] = {}
        self._elements: list[MapElement] = []
        self._media: MediaCache | None = None
        self._panning = False
        self._pan_last = QPointF()
        self._drag_token_id: str | None = None
        self._drag_start = Vec2()
        self._drag_preview: Vec2 | None = None
        self._dragged = False
        self._has_pending = False
        self._selected_token_id: str | None = None
        self._targeting_mode: str | None = None
        self._selected_target_ids: list[str] = []
        self._target_point: Vec2 | None = None

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(520, 400)

    # ------------------------------------------------------------------ state
    def set_projection(self, projection: dict[str, Any]) -> None:
        old_map_id = str((self.map_data or {}).get("map_id", ""))
        old_asset = self._asset_data() or {}
        old_compiled = old_asset.get("compiled", {}) if isinstance(old_asset.get("compiled"), dict) else {}
        old_source_hash = str(old_compiled.get("source_hash", ""))
        self.projection = dict(projection or {})
        self.own_entity = dict(self.projection.get("entity") or {})
        self.permissions = dict(self.projection.get("permissions") or {})
        self.combat = dict(self.projection.get("combat") or {})
        raw_map = self.projection.get("map")
        self.map_data = dict(raw_map) if isinstance(raw_map, dict) else None
        new_map_id = str((self.map_data or {}).get("map_id", ""))
        new_asset = self._asset_data() or {}
        new_compiled = new_asset.get("compiled", {}) if isinstance(new_asset.get("compiled"), dict) else {}
        new_source_hash = str(new_compiled.get("source_hash", ""))
        if new_map_id != old_map_id or new_source_hash != old_source_hash:
            self.center = Vec2()
            self.zoom = 1.0
            self._tile_cache.clear()
            self._selected_target_ids.clear()
            self._selected_token_id = None
            self._target_point = None
            self._drag_token_id = None
            self._drag_preview = None
            if new_map_id:
                from PySide6.QtCore import QTimer
                QTimer.singleShot(0, self.fit_map)
        self._elements = []
        self._media = None
        if self.map_data is not None:
            map_id = str(self.map_data.get("map_id", ""))
            safe_map_id = PurePosixPath(map_id.replace("\\", "/"))
            if map_id and not safe_map_id.is_absolute() and len(safe_map_id.parts) == 1 and map_id not in {".", ".."}:
                self._media = MediaCache(self.cache_root / "maps" / map_id)
            for raw in self.map_data.get("elements", []):
                if not isinstance(raw, dict):
                    continue
                try:
                    safe_raw = dict(raw)
                    if isinstance(safe_raw.get("asset"), str):
                        safe_raw["asset"] = safe_raw["asset"].replace("\\", "/")
                    element = MapElement.from_dict(safe_raw)
                    if element.visible:
                        self._elements.append(element)
                except (TypeError, ValueError, KeyError):
                    continue
        visible_ids = {str(token.get("id", "")) for token in self._tokens() if token.get("id")}
        self._selected_target_ids = [token_id for token_id in self._selected_target_ids if token_id in visible_ids]
        if self._selected_token_id not in visible_ids:
            self._selected_token_id = None
        if self._targeting_mode == "self":
            own = self._own_token()
            self._selected_target_ids = [str(own.get("id", ""))] if own is not None else []
        self._emit_target_selection()
        self.update()
        self.viewport_changed.emit()

    def set_cache_root(self, cache_root: str | Path) -> None:
        self.cache_root = Path(cache_root)
        self.invalidate_cached_assets()

    def invalidate_cached_assets(self) -> None:
        self._tile_cache.clear()
        self._media = None
        self.update()

    def set_has_pending(self, pending: bool) -> None:
        self._has_pending = bool(pending)
        if pending:
            self._drag_token_id = None
            self._drag_preview = None
        self.update()

    def set_targeting_mode(self, mode: str | None) -> None:
        self._targeting_mode = str(mode).lower() if mode else None
        self._selected_target_ids.clear()
        if self._targeting_mode is None or self._targeting_mode == "area":
            self._selected_token_id = None
        self._target_point = None
        if self._targeting_mode == "self":
            own = self._own_token()
            if own is not None:
                self._selected_target_ids = [str(own.get("id", ""))]
        elif self._targeting_mode in {"single", "multi"} and self._selected_token_id:
            selected = next((t for t in self._tokens() if str(t.get("id", "")) == self._selected_token_id), None)
            if selected is not None and not selected.get("self"):
                self._selected_target_ids = [self._selected_token_id]
        self._emit_target_selection()
        self.update()

    def clear_targeting(self) -> None:
        self._targeting_mode = None
        self._selected_target_ids.clear()
        self._target_point = None
        self._emit_target_selection()
        self.update()

    def center_on_me(self) -> None:
        token = self._own_token()
        if token is None:
            return
        pos = self._token_pos(token)
        self.center = pos
        self.update()
        self.viewport_changed.emit()

    def fit_map(self) -> None:
        asset = self._asset_data()
        size = asset.get("size") if asset else None
        if not isinstance(size, (list, tuple)) or len(size) != 2 or self.width() <= 0 or self.height() <= 0:
            return
        width, height = max(1.0, float(size[0])), max(1.0, float(size[1]))
        margin = 28.0
        zx = max(1.0, self.width() - margin * 2) / width
        zy = max(1.0, self.height() - margin * 2) / height
        self.zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, min(zx, zy)))
        self.center = Vec2(width / 2, height / 2)
        self.update()
        self.viewport_changed.emit()

    def visible_tile_keys(self) -> set[str]:
        """Return only compiled tile files intersecting the current viewport."""
        asset = self._asset_data()
        compiled = asset.get("compiled") if isinstance(asset, dict) else None
        if not isinstance(compiled, dict):
            return set()
        tiles = compiled.get("tiles", {})
        if not isinstance(tiles, dict) or not tiles:
            return set()
        try:
            tile_size = max(1, int(compiled.get("tile_size", 512)))
            width, height = (float(value) for value in asset.get("size", [0, 0]))
        except (TypeError, ValueError):
            return set()
        if width <= 0 or height <= 0 or self.width() <= 0 or self.height() <= 0:
            return set()
        visible = self._visible_world_rect().intersected(QRectF(0, 0, width, height))
        if visible.isEmpty():
            return set()
        first_col = max(0, int(floor(visible.left() / tile_size)))
        first_row = max(0, int(floor(visible.top() / tile_size)))
        last_col = min(int(ceil(width / tile_size)) - 1, max(0, int(ceil(visible.right() / tile_size)) - 1))
        last_row = min(int(ceil(height / tile_size)) - 1, max(0, int(ceil(visible.bottom() / tile_size)) - 1))
        return {
            f"{col}_{row}"
            for row in range(first_row, last_row + 1)
            for col in range(first_col, last_col + 1)
            if f"{col}_{row}" in tiles
        }

    # -------------------------------------------------------------- coordinates
    def world_to_screen(self, point: Vec2) -> QPointF:
        return QPointF(
            self.width() / 2 + (point.x - self.center.x) * self.zoom,
            self.height() / 2 + (point.y - self.center.y) * self.zoom,
        )

    def screen_to_world(self, point: QPointF) -> Vec2:
        return Vec2(
            self.center.x + (point.x() - self.width() / 2) / self.zoom,
            self.center.y + (point.y() - self.height() / 2) / self.zoom,
        )

    def _visible_world_rect(self) -> QRectF:
        top_left = self.screen_to_world(QPointF(0, 0))
        bottom_right = self.screen_to_world(QPointF(self.width(), self.height()))
        return QRectF(top_left.x, top_left.y, bottom_right.x - top_left.x, bottom_right.y - top_left.y)

    def _asset_data(self) -> dict[str, Any] | None:
        if self.map_data is None:
            return None
        asset = self.map_data.get("asset")
        return asset if isinstance(asset, dict) else None

    def _grid(self) -> dict[str, Any]:
        asset = self._asset_data() or {}
        grid = asset.get("grid")
        return grid if isinstance(grid, dict) else {}

    def _tokens(self) -> list[dict[str, Any]]:
        if self.map_data is None:
            return []
        return [item for item in self.map_data.get("tokens", []) if isinstance(item, dict)]

    def _own_token(self) -> dict[str, Any] | None:
        return next((token for token in self._tokens() if token.get("self")), None)

    @staticmethod
    def _token_pos(token: dict[str, Any]) -> Vec2:
        raw = token.get("pos", [0, 0])
        try:
            return Vec2(float(raw[0]), float(raw[1]))
        except (TypeError, ValueError, IndexError):
            return Vec2()

    def _token_size(self, token: dict[str, Any]) -> float:
        grid = self._grid()
        cell_size = float(grid.get("cell_size", 64) or 64)
        return max(12.0, cell_size * max(0.25, float(token.get("size_cells", 1.0))))

    def _token_rect(self, token: dict[str, Any], pos: Vec2 | None = None) -> QRectF:
        center = pos or self._token_pos(token)
        size = self._token_size(token)
        return QRectF(center.x - size / 2, center.y - size / 2, size, size)

    def _token_at(self, world: Vec2) -> dict[str, Any] | None:
        point = QPointF(world.x, world.y)
        for token in reversed(self._tokens()):
            if self._token_rect(token).contains(point):
                return token
        return None

    def _emit_target_selection(self) -> None:
        self.target_selection_changed.emit({
            "token_ids": list(self._selected_target_ids),
            "point": self._target_point.to_list() if self._target_point is not None else None,
        })

    # ------------------------------------------------------------------ drawing
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor("#11141a"))
            asset = self._asset_data()
            if self.map_data is None or asset is None:
                painter.setPen(QColor("#98a2b1"))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "En attente de la carte du MJ…")
                return
            size = asset.get("size", [0, 0])
            if not isinstance(size, (list, tuple)) or len(size) != 2:
                return

            painter.save()
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                painter.translate(self.width() / 2, self.height() / 2)
                painter.scale(self.zoom, self.zoom)
                painter.translate(-self.center.x, -self.center.y)
                map_rect = QRectF(0, 0, float(size[0]), float(size[1]))
                painter.fillRect(map_rect, QColor("#1e1e2e"))
                painter.save()
                try:
                    painter.setClipRect(map_rect)
                    self._draw_compiled_tiles(painter, asset, map_rect)
                    self._draw_runtime_elements(painter)
                finally:
                    painter.restore()
                painter.save()
                try:
                    painter.setClipRect(map_rect)
                    self._draw_fov(painter)
                    self._draw_movement_range(painter)
                    self._draw_grid(painter, map_rect)
                    self._draw_tokens(painter)
                    self._draw_target_marker(painter)
                finally:
                    painter.restore()
            finally:
                painter.restore()

            compiled = asset.get("compiled")
            if not isinstance(compiled, dict):
                painter.setPen(QColor("#f0ad4e"))
                painter.drawText(12, 22, "Carte non compilée par le MJ")
        finally:
            painter.end()

    def _draw_compiled_tiles(self, painter: QPainter, asset: dict[str, Any], map_rect: QRectF) -> None:
        compiled = asset.get("compiled")
        if not isinstance(compiled, dict):
            return
        tile_size = max(1, int(compiled.get("tile_size", 512)))
        tile_format = str(compiled.get("format", "webp"))
        if tile_format not in {"webp", "png"}:
            return
        map_id = str((self.map_data or {}).get("map_id", ""))
        safe_map_id = PurePosixPath(map_id.replace("\\", "/"))
        if not map_id or safe_map_id.is_absolute() or len(safe_map_id.parts) != 1 or map_id in {".", ".."}:
            return
        tiles = compiled.get("tiles", {}) if isinstance(compiled.get("tiles"), dict) else {}
        uniform = compiled.get("uniform", {}) if isinstance(compiled.get("uniform"), dict) else {}
        visible = self._visible_world_rect().intersected(map_rect)
        if visible.isEmpty():
            return
        first_col = max(0, int(floor(visible.left() / tile_size)))
        last_col = max(0, int(floor(visible.right() / tile_size)))
        first_row = max(0, int(floor(visible.top() / tile_size)))
        last_row = max(0, int(floor(visible.bottom() / tile_size)))
        root = self.cache_root / "maps" / map_id / "tiles"
        for row in range(first_row, last_row + 1):
            for col in range(first_col, last_col + 1):
                key = f"{col}_{row}"
                rect = QRectF(col * tile_size, row * tile_size, tile_size, tile_size)
                if key in uniform:
                    painter.fillRect(rect, QColor(str(uniform[key])))
                    continue
                if key not in tiles:
                    continue
                if not all(part.isdigit() for part in key.split("_", 1)):
                    continue
                path = root / f"{key}.{tile_format}"
                image = self._tile_cache.get(path)
                if image is None:
                    if path.is_file():
                        image = QImage(str(path))
                        if not image.isNull():
                            self._tile_cache[path] = image
                    else:
                        image = QImage()
                if not image.isNull():
                    painter.drawImage(rect.topLeft(), image)
                else:
                    painter.fillRect(rect, QColor("#252b36"))
                    painter.setPen(QPen(QColor("#3b4553"), 1.0 / max(self.zoom, 0.05)))
                    painter.drawRect(rect)

    def _draw_runtime_elements(self, painter: QPainter) -> None:
        if self._media is None:
            return
        for element in self._elements:
            try:
                element.render(painter, self._media)
            except (OSError, ValueError, RuntimeError):
                continue

    def _draw_fov(self, painter: QPainter) -> None:
        fov = self.map_data.get("fov", {}) if self.map_data else {}
        origin = fov.get("origin") if isinstance(fov, dict) else None
        radius = int(fov.get("radius_cells", 0)) if isinstance(fov, dict) else 0
        grid = self._grid()
        if not origin or radius <= 0 or not grid.get("enabled", True):
            return
        try:
            x, y = float(origin[0]), float(origin[1])
            cell = float(grid.get("cell_size", 64))
            offset = grid.get("offset", [0, 0])
            ox, oy = float(offset[0]), float(offset[1])
        except (TypeError, ValueError, IndexError):
            return
        col = floor((x - ox) / cell)
        row = floor((y - oy) / cell)
        rect = QRectF(
            ox + (col - radius) * cell,
            oy + (row - radius) * cell,
            (radius * 2 + 1) * cell,
            (radius * 2 + 1) * cell,
        )
        color = QColor("#89dceb")
        color.setAlpha(45)
        painter.fillRect(rect, color)
        pen = QPen(QColor("#89dceb"), max(1.0, 1.5 / self.zoom), Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

    def _draw_movement_range(self, painter: QPainter) -> None:
        if self._has_pending or not self.combat.get("active") or not self.combat.get("own_turn"):
            return
        if not self.permissions.get("can_move") or not self.permissions.get("movement_limited"):
            return
        turn = self.combat.get("turn_state", {})
        if not isinstance(turn, dict):
            return
        own = self._own_token()
        grid = self._grid()
        if own is None or not grid.get("enabled", True):
            return
        try:
            remaining = max(0, int(turn.get("movement_remaining", 0)))
            cell = float(grid.get("cell_size", 64))
            offset = grid.get("offset", [0, 0])
            ox, oy = float(offset[0]), float(offset[1])
        except (TypeError, ValueError, IndexError):
            return
        if cell <= 0:
            return
        pos = self._token_pos(own)
        col = floor((pos.x - ox) / cell)
        row = floor((pos.y - oy) / cell)
        rect = QRectF(
            ox + (col - remaining) * cell,
            oy + (row - remaining) * cell,
            (remaining * 2 + 1) * cell,
            (remaining * 2 + 1) * cell,
        )
        fill = QColor("#a6e3a1")
        fill.setAlpha(24)
        painter.fillRect(rect, fill)
        pen = QPen(QColor("#a6e3a1"), max(1.0, 1.25 / self.zoom), Qt.PenStyle.DotLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

    def _draw_grid(self, painter: QPainter, map_rect: QRectF) -> None:
        grid = self._grid()
        if not grid.get("enabled", True):
            return
        cell = int(grid.get("cell_size", 64) or 64)
        if cell <= 0:
            return
        offset = grid.get("offset", [0, 0])
        try:
            ox, oy = float(offset[0]), float(offset[1])
        except (TypeError, ValueError, IndexError):
            ox = oy = 0.0
        color = QColor(str(grid.get("color", "#4a4a4a")))
        color.setAlphaF(max(0.0, min(1.0, float(grid.get("opacity", 0.35)))))
        pen = QPen(color, max(0.5, 1.0 / self.zoom))
        painter.setPen(pen)
        visible = self._visible_world_rect().intersected(map_rect)
        first_col = int(floor((visible.left() - ox) / cell))
        last_col = int(ceil((visible.right() - ox) / cell))
        first_row = int(floor((visible.top() - oy) / cell))
        last_row = int(ceil((visible.bottom() - oy) / cell))
        for col in range(first_col, last_col + 1):
            x = ox + col * cell
            painter.drawLine(QPointF(x, visible.top()), QPointF(x, visible.bottom()))
        for row in range(first_row, last_row + 1):
            y = oy + row * cell
            painter.drawLine(QPointF(visible.left(), y), QPointF(visible.right(), y))

    def _draw_tokens(self, painter: QPainter) -> None:
        own = self.own_entity
        own_name = str(own.get("name", "Vous"))
        for token in self._tokens():
            pos = self._drag_preview if token.get("id") == self._drag_token_id and self._drag_preview else self._token_pos(token)
            rect = self._token_rect(token, pos)
            painter.save()
            painter.setOpacity(max(0.0, min(1.0, float(token.get("opacity", 1.0)))))
            painter.setBrush(QBrush(QColor("#334155" if not token.get("self") else "#315b66")))
            outline = QColor("#89dceb") if token.get("self") else QColor("#64748b")
            token_id = str(token.get("id", ""))
            if token_id in self._selected_target_ids or token_id == self._selected_token_id:
                outline = QColor("#cba6f7")
            if token_id == self._drag_token_id:
                outline = QColor("#f9e2af")
            painter.setPen(QPen(outline, max(1.5, 2.0 / self.zoom)))
            painter.drawEllipse(rect)
            public = token.get("public") if isinstance(token.get("public"), dict) else {}
            label = own_name if token.get("self") else str(public.get("name", "?"))
            initials = "".join(part[:1] for part in label.split())[:2].upper() or "?"
            painter.setPen(QColor("#f4f4f5"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, initials)
            painter.restore()

    def _draw_target_marker(self, painter: QPainter) -> None:
        if self._target_point is None:
            return
        point = self._target_point
        size = 18.0 / self.zoom
        pen = QPen(QColor("#f9e2af"), max(1.0, 2.0 / self.zoom))
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(point.x, point.y), size, size)
        painter.drawLine(QPointF(point.x - size * 1.5, point.y), QPointF(point.x + size * 1.5, point.y))
        painter.drawLine(QPointF(point.x, point.y - size * 1.5), QPointF(point.x, point.y + size * 1.5))

    # ------------------------------------------------------------------ input
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_last = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        world = self.screen_to_world(event.position())
        token = self._token_at(world)
        if event.button() == Qt.MouseButton.RightButton:
            if token is not None and self.permissions.get("can_inspect_visible_tokens", False):
                self.token_inspect_requested.emit(str(token.get("id", "")))
                event.accept()
                return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._targeting_mode:
            if self._targeting_mode == "area":
                self._target_point = self._snap_world(world)
            elif self._targeting_mode == "self":
                pass
            elif token is not None:
                token_id = str(token.get("id", ""))
                if token_id:
                    if self._targeting_mode == "multi":
                        if token_id in self._selected_target_ids:
                            self._selected_target_ids.remove(token_id)
                        else:
                            self._selected_target_ids.append(token_id)
                    else:
                        self._selected_target_ids = [token_id]
            self._emit_target_selection()
            self.update()
            event.accept()
            return

        if token is not None:
            self._selected_token_id = str(token.get("id", ""))
            if token.get("self") and self.permissions.get("can_move", False) and not self._has_pending:
                self._drag_token_id = self._selected_token_id
                self._drag_start = self._token_pos(token)
                self._drag_preview = Vec2(self._drag_start.x, self._drag_start.y)
                self._dragged = False
            self.update()
            event.accept()
            return
        self._selected_token_id = None
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._panning:
            delta = event.position() - self._pan_last
            self._pan_last = event.position()
            self.center = Vec2(
                self.center.x - delta.x() / self.zoom,
                self.center.y - delta.y() / self.zoom,
            )
            self.update()
            self.viewport_changed.emit()
            return
        if self._drag_token_id is not None and self._drag_preview is not None:
            world = self.screen_to_world(event.position())
            self._drag_preview = self._snap_world(world)
            self._dragged = True
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self.unsetCursor()
            return
        if event.button() != Qt.MouseButton.LeftButton or self._drag_token_id is None:
            return
        token_id = self._drag_token_id
        target = self._drag_preview or self._drag_start
        self._drag_token_id = None
        self._drag_preview = None
        if self._dragged and (abs(target.x - self._drag_start.x) > 0.5 or abs(target.y - self._drag_start.y) > 0.5):
            if self._movement_would_exceed_budget(self._drag_start, target):
                self.status_message.emit("Déplacement supérieur au budget restant.")
            else:
                self.move_requested.emit(target.x, target.y)
        self._dragged = False
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self.map_data is None:
            return
        before = self.screen_to_world(event.position())
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self.zoom * factor))
        after = self.screen_to_world(event.position())
        self.center = Vec2(
            self.center.x + before.x - after.x,
            self.center.y + before.y - after.y,
        )
        self.update()
        self.viewport_changed.emit()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.viewport_changed.emit()

    # --------------------------------------------------------------- movement
    def _snap_world(self, point: Vec2) -> Vec2:
        grid = self._grid()
        if not grid.get("enabled", True):
            return point
        cell = float(grid.get("cell_size", 64) or 64)
        if cell <= 0:
            return point
        offset = grid.get("offset", [0, 0])
        try:
            ox, oy = float(offset[0]), float(offset[1])
        except (TypeError, ValueError, IndexError):
            ox = oy = 0.0
        col = floor((point.x - ox) / cell)
        row = floor((point.y - oy) / cell)
        return Vec2(ox + (col + 0.5) * cell, oy + (row + 0.5) * cell)

    def _movement_would_exceed_budget(self, start: Vec2, target: Vec2) -> bool:
        if not self.combat.get("active") or not self.combat.get("own_turn"):
            return False
        turn = self.combat.get("turn_state")
        if not isinstance(turn, dict):
            return False
        grid = self._grid()
        cell = float(grid.get("cell_size", 64) or 64)
        offset = grid.get("offset", [0, 0])
        try:
            ox, oy = float(offset[0]), float(offset[1])
        except (TypeError, ValueError, IndexError):
            ox = oy = 0.0
        if cell <= 0:
            return False
        first = (floor((start.x - ox) / cell), floor((start.y - oy) / cell))
        second = (floor((target.x - ox) / cell), floor((target.y - oy) / cell))
        cost = max(abs(second[0] - first[0]), abs(second[1] - first[1]))
        return cost > int(turn.get("movement_remaining", 0))
