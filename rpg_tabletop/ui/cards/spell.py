# -*- coding: utf-8 -*-

"""
Spell card module for the RPG Tabletop App.
"""

from __future__ import annotations

# import third-party modules
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QScrollArea,
    QSizePolicy,
)

# import local modules
from .base import ResourceCard
from .formula import (
    EntityStatBadge,
    FormulaPreview,
)
from rpg_tabletop.assets.spell import Spell


# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

POLICY_COLOR: dict[str, str] = {
    "instant": "#4C8ED9",
    "maintain": "#D8B84E",
    "refresh": "#58C26A",
    "delay": "#D76AAE",
    "passive": "#7F8A9B",

    # Current spell resources still use this spelling.
    "passiv": "#7F8A9B",

    "toggle": "#9B6BDB",
    "trigger": "#E35D6A",
}
EFFECT_TYPE_COLOR: dict[str, str] = {
    "damage": "#E35D6A",               # rouge
    "heal": "#58C26A",                 # vert
    "shield": "#4C8ED9",               # bleu
    "damage_reduction": "#43B6D7",     # cyan
    "damage_amplification": "#E39A4A", # orange
    "buff": "#9B6BDB",                 # violet
    "debuff": "#D76AAE",               # rose / magenta
    "control": "#D8B84E",              # or
    "other": "#7F8A9B",                # gris bleu
}


COST_COLOR = "#E39A4A"
TARGETING_COLOR = "#43B6D7"
DELAY_COLOR = "#7F8A9B"

EFFECT_BONUS_COLOR = "#58C26A"
EFFECT_MALUS_COLOR = "#E35D6A"
EFFECT_OTHER_COLOR = "#7F8A9B"


THEME = """
QLabel#SpellCardName {
    background: transparent;

    color: #F2F3F5;

    font-size: 16px;
    font-weight: 700;
}

QLabel#SpellCardDescription {
    background: transparent;

    color: #AEB6C8;

    font-size: 11px;
}

QFrame#SpellCardEffect {
    background-color: #202532;

    border: none;
    border-radius: 6px;
}
QScrollArea#SpellCardEffectsScroll {
    background: transparent;
    border: none;
}

QWidget#SpellCardEffectsContainer {
    background: transparent;
}

QScrollArea#SpellCardEffectsScroll QScrollBar:vertical {
    background: transparent;
    width: 7px;
    margin: 0px;
}

QScrollArea#SpellCardEffectsScroll QScrollBar::handle:vertical {
    background-color: #4A5262;
    border-radius: 3px;
    min-height: 20px;
}

QScrollArea#SpellCardEffectsScroll QScrollBar::handle:vertical:hover {
    background-color: #626C80;
}

QScrollArea#SpellCardEffectsScroll QScrollBar::add-line:vertical,
QScrollArea#SpellCardEffectsScroll QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollArea#SpellCardEffectsScroll QScrollBar::add-page:vertical,
QScrollArea#SpellCardEffectsScroll QScrollBar::sub-page:vertical {
    background: transparent;
}
"""


# ------------------------------------------------------------------
# Spell Card
# ------------------------------------------------------------------

class SpellCard(ResourceCard):
    """
    Preview card for a Spell.

    Layout:

        Spell name                      COST

        Description text...

        Effect
            target/stat       operator
            graphical formula

        Effect
            ...

                    TARGETING DELAY POLICY
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

        self.spell: Spell = Spell.from_name(
            resource_id
        )

        self.effect_widgets: list[QFrame] = []

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
        # Header
        # --------------------------------------------------------------

        header_layout = QHBoxLayout()

        header_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        header_layout.setSpacing(
            8
        )

        self.name_label = QLabel()

        self.name_label.setObjectName(
            "SpellCardName"
        )
        
        self.name_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        header_layout.addWidget(
            self.name_label
        )

        header_layout.addStretch(1)

        self.cost_label = QLabel()

        self._setup_tag(
            self.cost_label,
            "SpellCardCost",
            COST_COLOR,
        )

        header_layout.addWidget(
            self.cost_label
        )

        layout.addLayout(
            header_layout
        )

        # --------------------------------------------------------------
        # Description
        # --------------------------------------------------------------

        self.description_label = QLabel()

        self.description_label.setObjectName(
            "SpellCardDescription"
        )

        self.description_label.setWordWrap(
            True
        )

        self.description_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignTop
        )

        self.description_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )

        layout.addWidget(
            self.description_label
        )

        # --------------------------------------------------------------
        # Scrollable effects area
        # --------------------------------------------------------------

        self.effects_scroll = QScrollArea()

        self.effects_scroll.setObjectName(
            "SpellCardEffectsScroll"
        )

        self.effects_scroll.setWidgetResizable(
            True
        )

        self.effects_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.effects_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        self.effects_scroll.setFrameShape(
            QFrame.Shape.NoFrame
        )

        self.effects_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        self.effects_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        # --------------------------------------------------------------
        # Effects container
        # --------------------------------------------------------------

        self.effects_container = QWidget()

        self.effects_container.setObjectName(
            "SpellCardEffectsContainer"
        )

        self.effects_layout = QVBoxLayout(
            self.effects_container
        )

        self.effects_layout.setContentsMargins(
            0,
            4,
            4,
            4,
        )

        self.effects_layout.setSpacing(
            6
        )

        self.effects_layout.setAlignment(
            Qt.AlignmentFlag.AlignTop
        )

        self.effects_scroll.setWidget(
            self.effects_container
        )

        layout.addWidget(
            self.effects_scroll,
            1,
        )

        # --------------------------------------------------------------
        # Bottom metadata
        # --------------------------------------------------------------

        tags_layout = QHBoxLayout()

        tags_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        tags_layout.setSpacing(
            5
        )

        tags_layout.addStretch(1)

        self.targeting_label = QLabel()

        self._setup_tag(
            self.targeting_label,
            "SpellCardTargeting",
            TARGETING_COLOR,
        )

        self.delay_label = QLabel()

        self._setup_tag(
            self.delay_label,
            "SpellCardDelay",
            DELAY_COLOR,
        )

        self.runtime_label = QLabel()

        self._setup_tag(
            self.runtime_label,
            "SpellCardRuntime",
            "#7F8A9B",
        )

        # Group aligned right:
        #
        #    [TARGETING] [DELAY] [POLICY]
        #
        # Reading from the extreme right back toward the left:
        #
        #    POLICY <- DELAY <- TARGETING

        tags_layout.addWidget(
            self.targeting_label
        )

        tags_layout.addWidget(
            self.delay_label
        )

        tags_layout.addWidget(
            self.runtime_label
        )

        layout.addLayout(
            tags_layout
        )

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    def _setup_tag(
        self,
        label: QLabel,
        object_name: str,
        color: str,
    ) -> None:
        label.setObjectName(
            object_name
        )

        label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        self._set_tag_color(
            label,
            color,
        )
        label.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Fixed,
        )

    @staticmethod
    def _set_tag_color(
        label: QLabel,
        color: str,
    ) -> None:
        label.setStyleSheet(
            f"""
            QLabel {{
                background-color: {color};
                color: white;

                border: none;
                border-radius: 6px;

                padding: 2px 6px;

                font-size: 10px;
                font-weight: 600;
            }}
            """
        )

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        # --------------------------------------------------------------
        # Name
        # --------------------------------------------------------------

        self.name_label.setText(
            self.spell.name
        )

        # --------------------------------------------------------------
        # Cost
        # --------------------------------------------------------------

        self.cost_label.setText(
            f"{self.spell.cost} STAMINA"
        )

        # --------------------------------------------------------------
        # Description
        # --------------------------------------------------------------

        self.description_label.setText(
            self.spell.description
        )

        # --------------------------------------------------------------
        # Policy
        # --------------------------------------------------------------

        policy_name = self._value_of(
            self.spell.runtime_policy
        ).lower()

        self.runtime_label.setText(
            policy_name.upper()
        )

        self._set_tag_color(
            self.runtime_label,
            POLICY_COLOR.get(
                policy_name,
                "#7F8A9B",
            ),
        )

        # --------------------------------------------------------------
        # Delay
        # --------------------------------------------------------------

        delay = self.spell.delay

        if delay < 0:
            delay_text = "∞"
        else:
            delay_text = str(
                delay
            )

        self.delay_label.setText(
            delay_text
        )

        # --------------------------------------------------------------
        # Targeting
        # --------------------------------------------------------------

        targeting = self._value_of(
            self.spell.targeting
        )

        self.targeting_label.setText(
            targeting.upper()
        )

        # --------------------------------------------------------------
        # Effects
        # --------------------------------------------------------------

        self._refresh_effects()

    # ------------------------------------------------------------------
    # Effects
    # ------------------------------------------------------------------

    def _refresh_effects(self) -> None:
        while self.effects_layout.count():
            item = self.effects_layout.takeAt(
                0
            )

            widget = item.widget()

            if widget is not None:
                widget.deleteLater()

        self.effect_widgets.clear()

        for effect in self.spell.effects:
            effect_widget = (
                self._create_effect_widget(
                    effect
                )
            )

            self.effects_layout.addWidget(
                effect_widget
            )

            self.effect_widgets.append(
                effect_widget
            )

    def _create_effect_widget(
        self,
        effect,
    ) -> QFrame:
        operator = self._value_of(
            effect.operator
        ).lower()

        if operator == "bonus":
            accent_color = (
                EFFECT_BONUS_COLOR
            )

        elif operator in (
            "malus",
            "penalty",
        ):
            accent_color = (
                EFFECT_MALUS_COLOR
            )

        else:
            accent_color = (
                EFFECT_OTHER_COLOR
            )

        # --------------------------------------------------------------
        # Frame
        # --------------------------------------------------------------

        frame = QFrame()

        frame.setObjectName(
            "SpellCardEffect"
        )

        frame.setStyleSheet(
            f"""
            QFrame#SpellCardEffect {{
                background-color: #202532;

                border: none;
                border-left: 3px solid {accent_color};

                border-radius: 6px;
            }}
            """
        )

        layout = QVBoxLayout(
            frame
        )

        layout.setContentsMargins(
            8,
            6,
            8,
            6,
        )

        layout.setSpacing(
            5
        )

        # --------------------------------------------------------------
        # Effect header
        # --------------------------------------------------------------

        header_layout = QHBoxLayout()

        header_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        header_layout.setSpacing(
            5
        )

        target_entity, target_field = (
            self._effect_target_parts(
                effect.target
            )
        )

        target_badge = EntityStatBadge(
            target_entity,
            target_field,
        )

        header_layout.addWidget(
            target_badge
        )

        effect_type = self._value_of(
            effect.effect_type
        ).lower()

        effect_type_label = QLabel(
            effect_type
            .replace("_", " ")
            .upper()
        )

        effect_type_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        effect_type_color = EFFECT_TYPE_COLOR.get(
            effect_type,
            EFFECT_TYPE_COLOR["other"],
        )

        self._set_tag_color(
            effect_type_label,
            effect_type_color,
        )

        header_layout.addStretch(1)

        header_layout.addWidget(
            effect_type_label
        )

        layout.addLayout(
            header_layout
        )

        # --------------------------------------------------------------
        # Formula
        # --------------------------------------------------------------

        expression = (
            effect.formula.expression
        )

        formula_preview = FormulaPreview(
            expression
        )

        layout.addWidget(
            formula_preview
        )

        return frame

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _value_of(
        value,
    ) -> str:
        if hasattr(
            value,
            "value",
        ):
            return str(
                value.value
            )

        return str(
            value
        )

    @staticmethod
    def _effect_target_parts(
        target,
    ) -> tuple[str, str]:
        """
        Supports either:

            ("target", "hp")

        or:

            "target.hp"
        """
        if isinstance(
            target,
            str,
        ):
            if "." in target:
                entity, field = (
                    target.split(
                        ".",
                        1,
                    )
                )

                return (
                    entity,
                    field,
                )

            return (
                "target",
                target,
            )

        if (
            isinstance(
                target,
                (tuple, list),
            )
            and len(target) >= 2
        ):
            return (
                str(target[0]),
                str(target[1]),
            )

        return (
            "target",
            str(target),
        )

    # ------------------------------------------------------------------
    # Browser
    # ------------------------------------------------------------------

    def search_text(self) -> str:
        return " ".join(
            (
                str(
                    self.resource_id
                ),
                self.spell.name,
                self.spell.description,
            )
        )


