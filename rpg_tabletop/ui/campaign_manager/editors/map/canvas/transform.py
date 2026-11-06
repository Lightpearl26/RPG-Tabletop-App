# -*- coding: utf-8 -*-

"""Map canvas transforms: move, resize and rotate one element or a group, with their handles."""

from __future__ import annotations

from math import hypot, sqrt
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt

from rpg_tabletop.assets.map import (
    DrawingElement,
    DrawTool,
    ImageElement,
    MapElement,
    TextElement,
    Vec2,
)
from rpg_tabletop.ui.campaign_manager.editors.map.state import (
    HANDLE_SIZE_PX,
    MIN_ELEMENT_SIZE_PX,
    ROTATE_HANDLE_OFFSET_PX,
    ElementFrame,
    ElementSnapshot,
    GroupTransformSession,
    ResizeHandle,
    TransformMode,
    TransformSession,
    _angle,
    _midpoint,
    _rotate_vector,
)


class TransformMixin:
    """Move, resize and rotate of one element or a group, with their handles. Mixed into MapCanvas."""

    def _snapshot(self, element: MapElement) -> ElementSnapshot:
        if isinstance(element, ImageElement):
            return ElementSnapshot(
                pos=Vec2(element.pos.x, element.pos.y),
                width=element.width,
                height=element.height,
                rotation=element.rotation,
            )
        if isinstance(element, TextElement):
            return ElementSnapshot(
                pos=Vec2(element.pos.x, element.pos.y),
                rotation=element.rotation,
                font_size=element.font_size,
            )
        if isinstance(element, DrawingElement):
            return ElementSnapshot(
                points=[Vec2(point.x, point.y) for point in element.points],
            )
        return ElementSnapshot()

    @staticmethod
    def _drawing_can_rotate(element: DrawingElement) -> bool:
        # RECT/CIRCLE are stored as two opposite corners of an axis-aligned box.
        # Their current model cannot represent an oriented rectangle/ellipse.
        return element.tool in (DrawTool.FREEHAND, DrawTool.LINE)

    def _can_rotate(self, element: MapElement) -> bool:
        if isinstance(element, DrawingElement):
            return self._drawing_can_rotate(element)
        return isinstance(element, (ImageElement, TextElement))

    def _screen_handle_positions(self, frame: ElementFrame) -> dict[ResizeHandle, QPointF]:
        tl, tr, br, bl = self._frame_screen_points(frame)
        return {
            ResizeHandle.TOP_LEFT: tl,
            ResizeHandle.TOP: _midpoint(tl, tr),
            ResizeHandle.TOP_RIGHT: tr,
            ResizeHandle.RIGHT: _midpoint(tr, br),
            ResizeHandle.BOTTOM_RIGHT: br,
            ResizeHandle.BOTTOM: _midpoint(bl, br),
            ResizeHandle.BOTTOM_LEFT: bl,
            ResizeHandle.LEFT: _midpoint(tl, bl),
        }

    @staticmethod
    def _handle_rect(center: QPointF) -> QRectF:
        half = HANDLE_SIZE_PX / 2.0
        return QRectF(center.x() - half, center.y() - half, HANDLE_SIZE_PX, HANDLE_SIZE_PX)

    def _rotation_handle_pos(self, frame: ElementFrame) -> QPointF:
        tl, tr, br, bl = self._frame_screen_points(frame)
        top = _midpoint(tl, tr)
        center = QPointF(
            (tl.x() + tr.x() + br.x() + bl.x()) / 4.0,
            (tl.y() + tr.y() + br.y() + bl.y()) / 4.0,
        )
        dx, dy = top.x() - center.x(), top.y() - center.y()
        length = hypot(dx, dy)
        if length < 1e-6:
            dx, dy, length = 0.0, -1.0, 1.0
        return QPointF(
            top.x() + dx / length * ROTATE_HANDLE_OFFSET_PX,
            top.y() + dy / length * ROTATE_HANDLE_OFFSET_PX,
        )

    def _hit_resize_handle(self, frame: ElementFrame, point: QPointF) -> Optional[ResizeHandle]:
        for handle, center in self._screen_handle_positions(frame).items():
            if self._handle_rect(center).contains(point):
                return handle
        return None

    def _hit_rotation_handle(self, frame: ElementFrame, point: QPointF) -> bool:
        return self._handle_rect(self._rotation_handle_pos(frame)).contains(point)

    @staticmethod
    def _resize_cursor(handle: ResizeHandle) -> Qt.CursorShape:
        if handle in (ResizeHandle.TOP_LEFT, ResizeHandle.BOTTOM_RIGHT):
            return Qt.CursorShape.SizeFDiagCursor
        if handle in (ResizeHandle.TOP_RIGHT, ResizeHandle.BOTTOM_LEFT):
            return Qt.CursorShape.SizeBDiagCursor
        if handle in (ResizeHandle.TOP, ResizeHandle.BOTTOM):
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.SizeHorCursor

    def _update_hover_cursor(self, point: QPointF) -> None:
        if (
            self._panning
            or self._transform is not None
            or self._token_move is not None
        ):
            return

        camera_part = self._camera_hit_part(point)
        if camera_part is not None:
            self.setCursor(
                Qt.CursorShape.SizeAllCursor
                if camera_part == "move"
                else Qt.CursorShape.SizeFDiagCursor
            )
            return

        selected_tokens = self.selected_tokens()
        if selected_tokens:
            world = self.screen_to_world(
                point
            )

            if any(
                self._contains(
                    self._token_rect(token),
                    world,
                )
                for token in selected_tokens
            ):
                self.setCursor(
                    Qt.CursorShape.SizeAllCursor
                )
            else:
                self.unsetCursor()
            return

        selected = self.selected_elements()
        if not selected:
            self.unsetCursor()
            return

        if len(selected) > 1:
            if self._group_is_locked(selected):
                self.unsetCursor()
                return

            frame = self._selection_frame(selected)
            if frame is None:
                self.unsetCursor()
                return

            handle = self._hit_resize_handle(frame, point)
            if handle is not None:
                self.setCursor(self._resize_cursor(handle))
            elif self._group_can_rotate(selected) and self._hit_rotation_handle(frame, point):
                self.setCursor(Qt.CursorShape.CrossCursor)
            elif self._frame_contains_screen(frame, point):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.unsetCursor()
            return

        element = selected[0]
        if element.locked:
            self.unsetCursor()
            return

        frame = self._element_frame(element)
        handle = self._hit_resize_handle(frame, point)
        if handle is not None:
            self.setCursor(self._resize_cursor(handle))
        elif self._can_rotate(element) and self._hit_rotation_handle(frame, point):
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif self._frame_contains_screen(frame, point):
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.unsetCursor()

    # -------------------------------------------------------------------------
    # Transform sessions
    # -------------------------------------------------------------------------

    def _start_transform(
        self,
        element: MapElement,
        mode: TransformMode,
        screen_pos: QPointF,
        *,
        handle: Optional[ResizeHandle] = None,
    ) -> None:
        frame = self._element_frame(element)
        world = self.screen_to_world(screen_pos)
        self._transform = TransformSession(
            element_id=element.id,
            mode=mode,
            start_world=world,
            start_screen=QPointF(screen_pos),
            frame=frame,
            snapshot=self._snapshot(element),
            history_before=self._capture_state(),
            handle=handle,
            start_angle=_angle(frame.center, world),
        )
        if mode is TransformMode.MOVE:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif mode is TransformMode.ROTATE:
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif handle is not None:
            self.setCursor(self._resize_cursor(handle))

    def _start_group_transform(
        self,
        elements: list[MapElement],
        mode: TransformMode,
        screen_pos: QPointF,
        *,
        handle: Optional[ResizeHandle] = None,
    ) -> None:
        frame = self._selection_frame(elements)
        if frame is None:
            return

        world = self.screen_to_world(screen_pos)
        self._transform = GroupTransformSession(
            element_ids=[element.id for element in elements],
            mode=mode,
            start_world=world,
            start_screen=QPointF(screen_pos),
            frame=frame,
            snapshots={
                element.id: self._snapshot(element)
                for element in elements
            },
            element_frames={
                element.id: self._element_frame(element)
                for element in elements
            },
            history_before=self._capture_state(),
            handle=handle,
            start_angle=_angle(frame.center, world),
        )

        if mode is TransformMode.MOVE:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif mode is TransformMode.ROTATE:
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif handle is not None:
            self.setCursor(self._resize_cursor(handle))

    def _restore_snapshot(self, element: MapElement, snapshot: ElementSnapshot) -> None:
        if isinstance(element, ImageElement):
            if snapshot.pos is not None:
                element.pos = Vec2(snapshot.pos.x, snapshot.pos.y)
            if snapshot.width is not None:
                element.width = snapshot.width
            if snapshot.height is not None:
                element.height = snapshot.height
            if snapshot.rotation is not None:
                element.rotation = snapshot.rotation
        elif isinstance(element, TextElement):
            if snapshot.pos is not None:
                element.pos = Vec2(snapshot.pos.x, snapshot.pos.y)
            if snapshot.font_size is not None:
                element.font_size = snapshot.font_size
            if snapshot.rotation is not None:
                element.rotation = snapshot.rotation
        elif isinstance(element, DrawingElement) and snapshot.points is not None:
            element.points = [Vec2(point.x, point.y) for point in snapshot.points]

    def cancel_transform(self) -> None:
        session = self._transform
        if session is None:
            return

        if isinstance(session, GroupTransformSession):
            elements = {
                element.id: element
                for element in self.asset.elements
                if element.id in session.snapshots
            }
            for element_id, snapshot in session.snapshots.items():
                element = elements.get(element_id)
                if element is not None:
                    self._restore_snapshot(element, snapshot)
        else:
            element = self.selected_element()
            if element is not None and element.id == session.element_id:
                self._restore_snapshot(element, session.snapshot)

        self._transform = None
        self.unsetCursor()
        self.update()

    def _finish_transform(self) -> None:
        session = self._transform
        if session is not None and session.history_before is not None:
            self._commit_history(session.history_before)
        self._transform = None
        self.unsetCursor()
        self.update()

    def _apply_move(
        self,
        element: MapElement,
        session: TransformSession,
        world: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> None:
        delta = world - session.start_world
        delta = self._snap_move_delta(element, session, delta, modifiers)
        snapshot = session.snapshot
        if isinstance(element, (ImageElement, TextElement)) and snapshot.pos is not None:
            element.pos = snapshot.pos + delta
        elif isinstance(element, DrawingElement) and snapshot.points is not None:
            element.points = [point + delta for point in snapshot.points]

    @staticmethod
    def _resize_local_edges(
        frame: ElementFrame,
        handle: ResizeHandle,
        world: Vec2,
    ) -> tuple[float, float, float, float]:
        local = _rotate_vector(world - frame.center, -frame.rotation)
        left, right = -frame.width / 2.0, frame.width / 2.0
        top, bottom = -frame.height / 2.0, frame.height / 2.0
        minimum = MIN_ELEMENT_SIZE_PX

        if handle in (ResizeHandle.TOP_LEFT, ResizeHandle.LEFT, ResizeHandle.BOTTOM_LEFT):
            left = min(local.x, right - minimum)
        if handle in (ResizeHandle.TOP_RIGHT, ResizeHandle.RIGHT, ResizeHandle.BOTTOM_RIGHT):
            right = max(local.x, left + minimum)
        if handle in (ResizeHandle.TOP_LEFT, ResizeHandle.TOP, ResizeHandle.TOP_RIGHT):
            top = min(local.y, bottom - minimum)
        if handle in (ResizeHandle.BOTTOM_LEFT, ResizeHandle.BOTTOM, ResizeHandle.BOTTOM_RIGHT):
            bottom = max(local.y, top + minimum)
        return left, top, right, bottom

    def _apply_image_resize(
        self,
        element: ImageElement,
        session: TransformSession,
        world: Vec2,
    ) -> None:
        if session.handle is None:
            return
        left, top, right, bottom = self._resize_local_edges(session.frame, session.handle, world)
        width, height = right - left, bottom - top
        local_center = Vec2((left + right) / 2.0, (top + bottom) / 2.0)
        center = session.frame.center + _rotate_vector(local_center, session.frame.rotation)
        element.width = width
        element.height = height
        element.pos = Vec2(center.x - width / 2.0, center.y - height / 2.0)

    @staticmethod
    def _opposite_anchor(handle: ResizeHandle, width: float, height: float) -> Vec2:
        half_w, half_h = width / 2.0, height / 2.0
        anchors = {
            ResizeHandle.TOP_LEFT: Vec2(half_w, half_h),
            ResizeHandle.TOP: Vec2(0.0, half_h),
            ResizeHandle.TOP_RIGHT: Vec2(-half_w, half_h),
            ResizeHandle.RIGHT: Vec2(-half_w, 0.0),
            ResizeHandle.BOTTOM_RIGHT: Vec2(-half_w, -half_h),
            ResizeHandle.BOTTOM: Vec2(0.0, -half_h),
            ResizeHandle.BOTTOM_LEFT: Vec2(half_w, -half_h),
            ResizeHandle.LEFT: Vec2(half_w, 0.0),
        }
        return anchors[handle]

    def _apply_text_resize(
        self,
        element: TextElement,
        session: TransformSession,
        world: Vec2,
    ) -> None:
        if session.handle is None or session.snapshot.font_size is None:
            return
        frame = session.frame
        local = _rotate_vector(world - frame.center, -frame.rotation)
        half_w = max(frame.width / 2.0, 0.5)
        half_h = max(frame.height / 2.0, 0.5)
        handle = session.handle

        if handle in (ResizeHandle.LEFT, ResizeHandle.RIGHT):
            scale = abs(local.x) / half_w
        elif handle in (ResizeHandle.TOP, ResizeHandle.BOTTOM):
            scale = abs(local.y) / half_h
        else:
            original_radius = hypot(half_w, half_h)
            scale = hypot(local.x, local.y) / max(original_radius, 1e-6)

        scale = max(1.0 / max(1.0, session.snapshot.font_size), scale)
        element.font_size = max(1.0, session.snapshot.font_size * scale)
        new_width, new_height = self._text_size(element)

        old_anchor_local = self._opposite_anchor(handle, frame.width, frame.height)
        fixed_world = frame.center + _rotate_vector(old_anchor_local, frame.rotation)
        new_anchor_local = self._opposite_anchor(handle, new_width, new_height)
        new_center = fixed_world - _rotate_vector(new_anchor_local, frame.rotation)
        element.pos = Vec2(new_center.x - new_width / 2.0, new_center.y - new_height / 2.0)

    def _apply_drawing_resize(
        self,
        element: DrawingElement,
        session: TransformSession,
        world: Vec2,
    ) -> None:
        if session.handle is None or not session.snapshot.points:
            return
        frame = session.frame
        left, top, right, bottom = self._resize_local_edges(frame, session.handle, world)
        new_width = right - left
        new_height = bottom - top
        new_center = Vec2(
            frame.center.x + (left + right) / 2.0,
            frame.center.y + (top + bottom) / 2.0,
        )
        old_left = frame.center.x - frame.width / 2.0
        old_top = frame.center.y - frame.height / 2.0
        new_left = new_center.x - new_width / 2.0
        new_top = new_center.y - new_height / 2.0

        points: list[Vec2] = []
        for point in session.snapshot.points:
            if frame.width > 1e-6:
                x = new_left + (point.x - old_left) * (new_width / frame.width)
            else:
                x = new_center.x
            if frame.height > 1e-6:
                y = new_top + (point.y - old_top) * (new_height / frame.height)
            else:
                y = new_center.y
            points.append(Vec2(x, y))
        element.points = points

    def _apply_resize(
        self,
        element: MapElement,
        session: TransformSession,
        world: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> None:
        # The dragged handle is attracted to nearby grid intersections only. For
        # rotated image/text frames this world point is then projected into the
        # element's local axes by the existing resize math.
        world = self._snap_world_point(world, modifiers)

        if isinstance(element, ImageElement):
            self._apply_image_resize(element, session, world)
        elif isinstance(element, TextElement):
            self._apply_text_resize(element, session, world)
        elif isinstance(element, DrawingElement):
            self._apply_drawing_resize(element, session, world)

    def _apply_rotate(
        self,
        element: MapElement,
        session: TransformSession,
        world: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> None:
        current = _angle(session.frame.center, world)
        delta = current - session.start_angle

        # Keep the angular delta stable when atan2 crosses -180/180.
        delta = (delta + 180.0) % 360.0 - 180.0

        snapshot = session.snapshot
        snap_rotation = not (modifiers & Qt.KeyboardModifier.AltModifier)
        step = max(0.0, float(self.rotation_snap_deg))

        if isinstance(element, ImageElement) and snapshot.rotation is not None:
            rotation = snapshot.rotation + delta
            if snap_rotation and step > 0.0:
                rotation = self._snap_angle_near(rotation, step)
            element.rotation = rotation % 360.0

        elif isinstance(element, TextElement) and snapshot.rotation is not None:
            rotation = snapshot.rotation + delta
            if snap_rotation and step > 0.0:
                rotation = self._snap_angle_near(rotation, step)
            element.rotation = rotation % 360.0

        elif (
            isinstance(element, DrawingElement)
            and self._drawing_can_rotate(element)
            and snapshot.points is not None
        ):
            # Drawings do not store a rotation value, so snap the gesture delta.
            snapped_delta = delta
            if snap_rotation and step > 0.0:
                snapped_delta = self._snap_angle_near(snapped_delta, step)
            element.points = [
                session.frame.center
                + _rotate_vector(point - session.frame.center, snapped_delta)
                for point in snapshot.points
            ]

    def _session_elements(
        self,
        session: GroupTransformSession,
    ) -> list[MapElement]:
        ids = set(session.element_ids)
        return [
            element
            for element in self.asset.elements
            if element.id in ids
        ]

    def _snap_group_move_delta(
        self,
        session: GroupTransformSession,
        delta: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> Vec2:
        if not self._grid_snap_active(modifiers):
            return delta

        grid = self.asset.grid
        step = float(grid.cell_size)
        tolerance = self._grid_snap_tolerance_world()

        start_left = session.frame.center.x - session.frame.width / 2.0
        start_top = session.frame.center.y - session.frame.height / 2.0

        candidate_left = start_left + delta.x
        candidate_top = start_top + delta.y

        snapped_left = self._snap_value_near(
            candidate_left,
            grid.offset_x,
            step,
            tolerance,
        )
        snapped_top = self._snap_value_near(
            candidate_top,
            grid.offset_y,
            step,
            tolerance,
        )

        return Vec2(
            snapped_left - start_left,
            snapped_top - start_top,
        )

    def _apply_group_move(
        self,
        session: GroupTransformSession,
        world: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> None:
        delta = world - session.start_world
        delta = self._snap_group_move_delta(
            session,
            delta,
            modifiers,
        )

        elements = {
            element.id: element
            for element in self._session_elements(session)
        }

        for element_id, snapshot in session.snapshots.items():
            element = elements.get(element_id)
            if element is None:
                continue

            if isinstance(element, (ImageElement, TextElement)) and snapshot.pos is not None:
                element.pos = snapshot.pos + delta

            elif isinstance(element, DrawingElement) and snapshot.points is not None:
                element.points = [
                    point + delta
                    for point in snapshot.points
                ]

    @staticmethod
    def _map_group_point(
        point: Vec2,
        *,
        old_left: float,
        old_top: float,
        new_left: float,
        new_top: float,
        scale_x: float,
        scale_y: float,
    ) -> Vec2:
        return Vec2(
            new_left + (point.x - old_left) * scale_x,
            new_top + (point.y - old_top) * scale_y,
        )

    def _apply_group_resize(
        self,
        session: GroupTransformSession,
        world: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> None:
        if session.handle is None:
            return

        world = self._snap_world_point(
            world,
            modifiers,
        )

        frame = session.frame
        left, top, right, bottom = self._resize_local_edges(
            frame,
            session.handle,
            world,
        )

        new_width = max(MIN_ELEMENT_SIZE_PX, right - left)
        new_height = max(MIN_ELEMENT_SIZE_PX, bottom - top)

        old_left = frame.center.x - frame.width / 2.0
        old_top = frame.center.y - frame.height / 2.0
        new_left = frame.center.x + left
        new_top = frame.center.y + top

        scale_x = (
            new_width / frame.width
            if frame.width > 1e-6
            else 1.0
        )
        scale_y = (
            new_height / frame.height
            if frame.height > 1e-6
            else 1.0
        )

        elements = {
            element.id: element
            for element in self._session_elements(session)
        }

        horizontal_handle = session.handle in (
            ResizeHandle.LEFT,
            ResizeHandle.RIGHT,
        )
        vertical_handle = session.handle in (
            ResizeHandle.TOP,
            ResizeHandle.BOTTOM,
        )

        for element_id, snapshot in session.snapshots.items():
            element = elements.get(element_id)
            original_frame = session.element_frames.get(element_id)

            if element is None or original_frame is None:
                continue

            original_center = original_frame.center
            new_center = self._map_group_point(
                original_center,
                old_left=old_left,
                old_top=old_top,
                new_left=new_left,
                new_top=new_top,
                scale_x=scale_x,
                scale_y=scale_y,
            )

            if isinstance(element, ImageElement):
                if snapshot.width is None or snapshot.height is None:
                    continue

                element.width = max(
                    MIN_ELEMENT_SIZE_PX,
                    snapshot.width * scale_x,
                )
                element.height = max(
                    MIN_ELEMENT_SIZE_PX,
                    snapshot.height * scale_y,
                )
                element.pos = Vec2(
                    new_center.x - element.width / 2.0,
                    new_center.y - element.height / 2.0,
                )

            elif isinstance(element, TextElement):
                if snapshot.font_size is None:
                    continue

                if horizontal_handle:
                    text_scale = scale_x
                elif vertical_handle:
                    text_scale = scale_y
                else:
                    text_scale = sqrt(
                        max(0.0, scale_x * scale_y)
                    )

                element.font_size = max(
                    1.0,
                    snapshot.font_size * text_scale,
                )

                width, height = self._text_size(element)
                element.pos = Vec2(
                    new_center.x - width / 2.0,
                    new_center.y - height / 2.0,
                )

            elif isinstance(element, DrawingElement) and snapshot.points is not None:
                element.points = [
                    self._map_group_point(
                        point,
                        old_left=old_left,
                        old_top=old_top,
                        new_left=new_left,
                        new_top=new_top,
                        scale_x=scale_x,
                        scale_y=scale_y,
                    )
                    for point in snapshot.points
                ]

    def _apply_group_rotate(
        self,
        session: GroupTransformSession,
        world: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> None:
        current = _angle(
            session.frame.center,
            world,
        )
        delta = current - session.start_angle
        delta = (delta + 180.0) % 360.0 - 180.0

        if not (modifiers & Qt.KeyboardModifier.AltModifier):
            step = max(
                0.0,
                float(self.rotation_snap_deg),
            )
            if step > 0.0:
                delta = self._snap_angle_near(
                    delta,
                    step,
                )

        elements = {
            element.id: element
            for element in self._session_elements(session)
        }

        group_center = session.frame.center

        for element_id, snapshot in session.snapshots.items():
            element = elements.get(element_id)
            original_frame = session.element_frames.get(element_id)

            if element is None or original_frame is None:
                continue

            new_center = (
                group_center
                + _rotate_vector(
                    original_frame.center - group_center,
                    delta,
                )
            )

            if isinstance(element, ImageElement):
                if (
                    snapshot.width is None
                    or snapshot.height is None
                    or snapshot.rotation is None
                ):
                    continue

                element.pos = Vec2(
                    new_center.x - snapshot.width / 2.0,
                    new_center.y - snapshot.height / 2.0,
                )
                element.rotation = (
                    snapshot.rotation + delta
                ) % 360.0

            elif isinstance(element, TextElement):
                if snapshot.rotation is None:
                    continue

                element.rotation = (
                    snapshot.rotation + delta
                ) % 360.0

                width, height = self._text_size(
                    element,
                    snapshot.font_size,
                )
                element.pos = Vec2(
                    new_center.x - width / 2.0,
                    new_center.y - height / 2.0,
                )

            elif (
                isinstance(element, DrawingElement)
                and snapshot.points is not None
            ):
                element.points = [
                    group_center
                    + _rotate_vector(
                        point - group_center,
                        delta,
                    )
                    for point in snapshot.points
                ]

    def _update_transform(self, screen_pos: QPointF, modifiers: Qt.KeyboardModifier) -> None:
        session = self._transform
        if session is None:
            return

        world = self.screen_to_world(
            screen_pos
        )

        if isinstance(session, GroupTransformSession):
            current_ids = {
                element.id
                for element in self.selected_elements()
            }
            if current_ids != set(session.element_ids):
                self._transform = None
                return

            if session.mode is TransformMode.MOVE:
                self._apply_group_move(
                    session,
                    world,
                    modifiers,
                )
            elif session.mode is TransformMode.RESIZE:
                self._apply_group_resize(
                    session,
                    world,
                    modifiers,
                )
            elif session.mode is TransformMode.ROTATE:
                self._apply_group_rotate(
                    session,
                    world,
                    modifiers,
                )

            self.update()
            return

        element = self.selected_element()
        if element is None or element.id != session.element_id:
            self._transform = None
            return

        if session.mode is TransformMode.MOVE:
            self._apply_move(
                element,
                session,
                world,
                modifiers,
            )
        elif session.mode is TransformMode.RESIZE:
            self._apply_resize(
                element,
                session,
                world,
                modifiers,
            )
        elif session.mode is TransformMode.ROTATE:
            self._apply_rotate(
                element,
                session,
                world,
                modifiers,
            )

        self.update()
