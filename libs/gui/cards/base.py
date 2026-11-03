# -*- coding: utf-8 -*-
#pylint: disable=invalid-name

"""
Base resource card widgets.

This module defines the common behavior shared by every resource preview
card displayed inside the Campaign Manager.
"""
# import built-in modules
from __future__ import annotations

# import third-party modules
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import (
    QContextMenuEvent,
    QKeyEvent,
    QMouseEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QSizePolicy,
    QWidget,
)


class ResourceCard(QFrame):
    """
    Base class for every resource preview card.

    A ResourceCard only handles generic interaction:
        - selection
        - click
        - double click
        - context menu request
        - keyboard activation
        - common visual state

    Resource-specific content must be implemented by subclasses.

    Examples:
        CharacterCard
        EntityCard
        ItemCard
        SpellCard
        MapCard
        SessionCard
    """

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    # Emitted when the card is clicked.
    clicked = Signal(str)

    # Emitted when the card is double-clicked or activated with Enter.
    double_clicked = Signal(str)

    # Emitted when a context menu is requested.
    #
    # Arguments:
    #     resource_id
    #     global screen position
    context_menu_requested = Signal(str, QPoint)

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def __init__(
        self,
        resource_id: str,
        parent: QWidget | None = None,
    ) -> None:
        """
        Initialize the resource card.

        Args:
            resource_id:
                Unique identifier of the resource represented by the card.

            parent:
                Optional Qt parent widget.
        """
        super().__init__(parent)

        self.resource_id = str(resource_id)

        self._selected = False

        self._setup_card()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup_card(self) -> None:
        """
        Configure common card behavior and appearance properties.
        """
        self.setObjectName("resourceCard")

        # Dynamic properties used by the application stylesheet.
        self.setProperty("selected", False)

        # Cards are interactive.
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
        )

        # Allows keyboard navigation / activation.
        self.setFocusPolicy(
            Qt.FocusPolicy.StrongFocus
        )

        # The card should keep the size defined by its subclass while
        # remaining usable in responsive layouts.
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Preferred,
        )

        # The border itself will generally be controlled by the stylesheet.
        self.setFrameShape(
            QFrame.Shape.NoFrame
        )

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    @property
    def selected(self) -> bool:
        """
        Whether the card is currently selected.
        """
        return self._selected

    def set_selected(
        self,
        selected: bool,
    ) -> None:
        """
        Change the card selection state.

        The ResourceBrowser is expected to manage which card is selected.
        """
        selected = bool(selected)

        if selected == self._selected:
            return

        self._selected = selected

        self.setProperty(
            "selected",
            selected,
        )

        # Qt does not automatically recompute a stylesheet when a dynamic
        # property changes, so force a style refresh.
        self.style().unpolish(self)
        self.style().polish(self)

        self.update()

    # ------------------------------------------------------------------
    # Search text
    # ------------------------------------------------------------------
    def search_text(self) -> str:
        """
        Return the text used by ResourceBrowser filtering.

        Resource-specific cards should override this method.
        """
        return self.resource_id.casefold()

    # ------------------------------------------------------------------
    # Resource interface
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """
        Refresh the visual content of the card.

        Every resource-specific card must implement this method.

        Example:
            CharacterCard.refresh()
            SpellCard.refresh()
            MapCard.refresh()
        """
        raise NotImplementedError(
            f"{type(self).__name__}.refresh() must be implemented."
        )

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mousePressEvent(
        self,
        event: QMouseEvent,
    ) -> None:
        """
        Emit clicked when the left mouse button is pressed.
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(
                self.resource_id
            )

        super().mousePressEvent(event)

    def mouseDoubleClickEvent(
        self,
        event: QMouseEvent,
    ) -> None:
        """
        Emit double_clicked when the card is double-clicked.
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(
                self.resource_id
            )

        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def contextMenuEvent(
        self,
        event: QContextMenuEvent,
    ) -> None:
        """
        Request a context menu for this resource.

        The card does not create the menu itself because actions such as
        duplicate/delete belong to the ResourceBrowser / IDE layer.
        """
        self.context_menu_requested.emit(
            self.resource_id,
            event.globalPos(),
        )

        event.accept()

    # ------------------------------------------------------------------
    # Keyboard events
    # ------------------------------------------------------------------

    def keyPressEvent(
        self,
        event: QKeyEvent,
    ) -> None:
        """
        Allow keyboard activation of the card.

        Enter / Return:
            Open the resource.

        Space:
            Select the resource.
        """
        if event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        ):
            self.double_clicked.emit(
                self.resource_id
            )

            event.accept()
            return

        if event.key() == Qt.Key.Key_Space:
            self.clicked.emit(
                self.resource_id
            )

            event.accept()
            return

        super().keyPressEvent(event)


