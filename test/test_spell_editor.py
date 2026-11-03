import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import get_args
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from libs.assets.campaign import Campaign
from libs.assets.spell import TARGET_STATS, Formula, Spell, Targeting
from libs.gui.editor.formula_tester import (
    CUSTOM,
    DEFAULT_STATS,
    DRAWS,
    FormulaTester,
    Outcome,
    StubEntity,
    describe,
    read_character_stats,
    run_formula,
    uses_dice,
)
from libs.gui.editor.spell import (
    POLICIES,
    POLICY_HINTS,
    TARGETING_VALUES,
    TEST_DELAY_MS,
    SpellEditor,
)
from libs.gui.editor.spell_effect import (
    ERROR,
    WARNING,
    EffectEditor,
    suggest_effect_type,
    type_warnings,
)
from libs.gui.editor.spell_widgets import (
    SPELL_STATS,
    ClickableFrame,
    ElidedLabel,
    FormulaField,
    ValueComboBox,
)


def effect(**changes: object) -> dict:
    """An effect as stored in a spell file."""
    return {
        "target": "target.hp",
        "operator": "malus",
        "formula": "10",
        "effect_type": "damage",
        **changes,
    }


def spell_data(**changes: object) -> dict:
    """A spell as stored in a spell file."""
    return {
        "name": "Fireball",
        "description": "A ball of fire.",
        "cost": 30,
        "effects": [effect()],
        "targeting": "single",
        "runtime_policy": "instant",
        **changes,
    }


class SpellEditorCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.campaign = Campaign("Spell Test", Path(self.temporary_directory.name))
        self.campaign.ensure_directories()

    def tearDown(self) -> None:
        self.app.processEvents()
        self.temporary_directory.cleanup()

    def write_spell(self, stem: str, data: dict) -> Path:
        path = self.campaign.spell_path / f"{stem}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def write_character(self, stem: str, data: dict) -> Path:
        path = self.campaign.character_path / f"{stem}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def open_spell(self, data: dict | None = None, stem: str = "Fireball") -> SpellEditor:
        self.write_spell(stem, spell_data() if data is None else data)
        return SpellEditor(stem, self.campaign)

    def new_spell(self) -> SpellEditor:
        return SpellEditor("New Spell", self.campaign, is_new=True)

    def show_widget(self, widget: QWidget, size: tuple[int, int] = (1000, 1000)) -> None:
        widget.show()
        widget.resize(*size)
        self.addCleanup(widget.close)
        self.app.processEvents()

    @staticmethod
    def read(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def formulas(editor: SpellEditor) -> list[str]:
        return [row.formula_field.text() for row in editor.effect_rows]

    @staticmethod
    def messages(editor: SpellEditor, severity: str) -> list[str]:
        return [issue.message for issue in editor.issues() if issue.severity == severity]

    @staticmethod
    def expanded(editor: SpellEditor) -> list[bool]:
        return [row.expanded for row in editor.effect_rows]

    @staticmethod
    def stats_of(inputs: dict) -> dict[str, int]:
        return {stat: spin_box.value() for stat, spin_box in inputs.items()}


class SpellEditorFileTests(SpellEditorCase):
    def test_loading_shows_every_field(self) -> None:
        editor = self.open_spell(
            spell_data(
                description="Line one\nLine two",
                targeting="area",
                runtime_policy="maintain",
                delay=3,
                effects=[
                    effect(),
                    effect(
                        target="user.stamina",
                        operator="bonus",
                        formula="user.str",
                        effect_type="buff",
                    ),
                ],
            )
        )
        second = editor.effect_rows[1]

        self.assertEqual(editor.name_input.text(), "Fireball")
        self.assertEqual(editor.description_input.toPlainText(), "Line one\nLine two")
        self.assertEqual(editor.cost_input.value(), 30)
        self.assertEqual(editor.targeting_input.value, "area")
        self.assertEqual(editor.policy_input.value, "maintain")
        self.assertEqual(editor.delay_input.value(), 3)
        self.assertEqual(editor.effects_group.title(), "Effects (2)")
        self.assertTrue(editor.no_effects_label.isHidden())
        self.assertEqual(
            (
                second.scope_input.value,
                second.stat_input.value,
                second.operator_input.value,
                second.type_input.value,
                second.formula_field.text(),
            ),
            ("user", "stamina", "bonus", "buff", "user.str"),
        )
        self.assertFalse(editor.is_modified)

    def test_saving_without_changes_writes_the_same_content(self) -> None:
        original = spell_data(targeting="multi", runtime_policy="delay", delay=2)
        path = self.write_spell("Fireball", original)
        editor = SpellEditor("Fireball", self.campaign)

        self.assertEqual(editor.save(), path)

        self.assertEqual(self.read(path), original)

    def test_save_keeps_unknown_keys_and_the_key_order(self) -> None:
        original = {
            "icon": "fire.png",
            "name": "Fireball",
            "description": "Old",
            "cost": 30,
            "effects": [effect(radius=3, tags=["fire"])],
            "targeting": "single",
            "runtime_policy": "instant",
            "lore": {"author": "nobody"},
        }
        self.write_spell("Fireball", original)
        editor = SpellEditor("Fireball", self.campaign)
        editor.description_input.setPlainText("New")
        editor.effect_rows[0].formula_field.set_text("20")

        data = self.read(editor.save())

        self.assertEqual(list(data), list(original))
        self.assertEqual(data["description"], "New")
        self.assertEqual(data["icon"], "fire.png")
        self.assertEqual(data["lore"], {"author": "nobody"})
        self.assertEqual(
            data["effects"][0],
            effect(formula="20", radius=3, tags=["fire"]),
        )
        self.assertEqual(list(data["effects"][0]), list(original["effects"][0]))

    def test_files_are_written_indented_and_in_utf8(self) -> None:
        editor = self.open_spell(spell_data(description="Boule de feu \u00e9clatante"))
        editor.cost_input.setValue(31)

        text = editor.save().read_text(encoding="utf-8")

        self.assertIn("\u00e9clatante", text)
        self.assertIn('\n    "name": "Fireball"', text)

    def test_null_and_odd_stored_values_do_not_break_loading(self) -> None:
        editor = self.open_spell(
            {
                "name": None,
                "description": None,
                "cost": "lots",
                "effects": [
                    {"target": None, "operator": None, "formula": None, "effect_type": None},
                    "not an effect",
                ],
                "targeting": None,
                "runtime_policy": None,
                "delay": float("inf"),
            }
        )
        row = editor.effect_rows[0]

        self.assertEqual(editor.name_input.text(), "")
        self.assertEqual(editor.description_input.toPlainText(), "")
        self.assertEqual(editor.cost_input.value(), 0)
        self.assertEqual(editor.targeting_input.value, "single")
        self.assertEqual(editor.policy_input.value, "instant")
        self.assertEqual(editor.delay_input.value(), 1)
        self.assertEqual(len(editor.effect_rows), 1)
        self.assertEqual(
            (
                row.scope_input.value,
                row.stat_input.value,
                row.operator_input.value,
                row.type_input.value,
                row.formula_field.text(),
            ),
            ("target", "hp", "bonus", "other", ""),
        )

    def test_changing_the_name_renames_the_file_and_reports_it(self) -> None:
        old_path = self.write_spell("Fireball", spell_data())
        editor = SpellEditor("Fireball", self.campaign)
        saved: list[tuple[str, str]] = []
        editor.saved.connect(lambda path, name: saved.append((path, name)))
        editor.name_input.setText("Fire Storm")

        path = editor.save()

        self.assertEqual(path, self.campaign.spell_path / "fire_storm.json")
        self.assertFalse(old_path.exists())
        self.assertEqual(self.read(path)["name"], "Fire Storm")
        self.assertEqual(saved, [(str(path), "Fire Storm")])
        self.assertEqual((editor.resource_id, editor.title), ("fire_storm", "Fire Storm"))

    def test_renaming_a_spell_updates_the_characters_that_know_it(self) -> None:
        self.write_spell("Fireball", spell_data())
        knows_it = self.write_character(
            "Mage", {"name": "Mage", "spells": ["Fireball", "Bolt"]}
        )
        writes_it_in_lowercase = self.write_character(
            "Apprentice", {"name": "Apprentice", "spells": ["fireball"]}
        )
        unrelated = self.write_character("Brute", {"name": "Brute", "spells": ["Bolt"]})
        unrelated_before = unrelated.read_text(encoding="utf-8")
        editor = SpellEditor("Fireball", self.campaign)
        renames: list[tuple[str, str, int]] = []
        editor.renamed.connect(
            lambda old_stem, new_name, updates: renames.append(
                (old_stem, new_name, len(updates))
            )
        )
        editor.name_input.setText("Fire Storm")

        editor.save()

        self.assertEqual(self.read(knows_it)["spells"], ["Fire Storm", "Bolt"])
        self.assertEqual(self.read(writes_it_in_lowercase)["spells"], ["Fire Storm"])
        self.assertEqual(unrelated.read_text(encoding="utf-8"), unrelated_before)
        self.assertEqual(renames, [("Fireball", "Fire Storm", 2)])

    def test_a_failed_rename_leaves_the_spell_and_the_characters_untouched(self) -> None:
        spell_path = self.write_spell("Fireball", spell_data())
        character = self.write_character("Mage", {"name": "Mage", "spells": ["Fireball"]})
        spell_before = spell_path.read_text(encoding="utf-8")
        character_before = character.read_text(encoding="utf-8")
        editor = SpellEditor("Fireball", self.campaign)
        events: list[str] = []
        editor.saved.connect(lambda *_: events.append("saved"))
        editor.renamed.connect(lambda *_: events.append("renamed"))
        editor.name_input.setText("Fire Storm")

        with patch("libs.gui.editor.base.write_text_atomic", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                editor.save()

        self.assertEqual(spell_path.read_text(encoding="utf-8"), spell_before)
        self.assertEqual(character.read_text(encoding="utf-8"), character_before)
        self.assertFalse((self.campaign.spell_path / "fire_storm.json").exists())
        self.assertEqual(events, [])
        self.assertTrue(editor.is_modified)

        editor.save()

        self.assertEqual(self.read(character)["spells"], ["Fire Storm"])

    def test_renaming_is_refused_when_the_new_name_is_taken(self) -> None:
        taken = self.write_spell("fire_storm", spell_data(name="Fire Storm"))
        original = self.write_spell("Fireball", spell_data())
        editor = SpellEditor("Fireball", self.campaign)
        editor.name_input.setText("Fire Storm")

        with self.assertRaises(FileExistsError):
            editor.save()

        self.assertEqual(self.read(taken), spell_data(name="Fire Storm"))
        self.assertEqual(self.read(original), spell_data())
        self.assertTrue(editor.is_modified)

    def test_a_new_spell_gets_a_canonical_file_name_and_can_be_loaded(self) -> None:
        editor = self.new_spell()
        editor.name_input.setText("  Frost Nova  ")
        editor.description_input.setPlainText("Cold.")

        path = editor.save()

        self.assertEqual(path, self.campaign.spell_path / "frost_nova.json")
        data = self.read(path)
        self.assertEqual(
            data,
            {
                "name": "Frost Nova",
                "description": "Cold.",
                "cost": 0,
                "effects": [],
                "targeting": "single",
                "runtime_policy": "instant",
            },
        )
        self.assertEqual(Spell.from_dict(data).name, "Frost Nova")
        self.assertFalse(editor.is_new)

    def test_saving_again_edits_the_same_file(self) -> None:
        editor = self.new_spell()
        editor.name_input.setText("Frost Nova")
        first = editor.save()
        editor.cost_input.setValue(12)

        second = editor.save()

        self.assertEqual(first, second)
        self.assertEqual(self.read(second)["cost"], 12)
        self.assertEqual(len(list(self.campaign.spell_path.glob("*.json"))), 1)

    def test_a_new_spell_cannot_overwrite_an_existing_file(self) -> None:
        existing = self.write_spell("frost_nova", spell_data(name="Frost Nova"))
        editor = self.new_spell()
        editor.name_input.setText("Frost Nova")

        with self.assertRaises(FileExistsError):
            editor.save()

        self.assertEqual(self.read(existing), spell_data(name="Frost Nova"))

    def test_loading_problems_are_raised_to_the_caller(self) -> None:
        (self.campaign.spell_path / "Broken.json").write_text("not json", encoding="utf-8")
        (self.campaign.spell_path / "List.json").write_text("[]", encoding="utf-8")

        with self.assertRaises(FileNotFoundError):
            SpellEditor("Missing", self.campaign)
        with self.assertRaisesRegex(ValueError, "must be a file name"):
            SpellEditor("../outside", self.campaign)
        with self.assertRaises(ValueError):
            SpellEditor("Broken", self.campaign)
        with self.assertRaisesRegex(ValueError, "must contain a JSON object"):
            SpellEditor("List", self.campaign)

    def test_save_button_requests_a_save(self) -> None:
        editor = self.open_spell()
        requests: list[str] = []
        editor.save_requested.connect(lambda: requests.append("save"))

        editor.save_button.click()

        self.assertEqual(requests, ["save"])

    def test_spells_do_not_reference_other_resources(self) -> None:
        editor = self.open_spell()
        editor.cost_input.setValue(31)

        self.assertFalse(editor.update_references("items", "Shield", "Tower Shield"))
        self.assertFalse(editor.update_references("spells", "Bolt", "Spark"))

        self.assertEqual(editor.cost_input.value(), 31)


class SpellValidationTests(SpellEditorCase):
    def test_invalid_names_block_the_save(self) -> None:
        cases = {
            "": "Spell name is required.",
            "   ": "Spell name is required.",
            "bad/name": "Spell name contains a character invalid in file names.",
            "what?": "Spell name contains a character invalid in file names.",
        }
        for name, message in cases.items():
            with self.subTest(name=name):
                editor = self.new_spell()
                editor.name_input.setText(name)

                with self.assertRaises(ValueError) as caught:
                    editor.save()

                self.assertEqual(str(caught.exception), message)
                self.assertFalse(editor.name_issues_label.isHidden())
                self.assertEqual(list(self.campaign.spell_path.glob("*.json")), [])

    def test_a_formula_error_blocks_the_save_and_opens_the_faulty_effect(self) -> None:
        path = self.write_spell(
            "Fireball",
            spell_data(effects=[effect(), effect(formula="(user.int + "), effect()]),
        )
        before = path.read_text(encoding="utf-8")
        editor = SpellEditor("Fireball", self.campaign)
        editor.effect_rows[0].set_expanded(True)

        with self.assertRaises(ValueError) as caught:
            editor.save()

        self.assertEqual(str(caught.exception), "Effect 2: Unmatched opening parenthesis.")
        self.assertEqual(self.expanded(editor), [False, True, False])
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_every_effect_error_blocks_the_save(self) -> None:
        cases = {
            "empty formula": (effect(formula="  "), "Effect 1: The formula is required."),
            "unknown stat": (
                effect(formula="user.mana"),
                "Effect 1: Unknown stat 'mana' in 'user.mana'.",
            ),
            "unknown dice function": (
                effect(formula="dice(user.int)"),
                "Effect 1: Unknown function 'dice'.",
            ),
            "unknown type": (
                effect(effect_type="banana"),
                "Effect 1: Unknown effect type 'banana'.",
            ),
        }
        for label, (faulty_effect, message) in cases.items():
            with self.subTest(label):
                editor = self.open_spell(spell_data(effects=[faulty_effect]))

                with self.assertRaises(ValueError) as caught:
                    editor.save()

                self.assertEqual(str(caught.exception), message)
                self.assertTrue(editor.effect_rows[0].expanded)

    def test_an_unknown_policy_blocks_the_save_until_another_is_picked(self) -> None:
        editor = self.open_spell(spell_data(runtime_policy="weird"))

        with self.assertRaisesRegex(ValueError, "Unknown runtime policy 'weird'"):
            editor.save()
        self.assertEqual(
            editor.policy_hint.text(),
            "Unknown policy: the game cannot cast this spell.",
        )

        editor.policy_input.setCurrentText("instant")

        self.assertEqual(self.read(editor.save())["runtime_policy"], "instant")

    def test_the_trigger_policy_needs_a_valid_condition(self) -> None:
        editor = self.open_spell(spell_data(runtime_policy="trigger"))
        self.assertFalse(editor.trigger_input.isHidden())

        with self.assertRaisesRegex(ValueError, "needs a trigger condition"):
            editor.save()
        editor.trigger_input.set_text("user.hp <")
        with self.assertRaisesRegex(ValueError, "^Trigger condition: "):
            editor.save()
        editor.trigger_input.set_text("user.hp <= 20")
        data = self.read(editor.save())

        self.assertEqual(data["trigger_condition"], "user.hp <= 20")
        self.assertNotIn("delay", data)
        self.assertIsInstance(Spell.from_dict(data).trigger_condition, Formula)

    def test_a_trigger_condition_left_on_another_policy_is_flagged_and_removable(self) -> None:
        editor = self.open_spell(spell_data(trigger_condition="user.hp < 5"))

        self.assertFalse(editor.trigger_input.isHidden())
        self.assertEqual(
            self.messages(editor, WARNING),
            ["The trigger condition is ignored unless the policy is trigger."],
        )

        editor.trigger_input.set_text("")

        self.assertEqual(self.messages(editor, WARNING), [])
        self.assertNotIn("trigger_condition", self.read(editor.save()))

    def test_type_warnings_do_not_block_the_save(self) -> None:
        editor = self.open_spell(spell_data(effects=[effect(operator="bonus")]))

        self.assertEqual(
            self.messages(editor, WARNING),
            [
                "Effect 1: Raises hit points but is typed damage. Suggested type: heal.",
                "Effect 1: Typed damage but raises the stat.",
            ],
        )
        self.assertEqual(self.messages(editor, ERROR), [])

        data = self.read(editor.save())

        self.assertEqual(data["effects"][0]["operator"], "bonus")

    def test_values_the_editor_does_not_know_are_kept_and_flagged(self) -> None:
        editor = self.open_spell(
            spell_data(
                targeting="zone",
                effects=[effect(target="foe.mana", operator="swap")],
            )
        )
        row = editor.effect_rows[0]

        self.assertEqual(editor.targeting_input.value, "zone")
        self.assertEqual(
            (row.scope_input.value, row.stat_input.value, row.operator_input.value),
            ("foe", "mana", "swap"),
        )
        self.assertEqual(
            self.messages(editor, WARNING),
            [
                "Unknown targeting 'zone': kept as stored.",
                "Effect 1: Unknown target 'foe': kept as stored.",
                "Effect 1: Unknown stat 'mana': kept as stored.",
                "Effect 1: Unknown operator 'swap': it is handled as a bonus.",
            ],
        )
        self.assertEqual(self.messages(editor, ERROR), [])
        self.assertFalse(editor.has_changes)

        data = self.read(editor.save())

        self.assertEqual(data["targeting"], "zone")
        self.assertEqual(data["effects"][0]["target"], "foe.mana")
        self.assertEqual(data["effects"][0]["operator"], "swap")

    def test_a_target_without_a_scope_is_read_as_a_target_stat(self) -> None:
        editor = self.open_spell(spell_data(effects=[effect(target="hp")]))
        row = editor.effect_rows[0]

        self.assertEqual((row.scope_input.value, row.stat_input.value), ("target", "hp"))

        self.assertEqual(self.read(editor.save())["effects"][0]["target"], "target.hp")

    def test_a_spell_that_cannot_be_loaded_is_not_written(self) -> None:
        path = self.write_spell("Fireball", spell_data())
        before = path.read_text(encoding="utf-8")
        editor = SpellEditor("Fireball", self.campaign)
        editor.cost_input.setValue(31)

        with patch(
            "libs.gui.editor.spell.Spell.from_dict",
            side_effect=ValueError("boom"),
        ):
            with self.assertRaisesRegex(ValueError, "^The spell cannot be loaded: boom$"):
                editor.save()

        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_a_missing_delay_is_a_warning_and_is_written_on_save(self) -> None:
        cases = {
            "maintain": "No duration is stored yet: 1 will be saved.",
            "delay": "No delay is stored yet: 1 will be saved.",
        }
        for policy, message in cases.items():
            with self.subTest(policy):
                editor = self.open_spell(spell_data(runtime_policy=policy))
                self.assertEqual(self.messages(editor, WARNING), [message])

                editor.delay_input.setValue(4)
                data = self.read(editor.save())

                self.assertEqual(data["delay"], 4)
                self.assertEqual(editor.issues(), [])
                self.assertEqual(editor.issue_label.text(), "No issues")

    def test_the_delay_is_written_only_when_the_policy_reads_it_or_it_was_stored(self) -> None:
        cases = (
            ("instant", {}, None),
            ("toggle", {}, None),
            ("maintain", {}, 1),
            ("delay", {}, 1),
            ("passiv", {}, 1),
            ("instant", {"delay": 7}, 7),
        )
        for policy, stored, expected in cases:
            with self.subTest(policy=policy, stored=stored):
                editor = self.open_spell(spell_data(runtime_policy=policy, **stored))

                data = self.read(editor.save())

                self.assertEqual(data.get("delay"), expected)

    def test_the_casting_controls_follow_the_policy(self) -> None:
        # (delay used, delay wording, trigger hidden); a new policy must be listed here
        expectations = {
            "instant": (False, "Duration (turns)", True),
            "maintain": (True, "Duration (turns)", True),
            "refresh": (True, "Duration (turns)", True),
            "delay": (True, "Delay (turns)", True),
            "passiv": (True, "Duration (turns)", True),
            "toggle": (False, "Duration (turns)", True),
            "trigger": (False, "Duration (turns)", False),
        }
        self.assertEqual(set(expectations), set(POLICIES))
        self.assertEqual(set(POLICY_HINTS), set(POLICIES))
        editor = self.new_spell()

        for policy, (uses_delay, wording, trigger_hidden) in expectations.items():
            with self.subTest(policy):
                editor.policy_input.setCurrentText(policy)

                self.assertEqual(editor.delay_input.isEnabled(), uses_delay)
                self.assertEqual(editor.delay_label.text(), wording)
                self.assertEqual(editor.trigger_input.isHidden(), trigger_hidden)
                self.assertEqual(editor.trigger_label.isHidden(), trigger_hidden)
                self.assertEqual(editor.policy_hint.text(), POLICY_HINTS[policy])

    def test_the_issue_counter_summarizes_errors_and_warnings(self) -> None:
        editor = self.new_spell()
        self.assertEqual(editor.issue_label.text(), "1 error")
        self.assertEqual(editor.issue_label.property("severity"), "error")

        editor.name_input.setText("Spark")
        self.assertEqual(editor.issue_label.text(), "No issues")
        self.assertEqual(editor.issue_label.property("severity"), "ok")

        editor.policy_input.setCurrentText("maintain")
        self.assertEqual(editor.issue_label.text(), "1 warning")
        self.assertEqual(editor.issue_label.property("severity"), "warning")

        editor.add_effect_button.click()
        self.assertEqual(editor.issue_label.text(), "1 error, 1 warning")
        self.assertEqual(editor.issue_label.property("severity"), "error")

        editor.name_input.setText("")
        self.assertEqual(editor.issue_label.text(), "2 errors, 1 warning")

        editor.name_input.setText("Spark")
        row = editor.effect_rows[0]
        row.formula_field.set_text("7")
        row.type_input.setCurrentText("other")
        row.type_input.setCurrentText("damage")
        row.operator_input.setCurrentText("bonus")
        self.assertEqual(editor.issue_label.text(), "3 warnings")
        self.assertEqual(editor.issue_label.property("severity"), "warning")
        self.assertEqual(editor.issue_label.toolTip().count("\n"), 2)

    def test_the_stat_lists_of_the_editor_and_of_the_model_match(self) -> None:
        self.assertEqual(set(SPELL_STATS), set(TARGET_STATS))
        self.assertEqual(len(SPELL_STATS), len(set(SPELL_STATS)))
        self.assertEqual(SPELL_STATS[0], "hp")

    def test_targeting_offers_every_kind_the_model_knows(self) -> None:
        self.assertEqual(TARGETING_VALUES, get_args(Targeting))
        self.assertIn("self", TARGETING_VALUES)
        self.assertIn("area", TARGETING_VALUES)


class SpellEffectTests(SpellEditorCase):
    def test_effects_are_closed_when_the_spell_opens_and_open_one_at_a_time(self) -> None:
        editor = self.open_spell(
            spell_data(effects=[effect(), effect(formula="20"), effect(formula="30")])
        )
        rows = editor.effect_rows
        self.assertEqual(self.expanded(editor), [False, False, False])

        rows[0].header.clicked.emit()
        self.assertEqual(self.expanded(editor), [True, False, False])

        rows[2].header.clicked.emit()
        self.assertEqual(self.expanded(editor), [False, False, True])

        rows[2].header.clicked.emit()
        self.assertEqual(self.expanded(editor), [False, False, False])

    def test_clicking_a_summary_line_toggles_the_effect(self) -> None:
        editor = self.open_spell(spell_data(effects=[effect(), effect(formula="20")]))
        self.show_widget(editor)
        first, second = editor.effect_rows

        QTest.mouseClick(first.header, Qt.MouseButton.LeftButton)
        self.assertEqual(self.expanded(editor), [True, False])

        QTest.mouseClick(first.formula_label, Qt.MouseButton.LeftButton)
        self.assertEqual(self.expanded(editor), [False, False])

        QTest.mouseClick(second.header, Qt.MouseButton.RightButton)
        self.assertEqual(self.expanded(editor), [False, False])

    def test_the_buttons_of_a_summary_line_do_not_toggle_the_effect(self) -> None:
        editor = self.open_spell(spell_data(effects=[effect(formula="1"), effect(formula="2")]))
        self.show_widget(editor)

        QTest.mouseClick(editor.effect_rows[1].up_button, Qt.MouseButton.LeftButton)

        self.assertEqual(self.formulas(editor), ["2", "1"])
        self.assertEqual(self.expanded(editor), [False, False])

    def test_effects_can_be_added_moved_duplicated_and_removed(self) -> None:
        editor = self.open_spell(
            spell_data(effects=[effect(formula="1"), effect(formula="2", radius=7)])
        )
        first, second = editor.effect_rows
        self.assertEqual(editor.effects_group.title(), "Effects (2)")
        self.assertEqual(
            [
                (first.up_button.isEnabled(), first.down_button.isEnabled()),
                (second.up_button.isEnabled(), second.down_button.isEnabled()),
            ],
            [(False, True), (True, False)],
        )

        second.up_button.click()
        self.assertEqual(self.formulas(editor), ["2", "1"])
        self.assertEqual([row.index_label.text() for row in editor.effect_rows], ["1", "2"])
        self.assertEqual(
            [row.up_button.isEnabled() for row in editor.effect_rows],
            [False, True],
        )

        editor.effect_rows[0].duplicate_button.click()
        self.assertEqual(self.formulas(editor), ["2", "2", "1"])
        self.assertEqual(editor.effects_group.title(), "Effects (3)")
        self.assertEqual(self.expanded(editor), [False, True, False])

        editor.add_effect_button.click()
        self.assertEqual(self.formulas(editor), ["2", "2", "1", ""])
        self.assertEqual(self.expanded(editor), [False, False, False, True])

        editor.effect_rows[3].remove_button.click()
        editor.effect_rows[1].remove_button.click()
        editor.effect_rows[1].remove_button.click()
        self.assertEqual(self.formulas(editor), ["2"])
        self.assertEqual(editor.effects_group.title(), "Effects (1)")
        self.assertEqual(
            (editor.effect_rows[0].up_button.isEnabled(), editor.effect_rows[0].down_button.isEnabled()),
            (False, False),
        )
        self.assertTrue(editor.no_effects_label.isHidden())

        editor.effect_rows[0].remove_button.click()
        self.assertEqual(editor.effect_rows, [])
        self.assertEqual(editor.effects_group.title(), "Effects (0)")
        self.assertFalse(editor.no_effects_label.isHidden())

    def test_a_new_effect_hits_the_target_hit_points_with_an_empty_formula(self) -> None:
        editor = self.new_spell()

        editor.add_effect_button.click()

        self.assertEqual(
            editor.effect_rows[0].data(),
            {"target": "target.hp", "operator": "malus", "formula": "", "effect_type": "damage"},
        )
        self.assertTrue(editor.effect_rows[0].expanded)

    def test_unknown_effect_keys_travel_with_their_effect(self) -> None:
        editor = self.open_spell(
            spell_data(effects=[effect(formula="1", radius=1), effect(formula="2", radius=2)])
        )
        editor.effect_rows[0].duplicate_button.click()
        editor.effect_rows[2].up_button.click()

        data = self.read(editor.save())

        self.assertEqual(
            [(item["formula"], item["radius"]) for item in data["effects"]],
            [("1", 1), ("2", 2), ("1", 1)],
        )

    def test_the_effect_type_follows_the_target_until_it_is_picked_by_hand(self) -> None:
        editor = self.new_spell()
        editor.add_effect_button.click()
        row = editor.effect_rows[0]
        self.assertEqual(row.type_input.value, "damage")

        row.operator_input.setCurrentText("bonus")
        self.assertEqual(row.type_input.value, "heal")
        row.stat_input.setCurrentText("str")
        self.assertEqual(row.type_input.value, "buff")
        row.operator_input.setCurrentText("malus")
        self.assertEqual(row.type_input.value, "debuff")

        row.type_input.setCurrentText("control")
        row.stat_input.setCurrentText("hp")
        row.operator_input.setCurrentText("bonus")

        self.assertEqual(row.type_input.value, "control")

    def test_a_stored_effect_keeps_its_type_when_the_target_changes(self) -> None:
        editor = self.open_spell(spell_data(effects=[effect(effect_type="other")]))
        row = editor.effect_rows[0]

        row.operator_input.setCurrentText("bonus")
        row.stat_input.setCurrentText("str")

        self.assertEqual(row.type_input.value, "other")

    def test_suggested_types_and_type_warnings(self) -> None:
        suggestions = {
            ("hp", "malus"): "damage",
            ("hp", "bonus"): "heal",
            ("str", "malus"): "debuff",
            ("str", "bonus"): "buff",
        }
        for (stat, operator), expected in suggestions.items():
            with self.subTest(stat=stat, operator=operator):
                self.assertEqual(suggest_effect_type(stat, operator), expected)

        self.assertEqual(type_warnings("hp", "malus", "damage"), [])
        self.assertEqual(type_warnings("hp", "bonus", "heal"), [])
        self.assertEqual(type_warnings("hp", "bonus", "shield"), [])
        self.assertEqual(type_warnings("str", "bonus", "buff"), [])
        self.assertEqual(len(type_warnings("hp", "malus", "other")), 1)
        self.assertIn("Suggested type: damage", type_warnings("hp", "malus", "other")[0])
        self.assertIn("Suggested type: heal", type_warnings("hp", "bonus", "buff")[0])
        self.assertEqual(
            type_warnings("str", "malus", "heal"),
            ["Typed heal but lowers the stat."],
        )
        self.assertEqual(
            type_warnings("str", "bonus", "damage"),
            ["Typed damage but raises the stat."],
        )

    def test_the_summary_line_describes_the_effect(self) -> None:
        editor = self.open_spell(
            spell_data(effects=[effect(operator="bonus", formula="user.str", effect_type="buff")])
        )
        row = editor.effect_rows[0]

        self.assertEqual(row.sign_label.text(), "+")
        self.assertEqual(row.type_label.text(), "BUFF")
        self.assertEqual(row.formula_label.full_text, "user.str")
        self.assertEqual(row.heading(), "target.hp +")
        self.assertEqual(row.index_label.text(), "1")

        row.operator_input.setCurrentText("malus")
        row.formula_field.set_text("")

        self.assertEqual(row.sign_label.text(), "\u2212")
        self.assertEqual(row.formula_label.full_text, "No formula")
        self.assertFalse(row.issue_dot.isHidden())

    def test_an_effect_can_be_built_without_stored_data(self) -> None:
        row = EffectEditor()

        self.assertFalse(row.expanded)
        self.assertEqual(row.data()["target"], "target.hp")


class SpellUnsavedChangesTests(SpellEditorCase):
    def test_a_new_spell_is_unsaved_until_its_first_save(self) -> None:
        editor = self.new_spell()
        states: list[bool] = []
        editor.modified_changed.connect(states.append)

        self.assertTrue(editor.is_modified)
        self.assertFalse(editor.has_changes)
        editor.name_input.setText("Frost Nova")
        self.assertTrue(editor.is_modified)
        self.assertTrue(editor.has_changes)
        editor.save()

        self.assertFalse(editor.is_modified)
        self.assertFalse(editor.has_changes)
        self.assertEqual(editor.title, "Frost Nova")
        self.assertEqual(states, [False])

    def test_every_field_reports_unsaved_changes_until_it_is_reverted(self) -> None:
        editor = self.open_spell(
            spell_data(
                runtime_policy="maintain",
                delay=2,
                effects=[effect(), effect(formula="20")],
            )
        )
        row = editor.effect_rows[0]
        states: list[bool] = []
        editor.modified_changed.connect(states.append)
        self.assertFalse(editor.is_modified)

        # Each field: how to change it, then how to put it back
        edits = {
            "name": (
                lambda: editor.name_input.setText("Fire Storm"),
                lambda: editor.name_input.setText("Fireball"),
            ),
            "description": (
                lambda: editor.description_input.setPlainText("Changed"),
                lambda: editor.description_input.setPlainText("A ball of fire."),
            ),
            "cost": (
                lambda: editor.cost_input.setValue(31),
                lambda: editor.cost_input.setValue(30),
            ),
            "targeting": (
                lambda: editor.targeting_input.setCurrentText("multi"),
                lambda: editor.targeting_input.setCurrentText("single"),
            ),
            "policy": (
                lambda: editor.policy_input.setCurrentText("refresh"),
                lambda: editor.policy_input.setCurrentText("maintain"),
            ),
            "delay": (
                lambda: editor.delay_input.setValue(3),
                lambda: editor.delay_input.setValue(2),
            ),
            "trigger condition": (
                lambda: editor.trigger_input.set_text("user.hp < 5"),
                lambda: editor.trigger_input.set_text(""),
            ),
            "effect scope": (
                lambda: row.scope_input.setCurrentText("user"),
                lambda: row.scope_input.setCurrentText("target"),
            ),
            "effect stat": (
                lambda: row.stat_input.setCurrentText("stamina"),
                lambda: row.stat_input.setCurrentText("hp"),
            ),
            "effect operator": (
                lambda: row.operator_input.setCurrentText("bonus"),
                lambda: row.operator_input.setCurrentText("malus"),
            ),
            "effect type": (
                lambda: row.type_input.setCurrentText("other"),
                lambda: row.type_input.setCurrentText("damage"),
            ),
            "effect formula": (
                lambda: row.formula_field.set_text("11"),
                lambda: row.formula_field.set_text("10"),
            ),
        }
        for label, (change, revert) in edits.items():
            with self.subTest(label):
                states.clear()

                change()
                self.assertEqual(states, [True])
                self.assertTrue(editor.is_modified)

                revert()
                self.assertEqual(states, [True, False])
                self.assertFalse(editor.is_modified)

    def test_adding_moving_duplicating_and_removing_effects_report_unsaved_changes(self) -> None:
        editor = self.open_spell(spell_data(effects=[effect(formula="1"), effect(formula="2")]))
        states: list[bool] = []
        editor.modified_changed.connect(states.append)

        editor.effect_rows[1].up_button.click()
        editor.effect_rows[1].up_button.click()
        editor.add_effect_button.click()
        editor.effect_rows[2].remove_button.click()
        editor.effect_rows[0].duplicate_button.click()
        editor.effect_rows[1].remove_button.click()

        self.assertEqual(states, [True, False] * 3)
        self.assertFalse(editor.is_modified)

    def test_saving_clears_the_unsaved_state(self) -> None:
        editor = self.open_spell()
        states: list[bool] = []
        editor.modified_changed.connect(states.append)
        editor.effect_rows[0].formula_field.set_text("11")

        editor.save()

        self.assertEqual(states, [True, False])
        self.assertFalse(editor.has_changes)

    def test_a_failed_save_keeps_the_unsaved_state(self) -> None:
        editor = self.new_spell()
        states: list[bool] = []
        editor.modified_changed.connect(states.append)

        with self.assertRaises(ValueError):
            editor.save()

        self.assertTrue(editor.is_modified)
        self.assertEqual(states, [])

    def test_a_typed_number_counts_as_an_edit_immediately(self) -> None:
        editor = self.open_spell()
        editor.cost_input.selectAll()

        QTest.keyClicks(editor.cost_input, "45")

        self.assertTrue(editor.is_modified)
        self.assertEqual(editor.cost_input.value(), 45)

    def test_the_test_controls_never_mark_the_spell_as_modified(self) -> None:
        self.write_character("Alban", {"name": "Alban", "stats": {"int": 50, "dex": 70}})
        editor = self.open_spell(
            spell_data(effects=[effect(formula="10 + diceratio(user.int, 10)")])
        )
        states: list[bool] = []
        editor.modified_changed.connect(states.append)
        tester = editor.tester

        tester.header.clicked.emit()
        tester.caster_picker.setCurrentText("Alban")
        tester.target_picker.setCurrentText("Alban")
        tester.stats_button.click()
        tester.caster_stats["int"].setValue(10)
        tester.mode_input.setCurrentText("Fixed")
        tester.caster_roll_input.setValue(10)
        tester.mode_input.setCurrentText("Random")
        tester.roll_button.click()
        tester.reset_button.click()
        QTest.qWait(TEST_DELAY_MS + 150)

        self.assertEqual(states, [])
        self.assertFalse(editor.is_modified)
        self.assertFalse(editor.has_changes)


class FormulaTesterTests(SpellEditorCase):
    def test_describe_words_a_result_briefly_and_in_full(self) -> None:
        self.assertEqual(describe(None), ("", ""))
        self.assertEqual(describe(Outcome(7, 7.0, 7, 1)), ("7", "7"))
        self.assertEqual(describe(Outcome(7, 7.0, 7, DRAWS), rolled=7), ("7", "7"))
        self.assertEqual(
            describe(Outcome(5, 9.4, 14, 1000)),
            ("~9", "about 9 (from 5 to 14 over 1000 rolls)"),
        )
        self.assertEqual(describe(Outcome(5, 9.6, 14, 1000))[0], "~10")
        self.assertEqual(
            describe(Outcome(5, 9.4, 14, 1000), rolled=12)[1],
            "about 9 (from 5 to 14 over 1000 rolls), last roll 12",
        )

    def test_a_formula_is_run_the_way_the_game_runs_it(self) -> None:
        caster = StubEntity({"str": 5})
        target = StubEntity({})

        self.assertEqual(run_formula(Formula("2 + 3 * 4"), caster, target), 14)
        self.assertEqual(run_formula(Formula("user.str * 2"), caster, target), 10)
        self.assertEqual(run_formula(Formula("target.str + 1"), caster, target), 1)
        self.assertEqual(run_formula(Formula("user.str > 3"), caster, target), 1)
        self.assertEqual(run_formula(Formula("3 - 10"), caster, target), 0)
        self.assertEqual(run_formula(Formula("1 / 0"), caster, target), 0)
        self.assertIsNone(run_formula(Formula("("), caster, target))

    def test_dice_are_found_anywhere_in_a_formula(self) -> None:
        cases = {
            "5": False,
            "user.str + 2": False,
            "10 + diceratio(user.int, 10)": True,
            "diceattack(user.dex, 20, target.con, 20) * 2": True,
            "2 * (3 + diceratio(user.int, 10))": True,
        }
        for expression, expected in cases.items():
            with self.subTest(expression):
                formula = Formula(expression)
                formula.compile()

                self.assertEqual(uses_dice(formula), expected)

    def test_character_stats_come_with_their_computed_hit_points(self) -> None:
        path = self.write_character(
            "Alban",
            {"name": "Alban", "stats": {"int": 50, "con": 40, "wis": 60, "mystery": 9}},
        )

        stats = read_character_stats(path)

        self.assertEqual(set(stats), set(SPELL_STATS))
        self.assertEqual((stats["int"], stats["str"], stats["stamina"]), (50, 0, 100))
        self.assertEqual(stats["hp"], 20)

    def test_unreadable_character_files_are_reported(self) -> None:
        broken = self.campaign.character_path / "Broken.json"
        cases = {
            "invalid JSON": "not json",
            "no stats": json.dumps({"name": "Broken"}),
            "stats that are not an object": json.dumps({"stats": [1, 2]}),
            "stat that is not a number": json.dumps({"stats": {"int": "lots"}}),
            "stat that is empty": json.dumps({"stats": {"int": None}}),
        }
        for label, text in cases.items():
            with self.subTest(label):
                broken.write_text(text, encoding="utf-8")

                with self.assertRaisesRegex(
                    ValueError, "^Could not read the stats of Broken: "
                ):
                    read_character_stats(broken)

        with self.assertRaisesRegex(ValueError, "^Could not read the stats of Missing: "):
            read_character_stats(self.campaign.character_path / "Missing.json")

    def test_the_pickers_list_the_characters_and_load_their_stats(self) -> None:
        self.write_character(
            "Alban",
            {"name": "Alban", "stats": {"int": 50, "dex": 70, "con": 40, "wis": 60}},
        )
        self.write_character("Dummy", {"name": "Dummy", "stats": {"con": 50}})
        tester = FormulaTester(self.campaign)

        for picker in (tester.caster_picker, tester.target_picker):
            self.assertEqual(
                [picker.itemText(index) for index in range(picker.count())],
                [CUSTOM, "Alban", "Dummy"],
            )
        self.assertEqual(self.stats_of(tester.caster_stats), DEFAULT_STATS)
        self.assertEqual(self.stats_of(tester.target_stats), DEFAULT_STATS)
        self.assertEqual(tester.summary_label.text(), "Custom vs Custom, random dice")

        tester.caster_picker.setCurrentText("Alban")
        tester.target_picker.setCurrentText("Dummy")

        self.assertEqual(tester.caster_stats["dex"].value(), 70)
        self.assertEqual(tester.caster_stats["hp"].value(), 20)
        self.assertEqual(tester.target_stats["con"].value(), 50)
        self.assertEqual(tester.target_stats["hp"].value(), 15)
        self.assertEqual(tester.summary_label.text(), "Alban vs Dummy, random dice")

    def test_an_unreadable_character_is_reported_and_replaced_by_the_default_stats(self) -> None:
        (self.campaign.character_path / "Broken.json").write_text("not json", encoding="utf-8")
        tester = FormulaTester(self.campaign)

        tester.caster_picker.setCurrentText("Broken")

        self.assertFalse(tester.notice_label.isHidden())
        self.assertTrue(
            tester.notice_label.text().startswith("Could not read the stats of Broken:")
        )
        self.assertEqual(self.stats_of(tester.caster_stats), DEFAULT_STATS)

        tester.caster_picker.setCurrentText(CUSTOM)

        self.assertTrue(tester.notice_label.isHidden())

    def test_stats_can_be_edited_and_reset(self) -> None:
        self.write_character("Alban", {"name": "Alban", "stats": {"dex": 70}})
        tester = FormulaTester(self.campaign)
        changes: list[str] = []
        tester.context_changed.connect(lambda: changes.append("changed"))
        self.assertTrue(tester.stats_panel.isHidden())

        tester.stats_button.click()
        self.assertFalse(tester.stats_panel.isHidden())
        self.assertEqual(tester.stats_button.text(), "Hide stats")

        tester.caster_picker.setCurrentText("Alban")
        tester.caster_stats["dex"].setValue(99)
        self.assertEqual(len(changes), 2)

        tester.reset_button.click()

        self.assertEqual(tester.caster_stats["dex"].value(), 70)
        self.assertEqual(len(changes), 3)

        tester.stats_button.click()
        self.assertTrue(tester.stats_panel.isHidden())
        self.assertEqual(tester.stats_button.text(), "Edit stats")

    def test_the_section_opens_and_closes_from_its_header(self) -> None:
        tester = FormulaTester(self.campaign)
        self.assertFalse(tester.expanded)
        self.assertEqual(tester.chevron_label.text(), "\u25B8")

        tester.header.clicked.emit()
        self.assertTrue(tester.expanded)
        self.assertEqual(tester.chevron_label.text(), "\u25BE")

        tester.header.clicked.emit()
        self.assertFalse(tester.expanded)

    def test_opening_the_section_reads_the_characters_again(self) -> None:
        self.write_character("Alban", {"name": "Alban", "stats": {}})
        tester = FormulaTester(self.campaign)
        tester.caster_picker.setCurrentText("Alban")
        self.write_character("Zoe", {"name": "Zoe", "stats": {}})

        tester.set_expanded(True)

        self.assertEqual(
            [tester.caster_picker.itemText(index) for index in range(tester.caster_picker.count())],
            [CUSTOM, "Alban", "Zoe"],
        )
        self.assertEqual(tester.caster_picker.currentText(), "Alban")

        (self.campaign.character_path / "Alban.json").unlink()
        tester.refresh_characters()

        self.assertEqual(tester.caster_picker.currentText(), CUSTOM)

    def test_the_dice_mode_shows_its_own_controls(self) -> None:
        tester = FormulaTester(self.campaign)
        self.assertTrue(tester.fixed_dice.isHidden())
        self.assertTrue(tester.roll_button.isEnabled())

        tester.mode_input.setCurrentText("Fixed")
        self.assertFalse(tester.fixed_dice.isHidden())
        self.assertFalse(tester.roll_button.isEnabled())
        self.assertEqual(
            tester.summary_label.text(),
            "Custom vs Custom, fixed dice (50 / 50)",
        )

        tester.caster_roll_input.setValue(10)
        self.assertEqual(
            tester.summary_label.text(),
            "Custom vs Custom, fixed dice (10 / 50)",
        )

        tester.mode_input.setCurrentText("Random")
        self.assertTrue(tester.fixed_dice.isHidden())
        self.assertTrue(tester.roll_button.isEnabled())

    def test_a_constant_formula_has_an_exact_outcome_and_a_dice_formula_a_range(self) -> None:
        tester = FormulaTester(self.campaign)

        self.assertEqual(tester.outcome("5"), Outcome(5, 5.0, 5, 1))
        self.assertEqual(tester.outcome("  7 "), Outcome(7, 7.0, 7, 1))
        self.assertEqual(tester.outcome("user.hp <= 20"), Outcome(1, 1.0, 1, 1))
        self.assertIs(tester.outcome("5"), tester.outcome("5"))

        dice = tester.outcome("10 + diceratio(user.int, 10)")

        self.assertEqual(dice.draws, DRAWS)
        self.assertFalse(dice.exact)
        self.assertGreaterEqual(dice.minimum, 5)
        self.assertLessEqual(dice.maximum, 14)
        self.assertLessEqual(dice.minimum, dice.average)
        self.assertLessEqual(dice.average, dice.maximum)

    def test_an_unreadable_formula_has_no_outcome(self) -> None:
        tester = FormulaTester(self.campaign)

        for expression in ("", "   ", "user.a.b", "(", "user.mana", "5 +"):
            with self.subTest(expression):
                self.assertIsNone(tester.outcome(expression))
                self.assertIsNone(tester.last_roll(expression))

    def test_the_results_follow_the_stats(self) -> None:
        tester = FormulaTester(self.campaign)
        expression = "user.str + target.con"
        self.assertEqual(tester.outcome(expression).minimum, 100)

        tester.caster_stats["str"].setValue(7)
        self.assertEqual(tester.outcome(expression).minimum, 57)

        tester.target_stats["con"].setValue(3)
        self.assertEqual(tester.outcome(expression).minimum, 10)

    def test_fixed_dice_give_the_same_result_every_time(self) -> None:
        tester = FormulaTester(self.campaign)
        tester.mode_input.setCurrentText("Fixed")
        expression = "10 + diceratio(user.int, 10)"

        # Rolling under the stat is good: the lower the die, the higher the result
        self.assertEqual(tester.outcome(expression), Outcome(10, 10.0, 10, 1))
        tester.caster_roll_input.setValue(1)
        self.assertEqual(tester.outcome(expression), Outcome(14, 14.0, 14, 1))
        tester.caster_roll_input.setValue(100)
        self.assertEqual(tester.outcome(expression), Outcome(5, 5.0, 5, 1))

        tester.caster_roll_input.setValue(50)
        tester.caster_stats["int"].setValue(60)
        self.assertEqual(tester.outcome(expression), Outcome(11, 11.0, 11, 1))

    def test_a_fixed_attack_compares_the_dice_of_both_sides(self) -> None:
        tester = FormulaTester(self.campaign)
        tester.mode_input.setCurrentText("Fixed")
        tester.caster_stats["dex"].setValue(70)
        expression = "diceattack(user.dex, 20, target.con, 20) * 2"

        self.assertEqual(tester.outcome(expression).minimum, 8)

        tester.target_roll_input.setValue(1)
        self.assertEqual(tester.outcome(expression).minimum, 0)

    def test_roll_draws_the_dice_once_and_keeps_the_values_until_the_context_changes(self) -> None:
        tester = FormulaTester(self.campaign)
        expression = "10 + diceratio(user.int, 10)"
        self.assertIsNone(tester.last_roll(expression))

        tester.roll_button.click()
        rolled = tester.last_roll(expression)

        self.assertIn(rolled, range(5, 15))
        self.assertEqual(tester.last_roll(expression), rolled)
        self.assertEqual(tester.last_roll("7"), 7)
        self.assertIsNone(tester.last_roll("("))

        tester.caster_stats["int"].setValue(60)

        self.assertIsNone(tester.last_roll(expression))

    def test_every_change_of_the_context_is_announced(self) -> None:
        tester = FormulaTester(self.campaign)
        announcements: list[str] = []
        tester.context_changed.connect(lambda: announcements.append("changed"))

        tester.caster_stats["str"].setValue(7)
        tester.target_stats["str"].setValue(7)
        tester.mode_input.setCurrentText("Fixed")
        tester.caster_roll_input.setValue(7)
        tester.target_roll_input.setValue(7)
        tester.mode_input.setCurrentText("Random")
        tester.roll_button.click()

        self.assertEqual(len(announcements), 7)

    def test_the_results_are_listed_line_by_line(self) -> None:
        tester = FormulaTester(self.campaign)
        self.assertEqual(tester.results_label.text(), "Nothing to evaluate yet.")

        tester.show_results(["Effect 1: a < b", "Effect 2: 5"])

        self.assertEqual(tester.results_label.text(), "Effect 1: a &lt; b<br/>Effect 2: 5")

        tester.show_results([])

        self.assertEqual(tester.results_label.text(), "Nothing to evaluate yet.")


class SpellTestResultTests(SpellEditorCase):
    def test_effect_rows_show_their_result(self) -> None:
        editor = self.open_spell(
            spell_data(
                effects=[
                    effect(formula="5"),
                    effect(formula="10 + diceratio(user.int, 10)"),
                    effect(formula="user.a.b"),
                ]
            )
        )
        constant, dice, unreadable = editor.effect_rows

        self.assertEqual(constant.result_label.text(), "5")
        self.assertFalse(constant.result_label.isHidden())
        self.assertEqual(constant.test_label.text(), "Test: 5")
        self.assertTrue(dice.result_label.text().startswith("~"))
        self.assertIn("rolls", dice.test_label.text())
        self.assertTrue(unreadable.result_label.isHidden())
        self.assertTrue(unreadable.test_label.isHidden())
        self.assertTrue(
            editor.tester.results_label.text().startswith(
                "Effect 1: target.hp \u2212 5<br/>Effect 2: target.hp \u2212 about "
            )
        )
        self.assertEqual(editor.tester.results_label.text().count("<br/>"), 1)

    def test_the_results_follow_the_edits_after_a_short_pause(self) -> None:
        editor = self.open_spell(spell_data(effects=[effect(formula="10")]))
        row = editor.effect_rows[0]

        row.formula_field.set_text("7")
        self.assertEqual(row.result_label.text(), "10")

        QTest.qWait(TEST_DELAY_MS + 150)

        self.assertEqual(row.result_label.text(), "7")

        editor.add_effect_button.click()
        editor.effect_rows[1].formula_field.set_text("3")
        QTest.qWait(TEST_DELAY_MS + 150)

        self.assertTrue(
            editor.tester.results_label.text().endswith("Effect 2: target.hp \u2212 3")
        )

    def test_fixed_dice_show_an_exact_result_on_the_effect(self) -> None:
        editor = self.open_spell(
            spell_data(effects=[effect(formula="10 + diceratio(user.int, 10)")])
        )
        row = editor.effect_rows[0]

        editor.tester.mode_input.setCurrentText("Fixed")
        QTest.qWait(TEST_DELAY_MS + 150)

        self.assertEqual(row.result_label.text(), "10")
        self.assertEqual(row.test_label.text(), "Test: 10")

    def test_roll_shows_the_drawn_value(self) -> None:
        editor = self.open_spell(
            spell_data(effects=[effect(formula="10 + diceratio(user.int, 10)")])
        )
        row = editor.effect_rows[0]

        editor.tester.roll_button.click()
        QTest.qWait(TEST_DELAY_MS + 150)

        self.assertIn("last roll", row.test_label.text())
        self.assertIn("last roll", editor.tester.results_label.text())

    def test_the_result_uses_the_stats_of_the_picked_characters(self) -> None:
        self.write_character("Alban", {"name": "Alban", "stats": {"str": 33}})
        editor = self.open_spell(spell_data(effects=[effect(formula="user.str")]))
        row = editor.effect_rows[0]
        self.assertEqual(row.result_label.text(), "50")

        editor.tester.caster_picker.setCurrentText("Alban")
        QTest.qWait(TEST_DELAY_MS + 150)

        self.assertEqual(row.result_label.text(), "33")


class SpellWidgetTests(SpellEditorCase):
    def test_a_value_combo_box_keeps_the_values_it_does_not_know(self) -> None:
        combo = ValueComboBox(("a", "b"))
        picks: list[int] = []
        combo.currentIndexChanged.connect(picks.append)

        combo.set_value("b")
        self.assertEqual((combo.value, combo.is_known), ("b", True))

        combo.set_value("odd")
        self.assertEqual((combo.value, combo.is_known, combo.count()), ("odd", False, 3))

        combo.set_value("stranger")
        self.assertEqual((combo.value, combo.count()), ("stranger", 3))

        combo.set_value("a")
        self.assertEqual((combo.value, combo.is_known, combo.count()), ("a", True, 2))
        self.assertEqual(picks, [])

        combo.setCurrentIndex(1)
        self.assertEqual(picks, [1])

    def test_a_clickable_frame_reports_left_clicks_inside_it_only(self) -> None:
        frame = ClickableFrame()
        self.show_widget(frame, (120, 40))
        clicks: list[str] = []
        frame.clicked.connect(lambda: clicks.append("click"))

        QTest.mouseClick(frame, Qt.MouseButton.RightButton)
        self.assertEqual(clicks, [])

        QTest.mouseClick(frame, Qt.MouseButton.LeftButton)
        self.assertEqual(clicks, ["click"])

        QTest.mousePress(frame, Qt.MouseButton.LeftButton)
        QTest.mouseRelease(
            frame,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(500, 500),
        )
        self.assertEqual(clicks, ["click"])

    def test_an_elided_label_keeps_its_full_text(self) -> None:
        label = ElidedLabel("a very long formula")

        self.assertEqual(label.full_text, "a very long formula")
        self.assertEqual(label.toolTip(), "a very long formula")

        label.set_full_text("short")

        self.assertEqual((label.full_text, label.toolTip()), ("short", "short"))

    def test_the_insert_menu_adds_snippets_at_the_cursor(self) -> None:
        field = FormulaField("")
        changes: list[str] = []
        field.textChanged.connect(changes.append)
        actions = {action.text(): action for action in field.insert_button.menu().actions()}
        self.assertEqual(
            list(actions),
            ["User stat", "Target stat", "Dice ratio", "Dice attack"],
        )

        field.set_text("5 +")
        user_stats = {
            action.text(): action for action in actions["User stat"].menu().actions()
        }
        user_stats["str"].trigger()
        self.assertEqual(field.text(), "5 + user.str")

        field.set_text("2 * ")
        dice_ratio = {
            action.text(): action for action in actions["Dice ratio"].menu().actions()
        }
        target_ratio = {
            action.text(): action for action in dice_ratio["Target"].menu().actions()
        }
        target_ratio["con"].trigger()
        self.assertEqual(field.text(), "2 * diceratio(target.con, 10)")
        self.assertEqual(field.line_edit.selectedText(), "10")

        field.set_text("")
        actions["Dice attack"].trigger()
        self.assertEqual(field.text(), "diceattack(user.dex, 20, target.con, 20)")
        self.assertEqual(field.line_edit.selectedText(), "20")
        self.assertTrue(changes)

    def test_only_a_condition_offers_comparisons_and_logic(self) -> None:
        plain = FormulaField("")
        condition = FormulaField("", condition=True)
        plain_titles = [action.text() for action in plain.insert_button.menu().actions()]
        actions = {action.text(): action for action in condition.insert_button.menu().actions()}

        self.assertNotIn("Comparison and logic", plain_titles)
        self.assertIn("Comparison and logic", actions)

        condition.set_text("user.hp")
        operators = {
            action.text(): action for action in actions["Comparison and logic"].menu().actions()
        }
        operators["Less or equal  (<=)"].trigger()

        self.assertEqual(condition.text(), "user.hp <= ")

    def test_the_formula_field_previews_what_is_typed(self) -> None:
        field = FormulaField("")
        self.assertTrue(field.preview.isHidden())

        field.set_text("10 + diceratio(user.int, 10)")
        self.assertFalse(field.preview.isHidden())

        field.set_text("   ")
        self.assertTrue(field.preview.isHidden())


if __name__ == "__main__":
    unittest.main()
