#-*- coding: utf-8 -*-

"""
Test section of the spell editor: runs the formulas with chosen stats and dice.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import fields
from html import escape
from pathlib import Path
from typing import NamedTuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from rpg_tabletop.assets.campaign import Campaign
from rpg_tabletop.assets.character import Stats
from rpg_tabletop.assets.spell import Formula
from rpg_tabletop.core.dice import DiceAttack, DiceRatio
from rpg_tabletop.ui.cards import field_color
from .spell_widgets import SPELL_STATS, ClickableFrame


DRAWS = 1000
MAX_STAT = 9999
MIN_ROLL, MAX_ROLL = 1, 100
DEFAULT_ROLL = 50

CUSTOM = "Custom"
RANDOM = "Random"
FIXED = "Fixed"
DICE_MODES = (RANDOM, FIXED)

DEFAULT_STATS = {
    **dict.fromkeys(SPELL_STATS, 50),
    "hp": 20,
    "stamina": 100,
    "drug_health": 100,
    "mental_health": 100,
}
CASTER, TARGET = 0, 1


class StubEntity:
    """Stands in for a runtime entity: formulas only ask it for stat values."""

    def __init__(self, stats: Mapping[str, int]) -> None:
        self._stats = dict(stats)

    def get_stat(self, stat_name: str) -> int:
        """Return the value of a stat, 0 when it is unknown."""
        return self._stats.get(stat_name, 0)


class Outcome(NamedTuple):
    """Range of values a formula takes in the test context."""

    minimum: int
    average: float
    maximum: int
    draws: int

    @property
    def exact(self) -> bool:
        """Whether every draw gave the same value."""
        return self.minimum == self.maximum


def read_character_stats(path: Path) -> dict[str, int]:
    """
    Return the stat values of a character file, hit points included.

    Raises:
        ValueError: When the file cannot be read or holds no usable stats.
    """
    known = {field.name for field in fields(Stats)}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))["stats"]
        stats = Stats(**{name: int(value) for name, value in raw.items() if name in known})
    except (OSError, KeyError, TypeError, AttributeError, ValueError) as error:
        raise ValueError(f"Could not read the stats of {path.stem}: {error}") from error
    return {stat: getattr(stats, stat) for stat in SPELL_STATS}


def uses_dice(formula: Formula) -> bool:
    """Whether the compiled formula rolls dice anywhere."""
    for kind, value in formula.placeholders:
        if kind in (DiceRatio, DiceAttack) or (kind is Formula and uses_dice(value)):
            return True
    return False


def run_formula(
    formula: Formula,
    caster: StubEntity,
    target: StubEntity,
    caster_dices: dict[str, int] | None = None,
    target_dices: dict[str, int] | None = None,
) -> int | None:
    """
    Evaluate `formula` once the way the game does.

    Returns:
        The amount applied by the effect, never negative; `None` when the formula
        cannot be evaluated.
    """
    try:
        return max(0, int(formula.evaluate(caster, target, caster_dices, target_dices)))
    except (ArithmeticError, TypeError, ValueError):
        return None


def describe(outcome: Outcome | None, rolled: int | None = None) -> tuple[str, str]:
    """
    Return how a result is worded: briefly for a collapsed effect, in full elsewhere.

    Both texts are empty when the formula cannot be evaluated.
    """
    if outcome is None:
        return "", ""
    if outcome.exact:
        return str(outcome.minimum), str(outcome.minimum)

    average = round(outcome.average)
    detail = (
        f"about {average} (from {outcome.minimum} to {outcome.maximum} "
        f"over {outcome.draws} rolls)"
    )
    if rolled is not None:
        detail += f", last roll {rolled}"
    return f"~{average}", detail


class FormulaTester(QFrame):
    """Collapsible section that evaluates the formulas of a spell with chosen stats and dice."""

    # Stats, characters, dice or Roll changed: the results are out of date
    context_changed = Signal()

    def __init__(self, campaign: Campaign, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SpellTester")
        self._campaign = campaign
        self._cache: dict[str, Outcome | None] = {}
        self._rolls: dict[str, int | None] = {}
        self._rolling = False
        self._notices = ["", ""]

        self._create_widgets()
        self._build_layout()
        self._connect_signals()
        self.show_results([])
        self.refresh_characters()
        self._load_side(CASTER, notify=False)
        self._load_side(TARGET, notify=False)
        self._update_summary()
        self.set_expanded(False)

    @property
    def expanded(self) -> bool:
        """Whether the controls are shown."""
        return not self.body.isHidden()

    def set_expanded(self, expanded: bool) -> None:
        """Show or hide the controls below the title."""
        if expanded:
            self.refresh_characters()
        self.body.setVisible(expanded)
        self.chevron_label.setText("\u25BE" if expanded else "\u25B8")

    def refresh_characters(self) -> None:
        """Reread the characters of the campaign, keeping the picks that still exist."""
        names = sorted(path.stem for path in self._campaign.character_path.glob("*.json"))
        for picker in (self.caster_picker, self.target_picker):
            current = picker.currentText()
            blocker = picker.blockSignals(True)
            picker.clear()
            picker.addItem(CUSTOM)
            picker.addItems(names)
            picker.setCurrentIndex(max(0, picker.findText(current)))
            picker.blockSignals(blocker)

    def outcome(self, expression: str) -> Outcome | None:
        """
        Return the values `expression` gives with the current stats and dice.

        Returns:
            `None` when the formula cannot be read or evaluated.
        """
        if expression not in self._cache:
            self._cache[expression] = self._compute(expression)
        return self._cache[expression]

    def last_roll(self, expression: str) -> int | None:
        """Return the value of the last Roll for `expression`, `None` before any Roll."""
        if not self._rolling:
            return None
        if expression not in self._rolls:
            formula = self._readable(expression)
            caster, target = self._entities()
            self._rolls[expression] = (
                None if formula is None else run_formula(formula, caster, target)
            )
        return self._rolls[expression]

    def show_results(self, lines: Sequence[str]) -> None:
        """List the result of each effect."""
        if lines:
            self.results_label.setText("<br/>".join(escape(line) for line in lines))
        else:
            self.results_label.setText("Nothing to evaluate yet.")

    def _create_widgets(self) -> None:
        self.header = ClickableFrame()
        self.header.setObjectName("SpellTesterHeader")
        self.chevron_label = QLabel()
        self.chevron_label.setObjectName("SpellTesterChevron")
        self.summary_label = QLabel()
        self.summary_label.setObjectName("SpellTesterSummary")
        self.body = QFrame()
        self.body.setObjectName("SpellTesterBody")

        self.caster_picker = self._make_picker("Stats of the caster, written user in formulas")
        self.target_picker = self._make_picker("Stats of the target, written target in formulas")

        self.mode_input = QComboBox()
        self.mode_input.setObjectName("SpellCombo")
        self.mode_input.addItems(DICE_MODES)
        self.mode_input.setToolTip("Random rolls every die; fixed uses the values given here")
        self.fixed_dice = QWidget()
        self.fixed_dice.setObjectName("SpellTesterFixed")
        self.fixed_dice.hide()
        self.caster_roll_input = self._make_roll_input()
        self.target_roll_input = self._make_roll_input()
        self.roll_button = self._make_button("Roll", "Draw the dice once for every effect")

        self.stats_button = self._make_button("Edit stats")
        self.stats_button.setCheckable(True)
        self.reset_button = self._make_button(
            "Reset stats",
            "Load the stats of the picked characters again",
        )
        self.stats_panel = QWidget()
        self.stats_panel.setObjectName("SpellTesterStats")
        self.stats_panel.hide()
        self.caster_stats = {stat: self._make_stat_input() for stat in SPELL_STATS}
        self.target_stats = {stat: self._make_stat_input() for stat in SPELL_STATS}
        self._stat_inputs = (self.caster_stats, self.target_stats)

        self.notice_label = QLabel()
        self.notice_label.setObjectName("SpellTesterNotice")
        self.notice_label.setWordWrap(True)
        self.notice_label.hide()
        self.results_label = QLabel()
        self.results_label.setObjectName("SpellTesterResults")
        self.results_label.setTextFormat(Qt.TextFormat.RichText)
        self.results_label.setWordWrap(True)

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        title = QLabel("Test")
        title.setObjectName("SpellTesterTitle")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(12, 9, 12, 9)
        header_layout.setSpacing(8)
        header_layout.addWidget(self.chevron_label)
        header_layout.addWidget(title)
        header_layout.addWidget(self.summary_label, 1)
        layout.addWidget(self.header)

        heading = QLabel("Results")
        heading.setObjectName("SpellTesterHeading")
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(12, 2, 12, 12)
        body_layout.setSpacing(10)
        body_layout.addLayout(self._characters_row())
        body_layout.addLayout(self._dice_row())
        body_layout.addLayout(self._stats_row())
        self._build_stats_grid()
        body_layout.addWidget(self.stats_panel)
        body_layout.addWidget(self.notice_label)
        body_layout.addWidget(heading)
        body_layout.addWidget(self.results_label)
        layout.addWidget(self.body)

    def _connect_signals(self) -> None:
        self.header.clicked.connect(lambda: self.set_expanded(not self.expanded))
        self.caster_picker.currentIndexChanged.connect(lambda _index: self._load_side(CASTER))
        self.target_picker.currentIndexChanged.connect(lambda _index: self._load_side(TARGET))
        for spin_box in (*self.caster_stats.values(), *self.target_stats.values()):
            spin_box.valueChanged.connect(self._invalidate)
        self.mode_input.currentIndexChanged.connect(self._on_mode_changed)
        self.caster_roll_input.valueChanged.connect(self._invalidate)
        self.target_roll_input.valueChanged.connect(self._invalidate)
        self.roll_button.clicked.connect(self._on_roll)
        self.stats_button.toggled.connect(self._on_stats_toggled)
        self.reset_button.clicked.connect(self._on_reset)

    def _characters_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("Caster"))
        row.addWidget(self.caster_picker, 1)
        row.addSpacing(12)
        row.addWidget(QLabel("Target"))
        row.addWidget(self.target_picker, 1)
        return row

    def _dice_row(self) -> QHBoxLayout:
        fixed_layout = QHBoxLayout(self.fixed_dice)
        fixed_layout.setContentsMargins(0, 0, 0, 0)
        fixed_layout.setSpacing(8)
        fixed_layout.addWidget(QLabel("Caster dice"))
        fixed_layout.addWidget(self.caster_roll_input)
        fixed_layout.addWidget(QLabel("Target dice"))
        fixed_layout.addWidget(self.target_roll_input)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("Dice"))
        row.addWidget(self.mode_input)
        row.addWidget(self.fixed_dice)
        row.addStretch(1)
        row.addWidget(self.roll_button)
        return row

    def _stats_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.stats_button)
        row.addWidget(self.reset_button)
        row.addStretch(1)
        return row

    def _build_stats_grid(self) -> None:
        grid = QGridLayout(self.stats_panel)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        rows_per_block = (len(SPELL_STATS) + 1) // 2
        for index, stat in enumerate(SPELL_STATS):
            block, row = divmod(index, rows_per_block)
            column = block * 4
            if row == 0:
                grid.addWidget(QLabel("Caster"), 0, column + 1)
                grid.addWidget(QLabel("Target"), 0, column + 2)
            label = QLabel(stat)
            label.setStyleSheet(f"color: {field_color(stat)};")
            grid.addWidget(label, row + 1, column)
            grid.addWidget(self.caster_stats[stat], row + 1, column + 1)
            grid.addWidget(self.target_stats[stat], row + 1, column + 2)
        grid.setColumnMinimumWidth(3, 24)
        grid.setColumnStretch(7, 1)

    @staticmethod
    def _make_button(text: str, tooltip: str = "") -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("SpellTesterButton")
        button.setToolTip(tooltip)
        return button

    @staticmethod
    def _make_picker(tooltip: str) -> QComboBox:
        picker = QComboBox()
        picker.setObjectName("SpellCombo")
        picker.setToolTip(tooltip)
        return picker

    @staticmethod
    def _make_stat_input() -> QSpinBox:
        spin_box = QSpinBox()
        spin_box.setRange(0, MAX_STAT)
        spin_box.setFixedWidth(84)
        return spin_box

    @staticmethod
    def _make_roll_input() -> QSpinBox:
        spin_box = QSpinBox()
        spin_box.setRange(MIN_ROLL, MAX_ROLL)
        spin_box.setValue(DEFAULT_ROLL)
        return spin_box

    def _load_side(self, side: int, *, notify: bool = True) -> None:
        """Fill the stat boxes of the caster or of the target from the picked character."""
        name = (self.caster_picker, self.target_picker)[side].currentText()
        stats = DEFAULT_STATS
        self._notices[side] = ""
        if name != CUSTOM:
            try:
                stats = read_character_stats(self._campaign.character_path / f"{name}.json")
            except ValueError as error:
                self._notices[side] = str(error)
        self.notice_label.setText("\n".join(notice for notice in self._notices if notice))
        self.notice_label.setVisible(any(self._notices))

        for stat, spin_box in self._stat_inputs[side].items():
            blocker = spin_box.blockSignals(True)
            spin_box.setValue(stats.get(stat, 0))
            spin_box.blockSignals(blocker)
        if notify:
            self._invalidate()

    def _on_reset(self) -> None:
        self._load_side(CASTER, notify=False)
        self._load_side(TARGET, notify=False)
        self._invalidate()

    def _on_stats_toggled(self, checked: bool) -> None:
        self.stats_panel.setVisible(checked)
        self.stats_button.setText("Hide stats" if checked else "Edit stats")

    def _on_mode_changed(self, *_args: object) -> None:
        fixed = self.mode_input.currentText() == FIXED
        self.fixed_dice.setVisible(fixed)
        self.roll_button.setEnabled(not fixed)
        self._invalidate()

    def _on_roll(self) -> None:
        self._rolls.clear()
        self._rolling = True
        self.context_changed.emit()

    def _invalidate(self, *_args: object) -> None:
        """Forget every result: the stats, the dice or the characters changed."""
        self._cache.clear()
        self._rolls.clear()
        self._rolling = False
        self._update_summary()
        self.context_changed.emit()

    def _update_summary(self) -> None:
        if self.mode_input.currentText() == FIXED:
            dice = (
                f"fixed dice ({self.caster_roll_input.value()} / "
                f"{self.target_roll_input.value()})"
            )
        else:
            dice = "random dice"
        self.summary_label.setText(
            f"{self.caster_picker.currentText()} vs {self.target_picker.currentText()}, {dice}"
        )

    def _entities(self) -> tuple[StubEntity, StubEntity]:
        caster, target = (
            StubEntity({stat: spin_box.value() for stat, spin_box in inputs.items()})
            for inputs in self._stat_inputs
        )
        return caster, target

    def _fixed_dices(self) -> tuple[dict[str, int], dict[str, int]] | None:
        """The dice of every stat in fixed mode, `None` when the dice are random."""
        if self.mode_input.currentText() != FIXED:
            return None
        return (
            dict.fromkeys(SPELL_STATS, self.caster_roll_input.value()),
            dict.fromkeys(SPELL_STATS, self.target_roll_input.value()),
        )

    @staticmethod
    def _readable(expression: str) -> Formula | None:
        text = expression.strip()
        if not text:
            return None
        formula = Formula(text)
        try:
            formula.validate()
        except ValueError:
            return None
        return formula

    def _compute(self, expression: str) -> Outcome | None:
        formula = self._readable(expression)
        if formula is None:
            return None

        caster, target = self._entities()
        fixed = self._fixed_dices()
        caster_dices, target_dices = fixed if fixed is not None else (None, None)
        draws = DRAWS if fixed is None and uses_dice(formula) else 1
        values = [
            value
            for value in (
                run_formula(formula, caster, target, caster_dices, target_dices)
                for _ in range(draws)
            )
            if value is not None
        ]
        if not values:
            return None
        return Outcome(min(values), sum(values) / len(values), max(values), len(values))
