import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from libs.assets.campaign import Campaign
from libs.assets.character import Character
from libs.gui.editor.character import CharacterEditor


class CharacterEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.campaign = Campaign(
            "Editor Test",
            Path(self.temporary_directory.name),
        )
        self.campaign.ensure_directories()

    def tearDown(self) -> None:
        self.app.processEvents()
        self.temporary_directory.cleanup()

    def _write_character(self, filename: str, data: dict) -> Path:
        path = self.campaign.character_path / filename
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_load_save_preserves_legacy_fields_and_renames_file(self) -> None:
        original_path = self._write_character(
            "Legacy.json",
            {
                "name": "Legacy",
                "stats": {"str": 10, "con": 40, "wis": 60},
                "stats_modifier": {"con": 3},
                "spells": ["OldSpell"],
                "inventory": [["Potion", 2]],
                "custom_field": {"keep": True},
            },
        )
        editor = CharacterEditor("Legacy", self.campaign)
        saved_events: list[tuple[str, str]] = []
        editor.saved.connect(
            lambda path, name: saved_events.append((path, name))
        )

        self.assertEqual(editor.inventory_table.rowCount(), 1)
        self.assertEqual(editor.resource_inputs["stamina"].value(), 100)
        self.assertEqual(editor.hp_label.text(), "HP 20")

        editor.stat_inputs["str"].setValue(77)
        editor.name_input.setText("Renamed Hero")
        editor.spell_picker.setCurrentText("NewSpell")
        editor.add_spell_button.click()
        editor.item_picker.setCurrentText("Potion")
        editor.item_quantity.setValue(4)
        editor.add_item_button.click()

        saved_path = editor.save()
        data = json.loads(saved_path.read_text(encoding="utf-8"))

        self.assertEqual(saved_path.name, "renamed_hero.json")
        self.assertFalse(original_path.exists())
        self.assertEqual(data["name"], "Renamed Hero")
        self.assertEqual(data["stats"]["str"], 77)
        self.assertEqual(data["stats_modifier"], {"con": 3})
        self.assertEqual(data["spells"], ["OldSpell", "NewSpell"])
        self.assertEqual(data["inventory"], [["Potion", 6]])
        self.assertEqual(data["custom_field"], {"keep": True})
        self.assertEqual(saved_events, [(str(saved_path), "Renamed Hero")])

    def test_dictionary_inventory_schema_is_preserved(self) -> None:
        self._write_character(
            "Dictionary.json",
            {
                "name": "Dictionary",
                "stats": {},
                "spells": [],
                "items": {"Potion": 3},
            },
        )
        editor = CharacterEditor("Dictionary", self.campaign)
        editor.save()

        data = json.loads(editor.file_path.read_text(encoding="utf-8"))
        self.assertEqual(data["items"], {"Potion": 3})
        self.assertNotIn("inventory", data)

    def test_new_character_saves_to_canonical_filename(self) -> None:
        editor = CharacterEditor(
            "New Character",
            self.campaign,
            is_new=True,
        )
        editor.name_input.setText("New Hero")
        saved_path = editor.save()

        self.assertEqual(saved_path.name, "new_hero.json")
        self.assertTrue(saved_path.is_file())
        self.assertFalse(editor.is_new)

    def test_spell_and_item_pickers_start_without_a_selection(self) -> None:
        editor = CharacterEditor("New Character", self.campaign, is_new=True)

        self.assertEqual(editor.spell_picker.currentText(), "")
        self.assertEqual(editor.item_picker.currentText(), "")

        editor.add_spell_button.click()
        editor.add_item_button.click()

        self.assertEqual(editor.spells_list.count(), 0)
        self.assertEqual(editor.inventory_table.rowCount(), 0)

    def test_save_button_emits_the_editor_save_request(self) -> None:
        editor = CharacterEditor("New Character", self.campaign, is_new=True)
        requests: list[bool] = []
        editor.save_requested.connect(lambda: requests.append(True))

        editor.save_button.click()

        self.assertEqual(requests, [True])

    def test_new_character_does_not_overwrite_existing_file(self) -> None:
        self._write_character(
            "already_there.json",
            {"name": "Already There", "stats": {}, "spells": [], "inventory": []},
        )
        editor = CharacterEditor("New Character", self.campaign, is_new=True)
        editor.name_input.setText("Already There")

        with self.assertRaises(FileExistsError):
            editor.save()

    def test_renaming_a_character_changes_no_other_asset(self) -> None:
        self._write_character(
            "Alistair.json",
            {"name": "Alistair", "stats": {}, "spells": ["Heal"], "inventory": []},
        )
        bob = self._write_character(
            "Bob.json",
            {"name": "Bob", "stats": {}, "spells": ["Alistair"], "inventory": [["Alistair", 1]]},
        )
        bob_before = bob.read_text(encoding="utf-8")
        editor = CharacterEditor("Alistair", self.campaign)
        renames: list[tuple] = []
        editor.renamed.connect(lambda *event: renames.append(event))

        editor.name_input.setText("Al")
        editor.save()

        self.assertEqual(renames, [("Alistair", "Al", [])])
        self.assertEqual(bob.read_text(encoding="utf-8"), bob_before)

    def test_update_references_follows_renamed_spells_and_items(self) -> None:
        self._write_character(
            "Legacy.json",
            {
                "name": "Legacy",
                "stats": {"str": 10},
                "spells": ["Fireball", "Heal"],
                "inventory": [["Shield", 2], ["Potion", 1]],
            },
        )
        editor = CharacterEditor("Legacy", self.campaign)
        editor.stat_inputs["str"].setValue(33)

        self.assertTrue(editor.update_references("spells", "Fireball", "Fire Ball"))
        self.assertTrue(editor.update_references("items", "Shield", "Tower Shield"))
        self.assertFalse(editor.update_references("items", "Rope", "Cord"))

        self.assertEqual(
            [editor.spells_list.item(row).text() for row in range(editor.spells_list.count())],
            ["Fire Ball", "Heal"],
        )
        self.assertEqual(editor.inventory_table.rowCount(), 2)
        self.assertEqual(editor.inventory_table.item(0, 0).text(), "Tower Shield")
        self.assertEqual(editor.inventory_table.cellWidget(0, 1).value(), 2)
        self.assertEqual(editor.stat_inputs["str"].value(), 33)

        data = json.loads(editor.save().read_text(encoding="utf-8"))

        self.assertEqual(data["spells"], ["Fire Ball", "Heal"])
        self.assertEqual(data["inventory"], [["Tower Shield", 2], ["Potion", 1]])
        self.assertEqual(data["stats"]["str"], 33)

    def test_update_references_keeps_the_dictionary_inventory_schema(self) -> None:
        self._write_character(
            "Dictionary.json",
            {"name": "Dictionary", "stats": {}, "spells": [], "items": {"Shield": 2}},
        )
        editor = CharacterEditor("Dictionary", self.campaign)

        self.assertTrue(editor.update_references("items", "Shield", "Tower Shield"))
        data = json.loads(editor.save().read_text(encoding="utf-8"))

        self.assertEqual(data["items"], {"Tower Shield": 2})
        self.assertNotIn("inventory", data)

    def test_update_references_applies_to_a_character_that_is_not_saved_yet(self) -> None:
        editor = CharacterEditor("New Character", self.campaign, is_new=True)
        editor.spell_picker.setCurrentText("Fireball")
        editor.add_spell_button.click()

        self.assertTrue(editor.update_references("spells", "fireball", "Fire Ball"))

        self.assertEqual(editor.spells_list.count(), 1)
        self.assertEqual(editor.spells_list.item(0).text(), "Fire Ball")
        self.assertFalse(editor.update_references("spells", "Heal", "Cure"))

    def test_new_character_is_unsaved_until_its_first_save(self) -> None:
        editor = CharacterEditor("New Character", self.campaign, is_new=True)
        states: list[bool] = []
        editor.modified_changed.connect(states.append)

        self.assertTrue(editor.is_modified)
        self.assertFalse(editor.has_changes)
        editor.name_input.setText("New Hero")
        self.assertTrue(editor.has_changes)
        editor.save()

        self.assertFalse(editor.is_modified)
        self.assertFalse(editor.has_changes)
        self.assertEqual(editor.title, "New Hero")
        self.assertEqual(states, [False])

    def test_every_widget_edit_is_reported_until_it_is_undone(self) -> None:
        self._write_character(
            "Legacy.json",
            {
                "name": "Legacy",
                "stats": {"str": 10},
                "spells": ["Fireball"],
                "inventory": [["Potion", 2]],
            },
        )
        editor = CharacterEditor("Legacy", self.campaign)
        states: list[bool] = []
        editor.modified_changed.connect(states.append)
        self.assertFalse(editor.is_modified)

        def add_spell() -> None:
            editor.spell_picker.setCurrentText("Heal")
            editor.add_spell_button.click()

        def remove_spell() -> None:
            editor.spells_list.setCurrentRow(1)
            editor.remove_spell_button.click()

        def add_item() -> None:
            editor.item_picker.setCurrentText("Rope")
            editor.add_item_button.click()

        def remove_item() -> None:
            editor.inventory_table.selectRow(1)
            editor.remove_item_button.click()

        edits = (
            (lambda: editor.name_input.setText("Renamed"), lambda: editor.name_input.setText("Legacy")),
            (lambda: editor.stat_inputs["str"].setValue(77), lambda: editor.stat_inputs["str"].setValue(10)),
            (
                lambda: editor.resource_inputs["stamina"].setValue(5),
                lambda: editor.resource_inputs["stamina"].setValue(100),
            ),
            (add_spell, remove_spell),
            (add_item, remove_item),
            (
                lambda: editor.inventory_table.cellWidget(0, 1).setValue(9),
                lambda: editor.inventory_table.cellWidget(0, 1).setValue(2),
            ),
            (
                lambda: editor.inventory_table.item(0, 0).setText("Elixir"),
                lambda: editor.inventory_table.item(0, 0).setText("Potion"),
            ),
        )
        for edit, undo in edits:
            edit()
            self.assertTrue(editor.is_modified)
            undo()
            self.assertFalse(editor.is_modified)

        self.assertEqual(states, [True, False] * len(edits))

        editor.stat_inputs["str"].setValue(77)
        editor.save()

        self.assertFalse(editor.is_modified)
        self.assertEqual(states[-2:], [True, False])

    def test_reference_updates_keep_the_unsaved_state(self) -> None:
        self._write_character(
            "Legacy.json",
            {"name": "Legacy", "stats": {"str": 10}, "spells": [], "inventory": [["Shield", 2]]},
        )
        clean = CharacterEditor("Legacy", self.campaign)
        edited = CharacterEditor("Legacy", self.campaign)
        edited.stat_inputs["str"].setValue(33)
        states: list[bool] = []
        clean.modified_changed.connect(states.append)
        edited.modified_changed.connect(states.append)

        self.assertTrue(clean.update_references("items", "Shield", "Tower Shield"))
        self.assertTrue(edited.update_references("items", "Shield", "Tower Shield"))

        self.assertFalse(clean.is_modified)
        self.assertTrue(edited.is_modified)
        self.assertEqual(states, [])

    def test_reference_update_clears_an_edit_that_now_matches_the_file(self) -> None:
        self._write_character(
            "Legacy.json",
            {"name": "Legacy", "stats": {}, "spells": [], "inventory": [["Shield", 2]]},
        )
        editor = CharacterEditor("Legacy", self.campaign)
        states: list[bool] = []
        editor.modified_changed.connect(states.append)
        editor.inventory_table.item(0, 0).setText("Tower Shield")
        self.assertTrue(editor.is_modified)

        self.assertTrue(editor.update_references("items", "Shield", "Tower Shield"))

        self.assertFalse(editor.is_modified)
        self.assertEqual(states, [True, False])

    def test_reference_update_follows_the_file_for_a_reference_removed_in_the_editor(self) -> None:
        self._write_character(
            "Legacy.json",
            {"name": "Legacy", "stats": {}, "spells": [], "inventory": [["Shield", 2]]},
        )
        editor = CharacterEditor("Legacy", self.campaign)
        editor.inventory_table.selectRow(0)
        editor.remove_item_button.click()
        self.assertTrue(editor.is_modified)

        self.assertTrue(editor.update_references("items", "Shield", "Tower Shield"))
        self.assertTrue(editor.is_modified)

        editor.item_picker.setCurrentText("Tower Shield")
        editor.item_quantity.setValue(2)
        editor.add_item_button.click()

        self.assertFalse(editor.is_modified)

    def test_character_model_reads_legacy_inventory(self) -> None:
        character = Character.from_dict(
            {
                "name": "Legacy",
                "stats": {},
                "spells": [],
                "inventory": [["Potion", 2]],
            }
        )

        self.assertEqual(character.items.items, {"Potion": 2})
