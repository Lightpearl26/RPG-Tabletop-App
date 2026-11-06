# -*- coding: utf-8 -*-

"""
Building blocks of the formula preview: text, stat badges, ratios, dice attacks
and formula blocks.
"""

from __future__ import annotations

from PySide6.QtCore import (
    Qt,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QWidget,
)

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
