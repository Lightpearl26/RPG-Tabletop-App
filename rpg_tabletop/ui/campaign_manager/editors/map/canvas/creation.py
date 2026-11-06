# -*- coding: utf-8 -*-

"""Map canvas creation tools: drawings, walls, images and text."""

from __future__ import annotations

from math import hypot
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QPainter,
)

from rpg_tabletop.assets.map import (
    DrawingElement,
    DrawTool,
    ImageElement,
    MapElement,
    TextElement,
    Vec2,
    WallElement,
)
from rpg_tabletop.ui.campaign_manager.editors.map.state import (
    DRAW_TOOLS,
    EditorTool,
)


class CreationMixin:
    """Tools and element creation: drawings, walls, images and text. Mixed into MapCanvas."""

    # -------------------------------------------------------------------------
    # Editor tools / creation
    # -------------------------------------------------------------------------

    def set_tool(self, tool: EditorTool) -> None:
        if self._transform is not None:
            self.cancel_transform()
        if self._token_move is not None:
            self.cancel_token_move()
        if self._camera_edit is not None:
            self.cancel_camera_edit()
        self._cancel_creation()
        self._cancel_marquee()
        changed = tool is not self.active_tool
        self.active_tool = tool
        if tool is not EditorTool.SELECT:
            self.pending_token_entity_id = None

        if tool is EditorTool.SELECT:
            self.unsetCursor()
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

        if changed:
            self.tool_changed.emit(tool)
        self._sync_text_format_bar()
        self._sync_image_format_bar()
        self.update()

    def set_creation_style(
        self,
        *,
        stroke_color: Optional[str] = None,
        fill_color: object = ...,
        stroke_width: Optional[float] = None,
        opacity: Optional[float] = None,
        font_size: Optional[float] = None,
    ) -> None:
        if stroke_color is not None:
            self.stroke_color = stroke_color
        if fill_color is not ...:
            self.fill_color = fill_color if isinstance(fill_color, str) else None
        if stroke_width is not None:
            self.stroke_width = max(0.0, float(stroke_width))
        if opacity is not None:
            self.element_opacity = max(0.0, min(1.0, float(opacity)))
        if font_size is not None:
            self.text_font_size = max(1.0, float(font_size))
        self.update()

    def set_pending_image(self, asset: Path, width: float, height: float) -> None:
        self.pending_image_asset = Path(asset)
        self.pending_image_size = (max(1.0, float(width)), max(1.0, float(height)))
        self.set_tool(EditorTool.IMAGE)

    def _cancel_creation(self) -> None:
        self._creation_start = None
        self._creation_current = None
        self._creation_points = []
        self.update()

    def _append_element(self, element: MapElement, *, select: bool = True) -> None:
        before = self._capture_state()
        self.asset.elements.append(element)
        if select:
            self._select_element(element)
        self._commit_history(before)
        self.update()

    def _start_drawing_creation(self, world: Vec2) -> None:
        self._creation_start = Vec2(world.x, world.y)
        self._creation_current = Vec2(world.x, world.y)
        self._creation_points = [Vec2(world.x, world.y)]

    def _update_drawing_creation(self, world: Vec2) -> None:
        if self._creation_start is None:
            return
        self._creation_current = Vec2(world.x, world.y)
        if self.active_tool is EditorTool.FREEHAND:
            if not self._creation_points:
                self._creation_points.append(Vec2(world.x, world.y))
            else:
                last = self._creation_points[-1]
                # Avoid producing hundreds of identical points at high event rates.
                if hypot(world.x - last.x, world.y - last.y) >= max(0.5, 1.0 / self.camera.zoom):
                    self._creation_points.append(Vec2(world.x, world.y))
        self.update()

    def _snap_wall_point(self, point: Vec2) -> Vec2:
        """Walls follow cell edges: their ends go to the nearest grid corner."""
        grid = self.asset.grid
        if not grid.enabled or grid.cell_size <= 0:
            return Vec2(point.x, point.y)
        size = float(grid.cell_size)
        return Vec2(
            grid.offset_x + round((point.x - grid.offset_x) / size) * size,
            grid.offset_y + round((point.y - grid.offset_y) / size) * size,
        )

    def _drawing_preview(self) -> Optional[DrawingElement]:
        if self.active_tool is EditorTool.WALL:
            if self._creation_start is None:
                return None
            end = self._creation_current or self._creation_start
            return WallElement(
                points=[self._snap_wall_point(self._creation_start), self._snap_wall_point(end)]
            )
        draw_tool = DRAW_TOOLS.get(self.active_tool)
        if draw_tool is None or self._creation_start is None:
            return None

        if self.active_tool is EditorTool.FREEHAND:
            points = [Vec2(p.x, p.y) for p in self._creation_points]
            if self._creation_current is not None and points:
                last = points[-1]
                if last.x != self._creation_current.x or last.y != self._creation_current.y:
                    points.append(Vec2(self._creation_current.x, self._creation_current.y))
        else:
            end = self._creation_current or self._creation_start
            points = [
                Vec2(self._creation_start.x, self._creation_start.y),
                Vec2(end.x, end.y),
            ]

        return DrawingElement(
            tool=draw_tool,
            points=points,
            stroke_color=self.stroke_color,
            stroke_width=self.stroke_width,
            fill_color=(
                self.fill_color
                if draw_tool in (DrawTool.RECT, DrawTool.CIRCLE)
                else None
            ),
            opacity=self.element_opacity,
        )

    def _finish_drawing_creation(self, world: Vec2) -> None:
        if self._creation_start is None:
            return
        self._update_drawing_creation(world)
        element = self._drawing_preview()
        self._cancel_creation()
        if element is None or not element.points:
            return
        if isinstance(element, WallElement) and not element.segments():
            return  # a click without a drag: no zero-length wall
        self._append_element(element, select=True)

    def _place_image(self, world: Vec2) -> None:
        if self.pending_image_asset is None:
            return
        width, height = self.pending_image_size
        element = ImageElement(
            asset=self.pending_image_asset,
            pos=Vec2(world.x - width / 2.0, world.y - height / 2.0),
            width=width,
            height=height,
            opacity=self.element_opacity,
        )
        self._append_element(element, select=True)
        # Image placement is usually followed by a resize/rotation.
        self.set_tool(EditorTool.SELECT)

    def _place_text(self, world: Vec2) -> None:
        element = TextElement(
            text="Text",
            pos=Vec2(world.x, world.y),
            font_size=self.text_font_size,
            font_color=self.stroke_color,
            opacity=self.element_opacity,
        )
        self._append_element(element, select=True)
        self.set_tool(EditorTool.SELECT)
        self._sync_text_format_bar(focus=True, select_all=True)

    def _draw_creation_preview(self, painter: QPainter) -> None:
        preview = self._drawing_preview()
        if preview is not None:
            preview.render(painter, self.media)
            return

        if (
            self.active_tool is EditorTool.IMAGE
            and self.pending_image_asset is not None
            and self._hover_world is not None
        ):
            width, height = self.pending_image_size
            ghost = ImageElement(
                asset=self.pending_image_asset,
                pos=Vec2(
                    self._hover_world.x - width / 2.0,
                    self._hover_world.y - height / 2.0,
                ),
                width=width,
                height=height,
                opacity=min(self.element_opacity, 0.65),
            )
            try:
                ghost.render(painter, self.media)
            except Exception:
                # The regular render error path will report unreadable assets after placement.
                pass
