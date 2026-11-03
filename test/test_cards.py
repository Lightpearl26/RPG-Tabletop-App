import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QSizePolicy, QVBoxLayout

from libs.assets.character import Character, Stats
from libs.gui.cards import STAT_NAMES, STAT_PALETTE
from libs.gui.cards.character import CharacterCard
from libs.gui.cards.entity import EntityCard, RESOURCE_FIELDS, STAT_FIELDS
from libs.gui.cards.formula import EntityStatBadge, FormulaBlock, FormulaPreview
from libs.gui.cards.map import MapCard
from libs.gui.cards.session import SessionCard
from libs.gui.cards.spell import (
    EFFECT_BONUS_COLOR,
    EFFECT_TYPE_COLOR,
    SpellCard,
)
from libs.runtime.entity import Entity, StatsModifier


class CardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_session_card_formats_date_and_indexes_name(self) -> None:
        card = SessionCard("Session zero", date(2026, 10, 3))
        datetime_card = SessionCard(
            "Session one",
            datetime(2026, 10, 4, 19, 30, tzinfo=timezone.utc),
        )
        iso_card = SessionCard("Session two", "2026-10-05T19:30:00Z")

        self.assertEqual(card.name_label.text(), "Session zero")
        self.assertEqual(card.date_label.text(), "03/10/2026")
        self.assertEqual(datetime_card.date_label.text(), "04/10/2026")
        self.assertEqual(iso_card.date_label.text(), "05/10/2026")
        self.assertIn("03/10/2026", card.search_text())

    def test_map_card_handles_missing_and_valid_previews(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            missing_card = MapCard(
                "Missing map",
                preview_path=directory_path / "missing-map-preview.png",
            )
            preview_path = directory_path / "map.png"
            image = QImage(8, 8, QImage.Format.Format_RGB32)
            image.fill(QColor("green"))
            self.assertTrue(image.save(str(preview_path)))

            map_card = MapCard("Forest", preview_path=preview_path)

            self.assertFalse(map_card._source_pixmap.isNull())
            self.assertEqual(map_card.name_label.text(), "Forest")

        self.assertEqual(missing_card.preview_label.text(), "Aperçu indisponible")

    def test_entity_card_separates_resources_and_shows_value_over_max(self) -> None:
        character = Character(
            "Base",
            Stats(
                str=25,
                con=50,
                dex=75,
                agi=110,
                int=130,
                wis=50,
                cha=210,
                luc=40,
                sur=80,
                per=250,
                stamina=120,
                mental_health=80,
                drug_health=60,
            ),
        )
        entity = Entity(
            "Test entity",
            character,
            stats_modifier=StatsModifier(
                hp=-4,
                stamina=-30,
                mental_health=-15,
                drug_health=-5,
            ),
        )
        card = EntityCard(entity)

        self.assertEqual(
            tuple(name for name, _ in STAT_FIELDS),
            STAT_NAMES,
        )
        self.assertEqual(len(card.stats_bars), 10)
        self.assertEqual(len(card.resource_bars), len(RESOURCE_FIELDS))
        self.assertEqual(card.resource_value_labels["hp"].text(), "16 / 20")
        self.assertEqual(card.resource_bars["stamina"].maximum(), 120)
        self.assertEqual(card.resource_bars["stamina"].value(), 90)
        self.assertEqual(
            card.resource_value_labels["mental_health"].text(),
            "65 / 80",
        )
        self.assertEqual(
            card.resource_value_labels["drug_health"].text(),
            "55 / 60",
        )

    def test_entity_stat_bars_follow_value_color_tiers(self) -> None:
        character = Character(
            "Base",
            Stats(str=25, con=60, dex=75, agi=110, int=130, wis=150,
                  cha=210, luc=40, sur=80, per=250),
        )
        entity = Entity("Test entity", character)
        card = EntityCard(entity)

        self.assertEqual(
            {name: bar.property("statColor") for name, bar in card.stats_bars.items()},
            {
                "str": "red",
                "con": "orange",
                "dex": "orange",
                "agi": "green",
                "int": "green",
                "wis": "green",
                "cha": "purple",
                "luc": "red",
                "sur": "orange",
                "per": "purple",
            },
        )

        entity.stats_modifier.str = 40
        card.refresh()

        self.assertEqual(card.stats_bars["str"].property("statColor"), "orange")

    def test_character_card_bar_colors_follow_stat_palette(self) -> None:
        character = Character(
            "Palette",
            Stats(str=250, con=10),
        )

        with patch(
            "libs.gui.cards.character.Character.from_name",
            return_value=character,
        ):
            card = CharacterCard("Palette")

        self.assertIn(
            STAT_PALETTE["str"].lower(),
            card.stats_bars["str"].styleSheet().lower(),
        )
        self.assertIn(
            STAT_PALETTE["con"].lower(),
            card.stats_bars["con"].styleSheet().lower(),
        )

    def test_formula_preview_uses_vertical_atomic_terms(self) -> None:
        preview = FormulaPreview(
            "10 + diceratio(user.int, 36) "
            "+ diceratio(user.dex, 14) "
            "- diceratio(target.wis, 34)"
        )

        self.assertIsInstance(preview.layout(), QVBoxLayout)
        self.assertEqual(preview.layout().count(), 4)
        self.assertTrue(
            all(
                isinstance(preview.layout().itemAt(index).widget(), FormulaBlock)
                for index in range(preview.layout().count())
            )
        )
        self.assertEqual(
            preview.sizePolicy().verticalPolicy(),
            QSizePolicy.Policy.Minimum,
        )
        self.assertGreater(preview.sizeHint().height(), 0)

    def test_spell_card_displays_one_target_and_effect_type_badge(self) -> None:
        effect = SimpleNamespace(
            target=("target", "hp"),
            operator="bonus",
            effect_type="damage",
            formula=SimpleNamespace(
                expression="10 + diceratio(user.str, 20)"
            ),
        )
        spell = SimpleNamespace(
            name="Test spell",
            description="Test description",
            cost=24,
            targeting="single",
            runtime_policy="instant",
            delay=1,
            effects=[effect],
        )

        with patch(
            "libs.gui.cards.spell.Spell.from_name",
            return_value=spell,
        ):
            card = SpellCard("test-spell")

        effect_frame = card.effect_widgets[0]
        header = effect_frame.layout().itemAt(0).layout()
        header_widgets = [
            header.itemAt(index).widget()
            for index in range(header.count())
        ]
        target_badges = [
            widget
            for widget in header_widgets
            if isinstance(widget, EntityStatBadge)
        ]
        effect_type_label = header_widgets[-1]

        self.assertEqual(len(target_badges), 1)
        self.assertEqual(effect_type_label.text(), "DAMAGE")
        self.assertIn(
            EFFECT_TYPE_COLOR["damage"].lower(),
            effect_type_label.styleSheet().lower(),
        )
        self.assertIn(
            EFFECT_BONUS_COLOR.lower(),
            effect_frame.styleSheet().lower(),
        )
        self.assertEqual(card.effects_scroll.minimumHeight(), 0)
        self.assertEqual(
            card.effects_scroll.sizePolicy().verticalPolicy(),
            QSizePolicy.Policy.Expanding,
        )