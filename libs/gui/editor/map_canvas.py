# -*- coding: utf-8 -*-
"""Interactive map viewport: rendering, selection, transforms, tokens and camera."""

from __future__ import annotations

import json
from copy import deepcopy
from math import cos, hypot, radians, sin, sqrt
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import QMessageBox, QWidget

from ...assets.map import (
    CameraState,
    DrawTool,
    DrawingElement,
    ImageElement,
    InitialTokenPlacement,
    MapAsset,
    MapElement,
    MediaCache,
    Rect,
    TextElement,
    WallElement,
    Vec2,
)

from .map_tokens import (
    TOKEN_SOURCE_MIME,
    NullTokenEntityProvider,
    TokenEntityProvider,
    TokenViewData,
)
from .map_types import (
    DRAW_TOOLS,
    GRID_SNAP_TOLERANCE_PX,
    HANDLE_SIZE_PX,
    MARQUEE_CLICK_TOLERANCE_PX,
    MAX_UNDO_STEPS,
    MIN_ELEMENT_SIZE_PX,
    PASTE_OFFSET_PX,
    ROTATE_HANDLE_OFFSET_PX,
    ROTATION_SNAP_DEG,
    ROTATION_SNAP_TOLERANCE_DEG,
    CameraEditSession,
    EditorCamera,
    EditorState,
    EditorTool,
    ElementFrame,
    ElementSnapshot,
    GroupTransformSession,
    ResizeHandle,
    TokenMoveSession,
    TransformMode,
    TransformSession,
    _angle,
    _midpoint,
    _rotate_vector,
)
from .map_widgets import ImageFormattingBar, TextFormattingBar

class MapCanvas(QWidget):
    """
    Interactive viewport for a MapAsset.

    The static scene itself is always rendered by MapAsset.render_scene(), so the
    editor and flattened tiles share the exact same QPainter rendering path.

    Select is inherently multi-selection capable: click for one element, Shift/Ctrl
    click to modify the selection, or drag from empty canvas for a marquee selection.
    """

    selection_changed = Signal(object)
    mouse_world_moved = Signal(float, float)
    zoom_changed = Signal(float)
    tool_changed = Signal(object)
    image_replace_requested = Signal()
    token_double_clicked = Signal(str)
    token_placed = Signal(str)
    history_changed = Signal(bool, bool)
    clipboard_changed = Signal(bool)

    def __init__(
        self,
        asset: Optional[MapAsset] = None,
        map_dir: Optional[Path] = None,
        token_provider: Optional[TokenEntityProvider] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        self.asset = asset or MapAsset()
        self.map_dir = Path(map_dir) if map_dir is not None else Path.cwd()
        self.media = MediaCache(self.map_dir)
        self.token_provider: TokenEntityProvider = token_provider or NullTokenEntityProvider()
        self._token_image_cache: dict[Path, QImage] = {}
        self.pending_token_entity_id: Optional[str] = None

        self._drag_token_source_kind: Optional[str] = None
        self._drag_token_source_id: Optional[str] = None
        self._drag_token_preview: Optional[TokenViewData] = None

        self.camera = EditorCamera()
        self.selected_element_ids: set[str] = set()
        self.selected_element_id: Optional[str] = None
        self.selected_token_ids: set[str] = set()
        self.selected_token_id: Optional[str] = None
        self._token_move: Optional[TokenMoveSession] = None

        self.camera_selected = False
        self._camera_edit: Optional[CameraEditSession] = None

        # Editor tool state. These values are editor-only; they are copied into
        # newly created MapElements and never serialized by the canvas itself.
        self.active_tool = EditorTool.SELECT
        self.stroke_color = "#f9e2af"
        self.fill_color: Optional[str] = None
        self.stroke_width = 2.0
        self.element_opacity = 1.0
        self.text_font_size = 24.0
        self.pending_image_asset: Optional[Path] = None
        self.pending_image_size: tuple[float, float] = (128.0, 128.0)

        self._creation_start: Optional[Vec2] = None
        self._creation_current: Optional[Vec2] = None
        self._creation_points: list[Vec2] = []
        self._hover_world: Optional[Vec2] = None

        self._panning = False
        self._last_mouse_pos = QPointF()
        self._transform: Optional[TransformSession | GroupTransformSession] = None
        self._marquee_start_screen: Optional[QPointF] = None
        self._marquee_current_screen: Optional[QPointF] = None
        self._marquee_modifiers = Qt.KeyboardModifier.NoModifier
        self._render_error: Optional[str] = None

        # Editor history / internal element clipboard. The clipboard stores only
        # serialized element data; image pixels remain in the map media folder.
        self._undo_stack: list[EditorState] = []
        self._redo_stack: list[EditorState] = []
        self._element_clipboard: Optional[list[dict]] = None
        self._paste_count = 0

        # Transform snapping. Grid snapping follows GridSettings, including its
        # offset. Holding Alt during a transform temporarily disables snapping.
        self.snap_to_grid = True
        self.rotation_snap_deg = ROTATION_SNAP_DEG
        # Magnetic snapping only engages near a target. Grid tolerance is in
        # screen pixels so it feels the same at every zoom level.
        self.grid_snap_tolerance_px = GRID_SNAP_TOLERANCE_PX
        self.rotation_snap_tolerance_deg = ROTATION_SNAP_TOLERANCE_DEG

        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(640, 480)

        self.image_format_bar = ImageFormattingBar(self)
        self.image_format_bar.replace_requested.connect(self.image_replace_requested.emit)

        self.text_format_bar = TextFormattingBar(self)
        self.text_format_bar.text_changed.connect(self._set_selected_text)
        self.text_format_bar.font_family_changed.connect(self._set_selected_text_font)
        self.text_format_bar.font_size_changed.connect(self._set_selected_text_size)
        self.text_format_bar.color_changed.connect(self._set_selected_text_color)
        self.text_format_bar.bold_changed.connect(self._set_selected_text_bold)
        self.text_format_bar.italic_changed.connect(self._set_selected_text_italic)
        self.text_format_bar.underline_changed.connect(self._set_selected_text_underline)

        self.show_initial_camera()

    # -------------------------------------------------------------------------
    # Asset / media
    # -------------------------------------------------------------------------

    def set_asset(self, asset: MapAsset, map_dir: Path) -> None:
        self.asset = asset
        self.map_dir = Path(map_dir)
        self.media = MediaCache(self.map_dir)
        self.selected_element_ids.clear()
        self.selected_element_id = None
        self.selected_token_ids.clear()
        self.selected_token_id = None
        self.camera_selected = False
        self._transform = None
        self._token_move = None
        self._camera_edit = None
        self._cancel_marquee()
        self._cancel_creation()
        self.pending_image_asset = None
        self.pending_token_entity_id = None
        self._clear_token_drag_preview()
        self._token_image_cache.clear()
        self._render_error = None
        self.clear_history(clear_clipboard=True)
        self.set_tool(EditorTool.SELECT)
        self.show_initial_camera()
        self.selection_changed.emit(None)
        self._sync_text_format_bar()
        self._sync_image_format_bar()
        self.update()

    def reload_media(self) -> None:
        self.media = MediaCache(self.map_dir)
        self._token_image_cache.clear()
        self._render_error = None
        self.update()

    def set_token_provider(self, provider: Optional[TokenEntityProvider]) -> None:
        self.token_provider = provider or NullTokenEntityProvider()
        self._token_image_cache.clear()
        self.update()

    def begin_token_placement(self, entity_id: str) -> None:
        entity_id = str(entity_id).strip()
        if not entity_id:
            return
        self.set_tool(EditorTool.SELECT)
        self.pending_token_entity_id = entity_id
        self._select_elements([])
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    def cancel_token_placement(self) -> None:
        if self.pending_token_entity_id is None:
            return
        self.pending_token_entity_id = None
        self.unsetCursor()
        self.update()

    def replace_selected_image_asset(self, asset: Path) -> None:
        element = self.selected_element()
        if not isinstance(element, ImageElement) or element.locked:
            return
        asset = Path(asset)
        if element.asset == asset:
            return
        before = self._capture_state()
        element.asset = asset
        self._commit_history(before)
        self._sync_image_format_bar()
        self.update()

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
    # Undo / redo / clipboard
    # -------------------------------------------------------------------------

    def _capture_state(self) -> EditorState:
        return EditorState(
            elements=[deepcopy(element.to_dict()) for element in self.asset.elements],
            tokens=[deepcopy(token.to_dict()) for token in self.asset.initial_tokens],
            initial_camera=deepcopy(self.asset.initial_gm_camera.to_dict()),
            selected_element_ids=sorted(self.selected_element_ids),
            selected_element_id=self.selected_element_id,
            selected_token_ids=sorted(self.selected_token_ids),
            selected_token_id=self.selected_token_id,
            camera_selected=self.camera_selected,
        )

    def _restore_state(self, state: EditorState) -> None:
        self._transform = None
        self._token_move = None
        self._camera_edit = None
        self._cancel_creation()
        self.asset.elements = [
            MapElement.from_dict(deepcopy(data))
            for data in state.elements
        ]
        self.asset.initial_tokens = [
            InitialTokenPlacement.from_dict(deepcopy(data))
            for data in state.tokens
        ]
        self.asset.initial_gm_camera = CameraState.from_dict(
            deepcopy(state.initial_camera)
        )

        ids = {element.id for element in self.asset.elements}
        self.selected_element_ids = {
            element_id for element_id in state.selected_element_ids
            if element_id in ids
        }
        self.selected_element_id = (
            state.selected_element_id
            if state.selected_element_id in self.selected_element_ids
            else (next(iter(self.selected_element_ids), None))
        )

        token_ids = {
            token.id
            for token in self.asset.initial_tokens
        }
        self.selected_token_ids = {
            token_id
            for token_id in state.selected_token_ids
            if token_id in token_ids
        }
        self.selected_token_id = (
            state.selected_token_id
            if state.selected_token_id in self.selected_token_ids
            else (next(iter(self.selected_token_ids), None))
        )

        self.camera_selected = bool(state.camera_selected)

        if self.camera_selected:
            self.selected_element_ids.clear()
            self.selected_element_id = None
            self.selected_token_ids.clear()
            self.selected_token_id = None
        elif self.selected_token_ids:
            self.selected_element_ids.clear()
            self.selected_element_id = None

        self.selection_changed.emit(self.selected_element())
        self._sync_text_format_bar()
        self._sync_image_format_bar()
        self.update()

    def _commit_history(self, before: EditorState) -> None:
        after = self._capture_state()
        if before == after:
            return

        self._undo_stack.append(before)
        if len(self._undo_stack) > MAX_UNDO_STEPS:
            del self._undo_stack[0]
        self._redo_stack.clear()
        self.history_changed.emit(bool(self._undo_stack), bool(self._redo_stack))

    def clear_history(self, *, clear_clipboard: bool = False) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.history_changed.emit(False, False)

        if clear_clipboard:
            self._element_clipboard = None
            self._paste_count = 0
            self.clipboard_changed.emit(False)

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def undo(self) -> None:
        if not self._undo_stack:
            return

        current = self._capture_state()
        state = self._undo_stack.pop()
        self._redo_stack.append(current)
        self._restore_state(state)
        self.history_changed.emit(bool(self._undo_stack), bool(self._redo_stack))

    def redo(self) -> None:
        if not self._redo_stack:
            return

        current = self._capture_state()
        state = self._redo_stack.pop()
        self._undo_stack.append(current)
        self._restore_state(state)
        self.history_changed.emit(bool(self._undo_stack), bool(self._redo_stack))

    # Standard editor API used by CampaignManagerWindow._edit_focused().
    def cut(self) -> None:
        self.cut_selected()

    def copy(self) -> None:
        self.copy_selected()

    def paste(self) -> None:
        self.paste_element()

    def selectAll(self) -> None:  # noqa: N802 - Qt API compatibility
        if self.selected_token_ids:
            tokens = list(self.asset.initial_tokens)
            self._select_tokens(
                tokens,
                primary=tokens[-1] if tokens else None,
            )
            return

        elements = list(self.asset.elements)
        self._select_elements(
            elements,
            primary=elements[-1] if elements else None,
        )

    def selected_elements(self) -> list[MapElement]:
        """Return selected elements in scene z-order."""
        if not self.selected_element_ids:
            return []
        return [
            element for element in self.asset.elements
            if element.id in self.selected_element_ids
        ]

    def _copy_elements(self, elements: list[MapElement]) -> None:
        if not elements:
            return
        self._element_clipboard = [deepcopy(element.to_dict()) for element in elements]
        self._paste_count = 0
        self.clipboard_changed.emit(True)

    def copy_selected(self) -> None:
        self._copy_elements(self.selected_elements())

    @staticmethod
    def _translate_element(element: MapElement, delta: Vec2) -> None:
        if isinstance(element, (ImageElement, TextElement)):
            element.pos = Vec2(
                element.pos.x + delta.x,
                element.pos.y + delta.y,
            )
        elif isinstance(element, DrawingElement):
            element.points = [
                Vec2(point.x + delta.x, point.y + delta.y)
                for point in element.points
            ]

    def paste_element(self) -> None:
        if not self._element_clipboard:
            return

        before = self._capture_state()
        self._paste_count += 1
        offset = PASTE_OFFSET_PX * self._paste_count
        pasted: list[MapElement] = []

        for source_data in self._element_clipboard:
            data = deepcopy(source_data)
            data.pop("id", None)
            element = MapElement.from_dict(data)
            self._translate_element(element, Vec2(offset, offset))
            self.asset.elements.append(element)
            pasted.append(element)

        self._select_elements(pasted, primary=pasted[-1] if pasted else None)
        self._commit_history(before)
        self.update()

    def delete_selected(self) -> None:
        selected_tokens = self.selected_tokens()

        if selected_tokens:
            before = self._capture_state()
            removed_ids = {
                token.id
                for token in selected_tokens
            }

            self.asset.initial_tokens = [
                token
                for token in self.asset.initial_tokens
                if token.id not in removed_ids
            ]

            self.selected_token_ids.clear()
            self.selected_token_id = None
            self.selection_changed.emit(None)

            self._commit_history(
                before
            )
            self.update()
            return

        selected = self.selected_elements()
        removable = [element for element in selected if not element.locked]
        if not removable:
            return

        before = self._capture_state()
        removed_ids = {element.id for element in removable}
        self.asset.elements = [
            item for item in self.asset.elements
            if item.id not in removed_ids
        ]
        remaining = [
            element for element in selected
            if element.id not in removed_ids
        ]
        self._select_elements(remaining, primary=remaining[-1] if remaining else None)
        self._commit_history(before)
        self.update()

    def cut_selected(self) -> None:
        removable = [element for element in self.selected_elements() if not element.locked]
        if not removable:
            return
        self._copy_elements(removable)

        before = self._capture_state()
        removed_ids = {element.id for element in removable}
        self.asset.elements = [
            item for item in self.asset.elements
            if item.id not in removed_ids
        ]
        remaining = [
            element for element in self.selected_elements()
            if element.id not in removed_ids
        ]
        self._select_elements(remaining, primary=remaining[-1] if remaining else None)
        self._commit_history(before)
        self.update()

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

    # -------------------------------------------------------------------------
    # Selection / transform geometry
    # -------------------------------------------------------------------------

    def selected_element(self) -> Optional[MapElement]:
        """Return the primary selected element, if any."""
        if self.selected_element_id is None:
            return None
        return next(
            (element for element in self.asset.elements if element.id == self.selected_element_id),
            None,
        )

    @staticmethod
    def _contains(rect: Rect, point: Vec2) -> bool:
        return (
            rect.x <= point.x <= rect.right
            and rect.y <= point.y <= rect.bottom
        )

    @staticmethod
    def _text_size(element: TextElement, font_size: Optional[float] = None) -> tuple[float, float]:
        size = element.font_size if font_size is None else font_size
        font = QFont(element.font_family) if element.font_family else QFont()
        font.setPixelSize(max(1, round(size)))
        font.setBold(bool(getattr(element, "bold", False)))
        font.setItalic(bool(getattr(element, "italic", False)))
        font.setUnderline(bool(getattr(element, "underline", False)))
        metrics = QFontMetricsF(font)
        lines = element.text.split("\n")
        width = max((metrics.horizontalAdvance(line) for line in lines), default=0.0)
        height = metrics.lineSpacing() * max(1, len(lines))
        return max(1.0, width), max(1.0, height)

    def _element_frame(self, element: MapElement) -> ElementFrame:
        if isinstance(element, ImageElement):
            return ElementFrame(
                center=Vec2(
                    element.pos.x + element.width / 2.0,
                    element.pos.y + element.height / 2.0,
                ),
                width=max(0.0, element.width),
                height=max(0.0, element.height),
                rotation=element.rotation,
            )

        if isinstance(element, TextElement):
            width, height = self._text_size(element)
            return ElementFrame(
                center=Vec2(element.pos.x + width / 2.0, element.pos.y + height / 2.0),
                width=width,
                height=height,
                rotation=element.rotation,
            )

        if isinstance(element, DrawingElement):
            rect = Rect.from_points(element.points)
            return ElementFrame(
                center=Vec2(rect.x + rect.width / 2.0, rect.y + rect.height / 2.0),
                width=max(0.0, rect.width),
                height=max(0.0, rect.height),
                rotation=0.0,
            )

        bounds = element.bounds()
        return ElementFrame(
            center=Vec2(bounds.x + bounds.width / 2.0, bounds.y + bounds.height / 2.0),
            width=max(0.0, bounds.width),
            height=max(0.0, bounds.height),
            rotation=0.0,
        )

    def _frame_screen_points(self, frame: ElementFrame) -> list[QPointF]:
        return [self.world_to_screen(point) for point in frame.corners()]

    def _frame_contains_screen(self, frame: ElementFrame, point: QPointF) -> bool:
        polygon = QPolygonF(self._frame_screen_points(frame))
        return polygon.containsPoint(point, Qt.FillRule.WindingFill)

    def _element_contains(self, element: MapElement, point: Vec2) -> bool:
        if isinstance(element, (ImageElement, TextElement)):
            frame = self._element_frame(element)
            polygon = QPolygonF([QPointF(p.x, p.y) for p in frame.corners()])
            return polygon.containsPoint(QPointF(point.x, point.y), Qt.FillRule.WindingFill)
        return self._contains(element.bounds(), point)

    def element_at(self, point: Vec2) -> Optional[MapElement]:
        # elements are ordered bottom -> top, so hit-test in reverse.
        for element in reversed(self.asset.elements):
            if not element.visible:
                continue
            if self._element_contains(element, point):
                return element
        return None

    def _select_elements(
        self,
        elements: list[MapElement],
        *,
        primary: Optional[MapElement] = None,
    ) -> None:
        ids = {element.id for element in elements}
        primary_id = primary.id if primary is not None and primary.id in ids else None
        if primary_id is None and elements:
            primary_id = elements[-1].id

        # Selecting static scene elements leaves token/camera mode.
        self.selected_token_ids.clear()
        self.selected_token_id = None
        self.camera_selected = False

        if ids == self.selected_element_ids and primary_id == self.selected_element_id:
            self._sync_text_format_bar()
            self._sync_image_format_bar()
            return

        self.selected_element_ids = ids
        self.selected_element_id = primary_id
        self.selection_changed.emit(self.selected_element())
        self._sync_text_format_bar()
        self._sync_image_format_bar()
        self.update()

    def _select_element(self, element: Optional[MapElement]) -> None:
        self._select_elements([element] if element is not None else [], primary=element)

    def _toggle_element_selection(self, element: MapElement) -> None:
        selected = self.selected_elements()
        if element.id in self.selected_element_ids:
            selected = [item for item in selected if item.id != element.id]
            self._select_elements(selected, primary=selected[-1] if selected else None)
        else:
            selected.append(element)
            self._select_elements(selected, primary=element)

    def select_at(self, point: Vec2) -> None:
        self._select_element(self.element_at(point))

    def _selected_image_element(self) -> Optional[ImageElement]:
        if len(self.selected_element_ids) != 1:
            return None
        element = self.selected_element()
        return element if isinstance(element, ImageElement) else None

    def _sync_image_format_bar(self) -> None:
        element = self._selected_image_element()
        if element is None or self.active_tool is not EditorTool.SELECT:
            self.image_format_bar.hide()
            return
        self.image_format_bar.set_element(element)
        self.image_format_bar.show()
        self.image_format_bar.raise_()
        self._position_image_format_bar()

    def _position_image_format_bar(self) -> None:
        element = self._selected_image_element()
        if element is None or not self.image_format_bar.isVisible():
            return

        points = self._frame_screen_points(self._element_frame(element))
        min_x = min(point.x() for point in points)
        max_x = max(point.x() for point in points)
        min_y = min(point.y() for point in points)
        max_y = max(point.y() for point in points)

        self.image_format_bar.adjustSize()
        width = self.image_format_bar.width()
        height = self.image_format_bar.height()

        x = round((min_x + max_x - width) / 2.0)
        x = max(8, min(x, max(8, self.width() - width - 8)))

        y = round(min_y - height - ROTATE_HANDLE_OFFSET_PX - 10.0)
        if y < 8:
            y = round(max_y + 12.0)
        y = max(8, min(y, max(8, self.height() - height - 8)))

        self.image_format_bar.move(x, y)

    def _selected_text_element(self) -> Optional[TextElement]:
        if len(self.selected_element_ids) != 1:
            return None
        element = self.selected_element()
        return element if isinstance(element, TextElement) else None

    def _sync_text_format_bar(
        self,
        *,
        focus: bool = False,
        select_all: bool = False,
    ) -> None:
        element = self._selected_text_element()
        if element is None or self.active_tool is not EditorTool.SELECT:
            self.text_format_bar.hide()
            return
        self.text_format_bar.set_element(element)
        self.text_format_bar.show()
        self.text_format_bar.raise_()
        self._position_text_format_bar()
        if focus and not element.locked:
            self.text_format_bar.focus_text(select_all=select_all)

    def _position_text_format_bar(self) -> None:
        element = self._selected_text_element()
        if element is None or not self.text_format_bar.isVisible():
            return

        points = self._frame_screen_points(self._element_frame(element))
        min_x = min(point.x() for point in points)
        max_x = max(point.x() for point in points)
        min_y = min(point.y() for point in points)
        max_y = max(point.y() for point in points)

        self.text_format_bar.adjustSize()
        width = self.text_format_bar.width()
        height = self.text_format_bar.height()

        x = round((min_x + max_x - width) / 2.0)
        x = max(8, min(x, max(8, self.width() - width - 8)))

        # Leave room for the rotation handle above the frame.
        y = round(min_y - height - ROTATE_HANDLE_OFFSET_PX - 10.0)
        if y < 8:
            y = round(max_y + 12.0)
        y = max(8, min(y, max(8, self.height() - height - 8)))

        if self.text_format_bar.pos() != QPointF(float(x), float(y)).toPoint():
            self.text_format_bar.move(x, y)

    def _mutate_selected_text(self, change) -> None:
        element = self._selected_text_element()
        if element is None or element.locked:
            return

        before = self._capture_state()
        old_element = deepcopy(element.to_dict())
        change(element)

        if element.to_dict() != old_element:
            self._commit_history(before)

        self.update()
        self._position_text_format_bar()

    def _set_selected_text(self, text: str) -> None:
        self._mutate_selected_text(lambda element: setattr(element, "text", text))

    def _set_selected_text_font(self, family: str) -> None:
        self._mutate_selected_text(
            lambda element: setattr(element, "font_family", family or None)
        )

    def _set_selected_text_size(self, size: float) -> None:
        self._mutate_selected_text(
            lambda element: setattr(element, "font_size", max(1.0, float(size)))
        )

    def _set_selected_text_color(self, color: str) -> None:
        self._mutate_selected_text(lambda element: setattr(element, "font_color", color))

    def _set_selected_text_bold(self, enabled: bool) -> None:
        self._mutate_selected_text(lambda element: setattr(element, "bold", bool(enabled)))

    def _set_selected_text_italic(self, enabled: bool) -> None:
        self._mutate_selected_text(lambda element: setattr(element, "italic", bool(enabled)))

    def _set_selected_text_underline(self, enabled: bool) -> None:
        self._mutate_selected_text(lambda element: setattr(element, "underline", bool(enabled)))

    def _selection_bounds(self) -> Optional[Rect]:
        selected = self.selected_elements()
        if not selected:
            return None
        bounds = [element.bounds() for element in selected]
        left = min(rect.x for rect in bounds)
        top = min(rect.y for rect in bounds)
        right = max(rect.right for rect in bounds)
        bottom = max(rect.bottom for rect in bounds)
        return Rect(left, top, right - left, bottom - top)

    def _selection_frame(
        self,
        elements: Optional[list[MapElement]] = None,
    ) -> Optional[ElementFrame]:
        """Axis-aligned transform frame containing the whole selection."""
        items = self.selected_elements() if elements is None else elements
        if not items:
            return None

        bounds = [element.bounds() for element in items]
        left = min(rect.x for rect in bounds)
        top = min(rect.y for rect in bounds)
        right = max(rect.right for rect in bounds)
        bottom = max(rect.bottom for rect in bounds)

        return ElementFrame(
            center=Vec2((left + right) / 2.0, (top + bottom) / 2.0),
            width=max(0.0, right - left),
            height=max(0.0, bottom - top),
            rotation=0.0,
        )

    @staticmethod
    def _group_is_locked(elements: list[MapElement]) -> bool:
        """A group transform is disabled when any selected member is locked."""
        return any(element.locked for element in elements)

    def _group_can_rotate(self, elements: list[MapElement]) -> bool:
        """
        Whether every member can represent a rotation with the current model.

        RECT/CIRCLE DrawingElement instances are axis-aligned in map.py, so a
        group containing one cannot be rotated without changing its semantics.
        """
        return bool(elements) and all(self._can_rotate(element) for element in elements)

    def _cancel_marquee(self) -> None:
        self._marquee_start_screen = None
        self._marquee_current_screen = None
        self._marquee_modifiers = Qt.KeyboardModifier.NoModifier
        self.update()

    def _start_marquee(
        self,
        screen_pos: QPointF,
        modifiers: Qt.KeyboardModifier,
    ) -> None:
        self._marquee_start_screen = QPointF(screen_pos)
        self._marquee_current_screen = QPointF(screen_pos)
        self._marquee_modifiers = modifiers
        self.update()

    def _update_marquee(self, screen_pos: QPointF) -> None:
        if self._marquee_start_screen is None:
            return
        self._marquee_current_screen = QPointF(screen_pos)
        self.update()

    def _marquee_screen_rect(self) -> Optional[QRectF]:
        if self._marquee_start_screen is None or self._marquee_current_screen is None:
            return None
        return QRectF(self._marquee_start_screen, self._marquee_current_screen).normalized()

    def _marquee_world_rect(self) -> Optional[Rect]:
        screen_rect = self._marquee_screen_rect()
        if screen_rect is None:
            return None
        top_left = self.screen_to_world(screen_rect.topLeft())
        bottom_right = self.screen_to_world(screen_rect.bottomRight())
        return Rect(
            min(top_left.x, bottom_right.x),
            min(top_left.y, bottom_right.y),
            abs(bottom_right.x - top_left.x),
            abs(bottom_right.y - top_left.y),
        )

    def _elements_in_marquee(self, rect: Rect) -> list[MapElement]:
        return [
            element for element in self.asset.elements
            if element.visible and element.bounds().intersects(rect)
        ]

    def _apply_marquee_selection(self, end_screen: QPointF) -> None:
        start = self._marquee_start_screen
        if start is None:
            return

        dx = end_screen.x() - start.x()
        dy = end_screen.y() - start.y()
        distance = hypot(dx, dy)
        modifiers = self._marquee_modifiers

        if distance <= MARQUEE_CLICK_TOLERANCE_PX:
            element = self.element_at(self.screen_to_world(end_screen))
            if modifiers & Qt.KeyboardModifier.ControlModifier:
                if element is not None:
                    self._toggle_element_selection(element)
            elif modifiers & Qt.KeyboardModifier.ShiftModifier:
                if element is not None and element.id not in self.selected_element_ids:
                    selected = self.selected_elements()
                    selected.append(element)
                    self._select_elements(selected, primary=element)
            else:
                self._select_element(element)
            return

        world_rect = self._marquee_world_rect()
        if world_rect is None:
            return

        token_hits = self._tokens_in_marquee(
            world_rect
        )

        # Tokens are the visual overlay. If the marquee touches any token,
        # this gesture selects tokens rather than underlying map elements.
        if token_hits:
            if modifiers & Qt.KeyboardModifier.ControlModifier:
                selected_ids = set(
                    self.selected_token_ids
                )

                for token in token_hits:
                    if token.id in selected_ids:
                        selected_ids.remove(
                            token.id
                        )
                    else:
                        selected_ids.add(
                            token.id
                        )

                selected = [
                    token
                    for token in self.asset.initial_tokens
                    if token.id in selected_ids
                ]
                primary = (
                    token_hits[-1]
                    if token_hits[-1].id in selected_ids
                    else None
                )
                self._select_tokens(
                    selected,
                    primary=primary,
                )

            elif modifiers & Qt.KeyboardModifier.ShiftModifier:
                selected_ids = (
                    set(self.selected_token_ids)
                    | {
                        token.id
                        for token in token_hits
                    }
                )
                selected = [
                    token
                    for token in self.asset.initial_tokens
                    if token.id in selected_ids
                ]
                self._select_tokens(
                    selected,
                    primary=token_hits[-1],
                )

            else:
                self._select_tokens(
                    token_hits,
                    primary=token_hits[-1],
                )

            return

        hits = self._elements_in_marquee(world_rect)

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            selected_ids = set(self.selected_element_ids)
            for element in hits:
                if element.id in selected_ids:
                    selected_ids.remove(element.id)
                else:
                    selected_ids.add(element.id)
            selected = [
                element for element in self.asset.elements
                if element.id in selected_ids
            ]
            self._select_elements(selected, primary=hits[-1] if hits and hits[-1].id in selected_ids else None)
        elif modifiers & Qt.KeyboardModifier.ShiftModifier:
            selected_ids = set(self.selected_element_ids) | {element.id for element in hits}
            selected = [
                element for element in self.asset.elements
                if element.id in selected_ids
            ]
            self._select_elements(selected, primary=hits[-1] if hits else self.selected_element())
        else:
            self._select_elements(hits, primary=hits[-1] if hits else None)

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

    # ----- Transform snapping ------------------------------------------------

    def _grid_snap_active(self, modifiers: Qt.KeyboardModifier) -> bool:
        grid = self.asset.grid
        return (
            self.snap_to_grid
            and grid.enabled
            and grid.cell_size > 0
            and not (modifiers & Qt.KeyboardModifier.AltModifier)
        )

    @staticmethod
    def _snap_value_near(
        value: float,
        offset: float,
        step: float,
        tolerance: float,
    ) -> float:
        """Snap to the nearest grid line only when it is close enough."""
        target = offset + round((value - offset) / step) * step
        return target if abs(value - target) <= tolerance else value

    def _grid_snap_tolerance_world(self) -> float:
        """Convert the screen-space magnetic radius to world pixels."""
        return self.grid_snap_tolerance_px / max(self.camera.zoom, 1e-6)

    def _snap_angle_near(self, angle: float, step: float) -> float:
        """Snap an angle only when it is close to a multiple of ``step``."""
        if step <= 0.0:
            return angle
        target = round(angle / step) * step
        difference = (angle - target + 180.0) % 360.0 - 180.0
        if abs(difference) <= self.rotation_snap_tolerance_deg:
            return target
        return angle

    def _snap_world_point(
        self,
        point: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> Vec2:
        if not self._grid_snap_active(modifiers):
            return Vec2(point.x, point.y)

        grid = self.asset.grid
        step = float(grid.cell_size)
        tolerance = self._grid_snap_tolerance_world()
        return Vec2(
            self._snap_value_near(point.x, grid.offset_x, step, tolerance),
            self._snap_value_near(point.y, grid.offset_y, step, tolerance),
        )

    def _snap_move_delta(
        self,
        element: MapElement,
        session: TransformSession,
        delta: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> Vec2:
        if not self._grid_snap_active(modifiers):
            return delta

        grid = self.asset.grid
        step = float(grid.cell_size)
        tolerance = self._grid_snap_tolerance_world()

        # Images and text have a stable model-space top-left anchor. Each axis
        # snaps independently, and only inside the magnetic tolerance.
        if isinstance(element, (ImageElement, TextElement)) and session.snapshot.pos is not None:
            candidate = session.snapshot.pos + delta
            snapped = Vec2(
                self._snap_value_near(candidate.x, grid.offset_x, step, tolerance),
                self._snap_value_near(candidate.y, grid.offset_y, step, tolerance),
            )
            return snapped - session.snapshot.pos

        # Drawings are point clouds. Snap the top-left of their transform frame so
        # the whole drawing moves rigidly instead of snapping every point.
        if isinstance(element, DrawingElement):
            start_left = session.frame.center.x - session.frame.width / 2.0
            start_top = session.frame.center.y - session.frame.height / 2.0
            candidate_left = start_left + delta.x
            candidate_top = start_top + delta.y
            snapped_left = self._snap_value_near(
                candidate_left, grid.offset_x, step, tolerance
            )
            snapped_top = self._snap_value_near(
                candidate_top, grid.offset_y, step, tolerance
            )
            return Vec2(
                snapped_left - start_left,
                snapped_top - start_top,
            )

        return delta

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

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt API
        payload = self._decode_token_drag(event.mimeData())
        if payload is None:
            super().dragEnterEvent(event)
            return

        kind, source_id = payload
        self._set_token_drag_preview(
            kind,
            source_id,
            event.position(),
        )
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        payload = self._decode_token_drag(event.mimeData())
        if payload is None:
            super().dragMoveEvent(event)
            return

        kind, source_id = payload
        self._set_token_drag_preview(
            kind,
            source_id,
            event.position(),
        )
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._clear_token_drag_preview()
        event.accept()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt API
        payload = self._decode_token_drag(event.mimeData())
        if payload is None:
            super().dropEvent(event)
            return

        source_kind, source_id = payload
        world = self.screen_to_world(event.position())

        # Stop drawing the ghost before opening a potentially modal EntityEditor.
        self._clear_token_drag_preview()

        entity_id: Optional[str] = None

        try:
            if source_kind == "entity":
                entity_id = source_id

            elif source_kind == "character":
                # Character rule:
                #   create Entity -> open EntityEditor -> create token.
                entity_id = self.token_provider.create_entity_from_character(
                    source_id
                )

                if entity_id and not self.token_provider.edit_entity(entity_id):
                    entity_id = None

            if entity_id:
                self.place_token_at(
                    entity_id,
                    world,
                )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Place token",
                str(error),
            )
            entity_id = None

        if entity_id:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    # -------------------------------------------------------------------------
    # QWidget events
    # -------------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor("#11111b"))
            MapAsset._quality_hints(painter)

            painter.save()
            try:
                self._apply_camera_transform(painter)
                view = self.visible_world_rect()

                try:
                    self.asset.render_scene(painter, self.media, view)
                    self._render_error = None
                except Exception as error:  # Keep the editor usable if an asset is missing.
                    self._render_error = str(error)

                self._draw_grid(painter, view)
                for wall in self.asset.walls():
                    if wall.visible:
                        wall.render(painter, self.media)
                self._draw_initial_camera_overlay(painter)
                self._draw_tokens(painter, view)
                self._draw_map_border(painter)
                self._draw_creation_preview(painter)
            finally:
                painter.restore()

            if self.active_tool is EditorTool.SELECT:
                self._draw_selection(painter)
                self._draw_marquee(painter)

            if self._render_error:
                self._draw_render_error(painter, self._render_error)

            self._position_text_format_bar()
            self._position_image_format_bar()
        finally:
            painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._last_mouse_pos = QPointF(event.position())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            screen_pos = event.position()
            world = self.screen_to_world(screen_pos)

            if self.pending_token_entity_id is not None:
                self._place_pending_token(world)
                event.accept()
                return

            if self.active_tool in DRAW_TOOLS or self.active_tool is EditorTool.WALL:
                self._start_drawing_creation(world)
                event.accept()
                return

            if self.active_tool is EditorTool.IMAGE:
                self._place_image(world)
                event.accept()
                return

            if self.active_tool is EditorTool.TEXT:
                self._place_text(world)
                event.accept()
                return

            if self.active_tool is EditorTool.SELECT:
                modifiers = event.modifiers()
                additive = bool(
                    modifiers
                    & Qt.KeyboardModifier.ShiftModifier
                )
                toggle = bool(
                    modifiers
                    & Qt.KeyboardModifier.ControlModifier
                )

                selected_elements = self.selected_elements()

                # Tokens are rendered above MapElements, so they also win
                # hit-testing over transform frames underneath them.
                token = self.token_at(
                    world
                )

                if token is not None:
                    if toggle:
                        self._toggle_token_selection(
                            token
                        )
                        event.accept()
                        return

                    if additive:
                        if token.id not in self.selected_token_ids:
                            selected_tokens = self.selected_tokens()
                            selected_tokens.append(
                                token
                            )
                            self._select_tokens(
                                selected_tokens,
                                primary=token,
                            )
                        event.accept()
                        return

                    if token.id not in self.selected_token_ids:
                        self._select_token(
                            token
                        )

                    self._start_token_move(
                        screen_pos,
                        primary=token,
                    )
                    event.accept()
                    return

                camera_part = self._camera_hit_part(screen_pos)
                if camera_part is not None:
                    self._start_camera_edit(
                        camera_part,
                        screen_pos,
                    )
                    event.accept()
                    return

                # Selection modifiers always win over transform gestures.
                if not additive and not toggle and selected_elements:
                    if len(selected_elements) > 1:
                        group_frame = self._selection_frame(
                            selected_elements
                        )

                        if (
                            group_frame is not None
                            and not self._group_is_locked(selected_elements)
                        ):
                            handle = self._hit_resize_handle(
                                group_frame,
                                screen_pos,
                            )

                            if handle is not None:
                                self._start_group_transform(
                                    selected_elements,
                                    TransformMode.RESIZE,
                                    screen_pos,
                                    handle=handle,
                                )
                                event.accept()
                                return

                            if (
                                self._group_can_rotate(selected_elements)
                                and self._hit_rotation_handle(
                                    group_frame,
                                    screen_pos,
                                )
                            ):
                                self._start_group_transform(
                                    selected_elements,
                                    TransformMode.ROTATE,
                                    screen_pos,
                                )
                                event.accept()
                                return

                            if self._frame_contains_screen(
                                group_frame,
                                screen_pos,
                            ):
                                self._start_group_transform(
                                    selected_elements,
                                    TransformMode.MOVE,
                                    screen_pos,
                                )
                                event.accept()
                                return

                    else:
                        selected = selected_elements[0]
                        frame = self._element_frame(
                            selected
                        )

                        if not selected.locked:
                            handle = self._hit_resize_handle(
                                frame,
                                screen_pos,
                            )

                            if handle is not None:
                                self._start_transform(
                                    selected,
                                    TransformMode.RESIZE,
                                    screen_pos,
                                    handle=handle,
                                )
                                event.accept()
                                return

                            if (
                                self._can_rotate(selected)
                                and self._hit_rotation_handle(
                                    frame,
                                    screen_pos,
                                )
                            ):
                                self._start_transform(
                                    selected,
                                    TransformMode.ROTATE,
                                    screen_pos,
                                )
                                event.accept()
                                return

                            if self._frame_contains_screen(
                                frame,
                                screen_pos,
                            ):
                                self._start_transform(
                                    selected,
                                    TransformMode.MOVE,
                                    screen_pos,
                                )
                                event.accept()
                                return

                element = self.element_at(
                    world
                )

                # Shift/Ctrl click modifies the current selection without
                # starting a transform.
                if element is not None and (additive or toggle):
                    if toggle:
                        self._toggle_element_selection(
                            element
                        )
                    elif element.id not in self.selected_element_ids:
                        selected_elements = self.selected_elements()
                        selected_elements.append(
                            element
                        )
                        self._select_elements(
                            selected_elements,
                            primary=element,
                        )

                    event.accept()
                    return

                if element is not None:
                    # Clicking an already selected member of a multi-selection
                    # keeps the whole group selected and starts a group move.
                    if (
                        len(selected_elements) > 1
                        and element.id in self.selected_element_ids
                    ):
                        if not self._group_is_locked(selected_elements):
                            self._start_group_transform(
                                selected_elements,
                                TransformMode.MOVE,
                                screen_pos,
                            )

                        event.accept()
                        return

                    self._select_element(
                        element
                    )

                    if not element.locked:
                        self._start_transform(
                            element,
                            TransformMode.MOVE,
                            screen_pos,
                        )

                    event.accept()
                    return

                # Dragging from empty canvas is the normal rectangle selection
                # gesture. A click without enough movement simply clears the
                # selection unless a modifier is held.
                self._start_marquee(
                    screen_pos,
                    modifiers,
                )
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        world = self.screen_to_world(event.position())
        self._hover_world = Vec2(world.x, world.y)
        self.mouse_world_moved.emit(world.x, world.y)

        if self._panning:
            if not (event.buttons() & Qt.MouseButton.MiddleButton):
                self._panning = False
                self.unsetCursor()
            else:
                delta = event.position() - self._last_mouse_pos
                self._last_mouse_pos = QPointF(event.position())
                self.camera.center = Vec2(
                    self.camera.center.x - delta.x() / self.camera.zoom,
                    self.camera.center.y - delta.y() / self.camera.zoom,
                )
                self.update()
                event.accept()
                return

        if self._creation_start is not None:
            if event.buttons() & Qt.MouseButton.LeftButton:
                self._update_drawing_creation(world)
                event.accept()
                return
            self._cancel_creation()

        if self._token_move is not None:
            if not (
                event.buttons()
                & Qt.MouseButton.LeftButton
            ):
                self._finish_token_move()
            else:
                self._update_token_move(
                    event.position(),
                    event.modifiers(),
                )
                event.accept()
                return

        if self._camera_edit is not None:
            if not (
                event.buttons()
                & Qt.MouseButton.LeftButton
            ):
                self._finish_camera_edit()
            else:
                self._update_camera_edit(
                    event.position()
                )
                event.accept()
                return

        if self._marquee_start_screen is not None:
            if event.buttons() & Qt.MouseButton.LeftButton:
                self._update_marquee(event.position())
                event.accept()
                return
            self._cancel_marquee()

        if self._transform is not None:
            if not (event.buttons() & Qt.MouseButton.LeftButton):
                self._finish_transform()
            else:
                self._update_transform(event.position(), event.modifiers())
                event.accept()
                return

        if self.pending_token_entity_id is not None:
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.update()
        elif self.active_tool is EditorTool.SELECT:
            self._update_hover_cursor(event.position())
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self.unsetCursor()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self._creation_start is not None:
            self._finish_drawing_creation(self.screen_to_world(event.position()))
            event.accept()
            return

        if (
            event.button()
            == Qt.MouseButton.LeftButton
            and self._token_move is not None
        ):
            self._update_token_move(
                event.position(),
                event.modifiers(),
            )
            self._finish_token_move()
            self._update_hover_cursor(
                event.position()
            )
            event.accept()
            return

        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._camera_edit is not None
        ):
            self._update_camera_edit(event.position())
            self._finish_camera_edit()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self._marquee_start_screen is not None:
            self._update_marquee(event.position())
            self._apply_marquee_selection(event.position())
            self._cancel_marquee()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self._transform is not None:
            self._update_transform(event.position(), event.modifiers())
            self._finish_transform()
            self._update_hover_cursor(event.position())
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            if self.pending_token_entity_id is not None:
                self.cancel_token_placement()
            elif self._token_move is not None:
                self.cancel_token_move()
            elif self._camera_edit is not None:
                self.cancel_camera_edit()
            elif self._transform is not None:
                self.cancel_transform()
            elif self._creation_start is not None:
                self._cancel_creation()
            elif self._marquee_start_screen is not None:
                self._cancel_marquee()
            else:
                self.set_tool(EditorTool.SELECT)
            event.accept()
            return

        if event.key() == Qt.Key.Key_Delete:
            self.delete_selected()
            event.accept()
            return

        # Fallback when the canvas owns focus. Main-window actions handle the
        # same standard shortcuts globally and defer to text widgets when needed.
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selected()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Cut):
            self.cut_selected()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Paste):
            self.paste_element()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Undo):
            self.undo()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Redo):
            self.redo()
            event.accept()
            return

        super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            token = self.token_at(self.screen_to_world(event.position()))
            if token is not None:
                if token.id not in self.selected_token_ids:
                    self._select_token(
                        token
                    )
                self.token_double_clicked.emit(token.entity_id)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        steps = event.angleDelta().y() / 120.0
        if not steps:
            return

        factor = 1.15 ** steps
        self.set_zoom_at(
            self.camera.zoom * factor,
            event.position(),
        )
        event.accept()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_text_format_bar()
        self.update()
