# -*- coding: utf-8 -*-

"""
FlowLayout: a layout that places its widgets left to right and wraps them
onto new lines, like words in a paragraph.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QPoint,
    QRect,
    QSize,
    Qt,
)
from PySide6.QtWidgets import (
    QLayout,
    QLayoutItem,
    QWidget,
)

# ------------------------------------------------------------------
# Flow Layout
# ------------------------------------------------------------------

class FlowLayout(QLayout):
    """
    Wrapping layout used for logical formula blocks.

    Every widget inserted here is atomic from the wrapping point of view.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        horizontal_spacing: int = 7,
        vertical_spacing: int = 5,
    ) -> None:
        super().__init__(parent)

        self._items: list[QLayoutItem] = []

        self._horizontal_spacing = horizontal_spacing
        self._vertical_spacing = vertical_spacing

        self.setContentsMargins(
            0,
            0,
            0,
            0,
        )

    # ------------------------------------------------------------------
    # QLayout API
    # ------------------------------------------------------------------

    def addItem(
        self,
        item: QLayoutItem,
    ) -> None:
        self._items.append(
            item
        )

    def count(self) -> int:
        return len(
            self._items
        )

    def itemAt(
        self,
        index: int,
    ) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]

        return None

    def takeAt(
        self,
        index: int,
    ) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(
                index
            )

        return None

    def expandingDirections(
        self,
    ) -> Qt.Orientations:
        return Qt.Orientation(0)

    def hasHeightForWidth(
        self,
    ) -> bool:
        return True

    def heightForWidth(
        self,
        width: int,
    ) -> int:
        return self._do_layout(
            QRect(
                0,
                0,
                width,
                0,
            ),
            test_only=True,
        )

    def setGeometry(
        self,
        rect: QRect,
    ) -> None:
        super().setGeometry(
            rect
        )

        self._do_layout(
            rect,
            test_only=False,
        )

    def sizeHint(
        self,
    ) -> QSize:
        return self.minimumSize()

    def minimumSize(
        self,
    ) -> QSize:
        size = QSize()

        for item in self._items:
            size = size.expandedTo(
                item.minimumSize()
            )

        margins = self.contentsMargins()

        size += QSize(
            margins.left()
            + margins.right(),
            margins.top()
            + margins.bottom(),
        )

        return size

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _do_layout(
        self,
        rect: QRect,
        *,
        test_only: bool,
    ) -> int:
        margins = self.contentsMargins()

        effective_rect = rect.adjusted(
            margins.left(),
            margins.top(),
            -margins.right(),
            -margins.bottom(),
        )

        x = effective_rect.x()
        y = effective_rect.y()

        line_height = 0

        for item in self._items:
            hint = item.sizeHint()

            # New row when the complete logical block no longer fits.
            if (
                line_height > 0
                and x + hint.width()
                > effective_rect.right() + 1
            ):
                x = effective_rect.x()

                y += (
                    line_height
                    + self._vertical_spacing
                )

                line_height = 0

            if not test_only:
                item.setGeometry(
                    QRect(
                        QPoint(
                            x,
                            y,
                        ),
                        hint,
                    )
                )

            x += (
                hint.width()
                + self._horizontal_spacing
            )

            line_height = max(
                line_height,
                hint.height(),
            )

        return (
            y
            + line_height
            - rect.y()
            + margins.bottom()
        )
