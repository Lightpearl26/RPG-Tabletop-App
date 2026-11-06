"""One effect of a spell, as an expandable row of the spell editor."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from html import escape
from typing import NamedTuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from rpg_tabletop.assets.spell import TARGET_SCOPES, EffectType, Formula
from rpg_tabletop.ui.cards.formula import EntityStatBadge
from rpg_tabletop.ui.cards.spell import (
    EFFECT_BONUS_COLOR,
    EFFECT_MALUS_COLOR,
    EFFECT_OTHER_COLOR,
    EFFECT_TYPE_COLOR,
)
from .spell_widgets import (
    SPELL_STATS,
    ClickableFrame,
    ElidedLabel,
    FormulaField,
    ValueComboBox,
    pill_style,
    set_style_property,
)


ERROR = "error"
WARNING = "warning"
ISSUE_COLOR = {ERROR: "#E35D6A", WARNING: "#E39A4A"}

EFFECT_TYPES = tuple(member.value for member in EffectType)
OPERATORS = ("bonus", "malus")
NEW_EFFECT = {
    "target": "target.hp",
    "operator": "malus",
    "formula": "",
    "effect_type": "damage",
}


class Issue(NamedTuple):
    """A problem found in the spell being edited."""

    severity: str
    message: str


def issues_html(issues: Sequence[Issue]) -> str:
    """Rich text listing `issues`, each marked with the color of its severity."""
    return "<br/>".join(
        f'<span style="color: {ISSUE_COLOR[issue.severity]}">&#9679;</span> {escape(issue.message)}'
        for issue in issues
    )


def operator_sign(operator: str) -> tuple[str, str]:
    """Return the sign shown for an operator and its color."""
    if operator == "bonus":
        return "+", EFFECT_BONUS_COLOR
    if operator == "malus":
        return "\u2212", EFFECT_MALUS_COLOR
    return "?", EFFECT_OTHER_COLOR


def suggest_effect_type(stat: str, operator: str) -> str:
    """Return the effect type that usually goes with a stat and an operator."""
    if stat == "hp":
        return "damage" if operator == "malus" else "heal"
    return "debuff" if operator == "malus" else "buff"


def type_warnings(stat: str, operator: str, effect_type: str) -> list[str]:
    """List what looks incoherent between the target stat, the operator and the type."""
    warnings = []
    if stat == "hp" and operator == "malus" and effect_type != "damage":
        warnings.append(
            "Lowers hit points but is not typed damage, so shields and damage modifiers "
            "will not apply. Suggested type: damage."
        )
    if stat == "hp" and operator == "bonus" and effect_type not in ("heal", "shield"):
        warnings.append(f"Raises hit points but is typed {effect_type}. Suggested type: heal.")
    if effect_type == "heal" and operator == "malus":
        warnings.append("Typed heal but lowers the stat.")
    if effect_type == "damage" and operator == "bonus":
        warnings.append("Typed damage but raises the stat.")
    return warnings


class EffectEditor(QFrame):
    """One effect of a spell: a summary line that expands into the fields to edit it."""

    # Any edit of the content
    changed = Signal()
    # The summary line was clicked
    toggle_requested = Signal(object)
    # The row and the direction (-1 up, +1 down)
    move_requested = Signal(object, int)
    duplicate_requested = Signal(object)
    remove_requested = Signal(object)

    def __init__(self, data: dict | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SpellEffect")
        # Keys the editor does not know are written back untouched
        self._extra = deepcopy(data) if data is not None else {}
        # A type picked by hand is never replaced by the suggested one
        self._type_is_manual = data is not None
        self._updating = False
        self._badge: EntityStatBadge | None = None
        self._badge_key: tuple[str, str] | None = None
        self.position = 0

        self._build_ui()
        self._apply_data(data if data is not None else NEW_EFFECT)
        self.set_expanded(False)

    @property
    def expanded(self) -> bool:
        """Whether the fields of the effect are shown."""
        return not self.body.isHidden()

    def set_expanded(self, expanded: bool) -> None:
        """Show or hide the fields below the summary line."""
        self.body.setVisible(expanded)
        self.chevron_label.setText("\u25BE" if expanded else "\u25B8")
        set_style_property(self, "expanded", expanded)

    def set_position(self, index: int, count: int) -> None:
        """Tell the row its place among the `count` effects of the spell."""
        self.position = index
        self.index_label.setText(str(index + 1))
        self.up_button.setEnabled(index > 0)
        self.down_button.setEnabled(index < count - 1)

    def focus_formula(self) -> None:
        """Put the cursor in the formula field."""
        self.formula_field.line_edit.setFocus()

    def heading(self) -> str:
        """Short wording of what the effect changes, such as `target.hp -`."""
        sign, _color = operator_sign(self.operator_input.value)
        return f"{self.scope_input.value}.{self.stat_input.value} {sign}"

    def show_test(self, short: str, detail: str) -> None:
        """Show the result of the formula in the test context; both texts empty hide it."""
        self.result_label.setText(short)
        self.result_label.setToolTip(detail)
        self.result_label.setVisible(bool(short))
        self.test_label.setText(f"Test: {detail}" if detail else "")
        self.test_label.setVisible(bool(detail))

    def data(self) -> dict:
        """Return the effect as stored in the spell, with the keys the editor ignores kept."""
        data = deepcopy(self._extra)
        data["target"] = f"{self.scope_input.value}.{self.stat_input.value}"
        data["operator"] = self.operator_input.value
        data["formula"] = self.formula_field.text().strip()
        data["effect_type"] = self.type_input.value
        return data

    def issues(self) -> list[Issue]:
        """Return the errors that prevent saving and the warnings about this effect."""
        issues: list[Issue] = []
        expression = self.formula_field.text().strip()
        if not expression:
            issues.append(Issue(ERROR, "The formula is required."))
        else:
            try:
                Formula(expression).validate()
            except ValueError as error:
                issues.append(Issue(ERROR, str(error)))

        if not self.type_input.is_known:
            issues.append(Issue(ERROR, f"Unknown effect type '{self.type_input.value}'."))
        if not self.scope_input.is_known:
            issues.append(
                Issue(WARNING, f"Unknown target '{self.scope_input.value}': kept as stored.")
            )
        if not self.stat_input.is_known:
            issues.append(Issue(WARNING, f"Unknown stat '{self.stat_input.value}': kept as stored."))
        if not self.operator_input.is_known:
            issues.append(
                Issue(
                    WARNING,
                    f"Unknown operator '{self.operator_input.value}': it is handled as a bonus.",
                )
            )
        issues.extend(
            Issue(WARNING, message)
            for message in type_warnings(
                self.stat_input.value,
                self.operator_input.value,
                self.type_input.value,
            )
        )
        return issues

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = ClickableFrame()
        self.header.setObjectName("SpellEffectHeader")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(10, 6, 8, 6)
        header_layout.setSpacing(8)

        self.chevron_label = QLabel()
        self.chevron_label.setObjectName("SpellEffectChevron")
        self.index_label = QLabel()
        self.index_label.setObjectName("SpellEffectIndex")
        self._badge_layout = QHBoxLayout()
        self._badge_layout.setContentsMargins(0, 0, 0, 0)
        self.sign_label = QLabel()
        self.type_label = QLabel()
        self.formula_label = ElidedLabel()
        self.formula_label.setObjectName("SpellEffectFormula")
        self.result_label = QLabel()
        self.result_label.setObjectName("SpellEffectResult")
        self.result_label.hide()
        self.issue_dot = QLabel("\u25CF")
        self.issue_dot.hide()
        self.up_button = self._make_button("\u2191", "Move up")
        self.down_button = self._make_button("\u2193", "Move down")
        self.duplicate_button = self._make_button("\u29C9", "Duplicate")
        self.remove_button = self._make_button("\u2715", "Remove")

        header_layout.addWidget(self.chevron_label)
        header_layout.addWidget(self.index_label)
        header_layout.addLayout(self._badge_layout)
        header_layout.addWidget(self.sign_label)
        header_layout.addWidget(self.type_label)
        header_layout.addWidget(self.formula_label, 1)
        header_layout.addWidget(self.result_label)
        header_layout.addWidget(self.issue_dot)
        header_layout.addWidget(self.up_button)
        header_layout.addWidget(self.down_button)
        header_layout.addWidget(self.duplicate_button)
        header_layout.addWidget(self.remove_button)
        layout.addWidget(self.header)

        self.body = QFrame()
        self.body.setObjectName("SpellEffectBody")
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(12, 6, 12, 12)
        body_layout.setSpacing(10)

        form = QGridLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(8)
        self.scope_input = ValueComboBox(TARGET_SCOPES)
        self.stat_input = ValueComboBox(SPELL_STATS)
        self.operator_input = ValueComboBox(OPERATORS)
        self.type_input = ValueComboBox(EFFECT_TYPES)
        self.formula_field = FormulaField("10 + diceratio(user.int, 10)")
        formula_label = QLabel("Formula")
        formula_label.setContentsMargins(0, 5, 0, 0)
        form.addWidget(QLabel("Target"), 0, 0)
        form.addWidget(self.scope_input, 0, 1)
        form.addWidget(self.stat_input, 0, 2)
        form.addWidget(QLabel("Operator"), 0, 3)
        form.addWidget(self.operator_input, 0, 4)
        form.addWidget(QLabel("Type"), 1, 0)
        form.addWidget(self.type_input, 1, 1, 1, 2)
        form.addWidget(formula_label, 2, 0, Qt.AlignmentFlag.AlignTop)
        form.addWidget(self.formula_field, 2, 1, 1, 5)
        form.setColumnStretch(5, 1)
        body_layout.addLayout(form)

        self.issues_label = QLabel()
        self.issues_label.setObjectName("SpellEffectIssues")
        self.issues_label.setTextFormat(Qt.TextFormat.RichText)
        self.issues_label.setWordWrap(True)
        self.issues_label.hide()
        body_layout.addWidget(self.issues_label)

        self.test_label = QLabel()
        self.test_label.setObjectName("SpellEffectTest")
        self.test_label.setWordWrap(True)
        self.test_label.hide()
        body_layout.addWidget(self.test_label)
        layout.addWidget(self.body)

        self.header.clicked.connect(lambda: self.toggle_requested.emit(self))
        self.up_button.clicked.connect(lambda: self.move_requested.emit(self, -1))
        self.down_button.clicked.connect(lambda: self.move_requested.emit(self, 1))
        self.duplicate_button.clicked.connect(lambda: self.duplicate_requested.emit(self))
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self))
        for combo in (self.scope_input, self.stat_input, self.operator_input):
            combo.currentIndexChanged.connect(self._on_target_changed)
        self.type_input.currentIndexChanged.connect(self._on_type_changed)
        self.formula_field.textChanged.connect(self._on_edited)

    @staticmethod
    def _make_button(text: str, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName("SpellEffectButton")
        button.setText(text)
        button.setToolTip(tooltip)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.ArrowCursor)
        return button

    def _apply_data(self, data: dict) -> None:
        target = str(data.get("target") or "target.hp")
        scope, separator, stat = target.partition(".")
        if not separator:
            scope, stat = "target", target

        self._updating = True
        try:
            self.scope_input.set_value(scope)
            self.stat_input.set_value(stat)
            self.operator_input.set_value(str(data.get("operator") or "bonus"))
            self.type_input.set_value(str(data.get("effect_type") or "other"))
            self.formula_field.set_text(str(data.get("formula") or ""))
        finally:
            self._updating = False
        self._refresh_summary()

    def _on_target_changed(self, *_args: object) -> None:
        if self._updating:
            return
        if not self._type_is_manual:
            self.type_input.set_value(
                suggest_effect_type(self.stat_input.value, self.operator_input.value)
            )
        self._on_edited()

    def _on_type_changed(self, *_args: object) -> None:
        if self._updating:
            return
        self._type_is_manual = True
        self._on_edited()

    def _on_edited(self, *_args: object) -> None:
        if self._updating:
            return
        self._refresh_summary()
        self.changed.emit()

    def _refresh_summary(self) -> None:
        scope = self.scope_input.value
        stat = self.stat_input.value
        if (scope, stat) != self._badge_key:
            if self._badge is not None:
                self._badge.hide()
                self._badge.deleteLater()
            self._badge = EntityStatBadge(scope, stat)
            self._badge_layout.addWidget(self._badge)
            self._badge_key = (scope, stat)

        sign, color = operator_sign(self.operator_input.value)
        self.sign_label.setText(sign)
        self.sign_label.setStyleSheet(f"color: {color}; font-size: 15px; font-weight: 700;")

        effect_type = self.type_input.value
        self.type_label.setText(effect_type.replace("_", " ").upper())
        self.type_label.setStyleSheet(
            pill_style(EFFECT_TYPE_COLOR.get(effect_type, EFFECT_TYPE_COLOR["other"]))
        )

        expression = self.formula_field.text().strip()
        set_style_property(self.formula_label, "empty", not expression)
        self.formula_label.set_full_text(expression or "No formula")

        issues = self.issues()
        self.issues_label.setText(issues_html(issues))
        self.issues_label.setVisible(bool(issues))
        if issues:
            severity = ERROR if any(issue.severity == ERROR for issue in issues) else WARNING
            self.issue_dot.setStyleSheet(f"color: {ISSUE_COLOR[severity]};")
            self.issue_dot.setToolTip("\n".join(issue.message for issue in issues))
        self.issue_dot.setVisible(bool(issues))
