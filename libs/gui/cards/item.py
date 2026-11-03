# -*- coding: utf-8 -*-

"""
Item card module for the RPG Tabletop App.
"""

from __future__ import annotations

# import third-party modules
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
)

# import local modules
from .base import ResourceCard
from . import STAT_PALETTE
from ...assets.item import Item


# constants
THEME = """
QWidget {
    background-color: #181b24;
    color: #eeeeee;
    font-size: 13px;
}

QFrame#resourceCard {
    background-color: #252936;
    border: 1px solid #383d4d;
    border-radius: 8px;
}

QFrame#resourceCard:hover {
    background-color: #2b3040;
    border: 1px solid #6657d9;
}

QLabel#ItemCardName {
    font-size: 16px;
    font-weight: 700;
}

QLabel#ItemCardDescription {
    color: #a4aac0;
}
"""


# ------------------------------------------------------------------
# RightFlowLayout
# ------------------------------------------------------------------
class RightFlowLayout(QLayout):
    """
    Flow layout that wraps widgets to the next line
    and aligns each line to the right.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        spacing: int = 6,
    ) -> None:
        super().__init__(parent)

        self._items: list[QLayoutItem] = []
        self._spacing = spacing

        self.setContentsMargins(0, 0, 0, 0)

    def addItem(
        self,
        item: QLayoutItem,
    ) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

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
            return self._items.pop(index)

        return None

    def expandingDirections(
        self,
    ) -> Qt.Orientations:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(
        self,
        width: int,
    ) -> int:
        return self._do_layout(
            QRect(0, 0, width, 0),
            test_only=True,
        )

    def setGeometry(
        self,
        rect: QRect,
    ) -> None:
        super().setGeometry(rect)

        self._do_layout(
            rect,
            test_only=False,
        )

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()

        for item in self._items:
            size = size.expandedTo(
                item.minimumSize()
            )

        margins = self.contentsMargins()

        size += QSize(
            margins.left() + margins.right(),
            margins.top() + margins.bottom(),
        )

        return size

    def _do_layout(
        self,
        rect: QRect,
        *,
        test_only: bool,
    ) -> int:

        margins = self.contentsMargins()

        effective = rect.adjusted(
            margins.left(),
            margins.top(),
            -margins.right(),
            -margins.bottom(),
        )

        y = effective.y()

        row: list[QLayoutItem] = []
        row_width = 0
        row_height = 0

        def layout_row() -> None:
            nonlocal y, row, row_width, row_height

            if not row:
                return

            # Right-align the complete row.
            x = (
                effective.right()
                - row_width
                + 1
            )

            for item in row:
                hint = item.sizeHint()

                if not test_only:
                    item.setGeometry(
                        QRect(
                            QPoint(x, y),
                            hint,
                        )
                    )

                x += (
                    hint.width()
                    + self._spacing
                )

            y += (
                row_height
                + self._spacing
            )

            row = []
            row_width = 0
            row_height = 0

        for item in self._items:
            hint = item.sizeHint()

            added_width = hint.width()

            if row:
                added_width += self._spacing

            if (
                row
                and row_width + added_width
                > effective.width()
            ):
                layout_row()

            if row:
                row_width += self._spacing

            row.append(item)

            row_width += hint.width()

            row_height = max(
                row_height,
                hint.height(),
            )

        layout_row()

        return (
            y
            - rect.y()
            - self._spacing
            + margins.bottom()
        )


# ------------------------------------------------------------------
# Item card
# ------------------------------------------------------------------
class ItemCard(ResourceCard):
    """
    Preview card displaying Item information.
    """

    def __init__(
        self,
        resource_id: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            resource_id,
            parent,
        )

        self.item: Item = Item.from_name(
            resource_id
        )

        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        layout.setContentsMargins(
            10,
            10,
            10,
            10,
        )

        layout.setSpacing(
            6
        )

        # --------------------------------------------------------------
        # Name
        # --------------------------------------------------------------

        self.name_label = QLabel()

        self.name_label.setObjectName(
            "ItemCardName"
        )

        layout.addWidget(
            self.name_label
        )

        # --------------------------------------------------------------
        # Description
        # --------------------------------------------------------------

        self.description_label = QLabel()

        self.description_label.setObjectName(
            "ItemCardDescription"
        )

        self.description_label.setWordWrap(
            True
        )

        layout.addWidget(
            self.description_label
        )

        layout.addStretch(1)

        # --------------------------------------------------------------
        # Modifiers
        # --------------------------------------------------------------

        self.modifiers_layout = RightFlowLayout(spacing=6)

        self.modifiers_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        layout.addLayout(
            self.modifiers_layout
        )

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        self.name_label.setText(
            self.item.name
        )

        self.description_label.setText(
            self.item.description
        )

        self._refresh_modifiers()

    def _refresh_modifiers(self) -> None:
        while self.modifiers_layout.count():
            item: QLayoutItem = self.modifiers_layout.takeAt(0)

            widget = item.widget()

            if widget is not None:
                widget.deleteLater()

        for stat, value in self.item.modifier:
            stat = str(stat).lower()

            color = STAT_PALETTE.get(
                stat,
                "#6B7280",
            )

            if isinstance(value, (int, float)):
                value_text = f"{value:+g}"
            else:
                value_text = str(value)

            modifier_label = QLabel(
                f"{stat.upper()} {value_text}"
            )

            modifier_label.setObjectName(
                "ItemCardModifier"
            )

            modifier_label.setStyleSheet(
                f"""
                QLabel {{
                    background-color: {color};
                    color: white;
                    border-radius: 6px;
                    padding: 3px 7px;
                    font-weight: 600;
                    font-size: 11px;
                }}
                """
            )

            self.modifiers_layout.addWidget(
                modifier_label
            )

    # ------------------------------------------------------------------
    # Browser search
    # ------------------------------------------------------------------

    def search_text(self) -> str:
        return f"{self.resource_id} {self.item.name}"


