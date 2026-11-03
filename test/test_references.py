import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit

from libs.assets import references
from libs.assets.campaign import Campaign
from libs.assets.references import (
    REFERENCES,
    RESOURCE_DIRECTORIES,
    ReferenceUpdate,
    apply_updates,
    canonical_name,
    plan_rename,
    rename_in_data,
    revert_updates,
    write_text_atomic,
)
from libs.gui.editor.base import ResourceEditor


class RenameInDataTests(unittest.TestCase):
    def test_canonical_name_matches_the_asset_loaders(self) -> None:
        self.assertEqual(canonical_name("Shock sphere"), "shock_sphere")
        self.assertEqual(canonical_name("Red_Robin"), "red_robin")

    def test_every_declared_reference_can_be_located_on_disk(self) -> None:
        for reference in REFERENCES:
            self.assertIn(reference.source, RESOURCE_DIRECTORIES)
            self.assertIn(reference.target, RESOURCE_DIRECTORIES)
            self.assertTrue(reference.fields)

    def test_spells_are_rewritten_whatever_their_spelling(self) -> None:
        data = {"spells": ["Shock sphere", "Fireball", "Shock_Sphere", "shock_sphere"]}

        count = rename_in_data("characters", data, "spells", "shock_sphere", "Lightning Orb")

        self.assertEqual(count, 3)
        self.assertEqual(data["spells"], ["Lightning Orb", "Fireball"])

    def test_inventory_entries_are_rewritten_and_keep_their_quantity(self) -> None:
        data = {"inventory": [["Potion", 1], ["Shield", 2]]}

        count = rename_in_data("characters", data, "items", "Shield", "Tower Shield")

        self.assertEqual(count, 1)
        self.assertEqual(data["inventory"], [["Potion", 1], ["Tower Shield", 2]])

    def test_inventory_stored_as_a_dictionary_keeps_its_shape_and_order(self) -> None:
        data = {"items": {"Potion": 1, "Shield": 2, "Rope": 3}}

        count = rename_in_data("characters", data, "items", "shield", "Tower Shield")

        self.assertEqual(count, 1)
        self.assertEqual(
            list(data["items"].items()),
            [("Potion", 1), ("Tower Shield", 2), ("Rope", 3)],
        )

    def test_both_inventory_keys_are_rewritten(self) -> None:
        data = {"items": [["Shield", 1]], "inventory": [["Shield", 1]]}

        count = rename_in_data("characters", data, "items", "Shield", "Tower Shield")

        self.assertEqual(count, 2)
        self.assertEqual(data["items"], [["Tower Shield", 1]])
        self.assertEqual(data["inventory"], [["Tower Shield", 1]])

    def test_renaming_onto_an_existing_inventory_entry_merges_quantities(self) -> None:
        list_data = {"inventory": [["Shield", 2], ["Potion", 1], ["Tower Shield", 3]]}
        dict_data = {"inventory": {"Shield": 2, "Tower Shield": 3}}

        rename_in_data("characters", list_data, "items", "Shield", "Tower Shield")
        rename_in_data("characters", dict_data, "items", "Shield", "Tower Shield")

        self.assertEqual(list_data["inventory"], [["Tower Shield", 5], ["Potion", 1]])
        self.assertEqual(dict_data["inventory"], {"Tower Shield": 5})

    def test_data_without_a_reference_is_left_untouched(self) -> None:
        data = {"spells": ["Fireball"], "inventory": [["Potion", 1]], "name": "Shield"}
        expected = json.loads(json.dumps(data))

        count = rename_in_data("characters", data, "items", "Shield", "Tower Shield")

        self.assertEqual(count, 0)
        self.assertEqual(data, expected)

    def test_only_declared_resource_types_are_rewritten(self) -> None:
        spells = {"spells": ["Old"]}
        inventory = {"inventory": [["Old", 1]]}

        self.assertEqual(rename_in_data("items", spells, "spells", "Old", "New"), 0)
        self.assertEqual(rename_in_data("characters", spells, "items", "Old", "New"), 0)
        self.assertEqual(rename_in_data("characters", inventory, "spells", "Old", "New"), 0)
        self.assertEqual(rename_in_data("characters", inventory, "characters", "Old", "New"), 0)
        self.assertEqual(spells, {"spells": ["Old"]})
        self.assertEqual(inventory, {"inventory": [["Old", 1]]})

    def test_malformed_fields_are_ignored(self) -> None:
        data = {"spells": "Old", "inventory": ["Old", 5, None, [None, 1], ["Other", 1]]}
        expected = json.loads(json.dumps(data))

        count = rename_in_data("characters", data, "items", "Old", "New")
        count += rename_in_data("characters", data, "spells", "Old", "New")

        self.assertEqual(count, 0)
        self.assertEqual(data, expected)


class FileRewriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.campaign = Campaign(
            "References Test",
            Path(self.temporary_directory.name),
        )
        self.campaign.ensure_directories()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _write_character(self, filename: str, data: dict | str) -> Path:
        path = self.campaign.character_path / filename
        path.write_text(
            data if isinstance(data, str) else json.dumps(data, indent=4),
            encoding="utf-8",
        )
        return path

    def _snapshot(self) -> dict[str, str]:
        return {
            path.name: path.read_text(encoding="utf-8")
            for path in self.campaign.character_path.glob("*.json")
        }

    def test_plan_lists_only_the_files_that_reference_the_resource(self) -> None:
        alistair = self._write_character(
            "Alistair.json",
            {"name": "Alistair", "inventory": [["Shield", 1]], "spells": ["Fireball"]},
        )
        bob = self._write_character(
            "Bob.json",
            {"name": "Bob", "items": {"shield": 2}, "inventory": [["Shield", 3]]},
        )
        self._write_character("Cara.json", {"name": "Cara", "inventory": [["Potion", 1]]})
        self._write_character("Broken.json", "not json")
        self._write_character("Listing.json", "[1, 2]")
        before = self._snapshot()

        updates = plan_rename(self.campaign, "items", "Shield", "Tower Shield")

        self.assertEqual([update.path for update in updates], [alistair, bob])
        self.assertEqual([update.count for update in updates], [1, 2])
        self.assertEqual([update.source for update in updates], ["characters", "characters"])
        self.assertEqual(updates[0].original, before["Alistair.json"])
        self.assertEqual(
            json.loads(updates[0].updated),
            {"name": "Alistair", "inventory": [["Tower Shield", 1]], "spells": ["Fireball"]},
        )
        self.assertEqual(
            json.loads(updates[1].updated),
            {"name": "Bob", "items": {"Tower Shield": 2}, "inventory": [["Tower Shield", 3]]},
        )
        self.assertEqual(self._snapshot(), before)

    def test_plan_can_leave_one_file_out(self) -> None:
        alistair = self._write_character("Alistair.json", {"spells": ["Fireball"]})
        bob = self._write_character("Bob.json", {"spells": ["Fireball"]})

        updates = plan_rename(self.campaign, "spells", "Fireball", "Fire Ball", exclude=alistair)

        self.assertEqual([update.path for update in updates], [bob])

    def test_plan_is_empty_for_resources_nothing_refers_to(self) -> None:
        self._write_character("Alistair.json", {"name": "Alistair", "spells": ["Alistair"]})

        self.assertEqual(plan_rename(self.campaign, "characters", "Alistair", "Al"), [])

    def test_apply_writes_every_update(self) -> None:
        alistair = self._write_character("Alistair.json", {"spells": ["Fireball"]})
        bob = self._write_character("Bob.json", {"spells": ["Fireball"]})

        updates = plan_rename(self.campaign, "spells", "Fireball", "Fire Ball")
        apply_updates(updates)

        self.assertEqual(
            json.loads(alistair.read_text(encoding="utf-8")),
            {"spells": ["Fire Ball"]},
        )
        self.assertEqual(
            json.loads(bob.read_text(encoding="utf-8")),
            {"spells": ["Fire Ball"]},
        )

    def test_apply_restores_the_files_already_written_when_one_fails(self) -> None:
        alistair = self._write_character("Alistair.json", {"spells": ["Fireball"]})
        bob = self._write_character("Bob.json", {"spells": ["Fireball"]})
        original = {path: path.read_text(encoding="utf-8") for path in (alistair, bob)}
        updates = plan_rename(self.campaign, "spells", "Fireball", "Fire Ball")
        real_write = references.write_text_atomic
        calls: list[Path] = []

        def fail_on_second_file(path: Path, text: str) -> None:
            calls.append(path)
            if len(calls) == 2:
                raise OSError("disk full")
            real_write(path, text)

        with patch.object(references, "write_text_atomic", fail_on_second_file):
            with self.assertRaises(OSError):
                apply_updates(updates)

        self.assertEqual(
            {path: path.read_text(encoding="utf-8") for path in (alistair, bob)},
            original,
        )

    def test_revert_never_raises(self) -> None:
        path = self._write_character("Alistair.json", {"spells": ["Fire Ball"]})
        update = ReferenceUpdate("characters", path, 1, "{}", "{}")

        with patch.object(references, "write_text_atomic", side_effect=OSError("locked")):
            revert_updates([update])

        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"spells": ["Fire Ball"]})

    def test_atomic_write_replaces_the_file_without_leaving_a_temporary_one(self) -> None:
        path = Path(self.temporary_directory.name) / "nested" / "data.json"

        write_text_atomic(path, "first")
        write_text_atomic(path, "second")

        self.assertEqual(path.read_text(encoding="utf-8"), "second")
        self.assertEqual([entry.name for entry in path.parent.iterdir()], ["data.json"])


class SpellStubEditor(ResourceEditor):
    """The least a new editor has to provide to take part in the rename rule."""

    resource_label = "Spell"
    resource_type = "spells"

    def __init__(self, resource_id="", campaign=None, *, is_new=False, parent=None) -> None:
        super().__init__(resource_id, campaign, is_new=is_new, parent=parent)
        self.name_input = QLineEdit()
        self._populate_fields()

    @property
    def directory(self) -> Path:
        return self.campaign.spell_path

    def _collect_data(self) -> dict:
        return {**self._data, "name": self.name_input.text().strip()}

    def _populate_fields(self) -> None:
        self.name_input.setText(str(self._data.get("name", "")))


class EditorRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.campaign = Campaign(
            "Editor Rule Test",
            Path(self.temporary_directory.name),
        )
        self.campaign.ensure_directories()

    def tearDown(self) -> None:
        self.app.processEvents()
        self.temporary_directory.cleanup()

    def test_a_new_editor_rewrites_references_without_extra_code(self) -> None:
        spell_path = self.campaign.spell_path / "Fireball.json"
        spell_path.write_text('{"name": "Fireball"}', encoding="utf-8")
        character_path = self.campaign.character_path / "Alistair.json"
        character_path.write_text(
            '{"name": "Alistair", "spells": ["Fireball", "Heal"]}',
            encoding="utf-8",
        )
        editor = SpellStubEditor("Fireball", self.campaign)
        renames: list[tuple[str, str, list[ReferenceUpdate]]] = []
        editor.renamed.connect(lambda *event: renames.append(event))

        editor.name_input.setText("Fire Ball")
        saved_path = editor.save()

        self.assertEqual(saved_path.name, "fire_ball.json")
        self.assertFalse(spell_path.exists())
        self.assertEqual(
            json.loads(character_path.read_text(encoding="utf-8")),
            {"name": "Alistair", "spells": ["Fire Ball", "Heal"]},
        )
        self.assertEqual(len(renames), 1)
        old_stem, new_name, updates = renames[0]
        self.assertEqual((old_stem, new_name), ("Fireball", "Fire Ball"))
        self.assertEqual([update.path for update in updates], [character_path])

    def test_an_editor_must_declare_the_resource_type_it_edits(self) -> None:
        class UntypedEditor(SpellStubEditor):
            resource_type = ""

        with self.assertRaises(NotImplementedError):
            UntypedEditor("anything", self.campaign)


if __name__ == "__main__":
    unittest.main()
