# -*- coding: utf-8 -*-

"""Map canvas selection: hit-testing, selection, marquee and the image/text formatting bars."""

from __future__ import annotations

from copy import deepcopy
from math import hypot
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QFont,
    QFontMetricsF,
    QPolygonF,
)

from rpg_tabletop.assets.map import (
    DrawingElement,
    ImageElement,
    MapElement,
    Rect,
    TextElement,
    Vec2,
)
from rpg_tabletop.ui.campaign_manager.editors.map.state import (
    MARQUEE_CLICK_TOLERANCE_PX,
    ROTATE_HANDLE_OFFSET_PX,
    EditorTool,
    ElementFrame,
)


class SelectionMixin:
    """Element hit-testing, selection, marquee and the image/text formatting bars. Mixed into MapCanvas."""

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
