# -*- coding: utf-8 -*-

"""Interactive map viewport of the map editor: construction, public API and Qt events.

Each concern lives in its own mixin module of this package (camera, tokens, history,
creation, selection, transform, snapping, painting)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QWheelEvent,
)
from PySide6.QtWidgets import QMessageBox, QWidget

from rpg_tabletop.assets.map import (
    ImageElement,
    MapAsset,
    MediaCache,
    Vec2,
)
from rpg_tabletop.ui.campaign_manager.editors.map.state import (
    DRAW_TOOLS,
    GRID_SNAP_TOLERANCE_PX,
    ROTATION_SNAP_DEG,
    ROTATION_SNAP_TOLERANCE_DEG,
    CameraEditSession,
    EditorCamera,
    EditorState,
    EditorTool,
    GroupTransformSession,
    TokenMoveSession,
    TransformMode,
    TransformSession,
)
from rpg_tabletop.ui.campaign_manager.editors.map.tokens import (
    NullTokenEntityProvider,
    TokenEntityProvider,
    TokenViewData,
)
from rpg_tabletop.ui.campaign_manager.editors.map.widgets import (
    ImageFormattingBar,
    TextFormattingBar,
)

from .camera import CameraMixin
from .creation import CreationMixin
from .history import HistoryMixin
from .painting import PaintingMixin
from .selection import SelectionMixin
from .snapping import SnappingMixin
from .tokens import TokenMixin
from .transform import TransformMixin


class MapCanvas(
    CameraMixin,
    TokenMixin,
    HistoryMixin,
    CreationMixin,
    SelectionMixin,
    TransformMixin,
    SnappingMixin,
    PaintingMixin,
    QWidget,
):
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
