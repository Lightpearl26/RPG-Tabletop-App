import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QTabBar, QWidget

import libs.assets.campaign as campaign_module
from libs.assets.campaign import Campaign
from libs.assets.references import RESOURCE_DIRECTORIES
from libs.gui.editor.base import ResourceEditor
from libs.gui.editor.character import CharacterEditor
from libs.gui.editor.item import ItemEditor
from libs.gui.editor.spell import SpellEditor
from libs.gui.ide import EDITOR_TYPES, CampaignManagerWindow


class CampaignManagerWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.campaign = Campaign(
            "Test Campaign",
            Path(self.temporary_directory.name),
        )
        self.campaign.ensure_directories()
        self.active_campaign_patch = patch.object(
            campaign_module,
            "_CURRENT_CAMPAIGN",
            self.campaign,
        )
        self.active_campaign_patch.start()
        # The real prompt is modal and would block the tests
        self.real_ask_unsaved_changes = CampaignManagerWindow._ask_unsaved_changes
        ask_patch = patch.object(
            CampaignManagerWindow,
            "_ask_unsaved_changes",
            return_value=QMessageBox.StandardButton.Discard,
        )
        self.ask_unsaved_changes = ask_patch.start()
        self.addCleanup(ask_patch.stop)
        self.window = CampaignManagerWindow(self.campaign)

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.active_campaign_patch.stop()
        self.temporary_directory.cleanup()

    def _tab_text(self, page: QWidget) -> str:
        return self.window.documents.tabText(self.window.documents.indexOf(page))

    def _open_item(self, name: str = "Shield") -> ItemEditor:
        (self.campaign.item_path / f"{name}.json").write_text(
            json.dumps({"name": name, "description": "", "modifier": []}),
            encoding="utf-8",
        )
        self.window.navigation.setCurrentRow(3)
        self.window._browsers["items"].resource_open_requested.emit("items", name)
        return self.window.documents.currentWidget()

    def _write_spell(self, stem: str = "fireball", name: str = "Fireball") -> Path:
        path = self.campaign.spell_path / f"{stem}.json"
        path.write_text(
            json.dumps(
                {
                    "name": name,
                    "description": "Hot",
                    "cost": 30,
                    "effects": [
                        {
                            "target": "target.hp",
                            "operator": "malus",
                            "formula": "10",
                            "effect_type": "damage",
                        }
                    ],
                    "targeting": "single",
                    "runtime_policy": "instant",
                }
            ),
            encoding="utf-8",
        )
        return path

    def _close_tab_of(self, page: QWidget) -> None:
        self.window.documents.tabCloseRequested.emit(self.window.documents.indexOf(page))

    def test_navigation_opens_each_resource_browser_once(self) -> None:
        self.assertEqual(self.window.documents.count(), 1)
        self.assertEqual(self.window.navigation.count(), 7)
        self.assertIsNone(
            self.window.documents.tabBar().tabButton(
                0,
                QTabBar.ButtonPosition.RightSide,
            )
        )

        for row in range(1, self.window.navigation.count()):
            self.window.navigation.setCurrentRow(row)
            resource_type = self.window.navigation.item(row).data(
                Qt.ItemDataRole.UserRole
            )

            self.assertIn(resource_type, self.window._browsers)
            browser = self.window._browsers[resource_type]
            self.assertIs(
                self.window.documents.currentWidget(),
                self.window._category_pages[resource_type],
            )
            self.assertEqual(browser.count_label.text(), "0 resources")

        self.assertEqual(self.window.documents.count(), 7)

    def test_open_request_creates_and_reuses_document_tab(self) -> None:
        (self.campaign.character_path / "Alice.json").write_text(
            '{"name":"Alice","stats":{},"spells":[],"inventory":[]}',
            encoding="utf-8",
        )
        self.window.navigation.setCurrentRow(1)
        browser = self.window._browsers["characters"]
        opened_resources: list[tuple[str, str]] = []
        self.window.resource_open_requested.connect(
            lambda resource_type, resource_id: opened_resources.append(
                (resource_type, resource_id)
            )
        )

        browser.resource_open_requested.emit("characters", "Alice")
        first_document = self.window.documents.currentWidget()
        browser.resource_open_requested.emit("characters", "Alice")

        self.assertEqual(opened_resources, [("characters", "Alice")] * 2)
        self.assertIs(self.window.documents.currentWidget(), first_document)
        self.assertEqual(self.window.documents.count(), 3)
        self.assertEqual(self.window.inspector_name.text(), "Alice")
        self.assertEqual(self.window.inspector_type.text(), "CHARACTERS")

    def test_toolbar_actions_emit_application_requests(self) -> None:
        requests: list[str] = []
        self.window.save_requested.connect(lambda: requests.append("save"))
        self.window.validate_requested.connect(lambda: requests.append("validate"))
        self.window.start_session_requested.connect(
            lambda: requests.append("start_session")
        )

        self.window.save_button.click()
        self.window.validate_button.click()
        self.window.start_session_button.click()

        self.assertEqual(requests, ["save", "validate", "start_session"])

    def test_save_action_uses_standard_control_s_shortcut(self) -> None:
        self.assertEqual(
            self.window.save_action.shortcut(),
            QKeySequence(QKeySequence.StandardKey.Save),
        )

    def test_open_character_and_save_from_toolbar(self) -> None:
        character_path = self.campaign.character_path / "Alice.json"
        character_path.write_text(
            '{"name":"Alice","stats":{"str":10},"spells":[],"inventory":[]}',
            encoding="utf-8",
        )
        self.window.navigation.setCurrentRow(1)
        browser = self.window._browsers["characters"]
        browser.resource_open_requested.emit("characters", "Alice")
        editor = self.window.documents.currentWidget()

        self.assertIsInstance(editor, CharacterEditor)
        editor.stat_inputs["str"].setValue(55)
        editor.save_button.click()

        self.assertEqual(
            json.loads(character_path.read_text(encoding="utf-8"))["stats"]["str"],
            55,
        )
        self.assertEqual(browser.cards[0].character.stats.str, 55)
        self.assertEqual(self.window.inspector_name.text(), "Alice")

    def test_control_s_saves_the_active_character(self) -> None:
        character_path = self.campaign.character_path / "Alice.json"
        character_path.write_text(
            '{"name":"Alice","stats":{"str":10},"spells":[],"inventory":[]}',
            encoding="utf-8",
        )
        self.window.navigation.setCurrentRow(1)
        self.window._browsers["characters"].resource_open_requested.emit(
            "characters",
            "Alice",
        )
        editor = self.window.documents.currentWidget()
        self.assertIsInstance(editor, CharacterEditor)
        editor.stat_inputs["str"].setValue(66)
        self.window.show()
        editor.name_input.setFocus()
        self.app.processEvents()

        QTest.keyClick(
            editor.name_input,
            Qt.Key.Key_S,
            Qt.KeyboardModifier.ControlModifier,
        )
        self.app.processEvents()

        self.assertEqual(
            json.loads(character_path.read_text(encoding="utf-8"))["stats"]["str"],
            66,
        )

    def test_create_character_from_browser(self) -> None:
        self.window.navigation.setCurrentRow(1)
        browser = self.window._browsers["characters"]
        browser.create_button.click()
        editor = self.window.documents.currentWidget()

        self.assertIsInstance(editor, CharacterEditor)
        editor.name_input.setText("New Hero")
        self.window.save_button.click()

        character_path = self.campaign.character_path / "new_hero.json"
        self.assertTrue(character_path.is_file())
        self.assertEqual(browser.cards[0].character.name, "New Hero")
        self.assertEqual(self.window.overview.resource_counts["Characters"], 1)

    def test_open_item_and_save_from_toolbar(self) -> None:
        item_path = self.campaign.item_path / "Shield.json"
        item_path.write_text(
            '{"name":"Shield","description":"Sturdy","modifier":[["con",5]]}',
            encoding="utf-8",
        )
        self.window.navigation.setCurrentRow(3)
        browser = self.window._browsers["items"]
        browser.resource_open_requested.emit("items", "Shield")
        editor = self.window.documents.currentWidget()

        self.assertIsInstance(editor, ItemEditor)
        editor.modifiers_table.cellWidget(0, 1).setValue(9)
        editor.description_input.setPlainText("Very sturdy")
        self.window.save_button.click()

        data = json.loads(item_path.read_text(encoding="utf-8"))
        self.assertEqual(data["modifier"], [["con", 9]])
        self.assertEqual(data["description"], "Very sturdy")
        self.assertEqual(browser.cards[0].item.modifier, [["con", 9]])
        self.assertEqual(self.window.inspector_name.text(), "Shield")
        self.assertEqual(self.window.inspector_type.text(), "ITEMS")

    def test_create_item_from_browser(self) -> None:
        self.window.navigation.setCurrentRow(3)
        browser = self.window._browsers["items"]
        empty_state = self.window._category_pages["items"].findChild(
            QLabel,
            "BrowserEmptyState",
        )
        self.assertFalse(empty_state.isHidden())

        browser.create_button.click()
        editor = self.window.documents.currentWidget()

        self.assertIsInstance(editor, ItemEditor)
        editor.name_input.setText("Healing Potion")
        self.window.save_button.click()

        self.assertTrue((self.campaign.item_path / "healing_potion.json").is_file())
        self.assertEqual(browser.cards[0].item.name, "Healing Potion")
        self.assertEqual(self.window.overview.resource_counts["Items"], 1)
        self.assertTrue(empty_state.isHidden())
        self.assertEqual(
            self.window.documents.tabText(self.window.documents.indexOf(editor)),
            "Healing Potion",
        )

        self.window.navigation.setCurrentRow(1)
        document_count = self.window.documents.count()
        browser.resource_open_requested.emit("items", "healing_potion")

        self.assertIs(self.window.documents.currentWidget(), editor)
        self.assertEqual(self.window.documents.count(), document_count)

    def test_control_s_saves_the_active_item(self) -> None:
        item_path = self.campaign.item_path / "Shield.json"
        item_path.write_text(
            '{"name":"Shield","description":"","modifier":[]}',
            encoding="utf-8",
        )
        self.window.navigation.setCurrentRow(3)
        self.window._browsers["items"].resource_open_requested.emit("items", "Shield")
        editor = self.window.documents.currentWidget()
        self.assertIsInstance(editor, ItemEditor)
        editor.description_input.setPlainText("Saved with the shortcut")
        self.window.show()
        editor.name_input.setFocus()
        self.app.processEvents()

        QTest.keyClick(
            editor.name_input,
            Qt.Key.Key_S,
            Qt.KeyboardModifier.ControlModifier,
        )
        self.app.processEvents()

        self.assertEqual(
            json.loads(item_path.read_text(encoding="utf-8"))["description"],
            "Saved with the shortcut",
        )

    def test_failed_save_reports_the_resource_type(self) -> None:
        self.window.navigation.setCurrentRow(3)
        self.window._browsers["items"].create_button.click()

        self.window.save_button.click()

        self.assertEqual(
            self.window.statusBar().currentMessage(),
            "Could not save item: Item name is required.",
        )

    def test_unreadable_item_is_reported_instead_of_opened(self) -> None:
        self.window.navigation.setCurrentRow(3)
        browser = self.window._browsers["items"]
        (self.campaign.item_path / "Broken.json").write_text(
            "not json",
            encoding="utf-8",
        )
        document_count = self.window.documents.count()

        browser.resource_open_requested.emit("items", "Broken")

        self.assertEqual(self.window.documents.count(), document_count)
        self.assertTrue(
            self.window.statusBar().currentMessage().startswith("Could not open Broken:")
        )

    def test_open_spell_and_save_from_toolbar(self) -> None:
        spell_path = self._write_spell()
        self.window.navigation.setCurrentRow(4)
        browser = self.window._browsers["spells"]
        browser.resource_open_requested.emit("spells", "fireball")
        editor = self.window.documents.currentWidget()

        self.assertIsInstance(editor, SpellEditor)
        self.assertEqual(self._tab_text(editor), "fireball")
        editor.effect_rows[0].formula_field.set_text("20")
        editor.description_input.setPlainText("Very hot")
        self.assertEqual(self._tab_text(editor), "*fireball")
        self.window.save_button.click()

        data = json.loads(spell_path.read_text(encoding="utf-8"))
        self.assertEqual(data["effects"][0]["formula"], "20")
        self.assertEqual(data["description"], "Very hot")
        self.assertEqual(browser.cards[0].spell.description, "Very hot")
        self.assertEqual(self.window.inspector_name.text(), "fireball")
        self.assertEqual(self.window.inspector_type.text(), "SPELLS")
        self.assertEqual(self._tab_text(editor), "Fireball")

    def test_create_spell_from_browser(self) -> None:
        self.window.navigation.setCurrentRow(4)
        browser = self.window._browsers["spells"]

        browser.create_button.click()
        editor = self.window.documents.currentWidget()

        self.assertIsInstance(editor, SpellEditor)
        self.assertEqual(self._tab_text(editor), "*New Spell")
        editor.name_input.setText("Frost Nova")
        editor.add_effect_button.click()
        editor.effect_rows[0].formula_field.set_text("12")
        self.window.save_button.click()

        spell_path = self.campaign.spell_path / "frost_nova.json"
        self.assertEqual(
            json.loads(spell_path.read_text(encoding="utf-8"))["effects"],
            [
                {
                    "target": "target.hp",
                    "operator": "malus",
                    "formula": "12",
                    "effect_type": "damage",
                }
            ],
        )
        self.assertEqual(browser.cards[0].spell.name, "Frost Nova")
        self.assertEqual(self.window.overview.resource_counts["Spells"], 1)
        self.assertEqual(self._tab_text(editor), "Frost Nova")

    def test_control_s_saves_the_active_spell(self) -> None:
        spell_path = self._write_spell()
        self.window.navigation.setCurrentRow(4)
        self.window._browsers["spells"].resource_open_requested.emit("spells", "fireball")
        editor = self.window.documents.currentWidget()
        self.assertIsInstance(editor, SpellEditor)
        editor.description_input.setPlainText("Saved with the shortcut")
        self.window.show()
        editor.name_input.setFocus()
        self.app.processEvents()

        QTest.keyClick(
            editor.name_input,
            Qt.Key.Key_S,
            Qt.KeyboardModifier.ControlModifier,
        )
        self.app.processEvents()

        self.assertEqual(
            json.loads(spell_path.read_text(encoding="utf-8"))["description"],
            "Saved with the shortcut",
        )

    def test_failed_spell_save_reports_the_reason(self) -> None:
        self.window.navigation.setCurrentRow(4)
        browser = self.window._browsers["spells"]
        browser.create_button.click()

        self.window.save_button.click()

        self.assertEqual(
            self.window.statusBar().currentMessage(),
            "Could not save spell: Spell name is required.",
        )

        self._write_spell()
        browser.resource_open_requested.emit("spells", "fireball")
        editor = self.window.documents.currentWidget()
        editor.effect_rows[0].formula_field.set_text("")

        self.window.save_button.click()

        self.assertEqual(
            self.window.statusBar().currentMessage(),
            "Could not save spell: Effect 1: The formula is required.",
        )
        self.assertEqual(self._tab_text(editor), "*fireball")
        self.assertTrue(editor.effect_rows[0].expanded)

    def test_renaming_a_spell_updates_characters_on_disk_and_in_open_editors(self) -> None:
        self._write_spell()
        character_path = self.campaign.character_path / "Alice.json"
        character_path.write_text(
            '{"name":"Alice","stats":{"str":10},"spells":["Fireball","Bolt"],"inventory":[]}',
            encoding="utf-8",
        )
        self.window.navigation.setCurrentRow(1)
        characters = self.window._browsers["characters"]
        characters.resource_open_requested.emit("characters", "Alice")
        character_editor = self.window.documents.currentWidget()
        self.assertIsInstance(character_editor, CharacterEditor)
        character_editor.stat_inputs["str"].setValue(44)

        self.window.navigation.setCurrentRow(4)
        self.window._browsers["spells"].resource_open_requested.emit("spells", "fireball")
        spell_editor = self.window.documents.currentWidget()
        self.assertIsInstance(spell_editor, SpellEditor)
        spell_editor.name_input.setText("Fire Storm")
        self.window.save_button.click()

        self.assertEqual(
            json.loads(character_path.read_text(encoding="utf-8"))["spells"],
            ["Fire Storm", "Bolt"],
        )
        self.assertEqual(list(characters.cards[0].character.spells), ["Fire Storm"])
        self.assertEqual(
            [
                character_editor.spells_list.item(row).text()
                for row in range(character_editor.spells_list.count())
            ],
            ["Fire Storm", "Bolt"],
        )
        self.assertEqual(character_editor.stat_inputs["str"].value(), 44)
        self.assertEqual(self._tab_text(character_editor), "*Alice")
        self.assertEqual(
            self.window.statusBar().currentMessage(),
            "Saved Fire Storm. Updated 1 reference in 1 file.",
        )

    def test_every_editor_follows_the_rename_rule(self) -> None:
        self.assertTrue(EDITOR_TYPES)
        for resource_type, editor_type in EDITOR_TYPES.items():
            self.assertTrue(issubclass(editor_type, ResourceEditor), resource_type)
            self.assertEqual(editor_type.resource_type, resource_type)
            self.assertIn(resource_type, RESOURCE_DIRECTORIES)

    def test_every_editor_reports_unsaved_changes(self) -> None:
        # How to change the content of each editor; a new editor type must be listed here
        edits = {
            "characters": lambda editor, name: editor.name_input.setText(name),
            "items": lambda editor, name: editor.name_input.setText(name),
            "spells": lambda editor, name: editor.name_input.setText(name),
        }
        self.assertEqual(set(edits), set(EDITOR_TYPES))

        for resource_type, editor_type in EDITOR_TYPES.items():
            editor = editor_type("New", self.campaign, is_new=True)
            states: list[bool] = []
            editor.modified_changed.connect(states.append)
            self.assertTrue(editor.is_modified, resource_type)

            edits[resource_type](editor, "Rule Check")
            editor.save()
            self.assertFalse(editor.is_modified, resource_type)

            edits[resource_type](editor, "Rule Check Again")
            self.assertTrue(editor.is_modified, resource_type)
            editor.save()

            self.assertFalse(editor.is_modified, resource_type)
            self.assertEqual(states, [False, True, False], resource_type)

    def test_tab_shows_an_asterisk_while_an_editor_has_unsaved_changes(self) -> None:
        (self.campaign.item_path / "Shield.json").write_text(
            '{"name":"Shield","description":"","modifier":[]}',
            encoding="utf-8",
        )
        self.window.navigation.setCurrentRow(3)
        self.window._browsers["items"].resource_open_requested.emit("items", "Shield")
        editor = self.window.documents.currentWidget()
        self.assertEqual(self._tab_text(editor), "Shield")

        editor.description_input.setPlainText("Heavy")
        self.assertEqual(self._tab_text(editor), "*Shield")
        editor.description_input.setPlainText("")
        self.assertEqual(self._tab_text(editor), "Shield")

        editor.name_input.setText("Tower Shield")
        self.assertEqual(self._tab_text(editor), "*Shield")
        self.window.save_button.click()
        self.assertEqual(self._tab_text(editor), "Tower Shield")

    def test_new_resource_tab_is_marked_until_its_first_successful_save(self) -> None:
        self.window.navigation.setCurrentRow(3)
        self.window._browsers["items"].create_button.click()
        editor = self.window.documents.currentWidget()
        self.assertEqual(self._tab_text(editor), "*New Item")

        self.window.save_button.click()
        self.assertEqual(self._tab_text(editor), "*New Item")

        editor.name_input.setText("Healing Potion")
        self.window.save_button.click()
        self.assertEqual(self._tab_text(editor), "Healing Potion")

    def test_documents_without_an_editor_keep_a_plain_tab_title(self) -> None:
        self.window.navigation.setCurrentRow(2)
        self.window._browsers["entities"].resource_open_requested.emit("entities", "Goblin")

        self.assertEqual(self._tab_text(self.window.documents.currentWidget()), "Goblin")

    def test_renaming_an_item_updates_characters_on_disk_and_in_open_editors(self) -> None:
        item_path = self.campaign.item_path / "Shield.json"
        item_path.write_text(
            '{"name":"Shield","description":"","modifier":[]}',
            encoding="utf-8",
        )
        character_path = self.campaign.character_path / "Alice.json"
        character_path.write_text(
            '{"name":"Alice","stats":{"str":10},"spells":[],"inventory":[["Shield",2]]}',
            encoding="utf-8",
        )
        self.window.navigation.setCurrentRow(1)
        characters = self.window._browsers["characters"]
        characters.resource_open_requested.emit("characters", "Alice")
        character_editor = self.window.documents.currentWidget()
        self.assertIsInstance(character_editor, CharacterEditor)
        character_editor.stat_inputs["str"].setValue(44)

        self.window.navigation.setCurrentRow(3)
        self.window._browsers["items"].resource_open_requested.emit("items", "Shield")
        item_editor = self.window.documents.currentWidget()
        self.assertIsInstance(item_editor, ItemEditor)
        item_editor.name_input.setText("Tower Shield")
        self.window.save_button.click()

        self.assertEqual(
            json.loads(character_path.read_text(encoding="utf-8"))["inventory"],
            [["Tower Shield", 2]],
        )
        self.assertEqual(characters.cards[0].character.items.items, {"Tower Shield": 2})
        self.assertEqual(character_editor.inventory_table.item(0, 0).text(), "Tower Shield")
        self.assertEqual(character_editor.stat_inputs["str"].value(), 44)
        self.assertEqual(
            self.window.statusBar().currentMessage(),
            "Saved Tower Shield. Updated 1 reference in 1 file.",
        )

        self.window.documents.setCurrentWidget(character_editor)
        self.window.save_button.click()

        data = json.loads(character_path.read_text(encoding="utf-8"))
        self.assertEqual(data["inventory"], [["Tower Shield", 2]])
        self.assertEqual(data["stats"]["str"], 44)

    def test_rename_status_counts_every_rewritten_reference(self) -> None:
        (self.campaign.item_path / "Shield.json").write_text(
            '{"name":"Shield","description":"","modifier":[]}',
            encoding="utf-8",
        )
        for name in ("Alice", "Bob"):
            (self.campaign.character_path / f"{name}.json").write_text(
                json.dumps({"name": name, "inventory": [["Shield", 1]], "items": {"Shield": 1}}),
                encoding="utf-8",
            )
        self.window.navigation.setCurrentRow(3)
        self.window._browsers["items"].resource_open_requested.emit("items", "Shield")
        item_editor = self.window.documents.currentWidget()
        item_editor.name_input.setText("Tower Shield")

        self.window.save_button.click()

        self.assertEqual(
            self.window.statusBar().currentMessage(),
            "Saved Tower Shield. Updated 4 references in 2 files.",
        )

    def test_rename_keeps_the_unsaved_marker_of_the_other_open_editors(self) -> None:
        (self.campaign.item_path / "Shield.json").write_text(
            '{"name":"Shield","description":"","modifier":[]}',
            encoding="utf-8",
        )
        for name in ("Alice", "Bob"):
            (self.campaign.character_path / f"{name}.json").write_text(
                json.dumps({"name": name, "stats": {}, "spells": [], "inventory": [["Shield", 1]]}),
                encoding="utf-8",
            )
        self.window.navigation.setCurrentRow(1)
        characters = self.window._browsers["characters"]
        characters.resource_open_requested.emit("characters", "Alice")
        alice = self.window.documents.currentWidget()
        characters.resource_open_requested.emit("characters", "Bob")
        bob = self.window.documents.currentWidget()
        bob.stat_inputs["str"].setValue(12)
        self.window.navigation.setCurrentRow(3)
        self.window._browsers["items"].resource_open_requested.emit("items", "Shield")
        item_editor = self.window.documents.currentWidget()
        item_editor.name_input.setText("Tower Shield")

        self.window.save_button.click()

        self.assertEqual(self._tab_text(alice), "Alice")
        self.assertEqual(self._tab_text(bob), "*Bob")
        self.assertEqual(self._tab_text(item_editor), "Tower Shield")

    def test_closing_a_modified_tab_can_be_cancelled(self) -> None:
        editor = self._open_item()
        editor.description_input.setPlainText("Heavy")
        tab_count = self.window.documents.count()
        self.ask_unsaved_changes.return_value = QMessageBox.StandardButton.Cancel

        self._close_tab_of(editor)

        self.ask_unsaved_changes.assert_called_once_with(["Shield"])
        self.assertEqual(self.window.documents.count(), tab_count)
        self.assertEqual(self._tab_text(editor), "*Shield")

    def test_discarding_closes_the_tab_without_saving(self) -> None:
        editor = self._open_item()
        editor.description_input.setPlainText("Heavy")
        tab_count = self.window.documents.count()

        self._close_tab_of(editor)

        self.ask_unsaved_changes.assert_called_once_with(["Shield"])
        self.assertEqual(self.window.documents.count(), tab_count - 1)
        self.assertNotIn(editor, self.window._open_editors())
        saved = json.loads((self.campaign.item_path / "Shield.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["description"], "")

    def test_saving_from_the_prompt_writes_the_changes_then_closes_the_tab(self) -> None:
        editor = self._open_item()
        editor.description_input.setPlainText("Heavy")
        self.ask_unsaved_changes.return_value = QMessageBox.StandardButton.Save

        self._close_tab_of(editor)

        self.assertNotIn(editor, self.window._open_editors())
        saved = json.loads((self.campaign.item_path / "Shield.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["description"], "Heavy")

    def test_failed_save_from_the_prompt_keeps_the_tab_open(self) -> None:
        self.window.navigation.setCurrentRow(3)
        self.window._browsers["items"].create_button.click()
        editor = self.window.documents.currentWidget()
        editor.description_input.setPlainText("Nameless")
        self.ask_unsaved_changes.return_value = QMessageBox.StandardButton.Save

        self._close_tab_of(editor)

        self.ask_unsaved_changes.assert_called_once_with(["New Item"])
        self.assertIn(editor, self.window._open_editors())
        self.assertIs(self.window.documents.currentWidget(), editor)
        self.assertEqual(
            self.window.statusBar().currentMessage(),
            "Could not save item: Item name is required.",
        )

    def test_saved_and_untouched_tabs_close_without_asking(self) -> None:
        saved = self._open_item()
        saved.description_input.setPlainText("Heavy")
        self.window.save_button.click()
        self.window._browsers["items"].create_button.click()
        untouched = self.window.documents.currentWidget()
        self.assertTrue(untouched.is_modified)
        self.assertFalse(untouched.has_changes)

        self._close_tab_of(untouched)
        self._close_tab_of(saved)

        self.ask_unsaved_changes.assert_not_called()
        self.assertEqual(self.window._open_editors(), [])

    def test_close_all_asks_once_for_every_modified_editor(self) -> None:
        (self.campaign.character_path / "Alice.json").write_text(
            '{"name":"Alice","stats":{"str":10},"spells":[],"inventory":[]}',
            encoding="utf-8",
        )
        shield = self._open_item("Shield")
        shield.description_input.setPlainText("Heavy")
        self._open_item("Bow")
        self.window.navigation.setCurrentRow(1)
        self.window._browsers["characters"].resource_open_requested.emit("characters", "Alice")
        alice = self.window.documents.currentWidget()
        alice.stat_inputs["str"].setValue(12)
        self.ask_unsaved_changes.return_value = QMessageBox.StandardButton.Cancel

        self.window._close_all_tabs()

        self.ask_unsaved_changes.assert_called_once_with(["Shield", "Alice"])
        self.assertEqual(len(self.window._open_editors()), 3)

        self.ask_unsaved_changes.return_value = QMessageBox.StandardButton.Save
        self.window._close_all_tabs()

        self.assertEqual(self.window.documents.count(), 1)
        saved_item = json.loads(
            (self.campaign.item_path / "Shield.json").read_text(encoding="utf-8")
        )
        saved_character = json.loads(
            (self.campaign.character_path / "Alice.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved_item["description"], "Heavy")
        self.assertEqual(saved_character["stats"]["str"], 12)

    def test_closing_the_window_asks_about_unsaved_changes(self) -> None:
        editor = self._open_item()
        editor.description_input.setPlainText("Heavy")
        self.window.show()
        self.ask_unsaved_changes.return_value = QMessageBox.StandardButton.Cancel

        self.assertFalse(self.window.close())

        self.ask_unsaved_changes.assert_called_once_with(["Shield"])
        self.assertTrue(self.window.isVisible())

        self.ask_unsaved_changes.return_value = QMessageBox.StandardButton.Discard

        self.assertTrue(self.window.close())
        self.assertFalse(self.window.isVisible())

    def test_reload_campaign_asks_before_discarding_editors(self) -> None:
        editor = self._open_item()
        editor.description_input.setPlainText("Heavy")
        tab_count = self.window.documents.count()
        self.ask_unsaved_changes.return_value = QMessageBox.StandardButton.Cancel

        self.window._on_reload_campaign()

        self.ask_unsaved_changes.assert_called_once_with(["Shield"])
        self.assertEqual(self.window.documents.count(), tab_count)

        self.ask_unsaved_changes.return_value = QMessageBox.StandardButton.Discard
        self.window._on_reload_campaign()

        self.assertEqual(self.window.documents.count(), 1)

    def test_opening_a_campaign_asks_before_discarding_editors(self) -> None:
        other = Path(self.temporary_directory.name) / "Other"
        other.mkdir()
        (other / "campaign.json").write_text('{"name": "Other"}', encoding="utf-8")
        editor = self._open_item()
        editor.description_input.setPlainText("Heavy")
        self.ask_unsaved_changes.return_value = QMessageBox.StandardButton.Cancel

        with patch(
            "libs.gui.ide.QFileDialog.getExistingDirectory",
            return_value=str(other),
        ):
            self.window._on_open_campaign()

            self.assertIs(self.window.campaign, self.campaign)
            self.assertIn(editor, self.window._open_editors())

            self.ask_unsaved_changes.return_value = QMessageBox.StandardButton.Discard
            self.window._on_open_campaign()

        self.assertEqual(self.window.campaign.root, other)
        self.assertEqual(self.window.documents.count(), 1)

    def test_new_campaign_is_not_created_when_the_prompt_is_cancelled(self) -> None:
        editor = self._open_item()
        editor.description_input.setPlainText("Heavy")
        self.ask_unsaved_changes.return_value = QMessageBox.StandardButton.Cancel

        with patch(
            "libs.gui.ide.QInputDialog.getText",
            return_value=("Fresh", True),
        ), patch(
            "libs.gui.ide.QFileDialog.getExistingDirectory",
            return_value=self.temporary_directory.name,
        ):
            self.window._on_new_campaign()

        self.assertFalse((Path(self.temporary_directory.name) / "Fresh").exists())
        self.assertIs(self.window.campaign, self.campaign)
        self.assertIn(editor, self.window._open_editors())

    def test_prompt_offers_every_choice_and_names_the_modified_resources(self) -> None:
        answer = QMessageBox.StandardButton.Save
        with patch.object(QMessageBox, "question", return_value=answer) as question:
            self.assertEqual(self.real_ask_unsaved_changes(self.window, ["Shield"]), answer)
            self.real_ask_unsaved_changes(self.window, ["Shield", "Alice"])

        single, several = (call.args for call in question.call_args_list)
        self.assertIn('"Shield"', single[2])
        self.assertIn("- Shield", several[2])
        self.assertIn("- Alice", several[2])
        self.assertEqual(
            single[3],
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
