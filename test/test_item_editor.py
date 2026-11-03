import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from libs.assets.campaign import Campaign
from libs.assets.item import Item
from libs.gui.editor.item import ItemEditor


class ItemEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.campaign = Campaign(
            "Item Editor Test",
            Path(self.temporary_directory.name),
        )
        self.campaign.ensure_directories()

    def tearDown(self) -> None:
        self.app.processEvents()
        self.temporary_directory.cleanup()

    def _write_item(self, filename: str, data: dict) -> Path:
        path = self.campaign.item_path / filename
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def _write_character(self, filename: str, data: dict) -> Path:
        path = self.campaign.character_path / filename
        path.write_text(json.dumps(data, indent=4), encoding="utf-8")
        return path

    def test_load_save_preserves_unknown_fields_and_renames_file(self) -> None:
        original_path = self._write_item(
            "Shield.json",
            {
                "name": "Shield",
                "description": "Old description",
                "modifier": [["con", 5], ["agi", -2]],
                "rarity": "rare",
            },
        )
        editor = ItemEditor("Shield", self.campaign)
        saved_events: list[tuple[str, str]] = []
        editor.saved.connect(lambda path, name: saved_events.append((path, name)))

        self.assertEqual(editor.name_input.text(), "Shield")
        self.assertEqual(editor.description_input.toPlainText(), "Old description")
        self.assertEqual(editor.modifiers_table.rowCount(), 2)

        editor.name_input.setText("Tower Shield")
        editor.description_input.setPlainText("Heavy\nand sturdy")
        editor.modifiers_table.cellWidget(0, 1).setValue(12)
        editor.stat_picker.setCurrentText("str")
        editor.modifier_value.setValue(3)
        editor.add_modifier_button.click()

        saved_path = editor.save()
        data = json.loads(saved_path.read_text(encoding="utf-8"))

        self.assertEqual(saved_path.name, "tower_shield.json")
        self.assertFalse(original_path.exists())
        self.assertEqual(data["name"], "Tower Shield")
        self.assertEqual(data["description"], "Heavy\nand sturdy")
        self.assertEqual(data["modifier"], [["con", 12], ["agi", -2], ["str", 3]])
        self.assertEqual(data["rarity"], "rare")
        self.assertEqual(saved_events, [(str(saved_path), "Tower Shield")])

    def test_save_keeps_existing_filename_when_only_case_differs(self) -> None:
        original_path = self._write_item(
            "Exosquelette.json",
            {"name": "Exosquelette", "description": "", "modifier": []},
        )
        editor = ItemEditor("Exosquelette", self.campaign)

        editor.name_input.setText("exosquelette")
        saved_path = editor.save()

        self.assertEqual(saved_path.name, "Exosquelette.json")
        self.assertTrue(original_path.is_file())
        self.assertEqual(
            json.loads(saved_path.read_text(encoding="utf-8"))["name"],
            "exosquelette",
        )

    def test_new_item_is_saved_with_a_canonical_filename(self) -> None:
        editor = ItemEditor("New Item", self.campaign, is_new=True)

        self.assertEqual(editor.name_input.text(), "")
        self.assertEqual(editor.modifiers_table.rowCount(), 0)

        editor.name_input.setText("  Healing Potion ")
        editor.description_input.setPlainText("Restores health")
        editor.stat_picker.setCurrentText("hp")
        editor.modifier_value.setValue(25)
        editor.add_modifier_button.click()
        saved_path = editor.save()

        self.assertEqual(saved_path, self.campaign.item_path / "healing_potion.json")
        item = Item.from_dict(json.loads(saved_path.read_text(encoding="utf-8")))
        self.assertEqual(item.name, "Healing Potion")
        self.assertEqual(item.description, "Restores health")
        self.assertEqual(item.modifier, [["hp", 25]])

    def test_new_item_does_not_overwrite_an_existing_file(self) -> None:
        existing_path = self._write_item(
            "healing_potion.json",
            {"name": "Healing Potion", "description": "Keep me", "modifier": []},
        )
        editor = ItemEditor("New Item", self.campaign, is_new=True)

        editor.name_input.setText("Healing Potion")
        with self.assertRaises(FileExistsError):
            editor.save()

        self.assertEqual(
            json.loads(existing_path.read_text(encoding="utf-8"))["description"],
            "Keep me",
        )

    def test_saved_item_is_edited_in_place_afterwards(self) -> None:
        editor = ItemEditor("New Item", self.campaign, is_new=True)
        editor.name_input.setText("Dagger")
        first_path = editor.save()

        editor.description_input.setPlainText("Sharp")
        second_path = editor.save()

        self.assertEqual(first_path, second_path)
        self.assertEqual(
            json.loads(second_path.read_text(encoding="utf-8"))["description"],
            "Sharp",
        )

    def test_adding_the_same_stat_merges_modifier_values(self) -> None:
        editor = ItemEditor("New Item", self.campaign, is_new=True)

        editor.stat_picker.setCurrentText("str")
        editor.modifier_value.setValue(5)
        editor.add_modifier_button.click()
        editor.modifier_value.setValue(7)
        editor.add_modifier_button.click()

        self.assertEqual(editor.modifiers_table.rowCount(), 1)
        self.assertEqual(editor.modifiers_table.cellWidget(0, 1).value(), 12)

    def test_remove_button_drops_the_selected_modifier(self) -> None:
        self._write_item(
            "Ring.json",
            {
                "name": "Ring",
                "description": "",
                "modifier": [["str", 1], ["dex", 2], ["luc", 3]],
            },
        )
        editor = ItemEditor("Ring", self.campaign)

        editor.modifiers_table.selectRow(1)
        editor.remove_modifier_button.click()
        saved_path = editor.save()

        self.assertEqual(
            json.loads(saved_path.read_text(encoding="utf-8"))["modifier"],
            [["str", 1], ["luc", 3]],
        )

    def test_malformed_modifier_entries_are_ignored(self) -> None:
        self._write_item(
            "Odd.json",
            {
                "name": "Odd",
                "description": "",
                "modifier": [["str", 5], "bad", ["dex"], ["con", "x"], ["agi", "4"]],
            },
        )
        editor = ItemEditor("Odd", self.campaign)

        self.assertEqual(editor.modifiers_table.rowCount(), 2)
        saved_path = editor.save()

        self.assertEqual(
            json.loads(saved_path.read_text(encoding="utf-8"))["modifier"],
            [["str", 5], ["agi", 4]],
        )

    def test_save_requires_a_valid_name(self) -> None:
        editor = ItemEditor("New Item", self.campaign, is_new=True)

        for invalid_name in ("", "   ", "bad/name", "what?"):
            editor.name_input.setText(invalid_name)
            with self.assertRaises(ValueError):
                editor.save()

        self.assertEqual(list(self.campaign.item_path.glob("*.json")), [])

    def test_loading_rejects_missing_or_unsafe_resources(self) -> None:
        with self.assertRaises(FileNotFoundError):
            ItemEditor("Ghost", self.campaign)
        with self.assertRaises(ValueError):
            ItemEditor("../escape", self.campaign)

    def test_loading_rejects_invalid_json(self) -> None:
        (self.campaign.item_path / "Broken.json").write_text(
            "not json",
            encoding="utf-8",
        )

        with self.assertRaises(ValueError):
            ItemEditor("Broken", self.campaign)

    def test_save_button_requests_a_save(self) -> None:
        editor = ItemEditor("New Item", self.campaign, is_new=True)
        requests: list[str] = []
        editor.save_requested.connect(lambda: requests.append("save"))

        editor.save_button.click()

        self.assertEqual(requests, ["save"])

    def test_renaming_an_item_rewrites_the_characters_that_carry_it(self) -> None:
        self._write_item(
            "Shield.json",
            {"name": "Shield", "description": "", "modifier": []},
        )
        alistair = self._write_character(
            "Alistair.json",
            {
                "name": "Alistair",
                "stats": {"str": 50},
                "inventory": [["Shield", 2], ["Potion", 1]],
            },
        )
        bob = self._write_character("Bob.json", {"name": "Bob", "items": {"shield": 1}})
        cara = self._write_character(
            "Cara.json",
            {"name": "Cara", "inventory": [["Potion", 4]]},
        )
        cara_before = cara.read_text(encoding="utf-8")
        editor = ItemEditor("Shield", self.campaign)
        renames: list[tuple] = []
        editor.renamed.connect(lambda *event: renames.append(event))

        editor.name_input.setText("Tower Shield")
        editor.save()

        self.assertEqual(
            json.loads(alistair.read_text(encoding="utf-8")),
            {
                "name": "Alistair",
                "stats": {"str": 50},
                "inventory": [["Tower Shield", 2], ["Potion", 1]],
            },
        )
        self.assertEqual(
            json.loads(bob.read_text(encoding="utf-8")),
            {"name": "Bob", "items": {"Tower Shield": 1}},
        )
        self.assertEqual(cara.read_text(encoding="utf-8"), cara_before)
        self.assertEqual(len(renames), 1)
        old_stem, new_name, updates = renames[0]
        self.assertEqual((old_stem, new_name), ("Shield", "Tower Shield"))
        self.assertEqual(
            sorted(update.path.name for update in updates),
            ["Alistair.json", "Bob.json"],
        )

    def test_saving_without_renaming_leaves_the_characters_alone(self) -> None:
        self._write_item(
            "Shield.json",
            {"name": "Shield", "description": "", "modifier": []},
        )
        character = self._write_character(
            "Alistair.json",
            {"name": "Alistair", "inventory": [["Shield", 2]]},
        )
        character_before = character.read_text(encoding="utf-8")
        editor = ItemEditor("Shield", self.campaign)
        renames: list[tuple] = []
        editor.renamed.connect(lambda *event: renames.append(event))

        editor.description_input.setPlainText("Sturdy")
        editor.save()
        editor.name_input.setText("shield")
        editor.save()

        self.assertEqual(renames, [])
        self.assertEqual(character.read_text(encoding="utf-8"), character_before)

    def test_renaming_onto_an_existing_item_changes_nothing(self) -> None:
        self._write_item(
            "Shield.json",
            {"name": "Shield", "description": "", "modifier": []},
        )
        self._write_item(
            "tower_shield.json",
            {"name": "Tower Shield", "description": "", "modifier": []},
        )
        character = self._write_character(
            "Alistair.json",
            {"name": "Alistair", "inventory": [["Shield", 2]]},
        )
        character_before = character.read_text(encoding="utf-8")
        editor = ItemEditor("Shield", self.campaign)

        editor.name_input.setText("Tower Shield")
        with self.assertRaises(FileExistsError):
            editor.save()

        self.assertEqual(character.read_text(encoding="utf-8"), character_before)

    def test_failed_rename_leaves_the_item_and_the_characters_untouched(self) -> None:
        item_path = self._write_item(
            "Shield.json",
            {"name": "Shield", "description": "", "modifier": []},
        )
        character = self._write_character(
            "Alistair.json",
            {"name": "Alistair", "inventory": [["Shield", 2]]},
        )
        item_before = item_path.read_text(encoding="utf-8")
        character_before = character.read_text(encoding="utf-8")
        editor = ItemEditor("Shield", self.campaign)
        events: list[str] = []
        editor.saved.connect(lambda *_: events.append("saved"))
        editor.renamed.connect(lambda *_: events.append("renamed"))
        editor.name_input.setText("Tower Shield")

        with patch("libs.gui.editor.base.write_text_atomic", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                editor.save()

        self.assertEqual(item_path.read_text(encoding="utf-8"), item_before)
        self.assertEqual(character.read_text(encoding="utf-8"), character_before)
        self.assertFalse((self.campaign.item_path / "tower_shield.json").exists())
        self.assertEqual(editor.file_path, item_path)
        self.assertEqual(events, [])

        editor.save()

        self.assertEqual(
            json.loads(character.read_text(encoding="utf-8"))["inventory"],
            [["Tower Shield", 2]],
        )

    def test_new_item_is_unsaved_until_its_first_save(self) -> None:
        editor = ItemEditor("New Item", self.campaign, is_new=True)
        states: list[bool] = []
        editor.modified_changed.connect(states.append)

        self.assertTrue(editor.is_modified)
        self.assertFalse(editor.has_changes)
        editor.name_input.setText("Dagger")
        self.assertTrue(editor.is_modified)
        self.assertTrue(editor.has_changes)
        editor.save()

        self.assertFalse(editor.is_modified)
        self.assertFalse(editor.has_changes)
        self.assertEqual(editor.title, "Dagger")
        self.assertEqual(states, [False])

    def test_saved_item_reports_each_edit_until_it_is_undone_or_saved(self) -> None:
        self._write_item(
            "Shield.json",
            {"name": "Shield", "description": "Sturdy", "modifier": [["con", 5]]},
        )
        editor = ItemEditor("Shield", self.campaign)
        states: list[bool] = []
        editor.modified_changed.connect(states.append)
        self.assertFalse(editor.is_modified)
        self.assertEqual(editor.title, "Shield")

        editor.description_input.setPlainText("Heavy")
        editor.description_input.setPlainText("Sturdy")
        editor.name_input.setText("Tower Shield")
        editor.name_input.setText("Shield")
        editor.modifiers_table.cellWidget(0, 1).setValue(9)
        editor.modifiers_table.cellWidget(0, 1).setValue(5)
        editor.stat_picker.setCurrentText("str")
        editor.add_modifier_button.click()
        editor.modifiers_table.selectRow(1)
        editor.remove_modifier_button.click()

        self.assertEqual(states, [True, False] * 4)
        self.assertFalse(editor.is_modified)

        editor.description_input.setPlainText("Heavy")
        editor.save()

        self.assertEqual(states[-2:], [True, False])
        self.assertFalse(editor.is_modified)

    def test_failed_save_keeps_the_unsaved_state(self) -> None:
        editor = ItemEditor("New Item", self.campaign, is_new=True)
        states: list[bool] = []
        editor.modified_changed.connect(states.append)

        with self.assertRaises(ValueError):
            editor.save()

        self.assertTrue(editor.is_modified)
        self.assertEqual(states, [])

    def test_typed_spin_box_value_counts_as_an_edit_immediately(self) -> None:
        self._write_item(
            "Ring.json",
            {"name": "Ring", "description": "", "modifier": [["str", 1]]},
        )
        editor = ItemEditor("Ring", self.campaign)
        spin_box = editor.modifiers_table.cellWidget(0, 1)
        spin_box.selectAll()

        QTest.keyClicks(spin_box, "12")

        self.assertTrue(editor.is_modified)
        self.assertEqual(spin_box.value(), 12)


if __name__ == "__main__":
    unittest.main()
