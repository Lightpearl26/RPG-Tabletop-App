# -*- coding: utf-8 -*-

"""Map canvas painting helpers: grid, map border, selection frames, marquee and errors."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPen,
    QPolygonF,
)

from rpg_tabletop.assets.map import (
    Rect,
    Vec2,
)
from rpg_tabletop.ui.campaign_manager.editors.map.state import (
    _midpoint,
    _rotate_vector,
)


class PaintingMixin:
    """Painting helpers: grid, map border, selection frames, marquee and errors. Mixed into MapCanvas."""

    # -------------------------------------------------------------------------
    # Rendering overlays
    # -------------------------------------------------------------------------

    def _draw_grid(self, painter: QPainter, view: Rect) -> None:
        grid = self.asset.grid
        if not grid.enabled or grid.cell_size <= 0:
            return

        size = float(grid.cell_size)
        color = QColor(grid.color)
        color.setAlphaF(max(0.0, min(1.0, grid.opacity)))

        pen = QPen(color)
        # Keep the visual grid line approximately one physical pixel wide.
        pen.setWidthF(1.0 / self.camera.zoom)
        painter.setPen(pen)

        map_left = 0.0
        map_top = 0.0
        map_right = float(self.asset.width)
        map_bottom = float(self.asset.height)

        first_col = int((view.x - grid.offset_x) // size) - 1
        last_col = int((view.right - grid.offset_x) // size) + 1
        first_row = int((view.y - grid.offset_y) // size) - 1
        last_row = int((view.bottom - grid.offset_y) // size) + 1

        for column in range(first_col, last_col + 1):
            x = grid.offset_x + column * size
            if map_left <= x <= map_right:
                painter.drawLine(QPointF(x, map_top), QPointF(x, map_bottom))

        for row in range(first_row, last_row + 1):
            y = grid.offset_y + row * size
            if map_top <= y <= map_bottom:
                painter.drawLine(QPointF(map_left, y), QPointF(map_right, y))

    def _draw_map_border(self, painter: QPainter) -> None:
        pen = QPen(QColor("#7f849c"))
        pen.setWidthF(1.0 / self.camera.zoom)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(0.0, 0.0, float(self.asset.width), float(self.asset.height)))

    def _draw_selection(self, painter: QPainter) -> None:
        """Draw selection frames for tokens or static map elements."""
        selected_tokens = self.selected_tokens()

        if selected_tokens:
            painter.save()
            painter.resetTransform()

            polygons: list[QPolygonF] = []
            all_points: list[QPointF] = []

            pen = QPen(
                QColor("#89b4fa")
            )
            pen.setWidthF(2.0)
            painter.setPen(pen)
            painter.setBrush(
                Qt.BrushStyle.NoBrush
            )

            for token in selected_tokens:
                size = self._token_size(
                    token
                )
                half = size / 2.0
                local_corners = [
                    Vec2(-half, -half),
                    Vec2(half, -half),
                    Vec2(half, half),
                    Vec2(-half, half),
                ]
                world_corners = [
                    token.pos
                    + _rotate_vector(
                        corner,
                        token.rotation,
                    )
                    for corner in local_corners
                ]
                screen_points = [
                    self.world_to_screen(
                        point
                    )
                    for point in world_corners
                ]
                polygon = QPolygonF(
                    screen_points
                )
                polygons.append(
                    polygon
                )
                all_points.extend(
                    screen_points
                )
                painter.drawPolygon(
                    polygon
                )

            if len(selected_tokens) > 1 and all_points:
                left = min(
                    point.x()
                    for point in all_points
                )
                top = min(
                    point.y()
                    for point in all_points
                )
                right = max(
                    point.x()
                    for point in all_points
                )
                bottom = max(
                    point.y()
                    for point in all_points
                )

                group_pen = QPen(
                    QColor("#b4befe")
                )
                group_pen.setWidthF(1.5)
                group_pen.setStyle(
                    Qt.PenStyle.DashLine
                )
                painter.setPen(
                    group_pen
                )
                painter.drawRect(
                    QRectF(
                        QPointF(
                            left - 7.0,
                            top - 7.0,
                        ),
                        QPointF(
                            right + 7.0,
                            bottom + 7.0,
                        ),
                    )
                )

            painter.restore()
            return

        selected = self.selected_elements()
        if not selected:
            return

        painter.save()
        painter.resetTransform()

        # Multi-selection: member outlines + one active group transform frame.
        if len(selected) > 1:
            member_pen = QPen(QColor("#89b4fa"))
            member_pen.setWidthF(1.25)
            member_pen.setStyle(Qt.PenStyle.DotLine)
            painter.setPen(member_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            for element in selected:
                frame = self._element_frame(element)
                painter.drawPolygon(
                    QPolygonF(
                        self._frame_screen_points(frame)
                    )
                )

            group_frame = self._selection_frame(selected)
            if group_frame is not None:
                locked = self._group_is_locked(selected)

                pen = QPen(QColor("#b4befe"))
                pen.setWidthF(2.0)
                pen.setStyle(
                    Qt.PenStyle.DashLine
                    if locked
                    else Qt.PenStyle.SolidLine
                )
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)

                points = self._frame_screen_points(
                    group_frame
                )
                painter.drawPolygon(
                    QPolygonF(points)
                )

                if not locked:
                    handle_pen = QPen(
                        QColor("#1e1e2e")
                    )
                    handle_pen.setWidthF(1.0)
                    painter.setPen(handle_pen)
                    painter.setBrush(
                        QBrush(
                            QColor("#b4befe")
                        )
                    )

                    for center in self._screen_handle_positions(
                        group_frame
                    ).values():
                        painter.drawRect(
                            self._handle_rect(center)
                        )

                    if self._group_can_rotate(selected):
                        tl, tr, _br, _bl = points
                        top = _midpoint(tl, tr)
                        rotate_pos = self._rotation_handle_pos(
                            group_frame
                        )

                        painter.setPen(
                            QPen(
                                QColor("#b4befe"),
                                2.0,
                            )
                        )
                        painter.drawLine(
                            top,
                            rotate_pos,
                        )

                        painter.setPen(
                            handle_pen
                        )
                        painter.drawEllipse(
                            self._handle_rect(
                                rotate_pos
                            )
                        )

            painter.restore()
            return

        element = selected[0]
        frame = self._element_frame(element)
        points = self._frame_screen_points(frame)
        polygon = QPolygonF(points)

        pen = QPen(QColor("#89b4fa"))
        pen.setWidthF(2.0)
        pen.setStyle(Qt.PenStyle.DashLine if element.locked else Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(polygon)

        if element.locked:
            painter.restore()
            return

        handle_pen = QPen(QColor("#1e1e2e"))
        handle_pen.setWidthF(1.0)
        painter.setPen(handle_pen)
        painter.setBrush(QBrush(QColor("#89b4fa")))

        for center in self._screen_handle_positions(frame).values():
            painter.drawRect(self._handle_rect(center))

        if self._can_rotate(element):
            tl, tr, _br, _bl = points
            top = _midpoint(tl, tr)
            rotate_pos = self._rotation_handle_pos(frame)
            painter.setPen(QPen(QColor("#89b4fa"), 2.0))
            painter.drawLine(top, rotate_pos)
            painter.setPen(handle_pen)
            painter.drawEllipse(self._handle_rect(rotate_pos))

        painter.restore()

    def _draw_marquee(self, painter: QPainter) -> None:
        rect = self._marquee_screen_rect()
        if rect is None:
            return
        painter.save()
        painter.resetTransform()
        fill = QColor("#89b4fa")
        fill.setAlpha(42)
        painter.setBrush(QBrush(fill))
        pen = QPen(QColor("#89b4fa"))
        pen.setWidthF(1.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRect(rect)
        painter.restore()

    def _draw_render_error(self, painter: QPainter, message: str) -> None:
        painter.save()
        painter.resetTransform()
        painter.setPen(QColor("#f38ba8"))
        painter.drawText(
            QRectF(12.0, 12.0, max(100.0, self.width() - 24.0), 80.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
            f"Render error: {message}",
        )
        painter.restore()
