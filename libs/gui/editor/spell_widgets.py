"""Small reusable widgets of the spell editor."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...assets.spell import TARGET_SCOPES
from ..cards import RESOURCE_PALETTE, STAT_NAMES
from ..cards.formula import FormulaPreview


# Resources first: hit points are the most common target of an effect
SPELL_STATS = (*RESOURCE_PALETTE, *STAT_NAMES)

# Label and symbol of the operators offered for a trigger condition
CONDITION_OPERATORS = (
    ("Greater than", ">"),
    ("Greater or equal", ">="),
    ("Less than", "<"),
    ("Less or equal", "<="),
    ("Equal", "=="),
    ("Different", "!="),
    ("And", "&"),
    ("Or", "|"),
    ("Exclusive or", "^"),
    ("Not", "!"),
)

DICE_RATIO_TEMPLATE = "diceratio({scope}.{stat}, 10)"
DICE_ATTACK_TEMPLATE = "diceattack(user.dex, 20, target.con, 20)"


def set_style_property(widget: QWidget, name: str, value: object) -> None:
    """Set a dynamic property and restyle the widget so the stylesheet sees it."""
    widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def pill_style(color: str) -> str:
    """Stylesheet of a small rounded tag, like the ones of the spell cards."""
    return (
        "QLabel {"
        f"background-color: {color}; color: white; border: none;"
        "border-radius: 6px; padding: 2px 6px;"
        "font-size: 10px; font-weight: 600;"
        "}"
    )


class ValueComboBox(QComboBox):
    """Combo box over known values that also keeps a stored value it does not know."""

    def __init__(self, values: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SpellCombo")
        self._known = tuple(values)
        self.addItems(self._known)

    @property
    def value(self) -> str:
        """The selected text."""
        return self.currentText()

    @property
    def is_known(self) -> bool:
        """Whether the selection is one of the values this combo box was built with."""
        return 0 <= self.currentIndex() < len(self._known)

    def set_value(self, value: str) -> None:
        """
        Select `value` without emitting signals.

        A value outside the known ones is appended so it survives a save untouched;
        the extra item of a previous call is dropped.
        """
        blocker = self.blockSignals(True)
        try:
            while self.count() > len(self._known):
                self.removeItem(self.count() - 1)
            if value not in self._known:
                self.addItem(value)
            self.setCurrentIndex(self.findText(value))
        finally:
            self.blockSignals(blocker)


class ElidedLabel(QLabel):
    """Single-line label that ends its text with an ellipsis when it does not fit."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.set_full_text(text)

    @property
    def full_text(self) -> str:
        """The complete text, whether or not it is elided on screen."""
        return self._full_text

    def set_full_text(self, text: str) -> None:
        """Replace the text; the complete text is also the tooltip."""
        self._full_text = text
        self.setToolTip(text)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        return QSize(metrics.horizontalAdvance(self._full_text), metrics.height() + 2)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.fontMetrics().height() + 2)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setPen(self.palette().windowText().color())
        elided = self.fontMetrics().elidedText(
            self._full_text,
            Qt.TextElideMode.ElideRight,
            self.width(),
        )
        painter.drawText(
            self.rect(),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            elided,
        )
        painter.end()
        event.accept()


class ClickableFrame(QFrame):
    """Frame that reports a left click anywhere on it, except on its own buttons."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            # Accepting the press is what routes the matching release to this frame
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class FormulaField(QWidget):
    """Formula text input with a menu of snippets and a live graphical preview."""

    textChanged = Signal(str)

    def __init__(
        self,
        placeholder: str = "",
        *,
        condition: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SpellFormulaGroup")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.line_edit = QLineEdit()
        self.line_edit.setObjectName("SpellFormulaField")
        self.line_edit.setPlaceholderText(placeholder)
        self.insert_button = QToolButton()
        self.insert_button.setObjectName("SpellInsertButton")
        self.insert_button.setText("Insert")
        self.insert_button.setToolTip("Insert a stat, a dice function or an operator at the cursor")
        self.insert_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.insert_button.setMenu(self._build_menu(condition))
        row.addWidget(self.line_edit, 1)
        row.addWidget(self.insert_button)
        layout.addLayout(row)

        self.preview = FormulaPreview()
        self.preview.hide()
        layout.addWidget(self.preview)

        self.line_edit.textChanged.connect(self._on_text_changed)

    def text(self) -> str:
        """The formula as typed."""
        return self.line_edit.text()

    def set_text(self, text: str) -> None:
        """Replace the formula."""
        self.line_edit.setText(text)

    def _on_text_changed(self, text: str) -> None:
        self.preview.set_expression(text)
        self.preview.setVisible(bool(text.strip()))
        self.textChanged.emit(text)

    def _build_menu(self, condition: bool) -> QMenu:
        menu = QMenu(self.insert_button)
        for scope in TARGET_SCOPES:
            stat_menu = menu.addMenu(f"{scope.capitalize()} stat")
            for stat in SPELL_STATS:
                self._add_snippet(stat_menu, stat, f"{scope}.{stat}")

        ratio_menu = menu.addMenu("Dice ratio")
        for scope in TARGET_SCOPES:
            scope_menu = ratio_menu.addMenu(scope.capitalize())
            for stat in SPELL_STATS:
                self._add_snippet(
                    scope_menu,
                    stat,
                    DICE_RATIO_TEMPLATE.format(scope=scope, stat=stat),
                    select="10",
                )
        self._add_snippet(menu, "Dice attack", DICE_ATTACK_TEMPLATE, select="20")

        if condition:
            operator_menu = menu.addMenu("Comparison and logic")
            for label, symbol in CONDITION_OPERATORS:
                action = operator_menu.addAction(f"{label}  ({symbol})")
                action.triggered.connect(
                    lambda _checked=False, symbol=symbol: self._insert(f" {symbol} ", spaced=False)
                )
        return menu

    def _add_snippet(self, menu: QMenu, label: str, snippet: str, select: str = "") -> None:
        action = menu.addAction(label)
        action.triggered.connect(
            lambda _checked=False: self._insert(snippet, select=select)
        )

    def _insert(self, snippet: str, *, select: str = "", spaced: bool = True) -> None:
        """
        Insert `snippet` at the cursor, replacing the selection.

        Args:
            snippet: Text to insert.
            select: Part of the snippet to leave selected, ready to be typed over.
            spaced: Whether to separate the snippet from the text before it.
        """
        edit = self.line_edit
        start = edit.selectionStart() if edit.hasSelectedText() else edit.cursorPosition()
        before = edit.text()[:start]
        if spaced and before and not before[-1].isspace() and before[-1] != "(":
            snippet = f" {snippet}"
        edit.insert(snippet)
        edit.setFocus()
        if select:
            edit.setSelection(start + snippet.index(select), len(select))
