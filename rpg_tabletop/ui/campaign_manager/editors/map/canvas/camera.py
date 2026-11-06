# -*- coding: utf-8 -*-

"""Map canvas camera: world/screen transforms, zoom, fitting and the editable initial camera frame."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPen,
)

from rpg_tabletop.assets.map import (
    Rect,
    Vec2,
)
from rpg_tabletop.ui.campaign_manager.editors.map.state import (
    CameraEditSession,
)


class CameraMixin:
    """Camera: world/screen transforms, zoom, fitting, and the editable initial camera frame. Mixed into MapCanvas."""

    # -------------------------------------------------------------------------
    # Initial GM camera overlay
    # -------------------------------------------------------------------------

    def _initial_camera_frame(self) -> Rect:
        """
        Indicative frame for MapAsset.initial_gm_camera.

        CameraState stores center + zoom only. The frame therefore uses the
        current editor canvas size as a reference viewport. Resizing the editor
        changes this indication but never the saved CameraState.
        """
        state = self.asset.initial_gm_camera
        zoom = max(
            self.camera.MIN_ZOOM,
            min(
                self.camera.MAX_ZOOM,
                float(state.zoom if state.zoom > 0 else 1.0),
            ),
        )
        width = max(1.0, float(self.width()) / zoom)
        height = max(1.0, float(self.height()) / zoom)

        return Rect(
            state.center.x - width / 2.0,
            state.center.y - height / 2.0,
            width,
            height,
        )

    @staticmethod
    def _screen_distance(first: QPointF, second: QPointF) -> float:
        return (
            (first.x() - second.x()) ** 2
            + (first.y() - second.y()) ** 2
        ) ** 0.5

    def _camera_center_screen(self) -> QPointF:
        return self.world_to_screen(
            self.asset.initial_gm_camera.center
        )

    def _camera_zoom_handle_screen(self) -> QPointF:
        frame = self._initial_camera_frame()
        return self.world_to_screen(
            Vec2(frame.right, frame.bottom)
        )

    def _camera_hit_part(self, screen_pos: QPointF) -> Optional[str]:
        """
        Only the two explicit camera handles are interactive, so the large
        indicative frame never blocks normal element selection.
        """
        if self._screen_distance(
            screen_pos,
            self._camera_center_screen(),
        ) <= 11.0:
            return "move"

        if self._screen_distance(
            screen_pos,
            self._camera_zoom_handle_screen(),
        ) <= 11.0:
            return "zoom"

        return None

    def _select_camera(self) -> None:
        self.selected_element_ids.clear()
        self.selected_element_id = None
        self.selected_token_ids.clear()
        self.selected_token_id = None
        self.camera_selected = True

        self.selection_changed.emit(None)
        self._sync_text_format_bar()
        self._sync_image_format_bar()
        self.update()

    def _start_camera_edit(self, mode: str, screen_pos: QPointF) -> None:
        self._select_camera()

        state = self.asset.initial_gm_camera
        world = self.screen_to_world(screen_pos)

        self._camera_edit = CameraEditSession(
            mode=mode,
            start_screen=QPointF(screen_pos),
            start_world=Vec2(world.x, world.y),
            start_center=Vec2(state.center.x, state.center.y),
            start_zoom=float(state.zoom if state.zoom > 0 else 1.0),
            start_handle_distance=max(
                1.0,
                self._screen_distance(
                    self._camera_center_screen(),
                    self._camera_zoom_handle_screen(),
                ),
            ),
            history_before=self._capture_state(),
        )

        self.setCursor(
            Qt.CursorShape.SizeAllCursor
            if mode == "move"
            else Qt.CursorShape.SizeFDiagCursor
        )

    def _update_camera_edit(self, screen_pos: QPointF) -> None:
        session = self._camera_edit
        if session is None:
            return

        state = self.asset.initial_gm_camera

        if session.mode == "move":
            world = self.screen_to_world(screen_pos)
            dx = world.x - session.start_world.x
            dy = world.y - session.start_world.y

            state.center = Vec2(
                max(
                    0.0,
                    min(
                        float(self.asset.width),
                        session.start_center.x + dx,
                    ),
                ),
                max(
                    0.0,
                    min(
                        float(self.asset.height),
                        session.start_center.y + dy,
                    ),
                ),
            )

        elif session.mode == "zoom":
            center_screen = self.world_to_screen(
                session.start_center
            )
            distance = max(
                12.0,
                self._screen_distance(
                    center_screen,
                    screen_pos,
                ),
            )

            state.zoom = max(
                self.camera.MIN_ZOOM,
                min(
                    self.camera.MAX_ZOOM,
                    session.start_zoom
                    * session.start_handle_distance
                    / distance,
                ),
            )

        self.update()

    def _finish_camera_edit(self) -> None:
        session = self._camera_edit
        if (
            session is not None
            and session.history_before is not None
        ):
            self._commit_history(session.history_before)

        self._camera_edit = None
        self.unsetCursor()
        self.update()

    def cancel_camera_edit(self) -> None:
        session = self._camera_edit
        if session is None:
            return

        state = self.asset.initial_gm_camera
        state.center = Vec2(
            session.start_center.x,
            session.start_center.y,
        )
        state.zoom = session.start_zoom

        self._camera_edit = None
        self.unsetCursor()
        self.update()

    def _draw_initial_camera_overlay(self, painter: QPainter) -> None:
        frame = self._initial_camera_frame()
        state = self.asset.initial_gm_camera
        editor_zoom = max(0.001, self.camera.zoom)

        painter.save()

        color = QColor(
            "#74c7ec"
            if self.camera_selected
            else "#6c7086"
        )
        color.setAlpha(
            235 if self.camera_selected else 170
        )

        pen = QPen(color)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidthF(
            (2.0 if self.camera_selected else 1.25)
            / editor_zoom
        )
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(frame.to_qrectf())

        handle_radius = 7.0 / editor_zoom
        handle_size = 12.0 / editor_zoom

        painter.setPen(
            QPen(
                color,
                1.5 / editor_zoom,
            )
        )
        painter.setBrush(
            QColor(17, 17, 27, 210)
        )
        painter.drawEllipse(
            QPointF(
                state.center.x,
                state.center.y,
            ),
            handle_radius,
            handle_radius,
        )
        painter.drawLine(
            QPointF(
                state.center.x - handle_radius * 1.6,
                state.center.y,
            ),
            QPointF(
                state.center.x + handle_radius * 1.6,
                state.center.y,
            ),
        )
        painter.drawLine(
            QPointF(
                state.center.x,
                state.center.y - handle_radius * 1.6,
            ),
            QPointF(
                state.center.x,
                state.center.y + handle_radius * 1.6,
            ),
        )

        painter.setBrush(color)
        painter.drawRect(
            QRectF(
                frame.right - handle_size / 2.0,
                frame.bottom - handle_size / 2.0,
                handle_size,
                handle_size,
            )
        )

        font = QFont()
        font.setBold(self.camera_selected)
        font.setPixelSize(
            max(
                1,
                round(11.0 / editor_zoom),
            )
        )
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(
            QPointF(
                frame.x,
                frame.y - 8.0 / editor_zoom,
            ),
            f"INITIAL GM CAMERA  {state.zoom:.2f}x",
        )

        painter.restore()

    # -------------------------------------------------------------------------
    # Camera transforms
    # -------------------------------------------------------------------------

    def screen_to_world(self, point: QPointF) -> Vec2:
        zoom = self.camera.zoom
        return Vec2(
            self.camera.center.x + (point.x() - self.width() / 2.0) / zoom,
            self.camera.center.y + (point.y() - self.height() / 2.0) / zoom,
        )

    def world_to_screen(self, point: Vec2) -> QPointF:
        zoom = self.camera.zoom
        return QPointF(
            self.width() / 2.0 + (point.x - self.camera.center.x) * zoom,
            self.height() / 2.0 + (point.y - self.camera.center.y) * zoom,
        )

    def visible_world_rect(self) -> Rect:
        zoom = self.camera.zoom
        world_width = self.width() / zoom
        world_height = self.height() / zoom
        return Rect(
            self.camera.center.x - world_width / 2.0,
            self.camera.center.y - world_height / 2.0,
            world_width,
            world_height,
        )

    def _apply_camera_transform(self, painter: QPainter) -> None:
        painter.translate(self.width() / 2.0, self.height() / 2.0)
        painter.scale(self.camera.zoom, self.camera.zoom)
        painter.translate(-self.camera.center.x, -self.camera.center.y)

    def set_zoom_at(self, new_zoom: float, screen_pos: QPointF) -> None:
        old_world = self.screen_to_world(screen_pos)
        self.camera.set_zoom(new_zoom)

        # Preserve the world point under the mouse cursor.
        self.camera.center = Vec2(
            old_world.x - (screen_pos.x() - self.width() / 2.0) / self.camera.zoom,
            old_world.y - (screen_pos.y() - self.height() / 2.0) / self.camera.zoom,
        )

        self.zoom_changed.emit(self.camera.zoom)
        self.update()

    def show_initial_camera(self) -> None:
        state = self.asset.initial_gm_camera
        if state.zoom <= 0:
            self.fit_map()
            return
        self.camera.from_state(state)
        self.zoom_changed.emit(self.camera.zoom)
        self.update()

    def set_current_as_initial_camera(self) -> None:
        self.asset.initial_gm_camera = self.camera.to_state()

    def fit_map(self, margin_px: float = 32.0) -> None:
        if self.asset.width <= 0 or self.asset.height <= 0:
            return

        usable_width = max(1.0, self.width() - margin_px * 2.0)
        usable_height = max(1.0, self.height() - margin_px * 2.0)

        zoom = min(
            usable_width / self.asset.width,
            usable_height / self.asset.height,
        )

        self.camera.center = Vec2(
            self.asset.width / 2.0,
            self.asset.height / 2.0,
        )
        self.camera.set_zoom(zoom)
        self.zoom_changed.emit(self.camera.zoom)
        self.update()
