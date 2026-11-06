"""Spell resource editor."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, get_args

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from ...assets.campaign import Campaign
from ...assets.spell import DEFAULT_AREA_SIZE, Formula, RuntimePolicy, Spell, Targeting
from .base import INVALID_FILENAME_CHARACTERS, ResourceEditor
from .formula_tester import FormulaTester, describe
from .spell_effect import ERROR, WARNING, EffectEditor, Issue, issues_html
from .spell_widgets import FormulaField, ValueComboBox, set_style_property


TARGETING_VALUES = get_args(Targeting)
POLICIES = tuple(policy.value for policy in RuntimePolicy)
# Policies that read the `delay` field, as a duration or as a wait before the effects
DELAY_POLICIES = ("maintain", "refresh", "delay", "passiv")
POLICY_HINTS = {
    "instant": "Applied once when cast.",
    "maintain": "Cost paid every turn for the duration; new targets receive the effects.",
    "refresh": "Cost paid every turn for the duration; effects are recomputed every turn.",
    "delay": "Cost paid on cast; effects apply once after the delay.",
    "passiv": "Cost paid once; effects apply every turn for the duration.",
    "toggle": "Cost paid once; effects stay until the spell is stopped.",
    "trigger": "Cost paid once; effects apply when the condition becomes true.",
}
MAX_VALUE = 9999
# Wait after the last edit before the test results are computed again
TEST_DELAY_MS = 150


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


class SpellEditor(ResourceEditor):
    """Editable spell definition backed by its campaign JSON file."""

    resource_label = "Spell"
    resource_type = "spells"

    def __init__(
        self,
        resource_id: str = "",
        campaign: Campaign | None = None,
        *,
        is_new: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(resource_id, campaign, is_new=is_new, parent=parent)
        self.effect_rows: list[EffectEditor] = []
        self._loading = False
        self._test_timer = QTimer(self)
        self._test_timer.setSingleShot(True)
        self._test_timer.setInterval(TEST_DELAY_MS)
        self._test_timer.timeout.connect(self._refresh_test_results)
        self._create_widgets()
        self._build_layout()
        self._connect_signals()
        self._populate_fields()
        self._mark_clean()

    @property
    def directory(self) -> Path:
        return self.campaign.spell_path

    @property
    def spell_name(self) -> str:
        """
        The name of the spell as entered in the editor.

        Returns:
            str: The trimmed spell name.
        """
        return self.name_input.text().strip()

    def issues(self) -> list[Issue]:
        """Return every problem of the spell: errors block saving, warnings do not."""
        name, casting, effects = self._issue_groups()
        return [*name, *casting, *(issue for issue, _row in effects)]

    def save(self) -> Path:
        """
        Write the spell to disk and return the file path.

        Raises:
            ValueError: With the first error found, after opening the effect it belongs to.
        """
        failure = self._first_error()
        if failure is not None:
            issue, row = failure
            if row is not None:
                self._expand_only(row)
                self._scroll_to(row)
            raise ValueError(issue.message)

        data = self._collect_data()
        try:
            Spell.from_dict(data)
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError(f"The spell cannot be loaded: {error}") from error
        path = self._write(data, str(data["name"]))
        self._update_issues()
        return path

    def _new_data(self) -> dict:
        return {
            "name": "",
            "description": "",
            "cost": 0,
            "effects": [],
            "targeting": "single",
            "runtime_policy": "instant",
        }

    def _create_widgets(self) -> None:
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("SpellEditorScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        self.name_input = QLineEdit()
        self.name_input.setObjectName("SpellNameField")
        self.name_input.setPlaceholderText("Spell name")
        self.cost_input = self._make_spin_box(" stamina")
        self.name_issues_label = self._make_issues_label()
        self.description_input = QPlainTextEdit()
        self.description_input.setObjectName("SpellDescriptionField")
        self.description_input.setPlaceholderText("What does this spell do?")
        self.description_input.setMinimumHeight(80)
        self.description_input.setMaximumHeight(160)

        self.targeting_input = ValueComboBox(TARGETING_VALUES)
        self.area_label = QLabel("Area size")
        self.area_input = self._make_spin_box(" cells")
        self.area_input.setToolTip(
            "Radius around the chosen cell: every cell at that distance or closer, "
            "diagonals counting as one."
        )
        self.max_targets_label = QLabel("Max targets")
        self.max_targets_input = self._make_spin_box("")
        self.max_targets_input.setSpecialValueText("No limit")
        self.policy_input = ValueComboBox(POLICIES)
        self.policy_hint = QLabel()
        self.policy_hint.setObjectName("SpellEditorHint")
        self.policy_hint.setWordWrap(True)
        self.delay_label = QLabel("Duration (turns)")
        self.delay_input = self._make_spin_box(" turns")
        self.delay_input.setObjectName("SpellDelayField")
        self.trigger_label = QLabel("Trigger condition")
        self.trigger_input = FormulaField("user.hp <= 20", condition=True)
        self.casting_issues_label = self._make_issues_label()

        self.effects_group = QGroupBox("Effects (0)")
        self.effects_group.setObjectName("SpellEditorSection")
        self.add_effect_button = QPushButton("Add effect")
        self.add_effect_button.setObjectName("SpellAddEffectButton")
        self.no_effects_label = QLabel("No effects yet.")
        self.no_effects_label.setObjectName("SpellEditorHint")
        self.effects_container = QWidget()
        self.effects_container.setObjectName("SpellEffectsList")
        self.effects_layout = QVBoxLayout(self.effects_container)
        self.effects_layout.setContentsMargins(0, 0, 0, 0)
        self.effects_layout.setSpacing(6)

        self.tester = FormulaTester(self.campaign)
        self.issue_label = QLabel()
        self.issue_label.setObjectName("SpellEditorIssues")
        self.save_button = QPushButton("Save Spell")
        self.save_button.setObjectName("SpellEditorSaveButton")

    def _build_layout(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        content = QWidget()
        content.setObjectName("SpellEditorContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(14)
        self.scroll_area.setWidget(content)
        outer.addWidget(self.scroll_area, 1)

        identity = QHBoxLayout()
        identity.setSpacing(10)
        identity.addWidget(QLabel("Name"))
        identity.addWidget(self.name_input, 1)
        identity.addSpacing(8)
        identity.addWidget(QLabel("Cost"))
        identity.addWidget(self.cost_input)
        layout.addLayout(identity)
        layout.addWidget(self.name_issues_label)

        description_group = QGroupBox("Description")
        description_group.setObjectName("SpellEditorSection")
        description_layout = QVBoxLayout(description_group)
        description_layout.addWidget(self.description_input)
        layout.addWidget(description_group)

        layout.addWidget(self._casting_group())
        layout.addWidget(self._effects_section())
        layout.addWidget(self.tester)
        layout.addStretch(1)

        footer = QFrame()
        footer.setObjectName("SpellEditorFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 10, 24, 14)
        footer_layout.addWidget(self.issue_label, 1)
        footer_layout.addWidget(self.save_button)
        outer.addWidget(footer)

    def _connect_signals(self) -> None:
        self.name_input.textChanged.connect(self._on_edited)
        self.cost_input.valueChanged.connect(self._on_edited)
        self.description_input.textChanged.connect(self._on_edited)
        self.targeting_input.currentIndexChanged.connect(self._on_targeting_changed)
        self.area_input.valueChanged.connect(self._on_edited)
        self.max_targets_input.valueChanged.connect(self._on_edited)
        self.policy_input.currentIndexChanged.connect(self._on_policy_changed)
        self.delay_input.valueChanged.connect(self._on_edited)
        self.trigger_input.textChanged.connect(self._on_edited)
        self.add_effect_button.clicked.connect(self._add_effect)
        self.save_button.clicked.connect(self.save_requested)
        self.tester.context_changed.connect(self._schedule_test_refresh)

    def _casting_group(self) -> QGroupBox:
        group = QGroupBox("Casting")
        group.setObjectName("SpellEditorSection")
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.addWidget(QLabel("Targeting"), 0, 0)
        grid.addWidget(self.targeting_input, 0, 1)
        grid.addWidget(QLabel("Policy"), 0, 2)
        grid.addWidget(self.policy_input, 0, 3)
        grid.addWidget(self.area_label, 1, 0)
        grid.addWidget(self.area_input, 1, 1)
        grid.addWidget(self.max_targets_label, 1, 2)
        grid.addWidget(self.max_targets_input, 1, 3)
        grid.addWidget(self.policy_hint, 2, 0, 1, 4)
        grid.addWidget(self.delay_label, 3, 0)
        grid.addWidget(self.delay_input, 3, 1)
        grid.addWidget(self.trigger_label, 4, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.trigger_input, 4, 1, 1, 3)
        grid.addWidget(self.casting_issues_label, 5, 0, 1, 4)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        return group

    def _effects_section(self) -> QGroupBox:
        layout = QVBoxLayout(self.effects_group)
        layout.setSpacing(8)
        controls = QHBoxLayout()
        controls.addStretch(1)
        controls.addWidget(self.add_effect_button)
        layout.addLayout(controls)
        layout.addWidget(self.no_effects_label)
        layout.addWidget(self.effects_container)
        return self.effects_group

    @staticmethod
    def _make_spin_box(suffix: str) -> QSpinBox:
        spin_box = QSpinBox()
        spin_box.setRange(0, MAX_VALUE)
        spin_box.setSuffix(suffix)
        return spin_box

    @staticmethod
    def _make_issues_label() -> QLabel:
        label = QLabel()
        label.setObjectName("SpellEditorIssueList")
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        label.hide()
        return label

    def _populate_fields(self) -> None:
        self._loading = True
        try:
            data = self._data
            self.name_input.setText(str(data.get("name") or ""))
            self.description_input.setPlainText(str(data.get("description") or ""))
            self.cost_input.setValue(_as_int(data.get("cost"), 0))
            self.targeting_input.set_value(str(data.get("targeting") or "single"))
            self.area_input.setValue(_as_int(data.get("area_size"), DEFAULT_AREA_SIZE))
            self.max_targets_input.setValue(_as_int(data.get("max_targets"), 0))
            self.policy_input.set_value(str(data.get("runtime_policy") or "instant"))
            self.delay_input.setValue(_as_int(data.get("delay"), 1))
            self.trigger_input.set_text(str(data.get("trigger_condition") or ""))
            effects = data.get("effects", [])
            self._set_effects(effects if isinstance(effects, list) else [])
        finally:
            self._loading = False
        self._sync_policy_widgets()
        self._sync_targeting_widgets()
        self._update_issues()
        self._refresh_test_results()

    def _collect_data(self) -> dict:
        data = deepcopy(self._data)
        data["name"] = self.spell_name
        data["description"] = self.description_input.toPlainText()
        data["cost"] = self.cost_input.value()
        data["effects"] = [row.data() for row in self.effect_rows]
        data["targeting"] = self.targeting_input.value
        data["runtime_policy"] = self.policy_input.value
        # Like the delay, a stored value is kept when the targeting does not read it
        if self.targeting_input.value == "area" or "area_size" in self._data:
            data["area_size"] = self.area_input.value()
        if self.max_targets_input.value() > 0:
            data["max_targets"] = self.max_targets_input.value()
        else:
            data.pop("max_targets", None)

        # A stored delay is kept even when the policy does not read it
        if self.policy_input.value in DELAY_POLICIES or "delay" in self._data:
            data["delay"] = self.delay_input.value()
        condition = self.trigger_input.text().strip()
        if condition:
            data["trigger_condition"] = condition
        else:
            data.pop("trigger_condition", None)
        return data

    def _on_edited(self, *_args: object) -> None:
        if self._loading:
            return
        self._update_issues()
        self._refresh_modified()

    def _schedule_test_refresh(self, *_args: object) -> None:
        self._test_timer.start()

    def _refresh_test_results(self) -> None:
        """Evaluate every effect in the test context and show the results."""
        lines = []
        for row in self.effect_rows:
            expression = row.formula_field.text().strip()
            outcome = self.tester.outcome(expression)
            short, detail = describe(outcome, self.tester.last_roll(expression))
            row.show_test(short, detail)
            if outcome is not None:
                lines.append(f"Effect {row.position + 1}: {row.heading()} {detail}")
        self.tester.show_results(lines)

    def _on_policy_changed(self, *_args: object) -> None:
        if self._loading:
            return
        self._sync_policy_widgets()
        self._on_edited()

    def _on_targeting_changed(self, *_args: object) -> None:
        if self._loading:
            return
        self._sync_targeting_widgets()
        self._on_edited()

    def _sync_targeting_widgets(self) -> None:
        """Area size for area spells, max targets for multi-target spells."""
        targeting = self.targeting_input.value
        for widget in (self.area_label, self.area_input):
            widget.setEnabled(targeting == "area")
        for widget in (self.max_targets_label, self.max_targets_input):
            widget.setEnabled(targeting == "multi")

    def _sync_policy_widgets(self) -> None:
        """Show what the selected policy uses and grey out the rest."""
        policy = self.policy_input.value
        uses_delay = policy in DELAY_POLICIES
        self.delay_label.setText("Delay (turns)" if policy == "delay" else "Duration (turns)")
        self.delay_label.setEnabled(uses_delay)
        self.delay_input.setEnabled(uses_delay)
        self.policy_hint.setText(
            POLICY_HINTS.get(policy, "Unknown policy: the game cannot cast this spell.")
        )
        # A condition left over from another policy stays visible so it can be removed
        show_trigger = policy == "trigger" or bool(self.trigger_input.text().strip())
        self.trigger_label.setVisible(show_trigger)
        self.trigger_input.setVisible(show_trigger)

    def _name_issues(self) -> list[Issue]:
        name = self.spell_name
        if not name:
            return [Issue(ERROR, f"{self.resource_label} name is required.")]
        if any(character in INVALID_FILENAME_CHARACTERS for character in name):
            return [
                Issue(
                    ERROR,
                    f"{self.resource_label} name contains a character invalid in file names.",
                )
            ]
        return []

    def _casting_issues(self) -> list[Issue]:
        issues: list[Issue] = []
        policy = self.policy_input.value
        if not self.policy_input.is_known:
            issues.append(Issue(ERROR, f"Unknown runtime policy '{policy}'."))
        if not self.targeting_input.is_known:
            issues.append(
                Issue(WARNING, f"Unknown targeting '{self.targeting_input.value}': kept as stored.")
            )

        condition = self.trigger_input.text().strip()
        if policy == "trigger":
            if not condition:
                issues.append(Issue(ERROR, "The trigger policy needs a trigger condition."))
            else:
                try:
                    Formula(condition).validate()
                except ValueError as error:
                    issues.append(Issue(ERROR, f"Trigger condition: {error}"))
        elif condition:
            issues.append(
                Issue(WARNING, "The trigger condition is ignored unless the policy is trigger.")
            )

        if policy in DELAY_POLICIES and "delay" not in self._data:
            noun = "delay" if policy == "delay" else "duration"
            issues.append(
                Issue(
                    WARNING,
                    f"No {noun} is stored yet: {self.delay_input.value()} will be saved.",
                )
            )
        return issues

    def _issue_groups(
        self,
    ) -> tuple[list[Issue], list[Issue], list[tuple[Issue, EffectEditor]]]:
        effects = [
            (Issue(issue.severity, f"Effect {row.position + 1}: {issue.message}"), row)
            for row in self.effect_rows
            for issue in row.issues()
        ]
        return self._name_issues(), self._casting_issues(), effects

    def _first_error(self) -> tuple[Issue, EffectEditor | None] | None:
        name, casting, effects = self._issue_groups()
        for issue in (*name, *casting):
            if issue.severity == ERROR:
                return issue, None
        for issue, row in effects:
            if issue.severity == ERROR:
                return issue, row
        return None

    def _update_issues(self) -> None:
        name, casting, effects = self._issue_groups()
        self._show_issues(self.name_issues_label, name)
        self._show_issues(self.casting_issues_label, casting)

        issues = [*name, *casting, *(issue for issue, _row in effects)]
        errors = sum(issue.severity == ERROR for issue in issues)
        warnings = len(issues) - errors
        if not issues:
            text, severity = "No issues", "ok"
        else:
            parts = []
            if errors:
                parts.append(f"{errors} error{'s' if errors != 1 else ''}")
            if warnings:
                parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
            text = ", ".join(parts)
            severity = ERROR if errors else WARNING
        self.issue_label.setText(text)
        self.issue_label.setToolTip("\n".join(issue.message for issue in issues))
        set_style_property(self.issue_label, "severity", severity)

    @staticmethod
    def _show_issues(label: QLabel, issues: list[Issue]) -> None:
        label.setText(issues_html(issues))
        label.setVisible(bool(issues))

    def _set_effects(self, effects: list) -> None:
        for row in self.effect_rows:
            self.effects_layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.effect_rows = []
        for effect in effects:
            if isinstance(effect, dict):
                self._insert_effect_row(len(self.effect_rows), effect)
        self._after_structure_change()

    def _insert_effect_row(self, index: int, data: dict | None) -> EffectEditor:
        row = EffectEditor(data)
        row.changed.connect(self._on_edited)
        row.changed.connect(self._schedule_test_refresh)
        row.toggle_requested.connect(self._on_effect_toggled)
        row.move_requested.connect(self._move_effect)
        row.duplicate_requested.connect(self._duplicate_effect)
        row.remove_requested.connect(self._remove_effect)
        self.effects_layout.insertWidget(index, row)
        self.effect_rows.insert(index, row)
        return row

    def _after_structure_change(self) -> None:
        count = len(self.effect_rows)
        for index, row in enumerate(self.effect_rows):
            row.set_position(index, count)
        self.effects_group.setTitle(f"Effects ({count})")
        self.no_effects_label.setVisible(count == 0)

    def _on_structure_edited(self) -> None:
        self._after_structure_change()
        self._schedule_test_refresh()
        self._on_edited()

    def _expand_only(self, row: EffectEditor | None) -> None:
        """Open `row` and close the others; `None` closes them all."""
        for other in self.effect_rows:
            other.set_expanded(other is row)

    def _scroll_to(self, row: EffectEditor) -> None:
        def reveal() -> None:
            if isValid(row):
                self.scroll_area.ensureWidgetVisible(row)

        QTimer.singleShot(0, reveal)

    def _on_effect_toggled(self, row: EffectEditor) -> None:
        self._expand_only(None if row.expanded else row)

    def _add_effect(self) -> None:
        row = self._insert_effect_row(len(self.effect_rows), None)
        self._expand_only(row)
        self._on_structure_edited()
        row.focus_formula()
        self._scroll_to(row)

    def _duplicate_effect(self, row: EffectEditor) -> None:
        copy = self._insert_effect_row(self.effect_rows.index(row) + 1, row.data())
        self._expand_only(copy)
        self._on_structure_edited()
        self._scroll_to(copy)

    def _remove_effect(self, row: EffectEditor) -> None:
        self.effect_rows.remove(row)
        self.effects_layout.removeWidget(row)
        row.hide()
        row.deleteLater()
        self._on_structure_edited()

    def _move_effect(self, row: EffectEditor, step: int) -> None:
        index = self.effect_rows.index(row)
        target = index + step
        if not 0 <= target < len(self.effect_rows):
            return
        self.effect_rows.insert(target, self.effect_rows.pop(index))
        self.effects_layout.removeWidget(row)
        self.effects_layout.insertWidget(target, row)
        self._on_structure_edited()
