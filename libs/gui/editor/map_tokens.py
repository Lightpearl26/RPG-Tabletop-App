# -*- coding: utf-8 -*-
"""Character/Entity sources, token preview data and drag-and-drop support."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from PySide6.QtCore import QMimeData, QPoint, QSize, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...assets.campaign import get_current_campaign
from ...runtime.entity import Entity
from .entity import EntityEditor


TOKEN_SOURCE_MIME = "application/x-rpg-tabletop-token-source"

# =============================================================================
# Token provider / view data
# =============================================================================


@dataclass(frozen=True, slots=True)
class TokenSourceData:
    """One resource displayed in the token toolbar."""

    id: str
    name: str
    subtitle: str = ""
    portrait: Optional[Path] = None


@dataclass(frozen=True, slots=True)
class TokenStatusView:
    """Small visual status rendered below a token."""

    name: str
    short_label: str = ""
    color: str = "#cba6f7"


@dataclass(frozen=True, slots=True)
class TokenViewData:
    """Read-only Entity data needed by the map token renderer."""

    entity_id: str
    name: str
    portrait: Optional[Path] = None
    hp: int = 0
    hp_max: int = 1
    stamina: int = 0
    stamina_max: int = 100
    statuses: tuple[TokenStatusView, ...] = ()


class TokenEntityProvider(Protocol):
    """Interface consumed by MapCanvas and the right token toolbar."""

    def list_characters(self) -> list[TokenSourceData]:
        ...

    def list_entities(self) -> list[TokenSourceData]:
        ...

    def create_entity_from_character(self, character_id: str) -> Optional[str]:
        """Create and persist an Entity, then return ``str(entity.id)``."""
        ...

    def edit_entity(self, entity_id: str) -> bool:
        """Open EntityEditor. True means token placement may continue."""
        ...

    def token_view(self, entity_id: str) -> Optional[TokenViewData]:
        ...

    def source_view(
        self,
        source_kind: str,
        source_id: str,
    ) -> Optional[TokenViewData]:
        """Preview data used while dragging a Character or Entity."""
        ...


class CampaignTokenEntityProvider:
    """
    Direct bridge to RPG Tabletop App's existing components.

    Source of truth:
        - get_current_campaign()
        - Campaign.character_path / Campaign.entity_path
        - Campaign.entities registry
        - libs.runtime.entity.Entity
        - libs.gui.editors.entity.EntityEditor

    Token references always store the stable Entity UUID as a string.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
    ) -> None:
        self.parent = parent

    # ----- Campaign helpers -------------------------------------------------

    @property
    def campaign(self):
        return get_current_campaign()

    @staticmethod
    def _read_json(path: Path) -> dict:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _resource_name(path: Path, data: dict) -> str:
        name = str(data.get("name") or "").strip()
        if name:
            return name
        return path.stem.replace("_", " ")

    @staticmethod
    def _slug(name: str) -> str:
        return str(name).strip().lower().replace(" ", "_")

    def _entity_files(self) -> list[Path]:
        folder = Path(self.campaign.entity_path)
        if not folder.exists():
            return []
        return sorted(folder.glob("*.json"))

    def _character_files(self) -> list[Path]:
        folder = Path(self.campaign.character_path)
        if not folder.exists():
            return []
        return sorted(folder.glob("*.json"))

    def _entity_record_by_uuid(
        self,
        entity_id: str,
    ) -> Optional[tuple[Path, dict]]:
        target = str(entity_id)

        for path in self._entity_files():
            data = self._read_json(path)
            if str(data.get("id") or "") == target:
                return path, data

        return None

    def _entity_from_uuid(
        self,
        entity_id: str,
    ) -> Optional[Entity]:
        entity_id = str(entity_id)

        # Runtime registry first.
        entity = self.campaign.entities.get(entity_id)
        if entity is not None:
            return entity

        # Then restore from the persistent Entity resource.
        record = self._entity_record_by_uuid(entity_id)
        if record is None:
            return None

        path, data = record
        name = self._resource_name(path, data)

        try:
            entity = Entity.from_name(name)
        except Exception:
            return None

        return entity if str(entity.id) == entity_id else None

    def _ensure_entity_uuid(
        self,
        path: Path,
        data: dict,
    ) -> Optional[str]:
        """
        Return a persistent UUID for an Entity resource.

        Older Entity files may not contain ``id`` yet. Loading them through
        Entity.from_name creates one; saving immediately persists it so map token
        references remain stable across sessions.
        """
        existing = str(data.get("id") or "").strip()
        if existing:
            return existing

        name = self._resource_name(path, data)

        try:
            entity = Entity.from_name(name)
            entity.save(path)
        except Exception:
            return None

        return str(entity.id)

    def _unique_entity_name(
        self,
        base_name: str,
    ) -> str:
        existing = {
            self._resource_name(path, self._read_json(path)).casefold()
            for path in self._entity_files()
        }

        base = str(base_name).strip() or "New Entity"
        if base.casefold() not in existing:
            return base

        index = 2
        while f"{base} ({index})".casefold() in existing:
            index += 1

        return f"{base} ({index})"

    # ----- Toolbar source lists --------------------------------------------

    def _portrait_for_character_name(
        self,
        character_name: str,
    ) -> Optional[Path]:
        """
        Resolve the Character portrait without imposing a single resource schema.

        Supported sources:
            - portrait / image / image_path / token_image keys in Character JSON
            - image file beside the Character JSON with the same stem
        """
        wanted = str(character_name).strip()

        for path in self._character_files():
            data = self._read_json(path)
            name = self._resource_name(path, data)

            if name != wanted and path.stem != wanted:
                continue

            candidates: list[Path] = []

            for key in ("portrait", "image", "image_path", "token_image"):
                value = data.get(key)
                if not value:
                    continue
                raw = Path(str(value))
                candidates.append(raw)
                candidates.append(path.parent / raw)

            for suffix in (".png", ".webp", ".jpg", ".jpeg"):
                candidates.append(path.with_suffix(suffix))

            for candidate in candidates:
                candidate = Path(candidate)

                if candidate.is_file():
                    return candidate

                if not candidate.is_absolute():
                    relative = Path(self.campaign.character_path) / candidate
                    if relative.is_file():
                        return relative

            return None

        return None

    def list_characters(self) -> list[TokenSourceData]:
        result: list[TokenSourceData] = []

        for path in self._character_files():
            data = self._read_json(path)
            name = self._resource_name(path, data)

            subtitle_parts: list[str] = []

            stats = data.get("stats")
            if isinstance(stats, dict):
                # Level is calculated by Character at runtime, but keeping the
                # toolbar independent from the Character constructor makes the
                # resource list resilient to partially-edited JSON files.
                subtitle_parts.append(path.stem)

            result.append(
                TokenSourceData(
                    id=name,
                    name=name,
                    subtitle=" • ".join(subtitle_parts),
                    portrait=self._portrait_for_character_name(name),
                )
            )

        return result

    def list_entities(self) -> list[TokenSourceData]:
        result: list[TokenSourceData] = []

        for path in self._entity_files():
            data = self._read_json(path)
            name = self._resource_name(path, data)
            entity_id = self._ensure_entity_uuid(path, data)

            if not entity_id:
                continue

            character_name = str(data.get("character") or "").strip()
            subtitle = character_name

            result.append(
                TokenSourceData(
                    id=entity_id,
                    name=name,
                    subtitle=subtitle,
                    portrait=self._portrait_for_character_name(character_name),
                )
            )

        return result

    # ----- Character -> Entity ---------------------------------------------

    def create_entity_from_character(
        self,
        character_id: str,
    ) -> Optional[str]:
        character_name = str(character_id).strip()
        if not character_name:
            return None

        proposed = self._unique_entity_name(character_name)

        entity_name, accepted = QInputDialog.getText(
            self.parent,
            "Create Entity",
            "Entity name:",
            text=proposed,
        )

        if not accepted:
            return None

        entity_name = str(entity_name).strip()
        if not entity_name:
            return None

        # Protect against accidentally loading/overwriting an existing Entity,
        # because Entity.from_character() restores a file when the name exists.
        entity_name = self._unique_entity_name(entity_name)

        try:
            entity = Entity.from_character(
                entity_name,
                character_name,
            )
            entity.save()
        except Exception as error:
            QMessageBox.critical(
                self.parent,
                "Create Entity",
                str(error),
            )
            return None

        return str(entity.id)

    # ----- Existing EntityEditor -------------------------------------------

    def edit_entity(
        self,
        entity_id: str,
    ) -> bool:
        entity_id = str(entity_id)
        record = self._entity_record_by_uuid(entity_id)

        # The Entity can exist in the runtime registry before its file is on disk.
        if record is None:
            entity = self._entity_from_uuid(entity_id)
            if entity is None:
                QMessageBox.warning(
                    self.parent,
                    "Entity",
                    f"Entity '{entity_id}' could not be found.",
                )
                return False

            path = entity.save()
            data = self._read_json(path)
            record = (path, data)

        path, data = record
        resource_id = path.stem

        dialog = QDialog(self.parent)
        dialog.setWindowTitle(
            f"Entity — {self._resource_name(path, data)}"
        )
        dialog.resize(1000, 760)

        root = QVBoxLayout(dialog)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        editor = EntityEditor(
            resource_id=resource_id,
            campaign=self.campaign,
            is_new=False,
            parent=dialog,
        )
        root.addWidget(editor, 1)

        cancel_button = QPushButton("Cancel", dialog)
        cancel_button.setMaximumWidth(120)
        cancel_button.clicked.connect(dialog.reject)

        footer = QHBoxLayout()
        footer.setContentsMargins(12, 8, 12, 12)
        footer.addStretch(1)
        footer.addWidget(cancel_button)
        root.addLayout(footer)

        saved_path: Optional[Path] = None

        def save_and_close() -> None:
            nonlocal saved_path

            try:
                saved_path = Path(editor.save())
            except Exception as error:
                QMessageBox.critical(
                    dialog,
                    "Save Entity",
                    str(error),
                )
                return

            dialog.accept()

        save_signal = getattr(editor, "save_requested", None)
        if save_signal is not None and hasattr(save_signal, "connect"):
            save_signal.connect(save_and_close)
        else:
            # Fallback for a standalone/older ResourceEditor implementation.
            editor.save_button.clicked.connect(save_and_close)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        # EntityEditor preserves the UUID runtime keys when saving.
        # Reload the saved resource so Campaign.entities immediately reflects
        # name, Character, inventory, spells and modifiers edited by the user.
        if saved_path is not None and saved_path.is_file():
            saved_data = self._read_json(saved_path)
            saved_name = self._resource_name(saved_path, saved_data)

            try:
                refreshed = Entity.from_name(saved_name)
            except Exception as error:
                QMessageBox.warning(
                    self.parent,
                    "Reload Entity",
                    str(error),
                )
                return True

            if str(refreshed.id) != entity_id:
                QMessageBox.warning(
                    self.parent,
                    "Entity UUID",
                    "The Entity UUID changed unexpectedly while editing it.",
                )

        return True

    # ----- Token renderer data ---------------------------------------------

    def _portrait_for_entity(
        self,
        entity: Entity,
    ) -> Optional[Path]:
        """Resolve the portrait used by an Entity token."""
        character = entity.character

        for attr in ("portrait", "image", "image_path", "token_image"):
            value = getattr(character, attr, None)
            if not value:
                continue

            candidate = Path(value)
            if candidate.is_file():
                return candidate

            if not candidate.is_absolute():
                relative = Path(self.campaign.character_path) / candidate
                if relative.is_file():
                    return relative

        return self._portrait_for_character_name(
            str(getattr(character, "name", ""))
        )

    @staticmethod
    def _resource_max(
        entity: Entity,
        stat_name: str,
    ) -> int:
        """
        Maximum shown by the token bar.

        Spell effects are deliberately excluded; they represent runtime changes.
        Base Character + Entity modifier + equipped items form the resource max.
        """
        base = int(getattr(entity.character.stats, stat_name, 0))
        modifier = int(getattr(entity.stats_modifier, stat_name, 0))

        try:
            item_modifier = int(
                entity.items.get_stat_modifier(stat_name)
            )
        except Exception:
            item_modifier = 0

        return max(1, base + modifier + item_modifier)

    @staticmethod
    def _status_views(
        entity: Entity,
    ) -> tuple[TokenStatusView, ...]:
        """
        For now, CONTROL SpellEffects are surfaced as token statuses.

        Their associated SpellEvent gives us the spell name. A dedicated Status
        model can later be added without changing InitialTokenPlacement.
        """
        event_names: dict[str, str] = {}

        for event in getattr(entity, "spell_events", []):
            event_id = getattr(event, "id", None)
            spell_id = str(getattr(event, "spell_id", "") or "").strip()

            if event_id is not None and spell_id:
                event_names[str(event_id)] = spell_id

        statuses: list[TokenStatusView] = []
        seen: set[str] = set()

        for effect in getattr(entity, "spell_effects", []):
            effect_type = getattr(effect, "effect_type", None)
            type_value = str(getattr(effect_type, "value", effect_type)).casefold()

            if type_value != "control":
                continue

            event_id = getattr(effect, "event_id", None)
            name = event_names.get(str(event_id), "") if event_id is not None else ""

            if not name:
                name = str(getattr(effect, "target_stat", "Status"))

            if name in seen:
                continue

            seen.add(name)

            words = [
                word
                for word in name.replace("_", " ").split()
                if word
            ]
            short = "".join(word[0] for word in words[:2]).upper() or "!"

            statuses.append(
                TokenStatusView(
                    name=name,
                    short_label=short,
                    color="#cba6f7",
                )
            )

        return tuple(statuses)

    def token_view(
        self,
        entity_id: str,
    ) -> Optional[TokenViewData]:
        entity = self._entity_from_uuid(entity_id)
        if entity is None:
            return None

        hp = int(entity.get_stat("hp"))
        stamina = int(entity.get_stat("stamina"))

        return TokenViewData(
            entity_id=str(entity.id),
            name=str(entity.name),
            portrait=self._portrait_for_entity(entity),
            hp=hp,
            hp_max=self._resource_max(entity, "hp"),
            stamina=stamina,
            stamina_max=self._resource_max(entity, "stamina"),
            statuses=self._status_views(entity),
        )

    def source_view(
        self,
        source_kind: str,
        source_id: str,
    ) -> Optional[TokenViewData]:
        kind = str(source_kind).strip().casefold()

        if kind == "entity":
            return self.token_view(source_id)

        if kind != "character":
            return None

        name = str(source_id).strip()
        if not name:
            return None

        return TokenViewData(
            entity_id="",
            name=name,
            portrait=self._portrait_for_character_name(name),
            # The Character has not become an Entity yet. Keep the bars full in
            # the drag preview rather than inventing runtime values.
            hp=1,
            hp_max=1,
            stamina=1,
            stamina_max=1,
        )


class NullTokenEntityProvider:
    """Fallback used only when Campaign resources are unavailable."""

    def list_characters(self) -> list[TokenSourceData]:
        return []

    def list_entities(self) -> list[TokenSourceData]:
        return []

    def create_entity_from_character(self, character_id: str) -> Optional[str]:
        return None

    def edit_entity(self, entity_id: str) -> bool:
        return False

    def token_view(self, entity_id: str) -> Optional[TokenViewData]:
        label = entity_id[:8] if entity_id else "Entity"
        return TokenViewData(
            entity_id=entity_id,
            name=label,
        )

    def source_view(
        self,
        source_kind: str,
        source_id: str,
    ) -> Optional[TokenViewData]:
        label = str(source_id).strip() or "Token"
        return TokenViewData(
            entity_id="",
            name=label,
            hp=1,
            hp_max=1,
            stamina=1,
            stamina_max=1,
        )


class TokenSourceList(QListWidget):
    """QListWidget that exports Character/Entity resources as token drag payloads."""

    def __init__(
        self,
        source_kind: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.source_kind = str(source_kind)
        self.setDragEnabled(True)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def startDrag(self, supported_actions) -> None:  # noqa: N802 - Qt API
        item = self.currentItem()
        if item is None:
            return

        source_id = item.data(Qt.ItemDataRole.UserRole)
        if source_id is None:
            return

        payload = {
            "kind": self.source_kind,
            "id": str(source_id),
        }

        mime = QMimeData()
        mime.setData(
            TOKEN_SOURCE_MIME,
            json.dumps(payload).encode("utf-8"),
        )

        drag = QDrag(self)
        drag.setMimeData(mime)

        icon = item.icon()
        if not icon.isNull():
            pixmap = icon.pixmap(QSize(72, 86))
            if not pixmap.isNull():
                drag.setPixmap(pixmap)
                drag.setHotSpot(
                    QPoint(
                        pixmap.width() // 2,
                        pixmap.height() // 2,
                    )
                )

        drag.exec(Qt.DropAction.CopyAction)
