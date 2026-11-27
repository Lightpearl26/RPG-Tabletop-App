# -*- coding: utf-8 -*-
"""GM runtime map viewport."""
from __future__ import annotations

from math import hypot
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor, QImage, QMouseEvent, QPainter, QPen, QBrush, QWheelEvent,
    QDragEnterEvent, QDragMoveEvent, QDropEvent,
)
from PySide6.QtWidgets import QMenu, QWidget

from rpg_tabletop.assets.map import (
    DrawingElement, DrawTool, MapAsset, MapElement, MediaCache, Rect, Vec2,
)
from rpg_tabletop.core.geometry import GridGeometry, area_cells, chebyshev, line_cells
from rpg_tabletop.session.models import SessionToken
from rpg_tabletop.session.runtime import SessionRuntime
from .tokens import TOKEN_ENTITY_MIME
from rpg_tabletop.ui.common.token_overlay import draw_token_overlay


class RuntimeMapView(QWidget):
    runtime_changed = Signal()
    token_selected = Signal(str)
    token_context_requested = Signal(str)
    status_message = Signal(str)
    # Targets of an action the GM makes an entity do: {"token_ids", "point"}
    action_targets_chosen = Signal(object)
    targeting_cancelled = Signal()

    MIN_ZOOM = 0.05
    MAX_ZOOM = 6.0

    def __init__(self, runtime: SessionRuntime, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.asset: Optional[MapAsset] = None
        self.media: Optional[MediaCache] = None
        self.center = Vec2(0.0, 0.0)
        self.zoom = 1.0
        self.selected_token_id: str | None = None
        self._drag_token_id: str | None = None
        self._drag_offset = Vec2()
        self._panning = False
        self._pan_last = QPointF()
        self._token_images: dict[Path, QImage] = {}
        self._drag_start_pos: Vec2 | None = None
        self._drawing_tool: DrawTool | str | None = None
        self._drawing_color = "#f9e2af"
        self._drawing_width = 3.0
        self._drawing_points: list[Vec2] = []
        self._drawing_active = False
        # Annotations added during the session (drawings...): selection and move
        self.selected_element_id: str | None = None
        self._drag_element_id: str | None = None
        self._drag_element_snapshot: MapElement | None = None
        self._drag_element_start: Vec2 | None = None
        self._drag_element_press = QPointF()
        # Action targeting (GM acting for an entity): see start_targeting
        self._targeting: dict | None = None
        self._target_ids: list[str] = []
        self._hover_world: Vec2 | None = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.setMinimumSize(600, 420)
        self.reload_active_map(reset_camera=True)

    # ---------------------------------------------------------------- map
    def reload_active_map(self, *, reset_camera: bool = False) -> None:
        if self.runtime.active_map_id is None:
            self.asset = None
            self.media = None
            self.update()
            return
        try:
            map_dir = self.runtime.campaign.map_path(self.runtime.active_map_id)
            self.asset = MapAsset.load(map_dir)
            self.media = MediaCache(map_dir)
        except (OSError, ValueError) as exc:
            self.asset = None
            self.media = None
            self.status_message.emit(f"Impossible de charger la carte : {exc}")
            self.update()
            return
        self._token_images.clear()
        if reset_camera and self.runtime.active_map is not None:
            state = self.runtime.active_map.gm_camera
            self.center = Vec2(state.center.x, state.center.y)
            self.zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, float(state.zoom or 1.0)))
            if self.center.x == 0 and self.center.y == 0:
                self.fit_map()
        self.update()

    def fit_map(self) -> None:
        if self.asset is None or self.width() <= 0 or self.height() <= 0:
            return
        margin = 30.0
        zx = max(1.0, self.width() - margin * 2) / max(1.0, self.asset.width)
        zy = max(1.0, self.height() - margin * 2) / max(1.0, self.asset.height)
        self.zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, min(zx, zy)))
        self.center = Vec2(
            self.asset.origin_x + self.asset.width / 2,
            self.asset.origin_y + self.asset.height / 2,
        )
        self.update()

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

    def _visible_world_rect(self) -> Rect:
        tl = self.screen_to_world(QPointF(0, 0))
        br = self.screen_to_world(QPointF(self.width(), self.height()))
        return Rect(tl.x, tl.y, br.x - tl.x, br.y - tl.y)

    # ---------------------------------------------------------------- render
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor("#11141a"))
            if self.asset is None or self.media is None:
                painter.setPen(QColor("#98a2b1"))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Aucune carte active")
                return

            painter.save()
            try:
                painter.translate(self.width() / 2, self.height() / 2)
                painter.scale(self.zoom, self.zoom)
                painter.translate(-self.center.x, -self.center.y)
                self.asset.render_scene(painter, self.media, self._visible_world_rect())
                if self.runtime.active_map is not None:
                    for element in self.runtime.active_map.elements:
                        if getattr(element, "visible", True):
                            element.render(painter, self.media)
                self._draw_grid(painter)
                self._draw_element_selection(painter)
                for wall in self.asset.walls():
                    wall.render(painter, self.media)
                self._draw_tokens(painter)
                self._draw_targeting(painter)
                self._draw_preview(painter)
            finally:
                painter.restore()
            self._draw_targeting_banner(painter)
        finally:
            painter.end()

    def _draw_grid(self, painter: QPainter) -> None:
        if self.asset is None or not self.asset.grid.enabled or self.asset.grid.cell_size <= 0:
            return
        grid = self.asset.grid
        visible = self._visible_world_rect()
        size = float(grid.cell_size)
        first_col = int((visible.x - grid.offset_x) // size) - 1
        last_col = int((visible.right - grid.offset_x) // size) + 1
        first_row = int((visible.y - grid.offset_y) // size) - 1
        last_row = int((visible.bottom - grid.offset_y) // size) + 1
        color = QColor(grid.color)
        color.setAlphaF(max(0.0, min(1.0, grid.opacity)))
        pen = QPen(color)
        pen.setWidthF(max(0.5, 1.0 / self.zoom))
        painter.setPen(pen)
        for col in range(first_col, last_col + 1):
            x = grid.offset_x + col * size
            painter.drawLine(QPointF(x, visible.y), QPointF(x, visible.bottom))
        for row in range(first_row, last_row + 1):
            y = grid.offset_y + row * size
            painter.drawLine(QPointF(visible.x, y), QPointF(visible.right, y))

    def _token_size(self, token: SessionToken) -> float:
        if self.asset is not None and self.asset.grid.enabled and self.asset.grid.cell_size > 0:
            return float(self.asset.grid.cell_size) * max(0.25, float(token.size_cells))
        return 64.0 * max(0.25, float(token.size_cells))

    def _token_rect(self, token: SessionToken) -> QRectF:
        size = self._token_size(token)
        return QRectF(token.pos.x - size / 2, token.pos.y - size / 2, size, size)

    def _token_image(self, token: SessionToken) -> QImage | None:
        entity = self.runtime.entities.get(token.entity_id)
        if entity is None:
            return None
        path = entity.token
        if path is None:
            return None
        path = Path(path)
        cached = self._token_images.get(path)
        if cached is not None:
            return cached
        image = QImage(str(path))
        if image.isNull():
            return None
        self._token_images[path] = image
        return image

    def _draw_tokens(self, painter: QPainter) -> None:
        state = self.runtime.active_map
        if state is None:
            return
        painter.save()
        try:
            for token in state.tokens:
                rect = self._token_rect(token)
                painter.save()
                painter.setOpacity(max(0.0, min(1.0, token.opacity)))
                painter.translate(token.pos.x, token.pos.y)
                painter.rotate(token.rotation)
                local = QRectF(-rect.width() / 2, -rect.height() / 2, rect.width(), rect.height())
                image = self._token_image(token)
                if image is not None:
                    painter.setClipPath(self._ellipse_path(local))
                    painter.drawImage(local, image)
                    painter.setClipping(False)
                else:
                    painter.setBrush(QBrush(QColor("#2d3440")))
                    painter.setPen(QPen(QColor("#aeb7c4"), max(1.0, 1.5 / self.zoom)))
                    painter.drawEllipse(local)
                    entity = self.runtime.entities.get(token.entity_id)
                    label = "?" if entity is None else "".join(word[:1] for word in entity.name.split())[:2].upper()
                    painter.setPen(QColor("#eef2f7"))
                    painter.drawText(local, Qt.AlignmentFlag.AlignCenter, label)
                painter.restore()

                outline = QColor("#d8b84e") if token.hidden else QColor("#596578")
                if token.id == self.selected_token_id:
                    outline = QColor("#b58cff")
                pen = QPen(outline, max(1.0, 2.0 / self.zoom))
                if token.hidden:
                    pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(rect)
                # Draw token overlay (name, HP, stamina) if entity exists
                entity = self.runtime.entities.get(token.entity_id)
                if entity is not None:
                    painter.save()
                    painter.setOpacity(max(0.0, min(1.0, token.opacity)))
                    draw_token_overlay(
                        painter,
                        rect,
                        name=entity.name,
                        hp=entity.get_stat("hp"),
                        hp_max=entity.get_stat_max("hp"),
                        stamina=entity.get_stat("stamina"),
                        stamina_max=entity.get_stat_max("stamina"),
                    )
                    painter.restore()
        finally:
            painter.restore()

    @staticmethod
    def _ellipse_path(rect: QRectF):
        from PySide6.QtGui import QPainterPath
        path = QPainterPath()
        path.addEllipse(rect)
        return path

    # ------------------------------------------------------------- targeting
    @property
    def is_targeting(self) -> bool:
        return self._targeting is not None

    def start_targeting(self, spec: dict) -> None:
        """
        Let the GM pick the targets of an action on the map.

        `spec` comes from `targeting_spec` plus "entity_id" (who acts): valid
        tokens are circled in green; a click picks one (Enter sends a multi
        pick), a click on a cell sets an area or a line; Esc cancels.
        """
        self.set_drawing_tool(None)
        self._targeting = dict(spec)
        allowed = spec.get("allowed")
        self._targeting["allowed"] = None if allowed is None else {str(item) for item in allowed}
        self._target_ids = []
        self._hover_world = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update()

    def cancel_targeting(self, *, notify: bool = True) -> None:
        if self._targeting is None:
            return
        self._targeting = None
        self._target_ids = []
        self._hover_world = None
        self.unsetCursor()
        self.update()
        if notify:
            self.targeting_cancelled.emit()

    def _finish_targeting(self, token_ids: list[str], point: Vec2 | None = None) -> None:
        self._targeting = None
        self._target_ids = []
        self._hover_world = None
        self.unsetCursor()
        self.update()
        self.action_targets_chosen.emit({
            "token_ids": list(token_ids),
            "point": [point.x, point.y] if point is not None else None,
        })

    def _grid_geometry(self) -> GridGeometry | None:
        if self.asset is None or not self.asset.grid.enabled or self.asset.grid.cell_size <= 0:
            return None
        grid = self.asset.grid
        return GridGeometry(float(grid.cell_size), float(grid.offset_x), float(grid.offset_y))

    def _actor_cell(self, grid: GridGeometry) -> tuple[int, int] | None:
        state = self.runtime.active_map
        entity_id = str((self._targeting or {}).get("entity_id", ""))
        token = state.token_for_entity(entity_id) if state is not None and entity_id else None
        return grid.cell_of(token.pos.x, token.pos.y) if token is not None else None

    def _targeting_cells(self) -> list[tuple[int, int]]:
        """Cells the area or line under the cursor would cover."""
        spec = self._targeting
        grid = self._grid_geometry()
        if spec is None or grid is None or self._hover_world is None or self.asset is None:
            return []
        aim = grid.cell_of(self._hover_world.x, self._hover_world.y)
        walls = self.asset.wall_segments()
        if spec.get("mode") == "area":
            return area_cells(grid, walls, aim, int(spec.get("area_size", 0)))
        if spec.get("mode") == "line":
            origin = self._actor_cell(grid)
            if origin is None or origin == aim:
                return []
            return line_cells(grid, walls, origin, aim, int(spec.get("range", 0)))
        return []

    def _draw_targeting(self, painter: QPainter) -> None:
        spec = self._targeting
        state = self.runtime.active_map
        if spec is None or state is None:
            return
        width = max(1.5, 3.0 / self.zoom)
        actor = state.token_for_entity(str(spec.get("entity_id", "")))
        if actor is not None:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#fab387"), width))
            painter.drawEllipse(self._token_rect(actor).adjusted(-4, -4, 4, 4))
        allowed = spec.get("allowed")
        if allowed is not None:
            for token in state.tokens:
                if token.id in self._target_ids:
                    color = QColor("#a6e3a1")
                    fill = QColor("#a6e3a1")
                    fill.setAlpha(90)
                    painter.setBrush(QBrush(fill))
                elif token.id in allowed:
                    color = QColor("#40c057")
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                else:
                    continue
                painter.setPen(QPen(color, width))
                painter.drawEllipse(self._token_rect(token).adjusted(-2, -2, 2, 2))
            return
        grid = self._grid_geometry()
        if grid is None:
            return
        size = grid.cell_size
        fill = QColor("#fab387")
        fill.setAlpha(70)
        painter.setPen(QPen(QColor("#fab387"), max(1.0, 1.5 / self.zoom)))
        painter.setBrush(QBrush(fill))
        for column, row in self._targeting_cells():
            painter.drawRect(QRectF(grid.offset_x + column * size, grid.offset_y + row * size, size, size))
        origin = self._actor_cell(grid)
        reach = int(spec.get("range", 0))
        if spec.get("mode") == "area" and origin is not None and reach > 0:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#fab387"), max(1.0, 1.25 / self.zoom), Qt.PenStyle.DashLine))
            painter.drawRect(QRectF(
                grid.offset_x + (origin[0] - reach) * size,
                grid.offset_y + (origin[1] - reach) * size,
                (reach * 2 + 1) * size,
                (reach * 2 + 1) * size,
            ))

    def _draw_targeting_banner(self, painter: QPainter) -> None:
        """What the GM is picking, over the map (the status bar gets overwritten)."""
        text = str((self._targeting or {}).get("label", ""))
        if not text:
            return
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(text) + 28
        rect = QRectF((self.width() - width) / 2, 12, width, metrics.height() + 14)
        background = QColor("#11141a")
        background.setAlpha(225)
        painter.setPen(QPen(QColor("#fab387"), 1.5))
        painter.setBrush(QBrush(background))
        painter.drawRoundedRect(rect, 8, 8)
        painter.setPen(QColor("#eef2f7"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _targeting_click(self, world: Vec2) -> None:
        spec = self._targeting
        if spec is None:
            return
        mode = str(spec.get("mode", ""))
        allowed = spec.get("allowed")
        if allowed is None:  # area or line: a cell
            grid = self._grid_geometry()
            if grid is None:
                self.status_message.emit("Cette action demande une grille active.")
                return
            cell = grid.cell_of(world.x, world.y)
            origin = self._actor_cell(grid)
            reach = int(spec.get("range", 0))
            if origin is not None and mode == "line" and cell == origin:
                self.status_message.emit("Choisis une autre case que celle du lanceur pour la direction.")
                return
            if origin is not None and mode == "area" and chebyshev(origin, cell) > reach:
                self.status_message.emit(f"Hors de portée : la zone doit être centrée à {reach} case(s) au plus.")
                return
            center = grid.center(cell)
            self._finish_targeting([], Vec2(center[0], center[1]))
            return
        token = self.token_at(world)
        if token is None:
            return
        if token.id not in allowed:
            self.status_message.emit(
                "Cible impossible : hors de portée, hors de vue ou derrière un obstacle. "
                "Tu pourras forcer la cible dans la demande, onglet Demandes."
            )
            return
        if mode != "multi":
            self._finish_targeting([token.id])
            return
        if token.id in self._target_ids:
            self._target_ids.remove(token.id)
        elif spec.get("max") and len(self._target_ids) >= int(spec["max"]):
            self.status_message.emit(f"Ce sort cible au plus {int(spec['max'])} token(s).")
        else:
            self._target_ids.append(token.id)
        self.status_message.emit(f"{len(self._target_ids)} cible(s) — Entrée pour valider, Échap pour annuler.")
        self.update()

    def _draw_preview(self, painter: QPainter) -> None:
        if not self._drawing_active or not self._drawing_points or self.media is None:
            return
        tool = self._drawing_tool
        if not isinstance(tool, DrawTool):
            return
        points = list(self._drawing_points)
        if tool is not DrawTool.FREEHAND and len(points) == 1:
            points.append(points[0])
        preview = DrawingElement(
            tool=tool,
            points=points,
            stroke_color=self._drawing_color,
            stroke_width=self._drawing_width,
            opacity=0.72,
        )
        preview.render(painter, self.media)

    def set_drawing_tool(self, tool: DrawTool | str | None) -> None:
        if tool not in (None, "select", "selection"):
            self.cancel_targeting()
        if tool in (None, "select", "selection"):
            self._drawing_tool = None
        elif str(tool).lower() == "eraser":
            self._drawing_tool = "eraser"
        else:
            self._drawing_tool = tool if isinstance(tool, DrawTool) else DrawTool(str(tool))
        self._drawing_active = False
        self._drawing_points.clear()
        if self._drawing_tool is not None:
            self.selected_element_id = None
        self.setCursor(
            Qt.CursorShape.CrossCursor if self._drawing_tool is not None
            else Qt.CursorShape.ArrowCursor
        )
        self.update()

    def set_drawing_color(self, color: str) -> None:
        self._drawing_color = str(color)

    def set_drawing_width(self, width: float) -> None:
        self._drawing_width = max(0.5, min(30.0, float(width)))

    def _erase_at(self, world: Vec2) -> None:
        state = self.runtime.active_map
        if state is None:
            return
        element = self.element_at(world)
        if element is None:
            return
        try:
            self.runtime.remove_map_element(element.id)
        except Exception as exc:
            self.status_message.emit(str(exc))
            return
        if self.selected_element_id == element.id:
            self.selected_element_id = None
        self.runtime_changed.emit()
        self.update()

    def _commit_drawing(self) -> None:
        if not self._drawing_active or not isinstance(self._drawing_tool, DrawTool):
            self._drawing_active = False
            self._drawing_points.clear()
            return
        points = list(self._drawing_points)
        tool = self._drawing_tool
        self._drawing_active = False
        self._drawing_points.clear()
        if tool is not DrawTool.FREEHAND and len(points) >= 2:
            if points[0].x == points[-1].x and points[0].y == points[-1].y:
                self.update()
                return
        if not points:
            self.update()
            return
        try:
            self.runtime.add_map_element(DrawingElement(
                tool=tool,
                points=points,
                stroke_color=self._drawing_color,
                stroke_width=self._drawing_width,
            ))
        except Exception as exc:
            self.status_message.emit(str(exc))
            self.update()
            return
        self.runtime_changed.emit()
        self.update()

    # ------------------------------------------------------------ annotations
    def _runtime_element(self, element_id: str | None) -> MapElement | None:
        state = self.runtime.active_map
        if state is None or not element_id:
            return None
        return next((element for element in state.elements if element.id == element_id), None)

    def element_at(self, world: Vec2) -> MapElement | None:
        """The topmost session annotation under the cursor (a few pixels of slack)."""
        state = self.runtime.active_map
        if state is None:
            return None
        tolerance = 6.0 / max(self.zoom, 0.05)
        for element in reversed(state.elements):
            if getattr(element, "visible", True) and _element_hit(element, world, tolerance):
                return element
        return None

    def _select_element(self, element: MapElement | None) -> None:
        self.selected_element_id = element.id if element is not None else None
        if element is not None:
            self.selected_token_id = None
            hint = "verrouillée" if element.locked else "glisser pour déplacer"
            self.status_message.emit(f"Annotation sélectionnée — {hint}, Suppr pour l'effacer.")
        self.update()

    def _draw_element_selection(self, painter: QPainter) -> None:
        element = self._runtime_element(self.selected_element_id)
        if element is None:
            return
        bounds = element.bounds()
        margin = 4.0 / max(self.zoom, 0.05)
        pen = QPen(QColor("#b58cff"), max(1.0, 1.5 / self.zoom), Qt.PenStyle.DashLine)
        painter.save()
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(
            bounds.x - margin, bounds.y - margin, bounds.width + 2 * margin, bounds.height + 2 * margin
        ))
        painter.restore()

    def remove_selected_element(self) -> None:
        element = self._runtime_element(self.selected_element_id)
        self.selected_element_id = None
        if element is None:
            self.update()
            return
        try:
            self.runtime.remove_map_element(element.id)
        except Exception as exc:
            self.status_message.emit(str(exc))
            return
        self.status_message.emit("Annotation effacée.")
        self.runtime_changed.emit()
        self.update()

    def _show_element_menu(self, element: MapElement, global_pos) -> None:
        menu = QMenu(self)
        delete = menu.addAction("Effacer l'annotation")
        if menu.exec(global_pos) is delete and self.selected_element_id == element.id:
            self.remove_selected_element()

    def _finish_element_drag(self) -> None:
        element_id, snapshot = self._drag_element_id, self._drag_element_snapshot
        self._drag_element_id = None
        self._drag_element_snapshot = None
        self._drag_element_start = None
        live = self._runtime_element(element_id)
        if live is None or snapshot is None:
            return
        moved = MapElement.from_dict(live.to_dict())
        # The journal records "before -> after": put the original back first
        _copy_placement(live, snapshot)
        if moved.to_dict() == snapshot.to_dict():
            self.update()
            return
        try:
            self.runtime.update_map_element(moved)
        except Exception as exc:
            self.status_message.emit(str(exc))
            self.update()
            return
        self.runtime_changed.emit()
        self.update()

    # ---------------------------------------------------------------- hit / input
    def token_at(self, world: Vec2) -> SessionToken | None:
        state = self.runtime.active_map
        if state is None:
            return None
        point = QPointF(world.x, world.y)
        for token in reversed(state.tokens):
            if self._token_rect(token).contains(point):
                return token
        return None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt API
        if event.mimeData().hasFormat(TOKEN_ENTITY_MIME) and self.runtime.active_map is not None:
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802 - Qt API
        if event.mimeData().hasFormat(TOKEN_ENTITY_MIME) and self.runtime.active_map is not None:
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt API
        if not event.mimeData().hasFormat(TOKEN_ENTITY_MIME) or self.runtime.active_map is None:
            event.ignore()
            return
        try:
            entity_id = bytes(event.mimeData().data(TOKEN_ENTITY_MIME)).decode("utf-8").strip()
        except (UnicodeDecodeError, TypeError):
            event.ignore()
            return
        if entity_id not in self.runtime.entities:
            event.ignore()
            return
        world = self.screen_to_world(event.position())
        if self.asset is not None and self.asset.grid.enabled:
            world = self.asset.grid.cell_center(world)
        existing = self.runtime.active_map.token_for_entity(entity_id)
        try:
            if existing is None:
                self.runtime.add_token(SessionToken(entity_id=entity_id, pos=world))
                self.status_message.emit(f"Token placé : {self.runtime.entities[entity_id].name}")
            else:
                self.runtime.move_token(existing.id, world)
                self.status_message.emit(f"Token déplacé : {self.runtime.entities[entity_id].name}")
        except Exception as exc:
            self.status_message.emit(str(exc))
            event.ignore()
            return
        self.runtime_changed.emit()
        self.update()
        event.acceptProposedAction()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_last = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        world = self.screen_to_world(event.position())
        if self._targeting is not None and event.button() == Qt.MouseButton.LeftButton:
            self._targeting_click(world)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._drawing_tool == "eraser":
            self._erase_at(world)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and isinstance(self._drawing_tool, DrawTool):
            if self.runtime.active_map is None:
                self.status_message.emit("Aucune map active.")
                event.accept()
                return
            self._drawing_active = True
            self._drawing_points = [world]
            self.update()
            event.accept()
            return

        token = self.token_at(world)
        if event.button() == Qt.MouseButton.RightButton and token is not None:
            self.selected_token_id = token.id
            self.selected_element_id = None
            self.token_selected.emit(token.id)
            self.token_context_requested.emit(token.id)
            self.update()
            event.accept()
            return
        # Tokens come first; then the annotations drawn during the session
        element = self.element_at(world) if token is None else None
        if event.button() == Qt.MouseButton.RightButton and element is not None:
            self._select_element(element)
            self._show_element_menu(element, event.globalPosition().toPoint())
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected_token_id = token.id if token is not None else None
            if token is not None:
                self.selected_element_id = None
                self.token_selected.emit(token.id)
                self._drag_token_id = token.id
                self._drag_start_pos = Vec2(token.pos.x, token.pos.y)
                self._drag_offset = Vec2(world.x - token.pos.x, world.y - token.pos.y)
            elif element is not None:
                self._select_element(element)
                if not element.locked:
                    self._drag_element_id = element.id
                    self._drag_element_snapshot = MapElement.from_dict(element.to_dict())
                    self._drag_element_start = world
                    self._drag_element_press = event.position()
            else:
                self.selected_element_id = None
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._panning:
            delta = event.position() - self._pan_last
            self._pan_last = event.position()
            self.center.x -= delta.x() / self.zoom
            self.center.y -= delta.y() / self.zoom
            self.update()
            return

        if self._drawing_active and isinstance(self._drawing_tool, DrawTool):
            world = self.screen_to_world(event.position())
            if self._drawing_tool is DrawTool.FREEHAND:
                last = self._drawing_points[-1]
                if abs(world.x - last.x) + abs(world.y - last.y) >= max(1.0, 1.5 / self.zoom):
                    self._drawing_points.append(world)
            elif len(self._drawing_points) == 1:
                self._drawing_points.append(world)
            else:
                self._drawing_points[-1] = world
            self.update()
            return

        if self._drag_element_id and self._drag_element_snapshot is not None:
            live = self._runtime_element(self._drag_element_id)
            start = self._drag_element_start
            if live is not None and start is not None:
                press = self._drag_element_press
                if hypot(event.position().x() - press.x(), event.position().y() - press.y()) < 3:
                    return  # a click, not a drag
                world = self.screen_to_world(event.position())
                _copy_placement(live, self._drag_element_snapshot, Vec2(world.x - start.x, world.y - start.y))
                self.update()
            return

        if self._drag_token_id and self.runtime.active_map is not None:
            token = self.runtime.active_map.token_by_id(self._drag_token_id)
            if token is not None:
                world = self.screen_to_world(event.position())
                target = Vec2(world.x - self._drag_offset.x, world.y - self._drag_offset.y)
                if self.asset is not None and self.asset.grid.enabled:
                    target = self.asset.grid.cell_center(target)
                token.pos = target
                self.update()
            return

        world = self.screen_to_world(event.position())
        if self._targeting is not None:
            if self._targeting.get("allowed") is None:
                self._hover_world = world
                self.update()
            return
        self.status_message.emit(f"x={world.x:.1f}, y={world.y:.1f}  zoom={self.zoom:.2f}x")
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self.unsetCursor()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._drawing_active:
            if isinstance(self._drawing_tool, DrawTool) and self._drawing_tool is not DrawTool.FREEHAND:
                world = self.screen_to_world(event.position())
                if len(self._drawing_points) == 1:
                    self._drawing_points.append(world)
                else:
                    self._drawing_points[-1] = world
            self._commit_drawing()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._drag_element_id:
            self._finish_element_drag()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._drag_token_id:
            token_id = self._drag_token_id
            self._drag_token_id = None
            state = self.runtime.active_map
            if state is None:
                return
            token = state.token_by_id(token_id)
            if token is None:
                return
            final_pos = Vec2(token.pos.x, token.pos.y)
            start = self._drag_start_pos or Vec2(final_pos.x, final_pos.y)
            token.pos = Vec2(start.x, start.y)
            if final_pos.x != start.x or final_pos.y != start.y:
                try:
                    self.runtime.move_token(token_id, final_pos)
                    self.runtime_changed.emit()
                except Exception as exc:
                    self.status_message.emit(str(exc))
            self._drag_start_pos = None
            self.update()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._drawing_tool is not None:
            super().mouseDoubleClickEvent(event)
            return
        world = self.screen_to_world(event.position())
        token = self.token_at(world)
        if token is not None:
            self.token_selected.emit(token.id)
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt API
        before = self.screen_to_world(event.position())
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self.zoom * factor))
        after = self.screen_to_world(event.position())
        self.center.x += before.x - after.x
        self.center.y += before.y - after.y
        self.update()
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._targeting is not None:
            if event.key() == Qt.Key.Key_Escape:
                self.cancel_targeting()
                event.accept()
                return
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._targeting.get("mode") == "multi":
                if not self._target_ids:
                    self.status_message.emit("Choisis au moins une cible.")
                else:
                    self._finish_targeting(list(self._target_ids))
                event.accept()
                return
        if (
            event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace)
            and self.selected_element_id
            and self._drawing_tool is None
            and self._drag_element_id is None
        ):
            self.remove_selected_element()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self.selected_element_id:
            if self._drag_element_id is not None:
                live = self._runtime_element(self._drag_element_id)
                if live is not None and self._drag_element_snapshot is not None:
                    _copy_placement(live, self._drag_element_snapshot)
                self._drag_element_id = None
                self._drag_element_snapshot = None
            self.selected_element_id = None
            self.update()
            event.accept()
            return
        if (
            event.key() == Qt.Key.Key_Delete
            and self.selected_token_id
            and self._drawing_tool is None
        ):
            self._remove_selected_token()
            event.accept()
            return
        super().keyPressEvent(event)

    def _remove_selected_token(self) -> None:
        state = self.runtime.active_map
        token = state.token_by_id(self.selected_token_id) if state is not None else None
        if token is None:
            self.selected_token_id = None
            return
        entity = self.runtime.entities.get(token.entity_id)
        label = entity.name if entity is not None else token.entity_id
        try:
            self.runtime.remove_token(token.id)
        except Exception as exc:
            self.status_message.emit(str(exc))
            return
        self.selected_token_id = None
        self._drag_token_id = None
        self._drag_start_pos = None
        self.status_message.emit(f"Token retiré de la map : {label}")
        self.runtime_changed.emit()
        self.update()


# ---------------------------------------------------------------- annotations
def _segment_distance(point: Vec2, first: Vec2, second: Vec2) -> float:
    dx, dy = second.x - first.x, second.y - first.y
    length2 = dx * dx + dy * dy
    if length2 <= 0:
        return hypot(point.x - first.x, point.y - first.y)
    t = max(0.0, min(1.0, ((point.x - first.x) * dx + (point.y - first.y) * dy) / length2))
    return hypot(point.x - (first.x + t * dx), point.y - (first.y + t * dy))


def _element_hit(element: MapElement, point: Vec2, tolerance: float) -> bool:
    """
    Whether `point` touches what the element draws.

    Strokes are hit along their line (not anywhere in their bounding box), so a
    big rectangle or circle does not swallow the clicks made inside it, unless
    it is filled.
    """
    if not isinstance(element, DrawingElement):
        bounds = element.bounds()
        return (
            bounds.x - tolerance <= point.x <= bounds.x + bounds.width + tolerance
            and bounds.y - tolerance <= point.y <= bounds.y + bounds.height + tolerance
        )
    points = element.points
    if not points:
        return False
    reach = max(0.0, float(element.stroke_width)) / 2 + tolerance
    first, last = points[0], points[-1]
    if element.tool is DrawTool.LINE:
        return _segment_distance(point, first, last) <= reach
    if element.tool in (DrawTool.RECT, DrawTool.CIRCLE):
        left, right = sorted((first.x, last.x))
        top, bottom = sorted((first.y, last.y))
        filled = bool(element.fill_color)
        if element.tool is DrawTool.RECT:
            inside = left - reach <= point.x <= right + reach and top - reach <= point.y <= bottom + reach
            if not inside:
                return False
            if filled:
                return True
            corners = [Vec2(left, top), Vec2(right, top), Vec2(right, bottom), Vec2(left, bottom)]
            return any(
                _segment_distance(point, corners[i], corners[(i + 1) % 4]) <= reach for i in range(4)
            )
        rx, ry = (right - left) / 2, (bottom - top) / 2
        cx, cy = left + rx, top + ry
        if rx < 1e-6 or ry < 1e-6:  # flat ellipse: a segment
            return _segment_distance(point, Vec2(left, top), Vec2(right, bottom)) <= reach
        ratio = hypot((point.x - cx) / rx, (point.y - cy) / ry)
        if filled:
            return ratio <= 1 + reach / min(rx, ry)
        return abs(ratio - 1) * min(rx, ry) <= reach
    if len(points) == 1:
        return hypot(point.x - first.x, point.y - first.y) <= reach
    return any(
        _segment_distance(point, points[i], points[i + 1]) <= reach for i in range(len(points) - 1)
    )


def _copy_placement(target: MapElement, source: MapElement, offset: Vec2 | None = None) -> None:
    """Place `target` where `source` is, moved by `offset` (same element, two copies)."""
    delta = offset or Vec2(0.0, 0.0)
    if isinstance(target, DrawingElement) and isinstance(source, DrawingElement):
        target.points = [Vec2(point.x + delta.x, point.y + delta.y) for point in source.points]
    elif hasattr(target, "pos") and hasattr(source, "pos"):
        target.pos = Vec2(source.pos.x + delta.x, source.pos.y + delta.y)
