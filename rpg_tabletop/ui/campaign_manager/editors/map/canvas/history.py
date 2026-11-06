# -*- coding: utf-8 -*-

"""Map canvas history: undo/redo, clipboard (cut, copy, paste) and deletion."""

from __future__ import annotations

from copy import deepcopy

from rpg_tabletop.assets.map import (
    CameraState,
    DrawingElement,
    ImageElement,
    InitialTokenPlacement,
    MapElement,
    TextElement,
    Vec2,
)
from rpg_tabletop.ui.campaign_manager.editors.map.state import (
    MAX_UNDO_STEPS,
    PASTE_OFFSET_PX,
    EditorState,
)


class HistoryMixin:
    """Undo/redo history, clipboard (cut, copy, paste) and deletion. Mixed into MapCanvas."""

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
