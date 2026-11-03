# -*- coding: utf-8 -*-

"""
Card-based resource browser.

The ResourceBrowser displays ResourceCard widgets in a responsive grid and
provides:
    - search filtering
    - selection
    - double-click opening
    - context menus
    - resource creation
    - responsive card layout
    - card dealing animation on search
"""

from __future__ import annotations

# import built-in modules
from collections.abc import Iterable

# import third-party modules
from PySide6.QtCore import (
    QEvent,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QSize,
    Qt,
    QParallelAnimationGroup,
    QSequentialAnimationGroup,
    QPauseAnimation,
    Signal,
)
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# import local modules
from .cards.base import ResourceCard


# =============================================================================
# Card slot
# =============================================================================


class CardSlot(QWidget):
    """
    Fixed grid slot containing a ResourceCard.

    The QGridLayout positions CardSlot widgets while the ResourceCard itself
    remains free to move slightly inside the slot for animations.
    """

    def __init__(
        self,
        card: ResourceCard,
        size: QSize,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.card = card

        self.set_card_size(size)

        self.card.setParent(self)
        self.card.move(0, 0)

    def set_card_size(
        self,
        size: QSize,
    ) -> None:
        """
        Change the slot and card size.
        """
        self.setFixedSize(size)

        self.card.setFixedSize(size)
        self.card.move(0, 0)


# =============================================================================
# Resource browser
# =============================================================================


class ResourceBrowser(QWidget):
    """
    Generic browser displaying ResourceCard widgets.

    ResourceBrowser is responsible for:
        - card sizing
        - card positioning
        - responsive grid layout
        - search filtering
        - card selection
        - activation
        - context menus
        - deal/draw animations

    It deliberately does not know anything about the actual resource type.
    """

    # -------------------------------------------------------------------------
    # Signals
    # -------------------------------------------------------------------------

    resource_selected = Signal(str, str)

    resource_open_requested = Signal(str, str)

    resource_create_requested = Signal(str)

    resource_duplicate_requested = Signal(str, str)

    resource_delete_requested = Signal(str, str)

    # -------------------------------------------------------------------------
    # Initialization
    # -------------------------------------------------------------------------

    def __init__(
        self,
        resource_type: str = "",
        title: str = "Resources",
        card_size: QSize | tuple[int, int] = QSize(280, 160),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._resource_type = resource_type
        self._title = title

        if isinstance(card_size, tuple):
            card_size = QSize(
                card_size[0],
                card_size[1],
            )

        self._card_size = card_size

        self._cards: list[ResourceCard] = []

        self._slots: dict[
            ResourceCard,
            CardSlot,
        ] = {}

        self._visible_cards: list[
            ResourceCard
        ] = []

        self._selected_card: ResourceCard | None = None

        self._grid_columns = 1

        # Current draw/deal animation.
        self._draw_animation: (
            QParallelAnimationGroup | None
        ) = None

        self._build_ui()
        self._connect_signals()

    # =========================================================================
    # UI
    # =========================================================================

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)

        root_layout.setContentsMargins(
            12,
            10,
            12,
            10,
        )

        root_layout.setSpacing(12)

        # ---------------------------------------------------------------------
        # Header
        # ---------------------------------------------------------------------

        header_layout = QHBoxLayout()

        header_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        self.title_label = QLabel(
            self._title
        )

        self.title_label.setObjectName(
            "resourceBrowserTitle"
        )

        self.create_button = QPushButton()

        self.create_button.setObjectName(
            "resourceBrowserCreateButton"
        )

        header_layout.addWidget(
            self.title_label
        )

        header_layout.addStretch(1)

        header_layout.addWidget(
            self.create_button
        )

        root_layout.addLayout(
            header_layout
        )

        # ---------------------------------------------------------------------
        # Search
        # ---------------------------------------------------------------------

        self.search_edit = QLineEdit()

        self.search_edit.setObjectName(
            "resourceBrowserSearch"
        )

        self.search_edit.setPlaceholderText(
            "Search resources..."
        )

        self.search_edit.setClearButtonEnabled(
            True
        )

        root_layout.addWidget(
            self.search_edit
        )

        # ---------------------------------------------------------------------
        # Scroll area
        # ---------------------------------------------------------------------

        self.scroll_area = QScrollArea()

        self.scroll_area.setObjectName(
            "resourceBrowserScrollArea"
        )

        self.scroll_area.setWidgetResizable(
            True
        )

        self.scroll_area.setFrameShape(
            QScrollArea.Shape.NoFrame
        )

        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.scroll_area.viewport().installEventFilter(
            self
        )

        # ---------------------------------------------------------------------
        # Cards container
        # ---------------------------------------------------------------------

        self.cards_container = QWidget()

        self.cards_container.setObjectName(
            "resourceCardsContainer"
        )

        self.cards_container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        self.cards_layout = QGridLayout(
            self.cards_container
        )

        self.cards_layout.setContentsMargins(
            4,
            4,
            4,
            4,
        )

        self.cards_layout.setHorizontalSpacing(
            12
        )

        self.cards_layout.setVerticalSpacing(
            12
        )

        self.cards_layout.setAlignment(
            Qt.AlignmentFlag.AlignTop
            | Qt.AlignmentFlag.AlignLeft
        )

        self.cards_layout.setSizeConstraint(
            QLayout.SizeConstraint.SetMinAndMaxSize
        )

        self.scroll_area.setWidget(
            self.cards_container
        )

        root_layout.addWidget(
            self.scroll_area,
            1,
        )

        # ---------------------------------------------------------------------
        # Footer
        # ---------------------------------------------------------------------

        self.count_label = QLabel(
            "0 resources"
        )

        self.count_label.setObjectName(
            "resourceBrowserCount"
        )

        root_layout.addWidget(
            self.count_label
        )

        self._update_create_button()

    # =========================================================================
    # Connections
    # =========================================================================

    def _connect_signals(self) -> None:
        self.search_edit.textChanged.connect(
            self._on_search_changed
        )

        self.create_button.clicked.connect(
            self._on_create_clicked
        )

    # =========================================================================
    # Public API
    # =========================================================================

    @property
    def card_size(self) -> QSize:
        return QSize(
            self._card_size
        )

    @property
    def resource_type(self) -> str:
        return self._resource_type

    @property
    def cards(self) -> tuple[ResourceCard, ...]:
        return tuple(
            self._cards
        )

    @property
    def visible_cards(
        self,
    ) -> tuple[ResourceCard, ...]:
        return tuple(
            self._visible_cards
        )

    # -------------------------------------------------------------------------
    # Resource type
    # -------------------------------------------------------------------------

    def set_resource_type(
        self,
        resource_type: str,
        title: str | None = None,
    ) -> None:
        self._resource_type = resource_type

        if title is not None:
            self._title = title

        elif resource_type:
            self._title = (
                resource_type.capitalize()
            )

        else:
            self._title = "Resources"

        self.title_label.setText(
            self._title
        )

        self._update_create_button()

    # -------------------------------------------------------------------------
    # Card size
    # -------------------------------------------------------------------------

    def set_card_size(
        self,
        size: QSize | tuple[int, int],
    ) -> None:
        """
        Change the size imposed on every card in the browser.
        """
        if isinstance(size, tuple):
            size = QSize(
                size[0],
                size[1],
            )

        self._stop_draw_animation(
            reset=True
        )

        self._card_size = size

        for slot in self._slots.values():
            slot.set_card_size(
                size
            )

        self._rebuild_grid()

    # -------------------------------------------------------------------------
    # Cards
    # -------------------------------------------------------------------------

    def set_cards(
        self,
        cards: Iterable[ResourceCard],
    ) -> None:
        """
        Replace every card currently displayed by the browser.
        """
        self.clear_cards()

        for card in cards:
            self.add_card(
                card,
                rebuild=False,
            )

        self._apply_filter(
            animate=False
        )

    def add_card(
        self,
        card: ResourceCard,
        *,
        rebuild: bool = True,
    ) -> None:
        """
        Add one ResourceCard to the browser.
        """
        if card in self._cards:
            return

        slot = CardSlot(
            card,
            self._card_size,
            self.cards_container,
        )

        # Opacity animation support.
        opacity_effect = QGraphicsOpacityEffect(
            card
        )

        opacity_effect.setOpacity(
            1.0
        )

        card.setGraphicsEffect(
            opacity_effect
        )

        # Card interactions.
        card.clicked.connect(
            self._on_card_clicked
        )

        card.double_clicked.connect(
            self._on_card_double_clicked
        )

        card.context_menu_requested.connect(
            self._on_card_context_menu
        )

        self._cards.append(
            card
        )

        self._slots[card] = slot

        if rebuild:
            self._apply_filter(
                animate=False
            )

    def remove_card(
        self,
        resource_id: str,
    ) -> None:
        """
        Remove a card by its resource identifier.
        """
        card = self.get_card(
            resource_id
        )

        if card is None:
            return

        self._stop_draw_animation(
            reset=True
        )

        if card is self._selected_card:
            self._selected_card = None

        if card in self._visible_cards:
            self._visible_cards.remove(
                card
            )

        self._cards.remove(
            card
        )

        slot = self._slots.pop(
            card
        )

        self.cards_layout.removeWidget(
            slot
        )

        slot.deleteLater()

        self._apply_filter(
            animate=False
        )

    def clear_cards(self) -> None:
        """
        Remove all cards from the browser.
        """
        self._stop_draw_animation(
            reset=False
        )

        self._selected_card = None

        while self.cards_layout.count():
            self.cards_layout.takeAt(0)

        for slot in self._slots.values():
            slot.deleteLater()

        self._cards.clear()
        self._slots.clear()
        self._visible_cards.clear()

        self._update_count()

    def get_card(
        self,
        resource_id: str,
    ) -> ResourceCard | None:
        """
        Get a ResourceCard by resource id.
        """
        for card in self._cards:
            if card.resource_id == resource_id:
                return card

        return None

    def selected_resource_id(
        self,
    ) -> str | None:
        """
        Return the currently selected resource id.
        """
        if self._selected_card is None:
            return None

        return self._selected_card.resource_id

    def refresh_cards(self) -> None:
        """
        Refresh the contents of every card.
        """
        for card in self._cards:
            card.refresh()

        self._apply_filter(
            animate=False
        )

    # =========================================================================
    # Search
    # =========================================================================

    def _on_search_changed(
        self,
        _text: str,
    ) -> None:
        self._apply_filter(
            animate=True
        )

    def _apply_filter(
        self,
        *,
        animate: bool,
    ) -> None:
        """
        Filter cards according to ResourceCard.search_text().

        Non-matching cards disappear immediately so the grid collapses
        instantly.

        Matching cards are then dealt into their positions when animation
        is enabled.
        """
        self._stop_draw_animation(
            reset=True
        )

        query = (
            self.search_edit.text()
            .strip()
            .casefold()
        )

        visible_cards: list[
            ResourceCard
        ] = []

        for card in self._cards:
            searchable_text = str(
                card.search_text()
            ).casefold()

            matches = (
                not query
                or query in searchable_text
            )

            slot = self._slots[card]

            slot.setVisible(
                matches
            )

            if matches:
                visible_cards.append(
                    card
                )

        self._visible_cards = visible_cards

        # Collapse/rebuild immediately.
        self._rebuild_grid()

        self._update_count()

        if animate and visible_cards:
            self._draw_cards()

    # =========================================================================
    # Grid
    # =========================================================================

    def eventFilter(
        self,
        watched,
        event,
    ) -> bool:
        """
        Rebuild the grid when the scroll viewport changes width.
        """
        if (
            watched
            is self.scroll_area.viewport()
            and event.type()
            == QEvent.Type.Resize
        ):
            self._stop_draw_animation(
                reset=True
            )

            self._rebuild_grid()

        return super().eventFilter(
            watched,
            event,
        )

    def _calculate_column_count(
        self,
    ) -> int:
        """
        Calculate how many cards fit horizontally.
        """
        viewport_width = (
            self.scroll_area
            .viewport()
            .width()
        )

        margins = (
            self.cards_layout
            .contentsMargins()
        )

        available_width = (
            viewport_width
            - margins.left()
            - margins.right()
        )

        spacing = (
            self.cards_layout
            .horizontalSpacing()
        )

        card_width = (
            self._card_size.width()
        )

        if card_width <= 0:
            return 1

        columns = (
            available_width + spacing
        ) // (
            card_width + spacing
        )

        return max(
            1,
            int(columns),
        )

    def _rebuild_grid(self) -> None:
        """
        Rebuild the responsive card grid.
        """
        columns = self._calculate_column_count()
        if (
            columns == self._grid_columns
            and self.cards_layout.count() == len(self._visible_cards)
        ):
            return
        while self.cards_layout.count():
            self.cards_layout.takeAt(0)

        self._grid_columns = columns

        for index, card in enumerate(
            self._visible_cards
        ):
            row = (
                index // columns
            )

            column = (
                index % columns
            )

            slot = self._slots[
                card
            ]

            slot.show()

            self.cards_layout.addWidget(
                slot,
                row,
                column,
                Qt.AlignmentFlag.AlignTop
                | Qt.AlignmentFlag.AlignLeft,
            )

        self.cards_layout.invalidate()
        self.cards_layout.activate()

    # =========================================================================
    # Draw / deal animation
    # =========================================================================

    def _draw_cards(self) -> None:
        """
        Rapidly deal the visible cards into the grid.

        Cards begin slightly above and to the left of their final position,
        then quickly slide into place while fading in.
        """
        self._stop_draw_animation(
            reset=True
        )

        if not self._visible_cards:
            return

        master_animation = (
            QParallelAnimationGroup(self)
        )

        # ---------------------------------------------------------------------
        # Animation tuning
        # ---------------------------------------------------------------------

        start_offset = QPoint(
            -24,
            -12,
        )

        move_duration = 170
        fade_duration = 100

        stagger = 28
        maximum_delay = 250

        # ---------------------------------------------------------------------

        for index, card in enumerate(
            self._visible_cards
        ):
            effect = (
                card.graphicsEffect()
            )

            if not isinstance(
                effect,
                QGraphicsOpacityEffect,
            ):
                continue

            final_position = QPoint(
                0,
                0,
            )

            start_position = (
                final_position
                + start_offset
            )

            card.move(
                start_position
            )

            effect.setOpacity(
                0.0
            )

            # -----------------------------------------------------------------
            # Movement
            # -----------------------------------------------------------------

            move_animation = (
                QPropertyAnimation(
                    card,
                    b"pos",
                )
            )

            move_animation.setDuration(
                move_duration
            )

            move_animation.setStartValue(
                start_position
            )

            move_animation.setEndValue(
                final_position
            )

            move_animation.setEasingCurve(
                QEasingCurve.Type.OutCubic
            )

            # -----------------------------------------------------------------
            # Fade
            # -----------------------------------------------------------------

            fade_animation = (
                QPropertyAnimation(
                    effect,
                    b"opacity",
                )
            )

            fade_animation.setDuration(
                fade_duration
            )

            fade_animation.setStartValue(
                0.0
            )

            fade_animation.setEndValue(
                1.0
            )

            fade_animation.setEasingCurve(
                QEasingCurve.Type.OutCubic
            )

            # -----------------------------------------------------------------
            # Move + fade together
            # -----------------------------------------------------------------

            card_animation = (
                QParallelAnimationGroup()
            )

            card_animation.addAnimation(
                move_animation
            )

            card_animation.addAnimation(
                fade_animation
            )

            # -----------------------------------------------------------------
            # Cascade delay
            # -----------------------------------------------------------------

            delay = min(
                index * stagger,
                maximum_delay,
            )

            sequence = (
                QSequentialAnimationGroup()
            )

            sequence.addAnimation(
                QPauseAnimation(
                    delay
                )
            )

            sequence.addAnimation(
                card_animation
            )

            master_animation.addAnimation(
                sequence
            )

        self._draw_animation = (
            master_animation
        )

        master_animation.finished.connect(
            self._on_draw_animation_finished
        )

        master_animation.start()

    def _on_draw_animation_finished(
        self,
    ) -> None:
        """
        Normalize cards after the deal animation.
        """
        for card in self._visible_cards:
            card.move(
                0,
                0,
            )

            effect = (
                card.graphicsEffect()
            )

            if isinstance(
                effect,
                QGraphicsOpacityEffect,
            ):
                effect.setOpacity(
                    1.0
                )

        animation = (
            self._draw_animation
        )

        self._draw_animation = None

        if animation is not None:
            animation.deleteLater()

    def _stop_draw_animation(
        self,
        *,
        reset: bool,
    ) -> None:
        """
        Stop the currently running deal animation.
        """
        if self._draw_animation is not None:
            self._draw_animation.stop()
            self._draw_animation.deleteLater()

            self._draw_animation = None

        if not reset:
            return

        for card in self._cards:
            card.move(
                0,
                0,
            )

            effect = (
                card.graphicsEffect()
            )

            if isinstance(
                effect,
                QGraphicsOpacityEffect,
            ):
                effect.setOpacity(
                    1.0
                )

    # =========================================================================
    # Selection
    # =========================================================================

    def _select_card(
        self,
        card: ResourceCard,
    ) -> None:
        """
        Make one card the current selection.
        """
        if self._selected_card is card:
            return

        if self._selected_card is not None:
            self._selected_card.set_selected(
                False
            )

        self._selected_card = card

        card.set_selected(
            True
        )

    def _on_card_clicked(
        self,
        resource_id: str,
    ) -> None:
        card = self.get_card(
            resource_id
        )

        if card is None:
            return

        self._select_card(
            card
        )

        self.resource_selected.emit(
            self._resource_type,
            resource_id,
        )

    def _on_card_double_clicked(
        self,
        resource_id: str,
    ) -> None:
        card = self.get_card(
            resource_id
        )

        if card is None:
            return

        self._select_card(
            card
        )

        self.resource_open_requested.emit(
            self._resource_type,
            resource_id,
        )

    # =========================================================================
    # Create
    # =========================================================================

    def _on_create_clicked(self) -> None:
        if not self._resource_type:
            return

        self.resource_create_requested.emit(
            self._resource_type
        )

    # =========================================================================
    # Context menu
    # =========================================================================

    def _on_card_context_menu(
        self,
        resource_id: str,
        global_position: QPoint,
    ) -> None:
        card = self.get_card(
            resource_id
        )

        if card is None:
            return

        self._select_card(
            card
        )

        menu = QMenu(self)

        open_action = menu.addAction(
            "Open"
        )

        duplicate_action = (
            menu.addAction(
                "Duplicate"
            )
        )

        menu.addSeparator()

        delete_action = menu.addAction(
            "Delete"
        )

        selected_action = menu.exec(
            global_position
        )

        if selected_action == open_action:
            self.resource_open_requested.emit(
                self._resource_type,
                resource_id,
            )

        elif (
            selected_action
            == duplicate_action
        ):
            self.resource_duplicate_requested.emit(
                self._resource_type,
                resource_id,
            )

        elif (
            selected_action
            == delete_action
        ):
            self.resource_delete_requested.emit(
                self._resource_type,
                resource_id,
            )

    # =========================================================================
    # UI helpers
    # =========================================================================

    def _update_create_button(
        self,
    ) -> None:
        if not self._resource_type:
            self.create_button.setText(
                "+ New"
            )

            return

        title = (
            self._title.strip()
        )

        if title.endswith("ies"):
            singular = (
                title[:-3]
                + "y"
            )

        elif title.endswith("s"):
            singular = title[:-1]

        else:
            singular = title

        self.create_button.setText(
            f"+ New {singular}"
        )

    def _update_count(self) -> None:
        visible = len(
            self._visible_cards
        )

        total = len(
            self._cards
        )

        if visible == total:
            if total == 1:
                text = "1 resource"
            else:
                text = (
                    f"{total} resources"
                )

        else:
            text = (
                f"{visible} / "
                f"{total} resources"
            )

        self.count_label.setText(
            text
        )
