# -*- coding: utf-8 -*-

"""
Visual formula preview widgets for the RPG Tabletop App.

Examples:

    user.int
        -> [USER | INT]

    diceratio(user.int, 36)
        -> [USER | INT][36%]

    diceattack(user.dex, 20, target.con, 20)
        -> [USER | DEX][20%] ⚔ [TARGET | CON][20%]

Top-level additions/subtractions are grouped into atomic visual blocks:

    10
    + diceratio(user.int, 36)
    + diceratio(user.dex, 14)
    - diceratio(target.wis, 34)

becomes:

    [10]
    [+ [USER | INT][36%]]
    [+ [USER | DEX][14%]]
    [- [TARGET | WIS][34%]]

FormulaPreview places each additive term on its own row. Each term remains
horizontal and atomic, so its operator cannot be separated from its expression.
"""

from __future__ import annotations

# import built-in modules
import ast

# import third-party modules
from PySide6.QtCore import (
    QPoint,
    QRect,
    QSize,
    Qt,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QSizePolicy,
    QWidget,
)

# import local modules
from rpg_tabletop.ui.theme.palette import field_color


# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

ENTITY_COLOR = "#4A5262"

TEXT_COLOR = "#D9DEE8"
OPERATOR_COLOR = "#B7BFCE"

RATIO_BACKGROUND = "#171B24"

ATTACK_COLOR = "#E39A4A"
ATTACK_BACKGROUND = "#342B28"


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


# ------------------------------------------------------------------
# Formula Text
# ------------------------------------------------------------------

class FormulaText(QLabel):
    """
    Plain textual element inside a formula block.
    """

    def __init__(
        self,
        text: str,
        *,
        operator: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            text,
            parent,
        )

        self.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Fixed,
        )

        if operator:
            self.setStyleSheet(
                f"""
                QLabel {{
                    background: transparent;
                    color: {OPERATOR_COLOR};

                    padding: 1px;

                    font-size: 11px;
                    font-weight: 700;
                }}
                """
            )

        else:
            self.setStyleSheet(
                f"""
                QLabel {{
                    background: transparent;
                    color: {TEXT_COLOR};

                    padding: 1px;

                    font-size: 11px;
                    font-weight: 600;
                }}
                """
            )


# ------------------------------------------------------------------
# Entity / Stat Badge
# ------------------------------------------------------------------

class EntityStatBadge(QFrame):
    """
    Visual representation of an entity field.

        [ USER   | INT ]
        [ TARGET | CON ]
    """

    def __init__(
        self,
        entity: str,
        field: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            parent
        )

        self.entity = entity
        self.field = field

        self.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Fixed,
        )

        layout = QHBoxLayout(
            self
        )

        layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        layout.setSpacing(
            0
        )

        # --------------------------------------------------------------
        # Entity
        # --------------------------------------------------------------

        entity_label = QLabel(
            entity.upper()
        )

        entity_label.setStyleSheet(
            f"""
            QLabel {{
                background-color: {ENTITY_COLOR};
                color: #E5E7EB;

                border-top-left-radius: 5px;
                border-bottom-left-radius: 5px;

                padding: 2px 5px;

                font-size: 9px;
                font-weight: 600;
            }}
            """
        )

        # --------------------------------------------------------------
        # Field
        # --------------------------------------------------------------

        color = field_color(
            field
        )

        field_label = QLabel(
            field
            .replace("_", " ")
            .upper()
        )

        field_label.setStyleSheet(
            f"""
            QLabel {{
                background-color: {color};
                color: white;

                border-top-right-radius: 5px;
                border-bottom-right-radius: 5px;

                padding: 2px 6px;

                font-size: 9px;
                font-weight: 700;
            }}
            """
        )

        layout.addWidget(
            entity_label
        )

        layout.addWidget(
            field_label
        )


# ------------------------------------------------------------------
# Ratio Badge
# ------------------------------------------------------------------

class RatioBadge(QLabel):
    """
    Percentage badge.

        [36%]
    """

    def __init__(
        self,
        ratio: str,
        color: str,
        parent: QWidget | None = None,
    ) -> None:
        ratio_text = str(
            ratio
        )

        if not ratio_text.endswith(
            "%"
        ):
            ratio_text += "%"

        super().__init__(
            ratio_text,
            parent,
        )

        self.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        self.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Fixed,
        )

        self.setStyleSheet(
            f"""
            QLabel {{
                background-color: {RATIO_BACKGROUND};
                color: {color};

                border: 1px solid {color};
                border-radius: 5px;

                padding: 1px 5px;

                font-size: 9px;
                font-weight: 700;
            }}
            """
        )


# ------------------------------------------------------------------
# Ratio Term
# ------------------------------------------------------------------

class RatioTermWidget(QFrame):
    """
    Atomic representation of:

        diceratio(entity.stat, ratio)

    Display:

        [ENTITY | STAT][RATIO%]
    """

    def __init__(
        self,
        entity: str,
        field: str,
        ratio: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            parent
        )

        self.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Fixed,
        )

        layout = QHBoxLayout(
            self
        )

        layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        # The percentage visually belongs to the stat.
        layout.setSpacing(
            2
        )

        layout.addWidget(
            EntityStatBadge(
                entity,
                field,
            )
        )

        layout.addWidget(
            RatioBadge(
                ratio,
                field_color(field),
            )
        )


# ------------------------------------------------------------------
# Dice Attack
# ------------------------------------------------------------------

class DiceAttackWidget(QFrame):
    """
    Representation of:

        diceattack(
            user.dex,
            20,
            target.con,
            20,
        )

    Display:

        [USER | DEX][20%] ⚔ [TARGET | CON][20%]
    """

    def __init__(
        self,
        left_entity: str,
        left_field: str,
        left_ratio: str,
        right_entity: str,
        right_field: str,
        right_ratio: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            parent
        )

        self.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Fixed,
        )

        layout = QHBoxLayout(
            self
        )

        layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        layout.setSpacing(
            5
        )

        # Left side
        layout.addWidget(
            RatioTermWidget(
                left_entity,
                left_field,
                left_ratio,
            )
        )

        # Attack symbol
        attack_label = QLabel(
            "⚔"
        )

        attack_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        attack_label.setStyleSheet(
            f"""
            QLabel {{
                background-color: {ATTACK_BACKGROUND};
                color: {ATTACK_COLOR};

                border-radius: 5px;

                padding: 2px 5px;

                font-size: 10px;
                font-weight: 700;
            }}
            """
        )

        layout.addWidget(
            attack_label
        )

        # Right side
        layout.addWidget(
            RatioTermWidget(
                right_entity,
                right_field,
                right_ratio,
            )
        )


# ------------------------------------------------------------------
# Formula Block
# ------------------------------------------------------------------

class FormulaBlock(QFrame):
    """
    Atomic top-level formula term.

    Examples:

        [10]

        [+ [USER | INT][36%]]

        [- [TARGET | WIS][34%]]

    FormulaPreview places each block on its own row and keeps its operator
    attached to the expression.
    """

    def __init__(
        self,
        operator: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            parent
        )

        self.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Fixed,
        )

        self.layout = QHBoxLayout(
            self
        )

        self.layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        self.layout.setSpacing(
            4
        )

        if operator:
            self.layout.addWidget(
                FormulaText(
                    operator,
                    operator=True,
                )
            )

    def add_widget(
        self,
        widget: QWidget,
    ) -> None:
        self.layout.addWidget(
            widget
        )

    def add_text(
        self,
        text: str,
        *,
        operator: bool = False,
    ) -> None:
        self.layout.addWidget(
            FormulaText(
                text,
                operator=operator,
            )
        )


# ------------------------------------------------------------------
# Formula Preview
# ------------------------------------------------------------------

class FormulaPreview(QWidget):
    """
    Graphical representation of a Formula.

    Top-level + and - operations are split into logical blocks.

    Example:

        10
        + diceratio(user.int, 36)
        + diceratio(user.dex, 14)
        - diceratio(target.wis, 34)

    produces four independent rows.
    """

    _OPERATOR_TEXT = {
        ast.Add: "+",
        ast.Sub: "−",
        ast.Mult: "×",
        ast.Div: "÷",
    }

    _PRECEDENCE = {
        ast.Add: 10,
        ast.Sub: 10,
        ast.Mult: 20,
        ast.Div: 20,
    }

    def __init__(
        self,
        expression: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            parent
        )

        self._expression = ""

        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )

        self._layout = QVBoxLayout(self)

        self._layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        self._layout.setSpacing(
            3
        )

        self._layout.setAlignment(
            Qt.AlignmentFlag.AlignTop
            | Qt.AlignmentFlag.AlignLeft
        )

        if expression:
            self.set_expression(
                expression
            )

    # ------------------------------------------------------------------
    # Size
    # ------------------------------------------------------------------

    def sizeHint(self) -> QSize:
        """
        Let the internal vertical layout define the preview height.
        """
        return self._layout.sizeHint()

    def minimumSizeHint(self) -> QSize:
        return self._layout.minimumSize()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def expression(
        self,
    ) -> str:
        return self._expression

    def set_expression(
        self,
        expression: str,
    ) -> None:
        """
        Parse and rebuild the visual formula.
        """
        self._expression = expression

        self._clear()

        try:
            tree = ast.parse(
                expression,
                mode="eval",
            )

        except SyntaxError:
            self._add_fallback_block(
                expression
            )
            self._layout.invalidate()
            self.updateGeometry()

            return

        # Split the root expression into additive logical terms.
        terms: list[
            tuple[int, ast.AST]
        ] = []

        self._split_additive_terms(
            tree.body,
            1,
            terms,
        )

        for index, (
            sign,
            node,
        ) in enumerate(
            terms
        ):
            # First positive term doesn't need a '+' prefix.
            if index == 0 and sign > 0:
                operator = None

            elif sign > 0:
                operator = "+"

            else:
                operator = "−"

            block = FormulaBlock(
                operator
            )

            self._render_inline_node(
                block,
                node,
            )

            self._layout.addWidget(
                block
            )

        self._layout.invalidate()
        self._layout.activate()
        self.updateGeometry()

    # ------------------------------------------------------------------
    # Clear
    # ------------------------------------------------------------------

    def _clear(
        self,
    ) -> None:
        while self._layout.count():
            item = self._layout.takeAt(
                0
            )

            widget = item.widget()

            if widget is not None:
                widget.deleteLater()

    # ------------------------------------------------------------------
    # Top-level additive splitting
    # ------------------------------------------------------------------

    def _split_additive_terms(
        self,
        node: ast.AST,
        sign: int,
        result: list[
            tuple[int, ast.AST]
        ],
    ) -> None:
        """
        Flatten top-level additions and subtractions.

        Example:

            a + b - c

        becomes:

            (+1, a)
            (+1, b)
            (-1, c)

        This also handles:

            a - (b + c)

        as:

            (+1, a)
            (-1, b)
            (-1, c)
        """

        if isinstance(
            node,
            ast.BinOp,
        ):
            if isinstance(
                node.op,
                ast.Add,
            ):
                self._split_additive_terms(
                    node.left,
                    sign,
                    result,
                )

                self._split_additive_terms(
                    node.right,
                    sign,
                    result,
                )

                return

            if isinstance(
                node.op,
                ast.Sub,
            ):
                self._split_additive_terms(
                    node.left,
                    sign,
                    result,
                )

                self._split_additive_terms(
                    node.right,
                    -sign,
                    result,
                )

                return

        if (
            isinstance(
                node,
                ast.UnaryOp,
            )
            and isinstance(
                node.op,
                ast.USub,
            )
        ):
            self._split_additive_terms(
                node.operand,
                -sign,
                result,
            )

            return

        result.append(
            (
                sign,
                node,
            )
        )

    # ------------------------------------------------------------------
    # Inline renderer inside a block
    # ------------------------------------------------------------------

    def _render_inline_node(
        self,
        block: FormulaBlock,
        node: ast.AST,
        parent_operator: ast.operator | None = None,
        *,
        right_child: bool = False,
    ) -> None:
        """
        Render one AST expression inside a FormulaBlock.

        Unlike the outer FormulaPreview, this renderer does not split at
        + / -. Everything here belongs to the same logical block.
        """

        # --------------------------------------------------------------
        # Binary operation
        # --------------------------------------------------------------

        if isinstance(
            node,
            ast.BinOp,
        ):
            parentheses = self._needs_parentheses(
                node,
                parent_operator,
                right_child=right_child,
            )

            if parentheses:
                block.add_text(
                    "(",
                    operator=True,
                )

            self._render_inline_node(
                block,
                node.left,
                node.op,
                right_child=False,
            )

            operator = self._OPERATOR_TEXT.get(
                type(node.op)
            )

            if operator is None:
                block.add_text(
                    ast.unparse(node)
                )

                if parentheses:
                    block.add_text(
                        ")",
                        operator=True,
                    )

                return

            block.add_text(
                operator,
                operator=True,
            )

            self._render_inline_node(
                block,
                node.right,
                node.op,
                right_child=True,
            )

            if parentheses:
                block.add_text(
                    ")",
                    operator=True,
                )

            return

        # --------------------------------------------------------------
        # Unary operation
        # --------------------------------------------------------------

        if isinstance(
            node,
            ast.UnaryOp,
        ):
            if isinstance(
                node.op,
                ast.USub,
            ):
                block.add_text(
                    "−",
                    operator=True,
                )

            elif isinstance(
                node.op,
                ast.UAdd,
            ):
                block.add_text(
                    "+",
                    operator=True,
                )

            else:
                block.add_text(
                    ast.unparse(node)
                )

                return

            self._render_inline_node(
                block,
                node.operand,
            )

            return

        # --------------------------------------------------------------
        # Calls
        # --------------------------------------------------------------

        if isinstance(
            node,
            ast.Call,
        ):
            widget = self._create_call_widget(
                node
            )

            if widget is not None:
                block.add_widget(
                    widget
                )

            else:
                block.add_text(
                    ast.unparse(node)
                )

            return

        # --------------------------------------------------------------
        # Entity field
        # --------------------------------------------------------------

        if isinstance(
            node,
            ast.Attribute,
        ):
            parts = self._attribute_parts(
                node
            )

            if parts is not None:
                entity, field = parts

                block.add_widget(
                    EntityStatBadge(
                        entity,
                        field,
                    )
                )

            else:
                block.add_text(
                    ast.unparse(node)
                )

            return

        # --------------------------------------------------------------
        # Constant
        # --------------------------------------------------------------

        if isinstance(
            node,
            ast.Constant,
        ):
            block.add_text(
                str(node.value)
            )

            return

        # --------------------------------------------------------------
        # Name
        # --------------------------------------------------------------

        if isinstance(
            node,
            ast.Name,
        ):
            block.add_text(
                node.id
            )

            return

        # --------------------------------------------------------------
        # Fallback
        # --------------------------------------------------------------

        block.add_text(
            ast.unparse(node)
        )

    # ------------------------------------------------------------------
    # Calls
    # ------------------------------------------------------------------

    def _create_call_widget(
        self,
        node: ast.Call,
    ) -> QWidget | None:
        if not isinstance(
            node.func,
            ast.Name,
        ):
            return None

        function_name = (
            node.func.id.lower()
        )

        if function_name == "diceratio":
            return self._create_diceratio(
                node
            )

        if function_name == "diceattack":
            return self._create_diceattack(
                node
            )

        return None

    def _create_diceratio(
        self,
        node: ast.Call,
    ) -> QWidget | None:
        """
        diceratio(user.int, 36)

        ->

        [USER | INT][36%]
        """

        if len(node.args) != 2:
            return None

        source = self._attribute_parts(
            node.args[0]
        )

        if source is None:
            return None

        entity, field = source

        ratio = self._simple_expression(
            node.args[1]
        )

        return RatioTermWidget(
            entity,
            field,
            ratio,
        )

    def _create_diceattack(
        self,
        node: ast.Call,
    ) -> QWidget | None:
        """
        diceattack(
            user.dex,
            20,
            target.con,
            20,
        )

        ->

        [USER | DEX][20%] ⚔ [TARGET | CON][20%]
        """

        if len(node.args) != 4:
            return None

        left = self._attribute_parts(
            node.args[0]
        )

        right = self._attribute_parts(
            node.args[2]
        )

        if (
            left is None
            or right is None
        ):
            return None

        left_entity, left_field = left
        right_entity, right_field = right

        left_ratio = self._simple_expression(
            node.args[1]
        )

        right_ratio = self._simple_expression(
            node.args[3]
        )

        return DiceAttackWidget(
            left_entity,
            left_field,
            left_ratio,
            right_entity,
            right_field,
            right_ratio,
        )

    # ------------------------------------------------------------------
    # AST helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _attribute_parts(
        node: ast.AST,
    ) -> tuple[str, str] | None:
        if not isinstance(
            node,
            ast.Attribute,
        ):
            return None

        if not isinstance(
            node.value,
            ast.Name,
        ):
            return None

        return (
            node.value.id,
            node.attr,
        )

    @staticmethod
    def _simple_expression(
        node: ast.AST,
    ) -> str:
        if isinstance(
            node,
            ast.Constant,
        ):
            return str(
                node.value
            )

        return ast.unparse(
            node
        )

    def _needs_parentheses(
        self,
        node: ast.BinOp,
        parent_operator: ast.operator | None,
        *,
        right_child: bool,
    ) -> bool:
        if parent_operator is None:
            return False

        child_precedence = (
            self._PRECEDENCE.get(
                type(node.op),
                100,
            )
        )

        parent_precedence = (
            self._PRECEDENCE.get(
                type(parent_operator),
                100,
            )
        )

        if child_precedence < parent_precedence:
            return True

        if (
            right_child
            and child_precedence
            == parent_precedence
            and isinstance(
                parent_operator,
                (
                    ast.Sub,
                    ast.Div,
                ),
            )
        ):
            return True

        return False

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    def _add_fallback_block(
        self,
        text: str,
    ) -> None:
        block = FormulaBlock()

        label = QLabel(
            text
        )

        label.setStyleSheet(
            """
            QLabel {
                background-color: #171B24;
                color: #C7CDD8;

                border-radius: 4px;

                padding: 2px 4px;

                font-family:
                    Consolas,
                    "Courier New",
                    monospace;

                font-size: 10px;
            }
            """
        )

        block.add_widget(
            label
        )

        self._layout.addWidget(
            block
        )


