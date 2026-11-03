# -*- coding: utf-8 -*-

"""
First plug-and-play map editor shell for RPG Tabletop App.

Expected project location:
    libs/gui/map_editor.py

Expected map module:
    libs/assets/map.py

Implemented in this first version:
    - exact MapAsset rendering through MapAsset.render_scene()
    - map grid overlay
    - editor camera independent from MapAsset.initial_gm_camera
    - middle-mouse pan
    - wheel zoom around cursor
    - fit map / initial camera / save current view as initial camera
    - top-most element selection
    - transform frame with move / resize / rotation handles
    - map folder loading
    - save + compile
    - compile only
    - standalone __main__ test

Not implemented yet:
    - drawing tools
    - image import tool
    - text tool
    - token rendering / placement through RPG Tabletop App Campaign/Entity resources
    - layers/properties panels
    - undo/redo
    - selection tool with rectangle multi-selection and Shift/Ctrl modifiers
"""

from __future__ import annotations

import sys
import json
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum, auto
from math import atan2, cos, degrees, hypot, radians, sin, sqrt
from pathlib import Path
from typing import Optional, Protocol

from PySide6.QtCore import QMimeData, QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QDrag,
    QIcon,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QCheckBox,
    QColorDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QFontComboBox,
    QHBoxLayout,
    QLabel,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QToolBar,
    QToolButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

try:
    from libs.assets.map import (
        CameraState,
        DrawTool,
        DrawingElement,
        ImageElement,
        InitialTokenPlacement,
        MapAsset,
        MapCompileCancelled,
        MapElement,
        MediaCache,
        Rect,
        import_media,
        media_size,
        TextElement,
        Vec2,
    )
    from libs.assets.campaign import get_current_campaign
    from libs.runtime.entity import Entity
    from libs.gui.editor.entity import EntityEditor
except ModuleNotFoundError:
    # Allow direct execution from libs/gui/map_editor.py.
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from libs.assets.map import (
        CameraState,
        DrawTool,
        DrawingElement,
        ImageElement,
        InitialTokenPlacement,
        MapAsset,
        MapCompileCancelled,
        MapElement,
        MediaCache,
        Rect,
        import_media,
        media_size,
        TextElement,
        Vec2,
    )
    from libs.assets.campaign import get_current_campaign
    from libs.runtime.entity import Entity
    from libs.gui.editor.entity import EntityEditor


# =============================================================================
# Map folder lifecycle
# =============================================================================


_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _safe_map_folder_name(name: str) -> str:
    """Return a filesystem-safe folder name while keeping the display name unchanged."""
    invalid = '<>:"/\\|?*'
    cleaned = ''.join('_' if char in invalid else char for char in name).strip().rstrip('. ')
    cleaned = ' '.join(cleaned.split())
    if not cleaned:
        cleaned = "New Map"
    if cleaned.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def _unique_map_dir(display_name: str) -> Path:
    """Return a free map folder path inside the current campaign."""
    campaign = get_current_campaign()
    base_name = _safe_map_folder_name(display_name)
    candidate = Path(campaign.map_path(base_name))
    if not candidate.exists():
        return candidate

    index = 2
    while True:
        candidate = Path(campaign.map_path(f"{base_name} ({index})"))
        if not candidate.exists():
            return candidate
        index += 1


# =============================================================================
# Token drag & drop
# =============================================================================


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


# =============================================================================
# Camera
# =============================================================================


@dataclass(slots=True)
class EditorCamera:
    """Temporary editor camera. It is not saved until explicitly requested."""

    center: Vec2 = field(default_factory=Vec2)
    zoom: float = 1.0

    MIN_ZOOM: float = 0.05
    MAX_ZOOM: float = 16.0

    def clamp_zoom(self, value: float) -> float:
        return max(self.MIN_ZOOM, min(self.MAX_ZOOM, float(value)))

    def set_zoom(self, value: float) -> None:
        self.zoom = self.clamp_zoom(value)

    def to_state(self) -> CameraState:
        return CameraState(
            center=Vec2(self.center.x, self.center.y),
            zoom=self.zoom,
        )

    def from_state(self, state: CameraState) -> None:
        self.center = Vec2(state.center.x, state.center.y)
        self.zoom = self.clamp_zoom(state.zoom if state.zoom > 0 else 1.0)


# =============================================================================
# Selection / transform frame
# =============================================================================


class TransformMode(Enum):
    MOVE = auto()
    RESIZE = auto()
    ROTATE = auto()


class ResizeHandle(Enum):
    TOP_LEFT = auto()
    TOP = auto()
    TOP_RIGHT = auto()
    RIGHT = auto()
    BOTTOM_RIGHT = auto()
    BOTTOM = auto()
    BOTTOM_LEFT = auto()
    LEFT = auto()


class EditorTool(Enum):
    SELECT = auto()
    IMAGE = auto()
    FREEHAND = auto()
    LINE = auto()
    RECT = auto()
    CIRCLE = auto()
    TEXT = auto()


DRAW_TOOLS: dict[EditorTool, DrawTool] = {
    EditorTool.FREEHAND: DrawTool.FREEHAND,
    EditorTool.LINE: DrawTool.LINE,
    EditorTool.RECT: DrawTool.RECT,
    EditorTool.CIRCLE: DrawTool.CIRCLE,
}


@dataclass(slots=True)
class ElementFrame:
    """Oriented editor frame of an element, in world coordinates."""

    center: Vec2
    width: float
    height: float
    rotation: float = 0.0

    def corners(self) -> list[Vec2]:
        half_w = self.width / 2.0
        half_h = self.height / 2.0
        local = [
            Vec2(-half_w, -half_h),
            Vec2(half_w, -half_h),
            Vec2(half_w, half_h),
            Vec2(-half_w, half_h),
        ]
        return [_rotate_vector(point, self.rotation) + self.center for point in local]


@dataclass(slots=True)
class ElementSnapshot:
    """Mutable geometry copied at the beginning of a transform gesture."""

    pos: Optional[Vec2] = None
    width: Optional[float] = None
    height: Optional[float] = None
    rotation: Optional[float] = None
    font_size: Optional[float] = None
    points: Optional[list[Vec2]] = None


@dataclass(slots=True)
class EditorState:
    """Serializable editor state used by undo / redo."""

    elements: list[dict]
    tokens: list[dict]
    initial_camera: dict
    selected_element_ids: list[str]
    selected_element_id: Optional[str]
    selected_token_ids: list[str]
    selected_token_id: Optional[str]
    camera_selected: bool


@dataclass(slots=True)
class CameraEditSession:
    """One direct manipulation gesture on the initial GM camera."""

    mode: str  # "move" | "zoom"
    start_screen: QPointF
    start_world: Vec2
    start_center: Vec2
    start_zoom: float
    start_handle_distance: float
    history_before: Optional[EditorState] = None


@dataclass(slots=True)
class TokenMoveSession:
    """Initial positions of one or more selected tokens during a drag."""

    token_ids: list[str]
    primary_token_id: str
    start_world: Vec2
    positions: dict[str, Vec2]
    history_before: Optional[EditorState] = None


@dataclass(slots=True)
class TransformSession:
    element_id: str
    mode: TransformMode
    start_world: Vec2
    start_screen: QPointF
    frame: ElementFrame
    snapshot: ElementSnapshot
    history_before: Optional[EditorState] = None
    handle: Optional[ResizeHandle] = None
    start_angle: float = 0.0


@dataclass(slots=True)
class GroupTransformSession:
    """Initial state of a multi-selection transform gesture."""

    element_ids: list[str]
    mode: TransformMode
    start_world: Vec2
    start_screen: QPointF
    frame: ElementFrame
    snapshots: dict[str, ElementSnapshot]
    element_frames: dict[str, ElementFrame]
    history_before: Optional[EditorState] = None
    handle: Optional[ResizeHandle] = None
    start_angle: float = 0.0


HANDLE_SIZE_PX = 10.0
ROTATE_HANDLE_OFFSET_PX = 30.0
MIN_ELEMENT_SIZE_PX = 4.0
ROTATION_SNAP_DEG = 45.0
GRID_SNAP_TOLERANCE_PX = 5.0
ROTATION_SNAP_TOLERANCE_DEG = 5.0
MAX_UNDO_STEPS = 100
PASTE_OFFSET_PX = 20.0
MARQUEE_CLICK_TOLERANCE_PX = 4.0


def _rotate_vector(vector: Vec2, angle_deg: float) -> Vec2:
    if not angle_deg:
        return Vec2(vector.x, vector.y)
    angle = radians(angle_deg)
    c, s = cos(angle), sin(angle)
    return Vec2(
        vector.x * c - vector.y * s,
        vector.x * s + vector.y * c,
    )


def _angle(center: Vec2, point: Vec2) -> float:
    """Angle in map coordinates; positive values are clockwise on screen."""
    return degrees(atan2(point.y - center.y, point.x - center.x))


def _midpoint(a: QPointF, b: QPointF) -> QPointF:
    return QPointF((a.x() + b.x()) / 2.0, (a.y() + b.y()) / 2.0)


# =============================================================================
# Contextual image bar
# =============================================================================


class ImageFormattingBar(QFrame):
    """Floating source controls attached to a selected ImageElement."""

    replace_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("ImageFormattingBar")
        self.setFrameShape(QFrame.Shape.NoFrame)

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 7, 8, 7)
        root.setSpacing(7)

        self.source_label = QLabel("No image", self)
        self.source_label.setObjectName("ImageSourceLabel")
        self.source_label.setMinimumWidth(120)
        self.source_label.setMaximumWidth(220)
        root.addWidget(self.source_label, 1)

        self.replace_button = QPushButton("Choose image…", self)
        self.replace_button.setObjectName("ImageReplaceButton")
        self.replace_button.clicked.connect(self.replace_requested.emit)
        root.addWidget(self.replace_button)

        self.setStyleSheet(
            """
            QFrame#ImageFormattingBar {
                background: #181825;
                border: 1px solid #45475a;
                border-radius: 9px;
            }
            QLabel#ImageSourceLabel {
                color: #bac2de;
                padding: 0 4px;
            }
            QPushButton#ImageReplaceButton {
                min-height: 28px;
                color: #11111b;
                background: #89b4fa;
                border: 1px solid #89b4fa;
                border-radius: 6px;
                padding: 2px 10px;
                font-weight: 600;
            }
            QPushButton#ImageReplaceButton:hover {
                background: #b4befe;
                border-color: #b4befe;
            }
            QPushButton#ImageReplaceButton:disabled {
                color: #6c7086;
                background: #313244;
                border-color: #45475a;
            }
            """
        )

        self.adjustSize()
        self.hide()

    def set_element(self, element: ImageElement) -> None:
        name = element.asset.name or element.asset.as_posix() or "No image"
        self.source_label.setText(name)
        self.source_label.setToolTip(element.asset.as_posix())
        self.replace_button.setEnabled(not element.locked)
        self.adjustSize()


# =============================================================================
# Contextual text formatting bar
# =============================================================================


class TextFormattingBar(QFrame):
    """Floating formatting controls attached to a selected TextElement."""

    text_changed = Signal(str)
    font_family_changed = Signal(str)
    font_size_changed = Signal(float)
    color_changed = Signal(str)
    bold_changed = Signal(bool)
    italic_changed = Signal(bool)
    underline_changed = Signal(bool)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("TextFormattingBar")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._syncing = False
        self._color = "#f9e2af"

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 7, 8, 7)
        root.setSpacing(6)

        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setObjectName("TextInlineEditor")
        self.text_edit.setPlaceholderText("Text")
        self.text_edit.setFixedSize(190, 52)
        self.text_edit.textChanged.connect(self._emit_text)
        root.addWidget(self.text_edit)

        format_box = QVBoxLayout()
        format_box.setContentsMargins(0, 0, 0, 0)
        format_box.setSpacing(5)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(5)

        self.font_combo = QFontComboBox(self)
        self.font_combo.setObjectName("TextFontCombo")
        self.font_combo.setFixedWidth(150)
        self.font_combo.currentFontChanged.connect(
            lambda font: self._emit_family(font.family())
        )
        top.addWidget(self.font_combo)

        self.size_spin = QSpinBox(self)
        self.size_spin.setObjectName("TextSizeSpin")
        self.size_spin.setRange(1, 512)
        self.size_spin.setSuffix(" px")
        self.size_spin.setFixedWidth(82)
        self.size_spin.valueChanged.connect(
            lambda value: self._emit_size(float(value))
        )
        top.addWidget(self.size_spin)

        self.color_button = QPushButton(self)
        self.color_button.setObjectName("TextColorButton")
        self.color_button.setFixedSize(34, 30)
        self.color_button.setToolTip("Text color")
        self.color_button.clicked.connect(self._choose_color)
        top.addWidget(self.color_button)

        format_box.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(4)

        self.bold_button = self._style_button("B", "Bold")
        font = self.bold_button.font()
        font.setBold(True)
        self.bold_button.setFont(font)
        self.bold_button.toggled.connect(self._emit_bold)
        bottom.addWidget(self.bold_button)

        self.italic_button = self._style_button("I", "Italic")
        font = self.italic_button.font()
        font.setItalic(True)
        self.italic_button.setFont(font)
        self.italic_button.toggled.connect(self._emit_italic)
        bottom.addWidget(self.italic_button)

        self.underline_button = self._style_button("U", "Underline")
        font = self.underline_button.font()
        font.setUnderline(True)
        self.underline_button.setFont(font)
        self.underline_button.toggled.connect(self._emit_underline)
        bottom.addWidget(self.underline_button)
        bottom.addStretch(1)

        format_box.addLayout(bottom)
        root.addLayout(format_box)

        self.setStyleSheet(
            """
            QFrame#TextFormattingBar {
                background: #181825;
                border: 1px solid #45475a;
                border-radius: 9px;
            }
            QPlainTextEdit#TextInlineEditor {
                color: #cdd6f4;
                background: #11111b;
                border: 1px solid #313244;
                border-radius: 6px;
                padding: 4px 6px;
                selection-background-color: #89b4fa;
                selection-color: #11111b;
            }
            QPlainTextEdit#TextInlineEditor:focus,
            QFontComboBox#TextFontCombo:focus,
            QSpinBox#TextSizeSpin:focus {
                border-color: #89b4fa;
            }
            QFontComboBox#TextFontCombo,
            QSpinBox#TextSizeSpin {
                min-height: 28px;
                color: #cdd6f4;
                background: #11111b;
                border: 1px solid #313244;
                border-radius: 6px;
                padding: 1px 6px;
            }
            QToolButton#TextStyleButton {
                min-width: 31px;
                min-height: 27px;
                color: #cdd6f4;
                background: #11111b;
                border: 1px solid #313244;
                border-radius: 6px;
            }
            QToolButton#TextStyleButton:hover {
                border-color: #585b70;
                background: #252536;
            }
            QToolButton#TextStyleButton:checked {
                color: #11111b;
                background: #89b4fa;
                border-color: #89b4fa;
            }
            QPushButton#TextColorButton {
                border: 1px solid #585b70;
                border-radius: 6px;
            }
            QPushButton#TextColorButton:hover {
                border: 2px solid #cdd6f4;
            }
            """
        )

        self.adjustSize()
        self.hide()

    def _style_button(self, text: str, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("TextStyleButton")
        button.setText(text)
        button.setToolTip(tooltip)
        button.setCheckable(True)
        button.setFixedSize(33, 29)
        return button

    def _emit_text(self) -> None:
        if not self._syncing:
            self.text_changed.emit(self.text_edit.toPlainText())

    def _emit_family(self, family: str) -> None:
        if not self._syncing:
            self.font_family_changed.emit(family)

    def _emit_size(self, value: float) -> None:
        if not self._syncing:
            self.font_size_changed.emit(value)

    def _emit_bold(self, checked: bool) -> None:
        if not self._syncing:
            self.bold_changed.emit(checked)

    def _emit_italic(self, checked: bool) -> None:
        if not self._syncing:
            self.italic_changed.emit(checked)

    def _emit_underline(self, checked: bool) -> None:
        if not self._syncing:
            self.underline_changed.emit(checked)

    @staticmethod
    def _contrast_text(color: QColor) -> str:
        luminance = (
            0.2126 * color.redF()
            + 0.7152 * color.greenF()
            + 0.0722 * color.blueF()
        )
        return "#11111b" if luminance > 0.55 else "#f5e0dc"

    def _refresh_color(self) -> None:
        color = QColor(self._color)
        text = self._contrast_text(color)
        self.color_button.setStyleSheet(
            f"""
            QPushButton#TextColorButton {{
                color: {text};
                background: {self._color};
                border: 1px solid #585b70;
                border-radius: 6px;
            }}
            QPushButton#TextColorButton:hover {{
                border: 2px solid #cdd6f4;
            }}
            """
        )

    def _choose_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._color), self, "Text color")
        if not color.isValid():
            return
        self._color = color.name(QColor.NameFormat.HexRgb)
        self._refresh_color()
        if not self._syncing:
            self.color_changed.emit(self._color)

    def set_element(self, element: TextElement) -> None:
        self._syncing = True
        try:
            self.text_edit.setPlainText(element.text)
            self.font_combo.setCurrentFont(
                QFont(element.font_family) if element.font_family else QFont()
            )
            self.size_spin.setValue(max(1, round(element.font_size)))
            self._color = element.font_color
            self.bold_button.setChecked(bool(getattr(element, "bold", False)))
            self.italic_button.setChecked(bool(getattr(element, "italic", False)))
            self.underline_button.setChecked(bool(getattr(element, "underline", False)))
            self._refresh_color()
            enabled = not element.locked
            self.text_edit.setEnabled(enabled)
            self.font_combo.setEnabled(enabled)
            self.size_spin.setEnabled(enabled)
            self.color_button.setEnabled(enabled)
            self.bold_button.setEnabled(enabled)
            self.italic_button.setEnabled(enabled)
            self.underline_button.setEnabled(enabled)
        finally:
            self._syncing = False

    def focus_text(self, select_all: bool = False) -> None:
        self.text_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        if select_all:
            self.text_edit.selectAll()


# =============================================================================
# Canvas
# =============================================================================


class MapCanvas(QWidget):
    """
    Interactive viewport for a MapAsset.

    The static scene itself is always rendered by MapAsset.render_scene(), so the
    editor and flattened tiles share the exact same QPainter rendering path.

    Select is inherently multi-selection capable: click for one element, Shift/Ctrl
    click to modify the selection, or drag from empty canvas for a marquee selection.
    """

    selection_changed = Signal(object)
    mouse_world_moved = Signal(float, float)
    zoom_changed = Signal(float)
    tool_changed = Signal(object)
    image_replace_requested = Signal()
    token_double_clicked = Signal(str)
    token_placed = Signal(str)
    history_changed = Signal(bool, bool)
    clipboard_changed = Signal(bool)

    def __init__(
        self,
        asset: Optional[MapAsset] = None,
        map_dir: Optional[Path] = None,
        token_provider: Optional[TokenEntityProvider] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        self.asset = asset or MapAsset()
        self.map_dir = Path(map_dir) if map_dir is not None else Path.cwd()
        self.media = MediaCache(self.map_dir)
        self.token_provider: TokenEntityProvider = token_provider or NullTokenEntityProvider()
        self._token_image_cache: dict[Path, QImage] = {}
        self.pending_token_entity_id: Optional[str] = None

        self._drag_token_source_kind: Optional[str] = None
        self._drag_token_source_id: Optional[str] = None
        self._drag_token_preview: Optional[TokenViewData] = None

        self.camera = EditorCamera()
        self.selected_element_ids: set[str] = set()
        self.selected_element_id: Optional[str] = None
        self.selected_token_ids: set[str] = set()
        self.selected_token_id: Optional[str] = None
        self._token_move: Optional[TokenMoveSession] = None

        self.camera_selected = False
        self._camera_edit: Optional[CameraEditSession] = None

        # Editor tool state. These values are editor-only; they are copied into
        # newly created MapElements and never serialized by the canvas itself.
        self.active_tool = EditorTool.SELECT
        self.stroke_color = "#f9e2af"
        self.fill_color: Optional[str] = None
        self.stroke_width = 2.0
        self.element_opacity = 1.0
        self.text_font_size = 24.0
        self.pending_image_asset: Optional[Path] = None
        self.pending_image_size: tuple[float, float] = (128.0, 128.0)

        self._creation_start: Optional[Vec2] = None
        self._creation_current: Optional[Vec2] = None
        self._creation_points: list[Vec2] = []
        self._hover_world: Optional[Vec2] = None

        self._panning = False
        self._last_mouse_pos = QPointF()
        self._transform: Optional[TransformSession | GroupTransformSession] = None
        self._marquee_start_screen: Optional[QPointF] = None
        self._marquee_current_screen: Optional[QPointF] = None
        self._marquee_modifiers = Qt.KeyboardModifier.NoModifier
        self._render_error: Optional[str] = None

        # Editor history / internal element clipboard. The clipboard stores only
        # serialized element data; image pixels remain in the map media folder.
        self._undo_stack: list[EditorState] = []
        self._redo_stack: list[EditorState] = []
        self._element_clipboard: Optional[list[dict]] = None
        self._paste_count = 0

        # Transform snapping. Grid snapping follows GridSettings, including its
        # offset. Holding Alt during a transform temporarily disables snapping.
        self.snap_to_grid = True
        self.rotation_snap_deg = ROTATION_SNAP_DEG
        # Magnetic snapping only engages near a target. Grid tolerance is in
        # screen pixels so it feels the same at every zoom level.
        self.grid_snap_tolerance_px = GRID_SNAP_TOLERANCE_PX
        self.rotation_snap_tolerance_deg = ROTATION_SNAP_TOLERANCE_DEG

        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(640, 480)

        self.image_format_bar = ImageFormattingBar(self)
        self.image_format_bar.replace_requested.connect(self.image_replace_requested.emit)

        self.text_format_bar = TextFormattingBar(self)
        self.text_format_bar.text_changed.connect(self._set_selected_text)
        self.text_format_bar.font_family_changed.connect(self._set_selected_text_font)
        self.text_format_bar.font_size_changed.connect(self._set_selected_text_size)
        self.text_format_bar.color_changed.connect(self._set_selected_text_color)
        self.text_format_bar.bold_changed.connect(self._set_selected_text_bold)
        self.text_format_bar.italic_changed.connect(self._set_selected_text_italic)
        self.text_format_bar.underline_changed.connect(self._set_selected_text_underline)

        self.show_initial_camera()

    # -------------------------------------------------------------------------
    # Asset / media
    # -------------------------------------------------------------------------

    def set_asset(self, asset: MapAsset, map_dir: Path) -> None:
        self.asset = asset
        self.map_dir = Path(map_dir)
        self.media = MediaCache(self.map_dir)
        self.selected_element_ids.clear()
        self.selected_element_id = None
        self.selected_token_ids.clear()
        self.selected_token_id = None
        self.camera_selected = False
        self._transform = None
        self._token_move = None
        self._camera_edit = None
        self._cancel_marquee()
        self._cancel_creation()
        self.pending_image_asset = None
        self.pending_token_entity_id = None
        self._clear_token_drag_preview()
        self._token_image_cache.clear()
        self._render_error = None
        self.clear_history(clear_clipboard=True)
        self.set_tool(EditorTool.SELECT)
        self.show_initial_camera()
        self.selection_changed.emit(None)
        self._sync_text_format_bar()
        self._sync_image_format_bar()
        self.update()

    def reload_media(self) -> None:
        self.media = MediaCache(self.map_dir)
        self._token_image_cache.clear()
        self._render_error = None
        self.update()

    def set_token_provider(self, provider: Optional[TokenEntityProvider]) -> None:
        self.token_provider = provider or NullTokenEntityProvider()
        self._token_image_cache.clear()
        self.update()

    def begin_token_placement(self, entity_id: str) -> None:
        entity_id = str(entity_id).strip()
        if not entity_id:
            return
        self.set_tool(EditorTool.SELECT)
        self.pending_token_entity_id = entity_id
        self._select_elements([])
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    def cancel_token_placement(self) -> None:
        if self.pending_token_entity_id is None:
            return
        self.pending_token_entity_id = None
        self.unsetCursor()
        self.update()

    def replace_selected_image_asset(self, asset: Path) -> None:
        element = self.selected_element()
        if not isinstance(element, ImageElement) or element.locked:
            return
        asset = Path(asset)
        if element.asset == asset:
            return
        before = self._capture_state()
        element.asset = asset
        self._commit_history(before)
        self._sync_image_format_bar()
        self.update()

    # -------------------------------------------------------------------------
    # Initial GM camera overlay
    # -------------------------------------------------------------------------

    def _initial_camera_frame(self) -> Rect:
        """
        Indicative frame for MapAsset.initial_gm_camera.

        CameraState stores center + zoom only. The frame therefore uses the
        current editor canvas size as a reference viewport. Resizing the editor
        changes this indication but never the saved CameraState.
        """
        state = self.asset.initial_gm_camera
        zoom = max(
            self.camera.MIN_ZOOM,
            min(
                self.camera.MAX_ZOOM,
                float(state.zoom if state.zoom > 0 else 1.0),
            ),
        )
        width = max(1.0, float(self.width()) / zoom)
        height = max(1.0, float(self.height()) / zoom)

        return Rect(
            state.center.x - width / 2.0,
            state.center.y - height / 2.0,
            width,
            height,
        )

    @staticmethod
    def _screen_distance(first: QPointF, second: QPointF) -> float:
        return (
            (first.x() - second.x()) ** 2
            + (first.y() - second.y()) ** 2
        ) ** 0.5

    def _camera_center_screen(self) -> QPointF:
        return self.world_to_screen(
            self.asset.initial_gm_camera.center
        )

    def _camera_zoom_handle_screen(self) -> QPointF:
        frame = self._initial_camera_frame()
        return self.world_to_screen(
            Vec2(frame.right, frame.bottom)
        )

    def _camera_hit_part(self, screen_pos: QPointF) -> Optional[str]:
        """
        Only the two explicit camera handles are interactive, so the large
        indicative frame never blocks normal element selection.
        """
        if self._screen_distance(
            screen_pos,
            self._camera_center_screen(),
        ) <= 11.0:
            return "move"

        if self._screen_distance(
            screen_pos,
            self._camera_zoom_handle_screen(),
        ) <= 11.0:
            return "zoom"

        return None

    def _select_camera(self) -> None:
        self.selected_element_ids.clear()
        self.selected_element_id = None
        self.selected_token_ids.clear()
        self.selected_token_id = None
        self.camera_selected = True

        self.selection_changed.emit(None)
        self._sync_text_format_bar()
        self._sync_image_format_bar()
        self.update()

    def _start_camera_edit(self, mode: str, screen_pos: QPointF) -> None:
        self._select_camera()

        state = self.asset.initial_gm_camera
        world = self.screen_to_world(screen_pos)

        self._camera_edit = CameraEditSession(
            mode=mode,
            start_screen=QPointF(screen_pos),
            start_world=Vec2(world.x, world.y),
            start_center=Vec2(state.center.x, state.center.y),
            start_zoom=float(state.zoom if state.zoom > 0 else 1.0),
            start_handle_distance=max(
                1.0,
                self._screen_distance(
                    self._camera_center_screen(),
                    self._camera_zoom_handle_screen(),
                ),
            ),
            history_before=self._capture_state(),
        )

        self.setCursor(
            Qt.CursorShape.SizeAllCursor
            if mode == "move"
            else Qt.CursorShape.SizeFDiagCursor
        )

    def _update_camera_edit(self, screen_pos: QPointF) -> None:
        session = self._camera_edit
        if session is None:
            return

        state = self.asset.initial_gm_camera

        if session.mode == "move":
            world = self.screen_to_world(screen_pos)
            dx = world.x - session.start_world.x
            dy = world.y - session.start_world.y

            state.center = Vec2(
                max(
                    0.0,
                    min(
                        float(self.asset.width),
                        session.start_center.x + dx,
                    ),
                ),
                max(
                    0.0,
                    min(
                        float(self.asset.height),
                        session.start_center.y + dy,
                    ),
                ),
            )

        elif session.mode == "zoom":
            center_screen = self.world_to_screen(
                session.start_center
            )
            distance = max(
                12.0,
                self._screen_distance(
                    center_screen,
                    screen_pos,
                ),
            )

            state.zoom = max(
                self.camera.MIN_ZOOM,
                min(
                    self.camera.MAX_ZOOM,
                    session.start_zoom
                    * session.start_handle_distance
                    / distance,
                ),
            )

        self.update()

    def _finish_camera_edit(self) -> None:
        session = self._camera_edit
        if (
            session is not None
            and session.history_before is not None
        ):
            self._commit_history(session.history_before)

        self._camera_edit = None
        self.unsetCursor()
        self.update()

    def cancel_camera_edit(self) -> None:
        session = self._camera_edit
        if session is None:
            return

        state = self.asset.initial_gm_camera
        state.center = Vec2(
            session.start_center.x,
            session.start_center.y,
        )
        state.zoom = session.start_zoom

        self._camera_edit = None
        self.unsetCursor()
        self.update()

    def _draw_initial_camera_overlay(self, painter: QPainter) -> None:
        frame = self._initial_camera_frame()
        state = self.asset.initial_gm_camera
        editor_zoom = max(0.001, self.camera.zoom)

        painter.save()

        color = QColor(
            "#74c7ec"
            if self.camera_selected
            else "#6c7086"
        )
        color.setAlpha(
            235 if self.camera_selected else 170
        )

        pen = QPen(color)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidthF(
            (2.0 if self.camera_selected else 1.25)
            / editor_zoom
        )
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(frame.to_qrectf())

        handle_radius = 7.0 / editor_zoom
        handle_size = 12.0 / editor_zoom

        painter.setPen(
            QPen(
                color,
                1.5 / editor_zoom,
            )
        )
        painter.setBrush(
            QColor(17, 17, 27, 210)
        )
        painter.drawEllipse(
            QPointF(
                state.center.x,
                state.center.y,
            ),
            handle_radius,
            handle_radius,
        )
        painter.drawLine(
            QPointF(
                state.center.x - handle_radius * 1.6,
                state.center.y,
            ),
            QPointF(
                state.center.x + handle_radius * 1.6,
                state.center.y,
            ),
        )
        painter.drawLine(
            QPointF(
                state.center.x,
                state.center.y - handle_radius * 1.6,
            ),
            QPointF(
                state.center.x,
                state.center.y + handle_radius * 1.6,
            ),
        )

        painter.setBrush(color)
        painter.drawRect(
            QRectF(
                frame.right - handle_size / 2.0,
                frame.bottom - handle_size / 2.0,
                handle_size,
                handle_size,
            )
        )

        font = QFont()
        font.setBold(self.camera_selected)
        font.setPixelSize(
            max(
                1,
                round(11.0 / editor_zoom),
            )
        )
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(
            QPointF(
                frame.x,
                frame.y - 8.0 / editor_zoom,
            ),
            f"INITIAL GM CAMERA  {state.zoom:.2f}x",
        )

        painter.restore()

    # -------------------------------------------------------------------------
    # Tokens
    # -------------------------------------------------------------------------

    def _token_view(self, entity_id: str) -> TokenViewData:
        view = self.token_provider.token_view(entity_id)
        if view is not None:
            return view
        label = entity_id[:8] if entity_id else "Entity"
        return TokenViewData(entity_id=entity_id, name=label)

    def _token_size(self, placement: InitialTokenPlacement) -> float:
        cell = max(1.0, float(self.asset.grid.cell_size))
        return max(8.0, cell * max(0.1, float(placement.size_cells)))

    def _token_rect(self, placement: InitialTokenPlacement) -> Rect:
        size = self._token_size(placement)
        return Rect(
            placement.pos.x - size / 2.0,
            placement.pos.y - size / 2.0,
            size,
            size,
        )

    def token_at(self, point: Vec2) -> Optional[InitialTokenPlacement]:
        # Tokens are an overlay; the last placement is considered top-most.
        for placement in reversed(self.asset.initial_tokens):
            if self._contains(self._token_rect(placement), point):
                return placement
        return None

    def selected_tokens(self) -> list[InitialTokenPlacement]:
        """Return selected token placements in map order."""
        if not self.selected_token_ids:
            return []

        return [
            token
            for token in self.asset.initial_tokens
            if token.id in self.selected_token_ids
        ]

    def selected_token(self) -> Optional[InitialTokenPlacement]:
        if self.selected_token_id is None:
            return None

        return next(
            (
                token
                for token in self.asset.initial_tokens
                if token.id == self.selected_token_id
            ),
            None,
        )

    def _select_tokens(
        self,
        tokens: list[InitialTokenPlacement],
        *,
        primary: Optional[InitialTokenPlacement] = None,
    ) -> None:
        """
        Select token placements.

        Token selection and static MapElement selection are exclusive so their
        transformation semantics never conflict.
        """
        ids = {
            token.id
            for token in tokens
        }

        primary_id = (
            primary.id
            if primary is not None and primary.id in ids
            else None
        )
        if primary_id is None and tokens:
            primary_id = tokens[-1].id

        # Clear MapElement selection directly. Calling _select_elements() here
        # would also clear the token selection we are currently constructing.
        self.selected_element_ids.clear()
        self.selected_element_id = None
        self.camera_selected = False

        self.selected_token_ids = ids
        self.selected_token_id = primary_id

        self.selection_changed.emit(None)
        self._sync_text_format_bar()
        self._sync_image_format_bar()
        self.update()

    def _select_token(
        self,
        token: Optional[InitialTokenPlacement],
    ) -> None:
        self._select_tokens(
            [token] if token is not None else [],
            primary=token,
        )

    def _toggle_token_selection(
        self,
        token: InitialTokenPlacement,
    ) -> None:
        selected = self.selected_tokens()

        if token.id in self.selected_token_ids:
            selected = [
                item
                for item in selected
                if item.id != token.id
            ]
            self._select_tokens(
                selected,
                primary=selected[-1] if selected else None,
            )
            return

        selected.append(token)
        self._select_tokens(
            selected,
            primary=token,
        )

    def _tokens_in_marquee(
        self,
        rect: Rect,
    ) -> list[InitialTokenPlacement]:
        return [
            token
            for token in self.asset.initial_tokens
            if self._token_rect(token).intersects(rect)
        ]

    def _start_token_move(
        self,
        screen_pos: QPointF,
        *,
        primary: InitialTokenPlacement,
    ) -> None:
        selected = self.selected_tokens()

        if primary.id not in self.selected_token_ids:
            selected = [primary]
            self._select_tokens(
                selected,
                primary=primary,
            )

        if not selected:
            return

        world = self.screen_to_world(
            screen_pos
        )
        self._token_move = TokenMoveSession(
            token_ids=[
                token.id
                for token in selected
            ],
            primary_token_id=primary.id,
            start_world=Vec2(
                world.x,
                world.y,
            ),
            positions={
                token.id: Vec2(
                    token.pos.x,
                    token.pos.y,
                )
                for token in selected
            },
            history_before=self._capture_state(),
        )
        self.setCursor(
            Qt.CursorShape.SizeAllCursor
        )

    def _snap_token_move_delta(
        self,
        session: TokenMoveSession,
        delta: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> Vec2:
        """
        Tokens are strictly constrained to grid-cell centers.

        Unlike normal map elements, token movement is never free while a valid
        grid exists: the primary token is quantized to the nearest cell center
        for the whole drag, and every other selected token receives the same
        cell-aligned delta.

        ``Alt`` therefore does not disable snapping for token movement.
        """
        primary_start = session.positions.get(
            session.primary_token_id
        )
        if primary_start is None:
            return delta

        grid = self.asset.grid
        step = float(grid.cell_size)

        # No usable grid: keep the current position rather than allowing a
        # token to drift off-grid.
        if not grid.enabled or step <= 0:
            return Vec2(0.0, 0.0)

        center_x = float(grid.offset_x) + step / 2.0
        center_y = float(grid.offset_y) + step / 2.0

        candidate = primary_start + delta

        snapped = Vec2(
            center_x
            + round(
                (candidate.x - center_x)
                / step
            )
            * step,
            center_y
            + round(
                (candidate.y - center_y)
                / step
            )
            * step,
        )

        return snapped - primary_start

    def _update_token_move(
        self,
        screen_pos: QPointF,
        modifiers: Qt.KeyboardModifier,
    ) -> None:
        session = self._token_move
        if session is None:
            return

        current_ids = {
            token.id
            for token in self.selected_tokens()
        }
        if current_ids != set(session.token_ids):
            self._token_move = None
            return

        world = self.screen_to_world(
            screen_pos
        )
        delta = Vec2(
            world.x - session.start_world.x,
            world.y - session.start_world.y,
        )
        delta = self._snap_token_move_delta(
            session,
            delta,
            modifiers,
        )

        tokens_by_id = {
            token.id: token
            for token in self.asset.initial_tokens
            if token.id in session.positions
        }

        for token_id, start_pos in session.positions.items():
            token = tokens_by_id.get(
                token_id
            )
            if token is None:
                continue

            token.pos = Vec2(
                start_pos.x + delta.x,
                start_pos.y + delta.y,
            )

        self.update()

    def _finish_token_move(self) -> None:
        session = self._token_move
        if (
            session is not None
            and session.history_before is not None
        ):
            self._commit_history(
                session.history_before
            )

        self._token_move = None
        self.unsetCursor()
        self.update()

    def cancel_token_move(self) -> None:
        session = self._token_move
        if session is None:
            return

        tokens_by_id = {
            token.id: token
            for token in self.asset.initial_tokens
            if token.id in session.positions
        }

        for token_id, start_pos in session.positions.items():
            token = tokens_by_id.get(
                token_id
            )
            if token is None:
                continue

            token.pos = Vec2(
                start_pos.x,
                start_pos.y,
            )

        self._token_move = None
        self.unsetCursor()
        self.update()

    def _load_token_portrait(self, path: Optional[Path]) -> Optional[QImage]:
        if path is None:
            return None
        key = Path(path).expanduser()
        cached = self._token_image_cache.get(key)
        if cached is not None:
            return cached
        image = QImage(str(key))
        if image.isNull():
            return None
        self._token_image_cache[key] = image
        return image

    @staticmethod
    def _draw_cover_image(painter: QPainter, target: QRectF, image: QImage) -> None:
        if image.isNull() or target.width() <= 0 or target.height() <= 0:
            return
        source_w = float(image.width())
        source_h = float(image.height())
        target_ratio = target.width() / target.height()
        source_ratio = source_w / source_h if source_h else 1.0
        if source_ratio > target_ratio:
            crop_w = source_h * target_ratio
            source = QRectF((source_w - crop_w) / 2.0, 0.0, crop_w, source_h)
        else:
            crop_h = source_w / target_ratio if target_ratio else source_h
            source = QRectF(0.0, (source_h - crop_h) / 2.0, source_w, crop_h)
        painter.drawImage(target, image, source)

    @staticmethod
    def _resource_bar(
        painter: QPainter,
        rect: QRectF,
        value: int,
        maximum: int,
        color: QColor,
    ) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#313244"))
        painter.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
        ratio = 0.0 if maximum <= 0 else max(0.0, min(1.0, float(value) / float(maximum)))
        if ratio > 0:
            fill = QRectF(rect.x(), rect.y(), rect.width() * ratio, rect.height())
            painter.setBrush(color)
            painter.drawRoundedRect(fill, rect.height() / 2.0, rect.height() / 2.0)

    def _draw_token(
        self,
        painter: QPainter,
        placement: InitialTokenPlacement,
        *,
        ghost: bool = False,
        view: Optional[TokenViewData] = None,
    ) -> None:
        view = view or self._token_view(placement.entity_id)
        size = self._token_size(placement)
        half = size / 2.0
        opacity = max(0.0, min(1.0, float(placement.opacity)))
        if placement.hidden:
            opacity *= 0.40
        if ghost:
            opacity *= 0.60

        # Portrait: rotation belongs to the token image. Labels/resources stay upright.
        painter.save()
        painter.setOpacity(opacity)
        painter.translate(placement.pos.x, placement.pos.y)
        if placement.rotation:
            painter.rotate(placement.rotation)
        portrait_rect = QRectF(-half, -half, size, size)
        portrait = self._load_token_portrait(view.portrait)
        if portrait is not None:
            self._draw_cover_image(painter, portrait_rect, portrait)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#313244"))
            painter.drawRoundedRect(portrait_rect, size * 0.12, size * 0.12)
            initials = "".join(part[:1] for part in view.name.split()[:2]).upper() or "?"
            font = QFont()
            font.setBold(True)
            font.setPixelSize(max(8, round(size * 0.30)))
            painter.setFont(font)
            painter.setPen(QColor("#cdd6f4"))
            painter.drawText(portrait_rect, Qt.AlignmentFlag.AlignCenter, initials)
        border = QPen(QColor("#89b4fa" if not placement.hidden else "#6c7086"))
        border.setWidthF(max(1.0, size * 0.025))
        border.setStyle(Qt.PenStyle.DashLine if placement.hidden else Qt.PenStyle.SolidLine)
        painter.setPen(border)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(portrait_rect, size * 0.12, size * 0.12)
        painter.restore()

        painter.save()
        painter.setOpacity(opacity)
        # Name above the portrait.
        font = QFont()
        font.setBold(True)
        font.setPixelSize(max(7, round(size * 0.13)))
        painter.setFont(font)
        painter.setPen(QColor("#cdd6f4"))
        name_h = max(10.0, size * 0.20)
        painter.drawText(
            QRectF(placement.pos.x - size * 0.75, placement.pos.y - half - name_h - size * 0.04,
                   size * 1.5, name_h),
            Qt.AlignmentFlag.AlignCenter,
            view.name,
        )

        # Resource bars below the portrait.
        bar_h = max(3.0, size * 0.065)
        bar_gap = max(2.0, size * 0.025)
        bar_y = placement.pos.y + half + max(3.0, size * 0.05)
        hp_rect = QRectF(placement.pos.x - half, bar_y, size, bar_h)
        sta_rect = QRectF(placement.pos.x - half, bar_y + bar_h + bar_gap, size, bar_h)
        self._resource_bar(painter, hp_rect, view.hp, view.hp_max, QColor("#f38ba8"))
        self._resource_bar(painter, sta_rect, view.stamina, view.stamina_max, QColor("#fab387"))

        # Statuses: compact coloured badges under the bars.
        statuses = view.statuses[:8]
        if statuses:
            radius = max(4.0, size * 0.075)
            diameter = radius * 2.0
            gap = max(2.0, radius * 0.35)
            total_w = len(statuses) * diameter + max(0, len(statuses) - 1) * gap
            start_x = placement.pos.x - total_w / 2.0 + radius
            center_y = sta_rect.bottom() + max(3.0, size * 0.05) + radius
            status_font = QFont()
            status_font.setBold(True)
            status_font.setPixelSize(max(6, round(radius * 0.95)))
            painter.setFont(status_font)
            for index, status in enumerate(statuses):
                center_x = start_x + index * (diameter + gap)
                color = QColor(status.color)
                if not color.isValid():
                    color = QColor("#cba6f7")
                painter.setPen(QPen(QColor("#11111b"), max(1.0, radius * 0.12)))
                painter.setBrush(color)
                painter.drawEllipse(QPointF(center_x, center_y), radius, radius)
                label = (status.short_label or status.name[:1] or "?").upper()[:2]
                painter.setPen(QColor("#11111b"))
                painter.drawText(
                    QRectF(center_x - radius, center_y - radius, diameter, diameter),
                    Qt.AlignmentFlag.AlignCenter,
                    label,
                )
        painter.restore()

    def _draw_tokens(self, painter: QPainter, view: Rect) -> None:
        for placement in self.asset.initial_tokens:
            if not self._token_rect(placement).intersects(view):
                continue
            self._draw_token(painter, placement)

        if self.pending_token_entity_id is not None and self._hover_world is not None:
            pos = Vec2(self._hover_world.x, self._hover_world.y)
            if self.asset.grid.enabled and self.asset.grid.cell_size > 0:
                pos = self.asset.grid.cell_center(pos)
            ghost = InitialTokenPlacement(entity_id=self.pending_token_entity_id, pos=pos)
            self._draw_token(painter, ghost, ghost=True)

        if self._drag_token_preview is not None and self._hover_world is not None:
            pos = Vec2(self._hover_world.x, self._hover_world.y)
            if self.asset.grid.enabled and self.asset.grid.cell_size > 0:
                pos = self.asset.grid.cell_center(pos)

            ghost = InitialTokenPlacement(
                entity_id=self._drag_token_source_id or "",
                pos=pos,
            )
            self._draw_token(
                painter,
                ghost,
                ghost=True,
                view=self._drag_token_preview,
            )

    def place_token_at(
        self,
        entity_id: str,
        world: Vec2,
    ) -> InitialTokenPlacement:
        """Place or move one Entity token as a single undoable map edit."""
        before = self._capture_state()
        placement = self.asset.place_token(
            str(entity_id),
            world,
            snap=True,
        )
        self._select_token(
            placement
        )
        self._commit_history(before)
        self.token_placed.emit(placement.entity_id)
        self.update()
        return placement

    def _place_pending_token(self, world: Vec2) -> None:
        entity_id = self.pending_token_entity_id
        if entity_id is None:
            return

        self.pending_token_entity_id = None
        self.unsetCursor()
        self.place_token_at(entity_id, world)

    # -------------------------------------------------------------------------
    # Undo / redo / clipboard
    # -------------------------------------------------------------------------

    def _capture_state(self) -> EditorState:
        return EditorState(
            elements=[deepcopy(element.to_dict()) for element in self.asset.elements],
            tokens=[deepcopy(token.to_dict()) for token in self.asset.initial_tokens],
            initial_camera=deepcopy(self.asset.initial_gm_camera.to_dict()),
            selected_element_ids=sorted(self.selected_element_ids),
            selected_element_id=self.selected_element_id,
            selected_token_ids=sorted(self.selected_token_ids),
            selected_token_id=self.selected_token_id,
            camera_selected=self.camera_selected,
        )

    def _restore_state(self, state: EditorState) -> None:
        self._transform = None
        self._token_move = None
        self._camera_edit = None
        self._cancel_creation()
        self.asset.elements = [
            MapElement.from_dict(deepcopy(data))
            for data in state.elements
        ]
        self.asset.initial_tokens = [
            InitialTokenPlacement.from_dict(deepcopy(data))
            for data in state.tokens
        ]
        self.asset.initial_gm_camera = CameraState.from_dict(
            deepcopy(state.initial_camera)
        )

        ids = {element.id for element in self.asset.elements}
        self.selected_element_ids = {
            element_id for element_id in state.selected_element_ids
            if element_id in ids
        }
        self.selected_element_id = (
            state.selected_element_id
            if state.selected_element_id in self.selected_element_ids
            else (next(iter(self.selected_element_ids), None))
        )

        token_ids = {
            token.id
            for token in self.asset.initial_tokens
        }
        self.selected_token_ids = {
            token_id
            for token_id in state.selected_token_ids
            if token_id in token_ids
        }
        self.selected_token_id = (
            state.selected_token_id
            if state.selected_token_id in self.selected_token_ids
            else (next(iter(self.selected_token_ids), None))
        )

        self.camera_selected = bool(state.camera_selected)

        if self.camera_selected:
            self.selected_element_ids.clear()
            self.selected_element_id = None
            self.selected_token_ids.clear()
            self.selected_token_id = None
        elif self.selected_token_ids:
            self.selected_element_ids.clear()
            self.selected_element_id = None

        self.selection_changed.emit(self.selected_element())
        self._sync_text_format_bar()
        self._sync_image_format_bar()
        self.update()

    def _commit_history(self, before: EditorState) -> None:
        after = self._capture_state()
        if before == after:
            return

        self._undo_stack.append(before)
        if len(self._undo_stack) > MAX_UNDO_STEPS:
            del self._undo_stack[0]
        self._redo_stack.clear()
        self.history_changed.emit(bool(self._undo_stack), bool(self._redo_stack))

    def clear_history(self, *, clear_clipboard: bool = False) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.history_changed.emit(False, False)

        if clear_clipboard:
            self._element_clipboard = None
            self._paste_count = 0
            self.clipboard_changed.emit(False)

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def undo(self) -> None:
        if not self._undo_stack:
            return

        current = self._capture_state()
        state = self._undo_stack.pop()
        self._redo_stack.append(current)
        self._restore_state(state)
        self.history_changed.emit(bool(self._undo_stack), bool(self._redo_stack))

    def redo(self) -> None:
        if not self._redo_stack:
            return

        current = self._capture_state()
        state = self._redo_stack.pop()
        self._undo_stack.append(current)
        self._restore_state(state)
        self.history_changed.emit(bool(self._undo_stack), bool(self._redo_stack))

    # Standard editor API used by CampaignManagerWindow._edit_focused().
    def cut(self) -> None:
        self.cut_selected()

    def copy(self) -> None:
        self.copy_selected()

    def paste(self) -> None:
        self.paste_element()

    def selectAll(self) -> None:  # noqa: N802 - Qt API compatibility
        if self.selected_token_ids:
            tokens = list(self.asset.initial_tokens)
            self._select_tokens(
                tokens,
                primary=tokens[-1] if tokens else None,
            )
            return

        elements = list(self.asset.elements)
        self._select_elements(
            elements,
            primary=elements[-1] if elements else None,
        )

    def selected_elements(self) -> list[MapElement]:
        """Return selected elements in scene z-order."""
        if not self.selected_element_ids:
            return []
        return [
            element for element in self.asset.elements
            if element.id in self.selected_element_ids
        ]

    def _copy_elements(self, elements: list[MapElement]) -> None:
        if not elements:
            return
        self._element_clipboard = [deepcopy(element.to_dict()) for element in elements]
        self._paste_count = 0
        self.clipboard_changed.emit(True)

    def copy_selected(self) -> None:
        self._copy_elements(self.selected_elements())

    @staticmethod
    def _translate_element(element: MapElement, delta: Vec2) -> None:
        if isinstance(element, (ImageElement, TextElement)):
            element.pos = Vec2(
                element.pos.x + delta.x,
                element.pos.y + delta.y,
            )
        elif isinstance(element, DrawingElement):
            element.points = [
                Vec2(point.x + delta.x, point.y + delta.y)
                for point in element.points
            ]

    def paste_element(self) -> None:
        if not self._element_clipboard:
            return

        before = self._capture_state()
        self._paste_count += 1
        offset = PASTE_OFFSET_PX * self._paste_count
        pasted: list[MapElement] = []

        for source_data in self._element_clipboard:
            data = deepcopy(source_data)
            data.pop("id", None)
            element = MapElement.from_dict(data)
            self._translate_element(element, Vec2(offset, offset))
            self.asset.elements.append(element)
            pasted.append(element)

        self._select_elements(pasted, primary=pasted[-1] if pasted else None)
        self._commit_history(before)
        self.update()

    def delete_selected(self) -> None:
        selected_tokens = self.selected_tokens()

        if selected_tokens:
            before = self._capture_state()
            removed_ids = {
                token.id
                for token in selected_tokens
            }

            self.asset.initial_tokens = [
                token
                for token in self.asset.initial_tokens
                if token.id not in removed_ids
            ]

            self.selected_token_ids.clear()
            self.selected_token_id = None
            self.selection_changed.emit(None)

            self._commit_history(
                before
            )
            self.update()
            return

        selected = self.selected_elements()
        removable = [element for element in selected if not element.locked]
        if not removable:
            return

        before = self._capture_state()
        removed_ids = {element.id for element in removable}
        self.asset.elements = [
            item for item in self.asset.elements
            if item.id not in removed_ids
        ]
        remaining = [
            element for element in selected
            if element.id not in removed_ids
        ]
        self._select_elements(remaining, primary=remaining[-1] if remaining else None)
        self._commit_history(before)
        self.update()

    def cut_selected(self) -> None:
        removable = [element for element in self.selected_elements() if not element.locked]
        if not removable:
            return
        self._copy_elements(removable)

        before = self._capture_state()
        removed_ids = {element.id for element in removable}
        self.asset.elements = [
            item for item in self.asset.elements
            if item.id not in removed_ids
        ]
        remaining = [
            element for element in self.selected_elements()
            if element.id not in removed_ids
        ]
        self._select_elements(remaining, primary=remaining[-1] if remaining else None)
        self._commit_history(before)
        self.update()

    # -------------------------------------------------------------------------
    # Editor tools / creation
    # -------------------------------------------------------------------------

    def set_tool(self, tool: EditorTool) -> None:
        if self._transform is not None:
            self.cancel_transform()
        if self._token_move is not None:
            self.cancel_token_move()
        if self._camera_edit is not None:
            self.cancel_camera_edit()
        self._cancel_creation()
        self._cancel_marquee()
        changed = tool is not self.active_tool
        self.active_tool = tool
        if tool is not EditorTool.SELECT:
            self.pending_token_entity_id = None

        if tool is EditorTool.SELECT:
            self.unsetCursor()
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

        if changed:
            self.tool_changed.emit(tool)
        self._sync_text_format_bar()
        self._sync_image_format_bar()
        self.update()

    def set_creation_style(
        self,
        *,
        stroke_color: Optional[str] = None,
        fill_color: object = ...,
        stroke_width: Optional[float] = None,
        opacity: Optional[float] = None,
        font_size: Optional[float] = None,
    ) -> None:
        if stroke_color is not None:
            self.stroke_color = stroke_color
        if fill_color is not ...:
            self.fill_color = fill_color if isinstance(fill_color, str) else None
        if stroke_width is not None:
            self.stroke_width = max(0.0, float(stroke_width))
        if opacity is not None:
            self.element_opacity = max(0.0, min(1.0, float(opacity)))
        if font_size is not None:
            self.text_font_size = max(1.0, float(font_size))
        self.update()

    def set_pending_image(self, asset: Path, width: float, height: float) -> None:
        self.pending_image_asset = Path(asset)
        self.pending_image_size = (max(1.0, float(width)), max(1.0, float(height)))
        self.set_tool(EditorTool.IMAGE)

    def _cancel_creation(self) -> None:
        self._creation_start = None
        self._creation_current = None
        self._creation_points = []
        self.update()

    def _append_element(self, element: MapElement, *, select: bool = True) -> None:
        before = self._capture_state()
        self.asset.elements.append(element)
        if select:
            self._select_element(element)
        self._commit_history(before)
        self.update()

    def _start_drawing_creation(self, world: Vec2) -> None:
        self._creation_start = Vec2(world.x, world.y)
        self._creation_current = Vec2(world.x, world.y)
        self._creation_points = [Vec2(world.x, world.y)]

    def _update_drawing_creation(self, world: Vec2) -> None:
        if self._creation_start is None:
            return
        self._creation_current = Vec2(world.x, world.y)
        if self.active_tool is EditorTool.FREEHAND:
            if not self._creation_points:
                self._creation_points.append(Vec2(world.x, world.y))
            else:
                last = self._creation_points[-1]
                # Avoid producing hundreds of identical points at high event rates.
                if hypot(world.x - last.x, world.y - last.y) >= max(0.5, 1.0 / self.camera.zoom):
                    self._creation_points.append(Vec2(world.x, world.y))
        self.update()

    def _drawing_preview(self) -> Optional[DrawingElement]:
        draw_tool = DRAW_TOOLS.get(self.active_tool)
        if draw_tool is None or self._creation_start is None:
            return None

        if self.active_tool is EditorTool.FREEHAND:
            points = [Vec2(p.x, p.y) for p in self._creation_points]
            if self._creation_current is not None and points:
                last = points[-1]
                if last.x != self._creation_current.x or last.y != self._creation_current.y:
                    points.append(Vec2(self._creation_current.x, self._creation_current.y))
        else:
            end = self._creation_current or self._creation_start
            points = [
                Vec2(self._creation_start.x, self._creation_start.y),
                Vec2(end.x, end.y),
            ]

        return DrawingElement(
            tool=draw_tool,
            points=points,
            stroke_color=self.stroke_color,
            stroke_width=self.stroke_width,
            fill_color=(
                self.fill_color
                if draw_tool in (DrawTool.RECT, DrawTool.CIRCLE)
                else None
            ),
            opacity=self.element_opacity,
        )

    def _finish_drawing_creation(self, world: Vec2) -> None:
        if self._creation_start is None:
            return
        self._update_drawing_creation(world)
        element = self._drawing_preview()
        self._cancel_creation()
        if element is None or not element.points:
            return
        self._append_element(element, select=True)

    def _place_image(self, world: Vec2) -> None:
        if self.pending_image_asset is None:
            return
        width, height = self.pending_image_size
        element = ImageElement(
            asset=self.pending_image_asset,
            pos=Vec2(world.x - width / 2.0, world.y - height / 2.0),
            width=width,
            height=height,
            opacity=self.element_opacity,
        )
        self._append_element(element, select=True)
        # Image placement is usually followed by a resize/rotation.
        self.set_tool(EditorTool.SELECT)

    def _place_text(self, world: Vec2) -> None:
        element = TextElement(
            text="Text",
            pos=Vec2(world.x, world.y),
            font_size=self.text_font_size,
            font_color=self.stroke_color,
            opacity=self.element_opacity,
        )
        self._append_element(element, select=True)
        self.set_tool(EditorTool.SELECT)
        self._sync_text_format_bar(focus=True, select_all=True)

    def _draw_creation_preview(self, painter: QPainter) -> None:
        preview = self._drawing_preview()
        if preview is not None:
            preview.render(painter, self.media)
            return

        if (
            self.active_tool is EditorTool.IMAGE
            and self.pending_image_asset is not None
            and self._hover_world is not None
        ):
            width, height = self.pending_image_size
            ghost = ImageElement(
                asset=self.pending_image_asset,
                pos=Vec2(
                    self._hover_world.x - width / 2.0,
                    self._hover_world.y - height / 2.0,
                ),
                width=width,
                height=height,
                opacity=min(self.element_opacity, 0.65),
            )
            try:
                ghost.render(painter, self.media)
            except Exception:
                # The regular render error path will report unreadable assets after placement.
                pass

    # -------------------------------------------------------------------------
    # Camera transforms
    # -------------------------------------------------------------------------

    def screen_to_world(self, point: QPointF) -> Vec2:
        zoom = self.camera.zoom
        return Vec2(
            self.camera.center.x + (point.x() - self.width() / 2.0) / zoom,
            self.camera.center.y + (point.y() - self.height() / 2.0) / zoom,
        )

    def world_to_screen(self, point: Vec2) -> QPointF:
        zoom = self.camera.zoom
        return QPointF(
            self.width() / 2.0 + (point.x - self.camera.center.x) * zoom,
            self.height() / 2.0 + (point.y - self.camera.center.y) * zoom,
        )

    def visible_world_rect(self) -> Rect:
        zoom = self.camera.zoom
        world_width = self.width() / zoom
        world_height = self.height() / zoom
        return Rect(
            self.camera.center.x - world_width / 2.0,
            self.camera.center.y - world_height / 2.0,
            world_width,
            world_height,
        )

    def _apply_camera_transform(self, painter: QPainter) -> None:
        painter.translate(self.width() / 2.0, self.height() / 2.0)
        painter.scale(self.camera.zoom, self.camera.zoom)
        painter.translate(-self.camera.center.x, -self.camera.center.y)

    def set_zoom_at(self, new_zoom: float, screen_pos: QPointF) -> None:
        old_world = self.screen_to_world(screen_pos)
        self.camera.set_zoom(new_zoom)

        # Preserve the world point under the mouse cursor.
        self.camera.center = Vec2(
            old_world.x - (screen_pos.x() - self.width() / 2.0) / self.camera.zoom,
            old_world.y - (screen_pos.y() - self.height() / 2.0) / self.camera.zoom,
        )

        self.zoom_changed.emit(self.camera.zoom)
        self.update()

    def show_initial_camera(self) -> None:
        state = self.asset.initial_gm_camera
        if state.zoom <= 0:
            self.fit_map()
            return
        self.camera.from_state(state)
        self.zoom_changed.emit(self.camera.zoom)
        self.update()

    def set_current_as_initial_camera(self) -> None:
        self.asset.initial_gm_camera = self.camera.to_state()

    def fit_map(self, margin_px: float = 32.0) -> None:
        if self.asset.width <= 0 or self.asset.height <= 0:
            return

        usable_width = max(1.0, self.width() - margin_px * 2.0)
        usable_height = max(1.0, self.height() - margin_px * 2.0)

        zoom = min(
            usable_width / self.asset.width,
            usable_height / self.asset.height,
        )

        self.camera.center = Vec2(
            self.asset.width / 2.0,
            self.asset.height / 2.0,
        )
        self.camera.set_zoom(zoom)
        self.zoom_changed.emit(self.camera.zoom)
        self.update()

    # -------------------------------------------------------------------------
    # Selection / transform geometry
    # -------------------------------------------------------------------------

    def selected_element(self) -> Optional[MapElement]:
        """Return the primary selected element, if any."""
        if self.selected_element_id is None:
            return None
        return next(
            (element for element in self.asset.elements if element.id == self.selected_element_id),
            None,
        )

    @staticmethod
    def _contains(rect: Rect, point: Vec2) -> bool:
        return (
            rect.x <= point.x <= rect.right
            and rect.y <= point.y <= rect.bottom
        )

    @staticmethod
    def _text_size(element: TextElement, font_size: Optional[float] = None) -> tuple[float, float]:
        size = element.font_size if font_size is None else font_size
        font = QFont(element.font_family) if element.font_family else QFont()
        font.setPixelSize(max(1, round(size)))
        font.setBold(bool(getattr(element, "bold", False)))
        font.setItalic(bool(getattr(element, "italic", False)))
        font.setUnderline(bool(getattr(element, "underline", False)))
        metrics = QFontMetricsF(font)
        lines = element.text.split("\n")
        width = max((metrics.horizontalAdvance(line) for line in lines), default=0.0)
        height = metrics.lineSpacing() * max(1, len(lines))
        return max(1.0, width), max(1.0, height)

    def _element_frame(self, element: MapElement) -> ElementFrame:
        if isinstance(element, ImageElement):
            return ElementFrame(
                center=Vec2(
                    element.pos.x + element.width / 2.0,
                    element.pos.y + element.height / 2.0,
                ),
                width=max(0.0, element.width),
                height=max(0.0, element.height),
                rotation=element.rotation,
            )

        if isinstance(element, TextElement):
            width, height = self._text_size(element)
            return ElementFrame(
                center=Vec2(element.pos.x + width / 2.0, element.pos.y + height / 2.0),
                width=width,
                height=height,
                rotation=element.rotation,
            )

        if isinstance(element, DrawingElement):
            rect = Rect.from_points(element.points)
            return ElementFrame(
                center=Vec2(rect.x + rect.width / 2.0, rect.y + rect.height / 2.0),
                width=max(0.0, rect.width),
                height=max(0.0, rect.height),
                rotation=0.0,
            )

        bounds = element.bounds()
        return ElementFrame(
            center=Vec2(bounds.x + bounds.width / 2.0, bounds.y + bounds.height / 2.0),
            width=max(0.0, bounds.width),
            height=max(0.0, bounds.height),
            rotation=0.0,
        )

    def _frame_screen_points(self, frame: ElementFrame) -> list[QPointF]:
        return [self.world_to_screen(point) for point in frame.corners()]

    def _frame_contains_screen(self, frame: ElementFrame, point: QPointF) -> bool:
        polygon = QPolygonF(self._frame_screen_points(frame))
        return polygon.containsPoint(point, Qt.FillRule.WindingFill)

    def _element_contains(self, element: MapElement, point: Vec2) -> bool:
        if isinstance(element, (ImageElement, TextElement)):
            frame = self._element_frame(element)
            polygon = QPolygonF([QPointF(p.x, p.y) for p in frame.corners()])
            return polygon.containsPoint(QPointF(point.x, point.y), Qt.FillRule.WindingFill)
        return self._contains(element.bounds(), point)

    def element_at(self, point: Vec2) -> Optional[MapElement]:
        # elements are ordered bottom -> top, so hit-test in reverse.
        for element in reversed(self.asset.elements):
            if not element.visible:
                continue
            if self._element_contains(element, point):
                return element
        return None

    def _select_elements(
        self,
        elements: list[MapElement],
        *,
        primary: Optional[MapElement] = None,
    ) -> None:
        ids = {element.id for element in elements}
        primary_id = primary.id if primary is not None and primary.id in ids else None
        if primary_id is None and elements:
            primary_id = elements[-1].id

        # Selecting static scene elements leaves token/camera mode.
        self.selected_token_ids.clear()
        self.selected_token_id = None
        self.camera_selected = False

        if ids == self.selected_element_ids and primary_id == self.selected_element_id:
            self._sync_text_format_bar()
            self._sync_image_format_bar()
            return

        self.selected_element_ids = ids
        self.selected_element_id = primary_id
        self.selection_changed.emit(self.selected_element())
        self._sync_text_format_bar()
        self._sync_image_format_bar()
        self.update()

    def _select_element(self, element: Optional[MapElement]) -> None:
        self._select_elements([element] if element is not None else [], primary=element)

    def _toggle_element_selection(self, element: MapElement) -> None:
        selected = self.selected_elements()
        if element.id in self.selected_element_ids:
            selected = [item for item in selected if item.id != element.id]
            self._select_elements(selected, primary=selected[-1] if selected else None)
        else:
            selected.append(element)
            self._select_elements(selected, primary=element)

    def select_at(self, point: Vec2) -> None:
        self._select_element(self.element_at(point))

    def _selected_image_element(self) -> Optional[ImageElement]:
        if len(self.selected_element_ids) != 1:
            return None
        element = self.selected_element()
        return element if isinstance(element, ImageElement) else None

    def _sync_image_format_bar(self) -> None:
        element = self._selected_image_element()
        if element is None or self.active_tool is not EditorTool.SELECT:
            self.image_format_bar.hide()
            return
        self.image_format_bar.set_element(element)
        self.image_format_bar.show()
        self.image_format_bar.raise_()
        self._position_image_format_bar()

    def _position_image_format_bar(self) -> None:
        element = self._selected_image_element()
        if element is None or not self.image_format_bar.isVisible():
            return

        points = self._frame_screen_points(self._element_frame(element))
        min_x = min(point.x() for point in points)
        max_x = max(point.x() for point in points)
        min_y = min(point.y() for point in points)
        max_y = max(point.y() for point in points)

        self.image_format_bar.adjustSize()
        width = self.image_format_bar.width()
        height = self.image_format_bar.height()

        x = round((min_x + max_x - width) / 2.0)
        x = max(8, min(x, max(8, self.width() - width - 8)))

        y = round(min_y - height - ROTATE_HANDLE_OFFSET_PX - 10.0)
        if y < 8:
            y = round(max_y + 12.0)
        y = max(8, min(y, max(8, self.height() - height - 8)))

        self.image_format_bar.move(x, y)

    def _selected_text_element(self) -> Optional[TextElement]:
        if len(self.selected_element_ids) != 1:
            return None
        element = self.selected_element()
        return element if isinstance(element, TextElement) else None

    def _sync_text_format_bar(
        self,
        *,
        focus: bool = False,
        select_all: bool = False,
    ) -> None:
        element = self._selected_text_element()
        if element is None or self.active_tool is not EditorTool.SELECT:
            self.text_format_bar.hide()
            return
        self.text_format_bar.set_element(element)
        self.text_format_bar.show()
        self.text_format_bar.raise_()
        self._position_text_format_bar()
        if focus and not element.locked:
            self.text_format_bar.focus_text(select_all=select_all)

    def _position_text_format_bar(self) -> None:
        element = self._selected_text_element()
        if element is None or not self.text_format_bar.isVisible():
            return

        points = self._frame_screen_points(self._element_frame(element))
        min_x = min(point.x() for point in points)
        max_x = max(point.x() for point in points)
        min_y = min(point.y() for point in points)
        max_y = max(point.y() for point in points)

        self.text_format_bar.adjustSize()
        width = self.text_format_bar.width()
        height = self.text_format_bar.height()

        x = round((min_x + max_x - width) / 2.0)
        x = max(8, min(x, max(8, self.width() - width - 8)))

        # Leave room for the rotation handle above the frame.
        y = round(min_y - height - ROTATE_HANDLE_OFFSET_PX - 10.0)
        if y < 8:
            y = round(max_y + 12.0)
        y = max(8, min(y, max(8, self.height() - height - 8)))

        if self.text_format_bar.pos() != QPointF(float(x), float(y)).toPoint():
            self.text_format_bar.move(x, y)

    def _mutate_selected_text(self, change) -> None:
        element = self._selected_text_element()
        if element is None or element.locked:
            return

        before = self._capture_state()
        old_element = deepcopy(element.to_dict())
        change(element)

        if element.to_dict() != old_element:
            self._commit_history(before)

        self.update()
        self._position_text_format_bar()

    def _set_selected_text(self, text: str) -> None:
        self._mutate_selected_text(lambda element: setattr(element, "text", text))

    def _set_selected_text_font(self, family: str) -> None:
        self._mutate_selected_text(
            lambda element: setattr(element, "font_family", family or None)
        )

    def _set_selected_text_size(self, size: float) -> None:
        self._mutate_selected_text(
            lambda element: setattr(element, "font_size", max(1.0, float(size)))
        )

    def _set_selected_text_color(self, color: str) -> None:
        self._mutate_selected_text(lambda element: setattr(element, "font_color", color))

    def _set_selected_text_bold(self, enabled: bool) -> None:
        self._mutate_selected_text(lambda element: setattr(element, "bold", bool(enabled)))

    def _set_selected_text_italic(self, enabled: bool) -> None:
        self._mutate_selected_text(lambda element: setattr(element, "italic", bool(enabled)))

    def _set_selected_text_underline(self, enabled: bool) -> None:
        self._mutate_selected_text(lambda element: setattr(element, "underline", bool(enabled)))

    def _selection_bounds(self) -> Optional[Rect]:
        selected = self.selected_elements()
        if not selected:
            return None
        bounds = [element.bounds() for element in selected]
        left = min(rect.x for rect in bounds)
        top = min(rect.y for rect in bounds)
        right = max(rect.right for rect in bounds)
        bottom = max(rect.bottom for rect in bounds)
        return Rect(left, top, right - left, bottom - top)

    def _selection_frame(
        self,
        elements: Optional[list[MapElement]] = None,
    ) -> Optional[ElementFrame]:
        """Axis-aligned transform frame containing the whole selection."""
        items = self.selected_elements() if elements is None else elements
        if not items:
            return None

        bounds = [element.bounds() for element in items]
        left = min(rect.x for rect in bounds)
        top = min(rect.y for rect in bounds)
        right = max(rect.right for rect in bounds)
        bottom = max(rect.bottom for rect in bounds)

        return ElementFrame(
            center=Vec2((left + right) / 2.0, (top + bottom) / 2.0),
            width=max(0.0, right - left),
            height=max(0.0, bottom - top),
            rotation=0.0,
        )

    @staticmethod
    def _group_is_locked(elements: list[MapElement]) -> bool:
        """A group transform is disabled when any selected member is locked."""
        return any(element.locked for element in elements)

    def _group_can_rotate(self, elements: list[MapElement]) -> bool:
        """
        Whether every member can represent a rotation with the current model.

        RECT/CIRCLE DrawingElement instances are axis-aligned in map.py, so a
        group containing one cannot be rotated without changing its semantics.
        """
        return bool(elements) and all(self._can_rotate(element) for element in elements)

    def _cancel_marquee(self) -> None:
        self._marquee_start_screen = None
        self._marquee_current_screen = None
        self._marquee_modifiers = Qt.KeyboardModifier.NoModifier
        self.update()

    def _start_marquee(
        self,
        screen_pos: QPointF,
        modifiers: Qt.KeyboardModifier,
    ) -> None:
        self._marquee_start_screen = QPointF(screen_pos)
        self._marquee_current_screen = QPointF(screen_pos)
        self._marquee_modifiers = modifiers
        self.update()

    def _update_marquee(self, screen_pos: QPointF) -> None:
        if self._marquee_start_screen is None:
            return
        self._marquee_current_screen = QPointF(screen_pos)
        self.update()

    def _marquee_screen_rect(self) -> Optional[QRectF]:
        if self._marquee_start_screen is None or self._marquee_current_screen is None:
            return None
        return QRectF(self._marquee_start_screen, self._marquee_current_screen).normalized()

    def _marquee_world_rect(self) -> Optional[Rect]:
        screen_rect = self._marquee_screen_rect()
        if screen_rect is None:
            return None
        top_left = self.screen_to_world(screen_rect.topLeft())
        bottom_right = self.screen_to_world(screen_rect.bottomRight())
        return Rect(
            min(top_left.x, bottom_right.x),
            min(top_left.y, bottom_right.y),
            abs(bottom_right.x - top_left.x),
            abs(bottom_right.y - top_left.y),
        )

    def _elements_in_marquee(self, rect: Rect) -> list[MapElement]:
        return [
            element for element in self.asset.elements
            if element.visible and element.bounds().intersects(rect)
        ]

    def _apply_marquee_selection(self, end_screen: QPointF) -> None:
        start = self._marquee_start_screen
        if start is None:
            return

        dx = end_screen.x() - start.x()
        dy = end_screen.y() - start.y()
        distance = hypot(dx, dy)
        modifiers = self._marquee_modifiers

        if distance <= MARQUEE_CLICK_TOLERANCE_PX:
            element = self.element_at(self.screen_to_world(end_screen))
            if modifiers & Qt.KeyboardModifier.ControlModifier:
                if element is not None:
                    self._toggle_element_selection(element)
            elif modifiers & Qt.KeyboardModifier.ShiftModifier:
                if element is not None and element.id not in self.selected_element_ids:
                    selected = self.selected_elements()
                    selected.append(element)
                    self._select_elements(selected, primary=element)
            else:
                self._select_element(element)
            return

        world_rect = self._marquee_world_rect()
        if world_rect is None:
            return

        token_hits = self._tokens_in_marquee(
            world_rect
        )

        # Tokens are the visual overlay. If the marquee touches any token,
        # this gesture selects tokens rather than underlying map elements.
        if token_hits:
            if modifiers & Qt.KeyboardModifier.ControlModifier:
                selected_ids = set(
                    self.selected_token_ids
                )

                for token in token_hits:
                    if token.id in selected_ids:
                        selected_ids.remove(
                            token.id
                        )
                    else:
                        selected_ids.add(
                            token.id
                        )

                selected = [
                    token
                    for token in self.asset.initial_tokens
                    if token.id in selected_ids
                ]
                primary = (
                    token_hits[-1]
                    if token_hits[-1].id in selected_ids
                    else None
                )
                self._select_tokens(
                    selected,
                    primary=primary,
                )

            elif modifiers & Qt.KeyboardModifier.ShiftModifier:
                selected_ids = (
                    set(self.selected_token_ids)
                    | {
                        token.id
                        for token in token_hits
                    }
                )
                selected = [
                    token
                    for token in self.asset.initial_tokens
                    if token.id in selected_ids
                ]
                self._select_tokens(
                    selected,
                    primary=token_hits[-1],
                )

            else:
                self._select_tokens(
                    token_hits,
                    primary=token_hits[-1],
                )

            return

        hits = self._elements_in_marquee(world_rect)

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            selected_ids = set(self.selected_element_ids)
            for element in hits:
                if element.id in selected_ids:
                    selected_ids.remove(element.id)
                else:
                    selected_ids.add(element.id)
            selected = [
                element for element in self.asset.elements
                if element.id in selected_ids
            ]
            self._select_elements(selected, primary=hits[-1] if hits and hits[-1].id in selected_ids else None)
        elif modifiers & Qt.KeyboardModifier.ShiftModifier:
            selected_ids = set(self.selected_element_ids) | {element.id for element in hits}
            selected = [
                element for element in self.asset.elements
                if element.id in selected_ids
            ]
            self._select_elements(selected, primary=hits[-1] if hits else self.selected_element())
        else:
            self._select_elements(hits, primary=hits[-1] if hits else None)

    def _snapshot(self, element: MapElement) -> ElementSnapshot:
        if isinstance(element, ImageElement):
            return ElementSnapshot(
                pos=Vec2(element.pos.x, element.pos.y),
                width=element.width,
                height=element.height,
                rotation=element.rotation,
            )
        if isinstance(element, TextElement):
            return ElementSnapshot(
                pos=Vec2(element.pos.x, element.pos.y),
                rotation=element.rotation,
                font_size=element.font_size,
            )
        if isinstance(element, DrawingElement):
            return ElementSnapshot(
                points=[Vec2(point.x, point.y) for point in element.points],
            )
        return ElementSnapshot()

    @staticmethod
    def _drawing_can_rotate(element: DrawingElement) -> bool:
        # RECT/CIRCLE are stored as two opposite corners of an axis-aligned box.
        # Their current model cannot represent an oriented rectangle/ellipse.
        return element.tool in (DrawTool.FREEHAND, DrawTool.LINE)

    def _can_rotate(self, element: MapElement) -> bool:
        if isinstance(element, DrawingElement):
            return self._drawing_can_rotate(element)
        return isinstance(element, (ImageElement, TextElement))

    def _screen_handle_positions(self, frame: ElementFrame) -> dict[ResizeHandle, QPointF]:
        tl, tr, br, bl = self._frame_screen_points(frame)
        return {
            ResizeHandle.TOP_LEFT: tl,
            ResizeHandle.TOP: _midpoint(tl, tr),
            ResizeHandle.TOP_RIGHT: tr,
            ResizeHandle.RIGHT: _midpoint(tr, br),
            ResizeHandle.BOTTOM_RIGHT: br,
            ResizeHandle.BOTTOM: _midpoint(bl, br),
            ResizeHandle.BOTTOM_LEFT: bl,
            ResizeHandle.LEFT: _midpoint(tl, bl),
        }

    @staticmethod
    def _handle_rect(center: QPointF) -> QRectF:
        half = HANDLE_SIZE_PX / 2.0
        return QRectF(center.x() - half, center.y() - half, HANDLE_SIZE_PX, HANDLE_SIZE_PX)

    def _rotation_handle_pos(self, frame: ElementFrame) -> QPointF:
        tl, tr, br, bl = self._frame_screen_points(frame)
        top = _midpoint(tl, tr)
        center = QPointF(
            (tl.x() + tr.x() + br.x() + bl.x()) / 4.0,
            (tl.y() + tr.y() + br.y() + bl.y()) / 4.0,
        )
        dx, dy = top.x() - center.x(), top.y() - center.y()
        length = hypot(dx, dy)
        if length < 1e-6:
            dx, dy, length = 0.0, -1.0, 1.0
        return QPointF(
            top.x() + dx / length * ROTATE_HANDLE_OFFSET_PX,
            top.y() + dy / length * ROTATE_HANDLE_OFFSET_PX,
        )

    def _hit_resize_handle(self, frame: ElementFrame, point: QPointF) -> Optional[ResizeHandle]:
        for handle, center in self._screen_handle_positions(frame).items():
            if self._handle_rect(center).contains(point):
                return handle
        return None

    def _hit_rotation_handle(self, frame: ElementFrame, point: QPointF) -> bool:
        return self._handle_rect(self._rotation_handle_pos(frame)).contains(point)

    @staticmethod
    def _resize_cursor(handle: ResizeHandle) -> Qt.CursorShape:
        if handle in (ResizeHandle.TOP_LEFT, ResizeHandle.BOTTOM_RIGHT):
            return Qt.CursorShape.SizeFDiagCursor
        if handle in (ResizeHandle.TOP_RIGHT, ResizeHandle.BOTTOM_LEFT):
            return Qt.CursorShape.SizeBDiagCursor
        if handle in (ResizeHandle.TOP, ResizeHandle.BOTTOM):
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.SizeHorCursor

    def _update_hover_cursor(self, point: QPointF) -> None:
        if (
            self._panning
            or self._transform is not None
            or self._token_move is not None
        ):
            return

        camera_part = self._camera_hit_part(point)
        if camera_part is not None:
            self.setCursor(
                Qt.CursorShape.SizeAllCursor
                if camera_part == "move"
                else Qt.CursorShape.SizeFDiagCursor
            )
            return

        selected_tokens = self.selected_tokens()
        if selected_tokens:
            world = self.screen_to_world(
                point
            )

            if any(
                self._contains(
                    self._token_rect(token),
                    world,
                )
                for token in selected_tokens
            ):
                self.setCursor(
                    Qt.CursorShape.SizeAllCursor
                )
            else:
                self.unsetCursor()
            return

        selected = self.selected_elements()
        if not selected:
            self.unsetCursor()
            return

        if len(selected) > 1:
            if self._group_is_locked(selected):
                self.unsetCursor()
                return

            frame = self._selection_frame(selected)
            if frame is None:
                self.unsetCursor()
                return

            handle = self._hit_resize_handle(frame, point)
            if handle is not None:
                self.setCursor(self._resize_cursor(handle))
            elif self._group_can_rotate(selected) and self._hit_rotation_handle(frame, point):
                self.setCursor(Qt.CursorShape.CrossCursor)
            elif self._frame_contains_screen(frame, point):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.unsetCursor()
            return

        element = selected[0]
        if element.locked:
            self.unsetCursor()
            return

        frame = self._element_frame(element)
        handle = self._hit_resize_handle(frame, point)
        if handle is not None:
            self.setCursor(self._resize_cursor(handle))
        elif self._can_rotate(element) and self._hit_rotation_handle(frame, point):
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif self._frame_contains_screen(frame, point):
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.unsetCursor()

    # -------------------------------------------------------------------------
    # Transform sessions
    # -------------------------------------------------------------------------

    def _start_transform(
        self,
        element: MapElement,
        mode: TransformMode,
        screen_pos: QPointF,
        *,
        handle: Optional[ResizeHandle] = None,
    ) -> None:
        frame = self._element_frame(element)
        world = self.screen_to_world(screen_pos)
        self._transform = TransformSession(
            element_id=element.id,
            mode=mode,
            start_world=world,
            start_screen=QPointF(screen_pos),
            frame=frame,
            snapshot=self._snapshot(element),
            history_before=self._capture_state(),
            handle=handle,
            start_angle=_angle(frame.center, world),
        )
        if mode is TransformMode.MOVE:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif mode is TransformMode.ROTATE:
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif handle is not None:
            self.setCursor(self._resize_cursor(handle))

    def _start_group_transform(
        self,
        elements: list[MapElement],
        mode: TransformMode,
        screen_pos: QPointF,
        *,
        handle: Optional[ResizeHandle] = None,
    ) -> None:
        frame = self._selection_frame(elements)
        if frame is None:
            return

        world = self.screen_to_world(screen_pos)
        self._transform = GroupTransformSession(
            element_ids=[element.id for element in elements],
            mode=mode,
            start_world=world,
            start_screen=QPointF(screen_pos),
            frame=frame,
            snapshots={
                element.id: self._snapshot(element)
                for element in elements
            },
            element_frames={
                element.id: self._element_frame(element)
                for element in elements
            },
            history_before=self._capture_state(),
            handle=handle,
            start_angle=_angle(frame.center, world),
        )

        if mode is TransformMode.MOVE:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif mode is TransformMode.ROTATE:
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif handle is not None:
            self.setCursor(self._resize_cursor(handle))

    def _restore_snapshot(self, element: MapElement, snapshot: ElementSnapshot) -> None:
        if isinstance(element, ImageElement):
            if snapshot.pos is not None:
                element.pos = Vec2(snapshot.pos.x, snapshot.pos.y)
            if snapshot.width is not None:
                element.width = snapshot.width
            if snapshot.height is not None:
                element.height = snapshot.height
            if snapshot.rotation is not None:
                element.rotation = snapshot.rotation
        elif isinstance(element, TextElement):
            if snapshot.pos is not None:
                element.pos = Vec2(snapshot.pos.x, snapshot.pos.y)
            if snapshot.font_size is not None:
                element.font_size = snapshot.font_size
            if snapshot.rotation is not None:
                element.rotation = snapshot.rotation
        elif isinstance(element, DrawingElement) and snapshot.points is not None:
            element.points = [Vec2(point.x, point.y) for point in snapshot.points]

    def cancel_transform(self) -> None:
        session = self._transform
        if session is None:
            return

        if isinstance(session, GroupTransformSession):
            elements = {
                element.id: element
                for element in self.asset.elements
                if element.id in session.snapshots
            }
            for element_id, snapshot in session.snapshots.items():
                element = elements.get(element_id)
                if element is not None:
                    self._restore_snapshot(element, snapshot)
        else:
            element = self.selected_element()
            if element is not None and element.id == session.element_id:
                self._restore_snapshot(element, session.snapshot)

        self._transform = None
        self.unsetCursor()
        self.update()

    def _finish_transform(self) -> None:
        session = self._transform
        if session is not None and session.history_before is not None:
            self._commit_history(session.history_before)
        self._transform = None
        self.unsetCursor()
        self.update()

    # ----- Transform snapping ------------------------------------------------

    def _grid_snap_active(self, modifiers: Qt.KeyboardModifier) -> bool:
        grid = self.asset.grid
        return (
            self.snap_to_grid
            and grid.enabled
            and grid.cell_size > 0
            and not (modifiers & Qt.KeyboardModifier.AltModifier)
        )

    @staticmethod
    def _snap_value_near(
        value: float,
        offset: float,
        step: float,
        tolerance: float,
    ) -> float:
        """Snap to the nearest grid line only when it is close enough."""
        target = offset + round((value - offset) / step) * step
        return target if abs(value - target) <= tolerance else value

    def _grid_snap_tolerance_world(self) -> float:
        """Convert the screen-space magnetic radius to world pixels."""
        return self.grid_snap_tolerance_px / max(self.camera.zoom, 1e-6)

    def _snap_angle_near(self, angle: float, step: float) -> float:
        """Snap an angle only when it is close to a multiple of ``step``."""
        if step <= 0.0:
            return angle
        target = round(angle / step) * step
        difference = (angle - target + 180.0) % 360.0 - 180.0
        if abs(difference) <= self.rotation_snap_tolerance_deg:
            return target
        return angle

    def _snap_world_point(
        self,
        point: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> Vec2:
        if not self._grid_snap_active(modifiers):
            return Vec2(point.x, point.y)

        grid = self.asset.grid
        step = float(grid.cell_size)
        tolerance = self._grid_snap_tolerance_world()
        return Vec2(
            self._snap_value_near(point.x, grid.offset_x, step, tolerance),
            self._snap_value_near(point.y, grid.offset_y, step, tolerance),
        )

    def _snap_move_delta(
        self,
        element: MapElement,
        session: TransformSession,
        delta: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> Vec2:
        if not self._grid_snap_active(modifiers):
            return delta

        grid = self.asset.grid
        step = float(grid.cell_size)
        tolerance = self._grid_snap_tolerance_world()

        # Images and text have a stable model-space top-left anchor. Each axis
        # snaps independently, and only inside the magnetic tolerance.
        if isinstance(element, (ImageElement, TextElement)) and session.snapshot.pos is not None:
            candidate = session.snapshot.pos + delta
            snapped = Vec2(
                self._snap_value_near(candidate.x, grid.offset_x, step, tolerance),
                self._snap_value_near(candidate.y, grid.offset_y, step, tolerance),
            )
            return snapped - session.snapshot.pos

        # Drawings are point clouds. Snap the top-left of their transform frame so
        # the whole drawing moves rigidly instead of snapping every point.
        if isinstance(element, DrawingElement):
            start_left = session.frame.center.x - session.frame.width / 2.0
            start_top = session.frame.center.y - session.frame.height / 2.0
            candidate_left = start_left + delta.x
            candidate_top = start_top + delta.y
            snapped_left = self._snap_value_near(
                candidate_left, grid.offset_x, step, tolerance
            )
            snapped_top = self._snap_value_near(
                candidate_top, grid.offset_y, step, tolerance
            )
            return Vec2(
                snapped_left - start_left,
                snapped_top - start_top,
            )

        return delta

    def _apply_move(
        self,
        element: MapElement,
        session: TransformSession,
        world: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> None:
        delta = world - session.start_world
        delta = self._snap_move_delta(element, session, delta, modifiers)
        snapshot = session.snapshot
        if isinstance(element, (ImageElement, TextElement)) and snapshot.pos is not None:
            element.pos = snapshot.pos + delta
        elif isinstance(element, DrawingElement) and snapshot.points is not None:
            element.points = [point + delta for point in snapshot.points]

    @staticmethod
    def _resize_local_edges(
        frame: ElementFrame,
        handle: ResizeHandle,
        world: Vec2,
    ) -> tuple[float, float, float, float]:
        local = _rotate_vector(world - frame.center, -frame.rotation)
        left, right = -frame.width / 2.0, frame.width / 2.0
        top, bottom = -frame.height / 2.0, frame.height / 2.0
        minimum = MIN_ELEMENT_SIZE_PX

        if handle in (ResizeHandle.TOP_LEFT, ResizeHandle.LEFT, ResizeHandle.BOTTOM_LEFT):
            left = min(local.x, right - minimum)
        if handle in (ResizeHandle.TOP_RIGHT, ResizeHandle.RIGHT, ResizeHandle.BOTTOM_RIGHT):
            right = max(local.x, left + minimum)
        if handle in (ResizeHandle.TOP_LEFT, ResizeHandle.TOP, ResizeHandle.TOP_RIGHT):
            top = min(local.y, bottom - minimum)
        if handle in (ResizeHandle.BOTTOM_LEFT, ResizeHandle.BOTTOM, ResizeHandle.BOTTOM_RIGHT):
            bottom = max(local.y, top + minimum)
        return left, top, right, bottom

    def _apply_image_resize(
        self,
        element: ImageElement,
        session: TransformSession,
        world: Vec2,
    ) -> None:
        if session.handle is None:
            return
        left, top, right, bottom = self._resize_local_edges(session.frame, session.handle, world)
        width, height = right - left, bottom - top
        local_center = Vec2((left + right) / 2.0, (top + bottom) / 2.0)
        center = session.frame.center + _rotate_vector(local_center, session.frame.rotation)
        element.width = width
        element.height = height
        element.pos = Vec2(center.x - width / 2.0, center.y - height / 2.0)

    @staticmethod
    def _opposite_anchor(handle: ResizeHandle, width: float, height: float) -> Vec2:
        half_w, half_h = width / 2.0, height / 2.0
        anchors = {
            ResizeHandle.TOP_LEFT: Vec2(half_w, half_h),
            ResizeHandle.TOP: Vec2(0.0, half_h),
            ResizeHandle.TOP_RIGHT: Vec2(-half_w, half_h),
            ResizeHandle.RIGHT: Vec2(-half_w, 0.0),
            ResizeHandle.BOTTOM_RIGHT: Vec2(-half_w, -half_h),
            ResizeHandle.BOTTOM: Vec2(0.0, -half_h),
            ResizeHandle.BOTTOM_LEFT: Vec2(half_w, -half_h),
            ResizeHandle.LEFT: Vec2(half_w, 0.0),
        }
        return anchors[handle]

    def _apply_text_resize(
        self,
        element: TextElement,
        session: TransformSession,
        world: Vec2,
    ) -> None:
        if session.handle is None or session.snapshot.font_size is None:
            return
        frame = session.frame
        local = _rotate_vector(world - frame.center, -frame.rotation)
        half_w = max(frame.width / 2.0, 0.5)
        half_h = max(frame.height / 2.0, 0.5)
        handle = session.handle

        if handle in (ResizeHandle.LEFT, ResizeHandle.RIGHT):
            scale = abs(local.x) / half_w
        elif handle in (ResizeHandle.TOP, ResizeHandle.BOTTOM):
            scale = abs(local.y) / half_h
        else:
            original_radius = hypot(half_w, half_h)
            scale = hypot(local.x, local.y) / max(original_radius, 1e-6)

        scale = max(1.0 / max(1.0, session.snapshot.font_size), scale)
        element.font_size = max(1.0, session.snapshot.font_size * scale)
        new_width, new_height = self._text_size(element)

        old_anchor_local = self._opposite_anchor(handle, frame.width, frame.height)
        fixed_world = frame.center + _rotate_vector(old_anchor_local, frame.rotation)
        new_anchor_local = self._opposite_anchor(handle, new_width, new_height)
        new_center = fixed_world - _rotate_vector(new_anchor_local, frame.rotation)
        element.pos = Vec2(new_center.x - new_width / 2.0, new_center.y - new_height / 2.0)

    def _apply_drawing_resize(
        self,
        element: DrawingElement,
        session: TransformSession,
        world: Vec2,
    ) -> None:
        if session.handle is None or not session.snapshot.points:
            return
        frame = session.frame
        left, top, right, bottom = self._resize_local_edges(frame, session.handle, world)
        new_width = right - left
        new_height = bottom - top
        new_center = Vec2(
            frame.center.x + (left + right) / 2.0,
            frame.center.y + (top + bottom) / 2.0,
        )
        old_left = frame.center.x - frame.width / 2.0
        old_top = frame.center.y - frame.height / 2.0
        new_left = new_center.x - new_width / 2.0
        new_top = new_center.y - new_height / 2.0

        points: list[Vec2] = []
        for point in session.snapshot.points:
            if frame.width > 1e-6:
                x = new_left + (point.x - old_left) * (new_width / frame.width)
            else:
                x = new_center.x
            if frame.height > 1e-6:
                y = new_top + (point.y - old_top) * (new_height / frame.height)
            else:
                y = new_center.y
            points.append(Vec2(x, y))
        element.points = points

    def _apply_resize(
        self,
        element: MapElement,
        session: TransformSession,
        world: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> None:
        # The dragged handle is attracted to nearby grid intersections only. For
        # rotated image/text frames this world point is then projected into the
        # element's local axes by the existing resize math.
        world = self._snap_world_point(world, modifiers)

        if isinstance(element, ImageElement):
            self._apply_image_resize(element, session, world)
        elif isinstance(element, TextElement):
            self._apply_text_resize(element, session, world)
        elif isinstance(element, DrawingElement):
            self._apply_drawing_resize(element, session, world)

    def _apply_rotate(
        self,
        element: MapElement,
        session: TransformSession,
        world: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> None:
        current = _angle(session.frame.center, world)
        delta = current - session.start_angle

        # Keep the angular delta stable when atan2 crosses -180/180.
        delta = (delta + 180.0) % 360.0 - 180.0

        snapshot = session.snapshot
        snap_rotation = not (modifiers & Qt.KeyboardModifier.AltModifier)
        step = max(0.0, float(self.rotation_snap_deg))

        if isinstance(element, ImageElement) and snapshot.rotation is not None:
            rotation = snapshot.rotation + delta
            if snap_rotation and step > 0.0:
                rotation = self._snap_angle_near(rotation, step)
            element.rotation = rotation % 360.0

        elif isinstance(element, TextElement) and snapshot.rotation is not None:
            rotation = snapshot.rotation + delta
            if snap_rotation and step > 0.0:
                rotation = self._snap_angle_near(rotation, step)
            element.rotation = rotation % 360.0

        elif (
            isinstance(element, DrawingElement)
            and self._drawing_can_rotate(element)
            and snapshot.points is not None
        ):
            # Drawings do not store a rotation value, so snap the gesture delta.
            snapped_delta = delta
            if snap_rotation and step > 0.0:
                snapped_delta = self._snap_angle_near(snapped_delta, step)
            element.points = [
                session.frame.center
                + _rotate_vector(point - session.frame.center, snapped_delta)
                for point in snapshot.points
            ]

    def _session_elements(
        self,
        session: GroupTransformSession,
    ) -> list[MapElement]:
        ids = set(session.element_ids)
        return [
            element
            for element in self.asset.elements
            if element.id in ids
        ]

    def _snap_group_move_delta(
        self,
        session: GroupTransformSession,
        delta: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> Vec2:
        if not self._grid_snap_active(modifiers):
            return delta

        grid = self.asset.grid
        step = float(grid.cell_size)
        tolerance = self._grid_snap_tolerance_world()

        start_left = session.frame.center.x - session.frame.width / 2.0
        start_top = session.frame.center.y - session.frame.height / 2.0

        candidate_left = start_left + delta.x
        candidate_top = start_top + delta.y

        snapped_left = self._snap_value_near(
            candidate_left,
            grid.offset_x,
            step,
            tolerance,
        )
        snapped_top = self._snap_value_near(
            candidate_top,
            grid.offset_y,
            step,
            tolerance,
        )

        return Vec2(
            snapped_left - start_left,
            snapped_top - start_top,
        )

    def _apply_group_move(
        self,
        session: GroupTransformSession,
        world: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> None:
        delta = world - session.start_world
        delta = self._snap_group_move_delta(
            session,
            delta,
            modifiers,
        )

        elements = {
            element.id: element
            for element in self._session_elements(session)
        }

        for element_id, snapshot in session.snapshots.items():
            element = elements.get(element_id)
            if element is None:
                continue

            if isinstance(element, (ImageElement, TextElement)) and snapshot.pos is not None:
                element.pos = snapshot.pos + delta

            elif isinstance(element, DrawingElement) and snapshot.points is not None:
                element.points = [
                    point + delta
                    for point in snapshot.points
                ]

    @staticmethod
    def _map_group_point(
        point: Vec2,
        *,
        old_left: float,
        old_top: float,
        new_left: float,
        new_top: float,
        scale_x: float,
        scale_y: float,
    ) -> Vec2:
        return Vec2(
            new_left + (point.x - old_left) * scale_x,
            new_top + (point.y - old_top) * scale_y,
        )

    def _apply_group_resize(
        self,
        session: GroupTransformSession,
        world: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> None:
        if session.handle is None:
            return

        world = self._snap_world_point(
            world,
            modifiers,
        )

        frame = session.frame
        left, top, right, bottom = self._resize_local_edges(
            frame,
            session.handle,
            world,
        )

        new_width = max(MIN_ELEMENT_SIZE_PX, right - left)
        new_height = max(MIN_ELEMENT_SIZE_PX, bottom - top)

        old_left = frame.center.x - frame.width / 2.0
        old_top = frame.center.y - frame.height / 2.0
        new_left = frame.center.x + left
        new_top = frame.center.y + top

        scale_x = (
            new_width / frame.width
            if frame.width > 1e-6
            else 1.0
        )
        scale_y = (
            new_height / frame.height
            if frame.height > 1e-6
            else 1.0
        )

        elements = {
            element.id: element
            for element in self._session_elements(session)
        }

        horizontal_handle = session.handle in (
            ResizeHandle.LEFT,
            ResizeHandle.RIGHT,
        )
        vertical_handle = session.handle in (
            ResizeHandle.TOP,
            ResizeHandle.BOTTOM,
        )

        for element_id, snapshot in session.snapshots.items():
            element = elements.get(element_id)
            original_frame = session.element_frames.get(element_id)

            if element is None or original_frame is None:
                continue

            original_center = original_frame.center
            new_center = self._map_group_point(
                original_center,
                old_left=old_left,
                old_top=old_top,
                new_left=new_left,
                new_top=new_top,
                scale_x=scale_x,
                scale_y=scale_y,
            )

            if isinstance(element, ImageElement):
                if snapshot.width is None or snapshot.height is None:
                    continue

                element.width = max(
                    MIN_ELEMENT_SIZE_PX,
                    snapshot.width * scale_x,
                )
                element.height = max(
                    MIN_ELEMENT_SIZE_PX,
                    snapshot.height * scale_y,
                )
                element.pos = Vec2(
                    new_center.x - element.width / 2.0,
                    new_center.y - element.height / 2.0,
                )

            elif isinstance(element, TextElement):
                if snapshot.font_size is None:
                    continue

                if horizontal_handle:
                    text_scale = scale_x
                elif vertical_handle:
                    text_scale = scale_y
                else:
                    text_scale = sqrt(
                        max(0.0, scale_x * scale_y)
                    )

                element.font_size = max(
                    1.0,
                    snapshot.font_size * text_scale,
                )

                width, height = self._text_size(element)
                element.pos = Vec2(
                    new_center.x - width / 2.0,
                    new_center.y - height / 2.0,
                )

            elif isinstance(element, DrawingElement) and snapshot.points is not None:
                element.points = [
                    self._map_group_point(
                        point,
                        old_left=old_left,
                        old_top=old_top,
                        new_left=new_left,
                        new_top=new_top,
                        scale_x=scale_x,
                        scale_y=scale_y,
                    )
                    for point in snapshot.points
                ]

    def _apply_group_rotate(
        self,
        session: GroupTransformSession,
        world: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> None:
        current = _angle(
            session.frame.center,
            world,
        )
        delta = current - session.start_angle
        delta = (delta + 180.0) % 360.0 - 180.0

        if not (modifiers & Qt.KeyboardModifier.AltModifier):
            step = max(
                0.0,
                float(self.rotation_snap_deg),
            )
            if step > 0.0:
                delta = self._snap_angle_near(
                    delta,
                    step,
                )

        elements = {
            element.id: element
            for element in self._session_elements(session)
        }

        group_center = session.frame.center

        for element_id, snapshot in session.snapshots.items():
            element = elements.get(element_id)
            original_frame = session.element_frames.get(element_id)

            if element is None or original_frame is None:
                continue

            new_center = (
                group_center
                + _rotate_vector(
                    original_frame.center - group_center,
                    delta,
                )
            )

            if isinstance(element, ImageElement):
                if (
                    snapshot.width is None
                    or snapshot.height is None
                    or snapshot.rotation is None
                ):
                    continue

                element.pos = Vec2(
                    new_center.x - snapshot.width / 2.0,
                    new_center.y - snapshot.height / 2.0,
                )
                element.rotation = (
                    snapshot.rotation + delta
                ) % 360.0

            elif isinstance(element, TextElement):
                if snapshot.rotation is None:
                    continue

                element.rotation = (
                    snapshot.rotation + delta
                ) % 360.0

                width, height = self._text_size(
                    element,
                    snapshot.font_size,
                )
                element.pos = Vec2(
                    new_center.x - width / 2.0,
                    new_center.y - height / 2.0,
                )

            elif (
                isinstance(element, DrawingElement)
                and snapshot.points is not None
            ):
                element.points = [
                    group_center
                    + _rotate_vector(
                        point - group_center,
                        delta,
                    )
                    for point in snapshot.points
                ]

    def _update_transform(self, screen_pos: QPointF, modifiers: Qt.KeyboardModifier) -> None:
        session = self._transform
        if session is None:
            return

        world = self.screen_to_world(
            screen_pos
        )

        if isinstance(session, GroupTransformSession):
            current_ids = {
                element.id
                for element in self.selected_elements()
            }
            if current_ids != set(session.element_ids):
                self._transform = None
                return

            if session.mode is TransformMode.MOVE:
                self._apply_group_move(
                    session,
                    world,
                    modifiers,
                )
            elif session.mode is TransformMode.RESIZE:
                self._apply_group_resize(
                    session,
                    world,
                    modifiers,
                )
            elif session.mode is TransformMode.ROTATE:
                self._apply_group_rotate(
                    session,
                    world,
                    modifiers,
                )

            self.update()
            return

        element = self.selected_element()
        if element is None or element.id != session.element_id:
            self._transform = None
            return

        if session.mode is TransformMode.MOVE:
            self._apply_move(
                element,
                session,
                world,
                modifiers,
            )
        elif session.mode is TransformMode.RESIZE:
            self._apply_resize(
                element,
                session,
                world,
                modifiers,
            )
        elif session.mode is TransformMode.ROTATE:
            self._apply_rotate(
                element,
                session,
                world,
                modifiers,
            )

        self.update()

    # -------------------------------------------------------------------------
    # Rendering overlays
    # -------------------------------------------------------------------------

    def _draw_grid(self, painter: QPainter, view: Rect) -> None:
        grid = self.asset.grid
        if not grid.enabled or grid.cell_size <= 0:
            return

        size = float(grid.cell_size)
        color = QColor(grid.color)
        color.setAlphaF(max(0.0, min(1.0, grid.opacity)))

        pen = QPen(color)
        # Keep the visual grid line approximately one physical pixel wide.
        pen.setWidthF(1.0 / self.camera.zoom)
        painter.setPen(pen)

        map_left = 0.0
        map_top = 0.0
        map_right = float(self.asset.width)
        map_bottom = float(self.asset.height)

        first_col = int((view.x - grid.offset_x) // size) - 1
        last_col = int((view.right - grid.offset_x) // size) + 1
        first_row = int((view.y - grid.offset_y) // size) - 1
        last_row = int((view.bottom - grid.offset_y) // size) + 1

        for column in range(first_col, last_col + 1):
            x = grid.offset_x + column * size
            if map_left <= x <= map_right:
                painter.drawLine(QPointF(x, map_top), QPointF(x, map_bottom))

        for row in range(first_row, last_row + 1):
            y = grid.offset_y + row * size
            if map_top <= y <= map_bottom:
                painter.drawLine(QPointF(map_left, y), QPointF(map_right, y))

    def _draw_map_border(self, painter: QPainter) -> None:
        pen = QPen(QColor("#7f849c"))
        pen.setWidthF(1.0 / self.camera.zoom)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(0.0, 0.0, float(self.asset.width), float(self.asset.height)))

    def _draw_selection(self, painter: QPainter) -> None:
        """Draw selection frames for tokens or static map elements."""
        selected_tokens = self.selected_tokens()

        if selected_tokens:
            painter.save()
            painter.resetTransform()

            polygons: list[QPolygonF] = []
            all_points: list[QPointF] = []

            pen = QPen(
                QColor("#89b4fa")
            )
            pen.setWidthF(2.0)
            painter.setPen(pen)
            painter.setBrush(
                Qt.BrushStyle.NoBrush
            )

            for token in selected_tokens:
                size = self._token_size(
                    token
                )
                half = size / 2.0
                local_corners = [
                    Vec2(-half, -half),
                    Vec2(half, -half),
                    Vec2(half, half),
                    Vec2(-half, half),
                ]
                world_corners = [
                    token.pos
                    + _rotate_vector(
                        corner,
                        token.rotation,
                    )
                    for corner in local_corners
                ]
                screen_points = [
                    self.world_to_screen(
                        point
                    )
                    for point in world_corners
                ]
                polygon = QPolygonF(
                    screen_points
                )
                polygons.append(
                    polygon
                )
                all_points.extend(
                    screen_points
                )
                painter.drawPolygon(
                    polygon
                )

            if len(selected_tokens) > 1 and all_points:
                left = min(
                    point.x()
                    for point in all_points
                )
                top = min(
                    point.y()
                    for point in all_points
                )
                right = max(
                    point.x()
                    for point in all_points
                )
                bottom = max(
                    point.y()
                    for point in all_points
                )

                group_pen = QPen(
                    QColor("#b4befe")
                )
                group_pen.setWidthF(1.5)
                group_pen.setStyle(
                    Qt.PenStyle.DashLine
                )
                painter.setPen(
                    group_pen
                )
                painter.drawRect(
                    QRectF(
                        QPointF(
                            left - 7.0,
                            top - 7.0,
                        ),
                        QPointF(
                            right + 7.0,
                            bottom + 7.0,
                        ),
                    )
                )

            painter.restore()
            return

        selected = self.selected_elements()
        if not selected:
            return

        painter.save()
        painter.resetTransform()

        # Multi-selection: member outlines + one active group transform frame.
        if len(selected) > 1:
            member_pen = QPen(QColor("#89b4fa"))
            member_pen.setWidthF(1.25)
            member_pen.setStyle(Qt.PenStyle.DotLine)
            painter.setPen(member_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            for element in selected:
                frame = self._element_frame(element)
                painter.drawPolygon(
                    QPolygonF(
                        self._frame_screen_points(frame)
                    )
                )

            group_frame = self._selection_frame(selected)
            if group_frame is not None:
                locked = self._group_is_locked(selected)

                pen = QPen(QColor("#b4befe"))
                pen.setWidthF(2.0)
                pen.setStyle(
                    Qt.PenStyle.DashLine
                    if locked
                    else Qt.PenStyle.SolidLine
                )
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)

                points = self._frame_screen_points(
                    group_frame
                )
                painter.drawPolygon(
                    QPolygonF(points)
                )

                if not locked:
                    handle_pen = QPen(
                        QColor("#1e1e2e")
                    )
                    handle_pen.setWidthF(1.0)
                    painter.setPen(handle_pen)
                    painter.setBrush(
                        QBrush(
                            QColor("#b4befe")
                        )
                    )

                    for center in self._screen_handle_positions(
                        group_frame
                    ).values():
                        painter.drawRect(
                            self._handle_rect(center)
                        )

                    if self._group_can_rotate(selected):
                        tl, tr, _br, _bl = points
                        top = _midpoint(tl, tr)
                        rotate_pos = self._rotation_handle_pos(
                            group_frame
                        )

                        painter.setPen(
                            QPen(
                                QColor("#b4befe"),
                                2.0,
                            )
                        )
                        painter.drawLine(
                            top,
                            rotate_pos,
                        )

                        painter.setPen(
                            handle_pen
                        )
                        painter.drawEllipse(
                            self._handle_rect(
                                rotate_pos
                            )
                        )

            painter.restore()
            return

        element = selected[0]
        frame = self._element_frame(element)
        points = self._frame_screen_points(frame)
        polygon = QPolygonF(points)

        pen = QPen(QColor("#89b4fa"))
        pen.setWidthF(2.0)
        pen.setStyle(Qt.PenStyle.DashLine if element.locked else Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(polygon)

        if element.locked:
            painter.restore()
            return

        handle_pen = QPen(QColor("#1e1e2e"))
        handle_pen.setWidthF(1.0)
        painter.setPen(handle_pen)
        painter.setBrush(QBrush(QColor("#89b4fa")))

        for center in self._screen_handle_positions(frame).values():
            painter.drawRect(self._handle_rect(center))

        if self._can_rotate(element):
            tl, tr, _br, _bl = points
            top = _midpoint(tl, tr)
            rotate_pos = self._rotation_handle_pos(frame)
            painter.setPen(QPen(QColor("#89b4fa"), 2.0))
            painter.drawLine(top, rotate_pos)
            painter.setPen(handle_pen)
            painter.drawEllipse(self._handle_rect(rotate_pos))

        painter.restore()

    def _draw_marquee(self, painter: QPainter) -> None:
        rect = self._marquee_screen_rect()
        if rect is None:
            return
        painter.save()
        painter.resetTransform()
        fill = QColor("#89b4fa")
        fill.setAlpha(42)
        painter.setBrush(QBrush(fill))
        pen = QPen(QColor("#89b4fa"))
        pen.setWidthF(1.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRect(rect)
        painter.restore()

    def _draw_render_error(self, painter: QPainter, message: str) -> None:
        painter.save()
        painter.resetTransform()
        painter.setPen(QColor("#f38ba8"))
        painter.drawText(
            QRectF(12.0, 12.0, max(100.0, self.width() - 24.0), 80.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
            f"Render error: {message}",
        )
        painter.restore()

    # -------------------------------------------------------------------------
    # Token drag & drop
    # -------------------------------------------------------------------------

    @staticmethod
    def _decode_token_drag(mime) -> Optional[tuple[str, str]]:
        if mime is None or not mime.hasFormat(TOKEN_SOURCE_MIME):
            return None

        try:
            raw = bytes(mime.data(TOKEN_SOURCE_MIME)).decode("utf-8")
            payload = json.loads(raw)
        except (UnicodeDecodeError, ValueError, TypeError):
            return None

        if not isinstance(payload, dict):
            return None

        kind = str(payload.get("kind") or "").strip().casefold()
        source_id = str(payload.get("id") or "").strip()

        if kind not in {"character", "entity"} or not source_id:
            return None

        return kind, source_id

    def _set_token_drag_preview(
        self,
        source_kind: str,
        source_id: str,
        position: QPointF,
    ) -> None:
        self._drag_token_source_kind = source_kind
        self._drag_token_source_id = source_id

        try:
            self._drag_token_preview = self.token_provider.source_view(
                source_kind,
                source_id,
            )
        except Exception:
            self._drag_token_preview = None

        world = self.screen_to_world(position)
        self._hover_world = Vec2(world.x, world.y)
        self.update()

    def _clear_token_drag_preview(self) -> None:
        self._drag_token_source_kind = None
        self._drag_token_source_id = None
        self._drag_token_preview = None
        self.update()

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt API
        payload = self._decode_token_drag(event.mimeData())
        if payload is None:
            super().dragEnterEvent(event)
            return

        kind, source_id = payload
        self._set_token_drag_preview(
            kind,
            source_id,
            event.position(),
        )
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        payload = self._decode_token_drag(event.mimeData())
        if payload is None:
            super().dragMoveEvent(event)
            return

        kind, source_id = payload
        self._set_token_drag_preview(
            kind,
            source_id,
            event.position(),
        )
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._clear_token_drag_preview()
        event.accept()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt API
        payload = self._decode_token_drag(event.mimeData())
        if payload is None:
            super().dropEvent(event)
            return

        source_kind, source_id = payload
        world = self.screen_to_world(event.position())

        # Stop drawing the ghost before opening a potentially modal EntityEditor.
        self._clear_token_drag_preview()

        entity_id: Optional[str] = None

        try:
            if source_kind == "entity":
                entity_id = source_id

            elif source_kind == "character":
                # Character rule:
                #   create Entity -> open EntityEditor -> create token.
                entity_id = self.token_provider.create_entity_from_character(
                    source_id
                )

                if entity_id and not self.token_provider.edit_entity(entity_id):
                    entity_id = None

            if entity_id:
                self.place_token_at(
                    entity_id,
                    world,
                )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Place token",
                str(error),
            )
            entity_id = None

        if entity_id:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    # -------------------------------------------------------------------------
    # QWidget events
    # -------------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        try:
            painter.fillRect(self.rect(), QColor("#11111b"))
            MapAsset._quality_hints(painter)

            painter.save()
            try:
                self._apply_camera_transform(painter)
                view = self.visible_world_rect()

                try:
                    self.asset.render_scene(painter, self.media, view)
                    self._render_error = None
                except Exception as error:  # Keep the editor usable if an asset is missing.
                    self._render_error = str(error)

                self._draw_grid(painter, view)
                self._draw_initial_camera_overlay(painter)
                self._draw_tokens(painter, view)
                self._draw_map_border(painter)
                self._draw_creation_preview(painter)
            finally:
                painter.restore()

            if self.active_tool is EditorTool.SELECT:
                self._draw_selection(painter)
                self._draw_marquee(painter)

            if self._render_error:
                self._draw_render_error(painter, self._render_error)

            self._position_text_format_bar()
            self._position_image_format_bar()
        finally:
            painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._last_mouse_pos = QPointF(event.position())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            screen_pos = event.position()
            world = self.screen_to_world(screen_pos)

            if self.pending_token_entity_id is not None:
                self._place_pending_token(world)
                event.accept()
                return

            if self.active_tool in DRAW_TOOLS:
                self._start_drawing_creation(world)
                event.accept()
                return

            if self.active_tool is EditorTool.IMAGE:
                self._place_image(world)
                event.accept()
                return

            if self.active_tool is EditorTool.TEXT:
                self._place_text(world)
                event.accept()
                return

            if self.active_tool is EditorTool.SELECT:
                modifiers = event.modifiers()
                additive = bool(
                    modifiers
                    & Qt.KeyboardModifier.ShiftModifier
                )
                toggle = bool(
                    modifiers
                    & Qt.KeyboardModifier.ControlModifier
                )

                selected_elements = self.selected_elements()

                # Tokens are rendered above MapElements, so they also win
                # hit-testing over transform frames underneath them.
                token = self.token_at(
                    world
                )

                if token is not None:
                    if toggle:
                        self._toggle_token_selection(
                            token
                        )
                        event.accept()
                        return

                    if additive:
                        if token.id not in self.selected_token_ids:
                            selected_tokens = self.selected_tokens()
                            selected_tokens.append(
                                token
                            )
                            self._select_tokens(
                                selected_tokens,
                                primary=token,
                            )
                        event.accept()
                        return

                    if token.id not in self.selected_token_ids:
                        self._select_token(
                            token
                        )

                    self._start_token_move(
                        screen_pos,
                        primary=token,
                    )
                    event.accept()
                    return

                camera_part = self._camera_hit_part(screen_pos)
                if camera_part is not None:
                    self._start_camera_edit(
                        camera_part,
                        screen_pos,
                    )
                    event.accept()
                    return

                # Selection modifiers always win over transform gestures.
                if not additive and not toggle and selected_elements:
                    if len(selected_elements) > 1:
                        group_frame = self._selection_frame(
                            selected_elements
                        )

                        if (
                            group_frame is not None
                            and not self._group_is_locked(selected_elements)
                        ):
                            handle = self._hit_resize_handle(
                                group_frame,
                                screen_pos,
                            )

                            if handle is not None:
                                self._start_group_transform(
                                    selected_elements,
                                    TransformMode.RESIZE,
                                    screen_pos,
                                    handle=handle,
                                )
                                event.accept()
                                return

                            if (
                                self._group_can_rotate(selected_elements)
                                and self._hit_rotation_handle(
                                    group_frame,
                                    screen_pos,
                                )
                            ):
                                self._start_group_transform(
                                    selected_elements,
                                    TransformMode.ROTATE,
                                    screen_pos,
                                )
                                event.accept()
                                return

                            if self._frame_contains_screen(
                                group_frame,
                                screen_pos,
                            ):
                                self._start_group_transform(
                                    selected_elements,
                                    TransformMode.MOVE,
                                    screen_pos,
                                )
                                event.accept()
                                return

                    else:
                        selected = selected_elements[0]
                        frame = self._element_frame(
                            selected
                        )

                        if not selected.locked:
                            handle = self._hit_resize_handle(
                                frame,
                                screen_pos,
                            )

                            if handle is not None:
                                self._start_transform(
                                    selected,
                                    TransformMode.RESIZE,
                                    screen_pos,
                                    handle=handle,
                                )
                                event.accept()
                                return

                            if (
                                self._can_rotate(selected)
                                and self._hit_rotation_handle(
                                    frame,
                                    screen_pos,
                                )
                            ):
                                self._start_transform(
                                    selected,
                                    TransformMode.ROTATE,
                                    screen_pos,
                                )
                                event.accept()
                                return

                            if self._frame_contains_screen(
                                frame,
                                screen_pos,
                            ):
                                self._start_transform(
                                    selected,
                                    TransformMode.MOVE,
                                    screen_pos,
                                )
                                event.accept()
                                return

                element = self.element_at(
                    world
                )

                # Shift/Ctrl click modifies the current selection without
                # starting a transform.
                if element is not None and (additive or toggle):
                    if toggle:
                        self._toggle_element_selection(
                            element
                        )
                    elif element.id not in self.selected_element_ids:
                        selected_elements = self.selected_elements()
                        selected_elements.append(
                            element
                        )
                        self._select_elements(
                            selected_elements,
                            primary=element,
                        )

                    event.accept()
                    return

                if element is not None:
                    # Clicking an already selected member of a multi-selection
                    # keeps the whole group selected and starts a group move.
                    if (
                        len(selected_elements) > 1
                        and element.id in self.selected_element_ids
                    ):
                        if not self._group_is_locked(selected_elements):
                            self._start_group_transform(
                                selected_elements,
                                TransformMode.MOVE,
                                screen_pos,
                            )

                        event.accept()
                        return

                    self._select_element(
                        element
                    )

                    if not element.locked:
                        self._start_transform(
                            element,
                            TransformMode.MOVE,
                            screen_pos,
                        )

                    event.accept()
                    return

                # Dragging from empty canvas is the normal rectangle selection
                # gesture. A click without enough movement simply clears the
                # selection unless a modifier is held.
                self._start_marquee(
                    screen_pos,
                    modifiers,
                )
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        world = self.screen_to_world(event.position())
        self._hover_world = Vec2(world.x, world.y)
        self.mouse_world_moved.emit(world.x, world.y)

        if self._panning:
            if not (event.buttons() & Qt.MouseButton.MiddleButton):
                self._panning = False
                self.unsetCursor()
            else:
                delta = event.position() - self._last_mouse_pos
                self._last_mouse_pos = QPointF(event.position())
                self.camera.center = Vec2(
                    self.camera.center.x - delta.x() / self.camera.zoom,
                    self.camera.center.y - delta.y() / self.camera.zoom,
                )
                self.update()
                event.accept()
                return

        if self._creation_start is not None:
            if event.buttons() & Qt.MouseButton.LeftButton:
                self._update_drawing_creation(world)
                event.accept()
                return
            self._cancel_creation()

        if self._token_move is not None:
            if not (
                event.buttons()
                & Qt.MouseButton.LeftButton
            ):
                self._finish_token_move()
            else:
                self._update_token_move(
                    event.position(),
                    event.modifiers(),
                )
                event.accept()
                return

        if self._camera_edit is not None:
            if not (
                event.buttons()
                & Qt.MouseButton.LeftButton
            ):
                self._finish_camera_edit()
            else:
                self._update_camera_edit(
                    event.position()
                )
                event.accept()
                return

        if self._marquee_start_screen is not None:
            if event.buttons() & Qt.MouseButton.LeftButton:
                self._update_marquee(event.position())
                event.accept()
                return
            self._cancel_marquee()

        if self._transform is not None:
            if not (event.buttons() & Qt.MouseButton.LeftButton):
                self._finish_transform()
            else:
                self._update_transform(event.position(), event.modifiers())
                event.accept()
                return

        if self.pending_token_entity_id is not None:
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.update()
        elif self.active_tool is EditorTool.SELECT:
            self._update_hover_cursor(event.position())
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self.unsetCursor()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self._creation_start is not None:
            self._finish_drawing_creation(self.screen_to_world(event.position()))
            event.accept()
            return

        if (
            event.button()
            == Qt.MouseButton.LeftButton
            and self._token_move is not None
        ):
            self._update_token_move(
                event.position(),
                event.modifiers(),
            )
            self._finish_token_move()
            self._update_hover_cursor(
                event.position()
            )
            event.accept()
            return

        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._camera_edit is not None
        ):
            self._update_camera_edit(event.position())
            self._finish_camera_edit()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self._marquee_start_screen is not None:
            self._update_marquee(event.position())
            self._apply_marquee_selection(event.position())
            self._cancel_marquee()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self._transform is not None:
            self._update_transform(event.position(), event.modifiers())
            self._finish_transform()
            self._update_hover_cursor(event.position())
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            if self.pending_token_entity_id is not None:
                self.cancel_token_placement()
            elif self._token_move is not None:
                self.cancel_token_move()
            elif self._camera_edit is not None:
                self.cancel_camera_edit()
            elif self._transform is not None:
                self.cancel_transform()
            elif self._creation_start is not None:
                self._cancel_creation()
            elif self._marquee_start_screen is not None:
                self._cancel_marquee()
            else:
                self.set_tool(EditorTool.SELECT)
            event.accept()
            return

        if event.key() == Qt.Key.Key_Delete:
            self.delete_selected()
            event.accept()
            return

        # Fallback when the canvas owns focus. Main-window actions handle the
        # same standard shortcuts globally and defer to text widgets when needed.
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selected()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Cut):
            self.cut_selected()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Paste):
            self.paste_element()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Undo):
            self.undo()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Redo):
            self.redo()
            event.accept()
            return

        super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            token = self.token_at(self.screen_to_world(event.position()))
            if token is not None:
                if token.id not in self.selected_token_ids:
                    self._select_token(
                        token
                    )
                self.token_double_clicked.emit(token.entity_id)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        steps = event.angleDelta().y() / 120.0
        if not steps:
            return

        factor = 1.15 ** steps
        self.set_zoom_at(
            self.camera.zoom * factor,
            event.position(),
        )
        event.accept()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_text_format_bar()
        self.update()


# =============================================================================
# Window
# =============================================================================


class MapEditorWindow(QMainWindow):
    """Map editor surface designed to be embedded in CampaignManagerWindow."""

    def __init__(
        self,
        asset: Optional[MapAsset] = None,
        map_dir: Optional[Path] = None,
        token_provider: Optional[TokenEntityProvider] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        self.setWindowTitle("RPG Tabletop - Map Editor")
        self.resize(1280, 800)

        self.map_dir: Optional[Path] = Path(map_dir) if map_dir is not None else None
        self.asset = asset or MapAsset()

        if token_provider is not None:
            self.token_provider: TokenEntityProvider = token_provider
        else:
            try:
                self.token_provider = CampaignTokenEntityProvider(parent=self)
                # Touch the campaign now so a missing current campaign is detected
                # before the toolbar starts requesting resources.
                _ = self.token_provider.campaign
            except Exception:
                self.token_provider = NullTokenEntityProvider()

        self.canvas = MapCanvas(
            asset=self.asset,
            map_dir=self.map_dir or Path.cwd(),
            token_provider=self.token_provider,
            parent=self,
        )
        self.setCentralWidget(self.canvas)

        self._build_editor_toolbar()
        self._build_token_toolbar()
        self._build_status_bar()
        self._connect_signals()

        self._set_map_loaded(self.map_dir is not None)

        if self.map_dir is not None:
            self.setWindowTitle(f"RPG Tabletop - Map Editor - {self.asset.name}")
        else:
            self.statusBar().showMessage("Create or open a map to start editing.")

    # -------------------------------------------------------------------------
    # UI construction
    # -------------------------------------------------------------------------

    def _build_editor_toolbar(self) -> None:
        toolbar = QToolBar("Tools", self)
        self._editor_toolbar = toolbar
        toolbar.setObjectName("MapEditorTools")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setOrientation(Qt.Orientation.Vertical)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setMinimumWidth(196)
        toolbar.setMaximumWidth(196)
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, toolbar)

        self._apply_editor_toolbar_style(toolbar)

        toolbar.addWidget(self._section_label("TOOLS"))

        self._tool_action_group = QActionGroup(self)
        self._tool_action_group.setExclusive(True)
        self._tool_actions: dict[EditorTool, QAction] = {}

        def add_tool(label: str, tool: EditorTool, shortcut: Optional[str] = None) -> QAction:
            action = QAction(self._tool_icon(tool), label, self)
            action.setCheckable(True)
            if shortcut:
                action.setShortcut(shortcut)
                action.setToolTip(f"{label} ({shortcut})")
            else:
                action.setToolTip(label)
            action.triggered.connect(lambda _checked=False, t=tool: self._activate_tool(t))
            self._tool_action_group.addAction(action)
            toolbar.addAction(action)
            self._tool_actions[tool] = action
            return action

        add_tool("Select", EditorTool.SELECT, "V")
        add_tool("Image", EditorTool.IMAGE, "I")
        add_tool("Freehand", EditorTool.FREEHAND, "P")
        add_tool("Line", EditorTool.LINE, "L")
        add_tool("Rectangle", EditorTool.RECT, "R")
        add_tool("Ellipse", EditorTool.CIRCLE, "O")
        add_tool("Text", EditorTool.TEXT, "T")
        self._tool_actions[EditorTool.SELECT].setChecked(True)

        toolbar.addSeparator()
        toolbar.addWidget(self._section_label("APPEARANCE"))

        toolbar.addWidget(self._field_label("Stroke / text color"))
        self._stroke_color_button = QPushButton()
        self._stroke_color_button.setObjectName("ColorSwatch")
        self._stroke_color_button.setToolTip("Stroke / text color")
        self._stroke_color_button.setFixedHeight(32)
        self._stroke_color_button.clicked.connect(self._choose_stroke_color)
        toolbar.addWidget(self._stroke_color_button)

        self._fill_enabled = QCheckBox("Fill shape")
        self._fill_enabled.toggled.connect(self._apply_tool_style)
        self._fill_enabled.toggled.connect(lambda _checked: self._update_tool_controls())
        toolbar.addWidget(self._fill_enabled)

        self._fill_color_button = QPushButton()
        self._fill_color_button.setObjectName("ColorSwatch")
        self._fill_color_button.setToolTip("Fill color")
        self._fill_color_button.setFixedHeight(32)
        self._fill_color_button.clicked.connect(self._choose_fill_color)
        toolbar.addWidget(self._fill_color_button)

        toolbar.addWidget(self._field_label("Stroke width"))
        self._stroke_width = QDoubleSpinBox()
        self._stroke_width.setRange(0.0, 100.0)
        self._stroke_width.setDecimals(1)
        self._stroke_width.setSingleStep(0.5)
        self._stroke_width.setSuffix(" px")
        self._stroke_width.setValue(self.canvas.stroke_width)
        self._stroke_width.valueChanged.connect(self._apply_tool_style)
        toolbar.addWidget(self._stroke_width)

        toolbar.addWidget(self._field_label("Opacity"))
        self._opacity = QDoubleSpinBox()
        self._opacity.setRange(0.0, 1.0)
        self._opacity.setDecimals(2)
        self._opacity.setSingleStep(0.05)
        self._opacity.setValue(self.canvas.element_opacity)
        self._opacity.valueChanged.connect(self._apply_tool_style)
        toolbar.addWidget(self._opacity)

        toolbar.addWidget(self._field_label("Text size"))
        self._font_size = QSpinBox()
        self._font_size.setRange(1, 512)
        self._font_size.setSuffix(" px")
        self._font_size.setValue(round(self.canvas.text_font_size))
        self._font_size.valueChanged.connect(self._apply_tool_style)
        toolbar.addWidget(self._font_size)

        self._stroke_color_value = self.canvas.stroke_color
        self._fill_color_value = "#89b4fa"
        self._refresh_color_buttons()
        self._update_tool_controls()

        toolbar.addSeparator()
        toolbar.addWidget(self._section_label("VIEW"))

        fit_action = QAction("Fit Map", self)
        fit_action.setShortcut("F")
        fit_action.setToolTip("Fit the whole map in the editor viewport (F)")
        fit_action.triggered.connect(self.canvas.fit_map)
        toolbar.addAction(fit_action)

        reload_media_action = QAction("Reload Media", self)
        reload_media_action.setToolTip("Reload imported image files from disk")
        reload_media_action.triggered.connect(self.canvas.reload_media)
        toolbar.addAction(reload_media_action)

    def _build_token_toolbar(self) -> None:
        toolbar = QToolBar("Tokens", self)
        self._token_toolbar = toolbar
        toolbar.setObjectName("MapTokenTools")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setOrientation(Qt.Orientation.Vertical)
        toolbar.setMinimumWidth(248)
        toolbar.setMaximumWidth(248)
        self.addToolBar(Qt.ToolBarArea.RightToolBarArea, toolbar)

        container = QWidget(toolbar)
        root = QVBoxLayout(container)
        root.setContentsMargins(9, 10, 9, 10)
        root.setSpacing(8)

        title = QLabel("TOKENS", container)
        title.setObjectName("TokenToolbarTitle")
        root.addWidget(title)

        self._token_search = QLineEdit(container)
        self._token_search.setPlaceholderText("Search characters / entities…")
        self._token_search.setClearButtonEnabled(True)
        self._token_search.textChanged.connect(self.refresh_token_sources)
        root.addWidget(self._token_search)

        self._token_tabs = QTabWidget(container)
        self._token_tabs.setDocumentMode(True)
        root.addWidget(self._token_tabs, 1)

        # Characters ---------------------------------------------------------
        character_page = QWidget(self._token_tabs)
        character_layout = QVBoxLayout(character_page)
        character_layout.setContentsMargins(5, 7, 5, 5)
        character_layout.setSpacing(7)
        self._character_list = TokenSourceList("character", character_page)
        self._character_list.setAlternatingRowColors(False)
        self._character_list.itemDoubleClicked.connect(
            lambda _item: self._create_selected_character_token()
        )
        character_layout.addWidget(self._character_list, 1)
        self._character_add_button = QPushButton("Create Entity && place", character_page)
        self._character_add_button.clicked.connect(self._create_selected_character_token)
        character_layout.addWidget(self._character_add_button)
        self._token_tabs.addTab(character_page, "Characters")

        # Entities -----------------------------------------------------------
        entity_page = QWidget(self._token_tabs)
        entity_layout = QVBoxLayout(entity_page)
        entity_layout.setContentsMargins(5, 7, 5, 5)
        entity_layout.setSpacing(7)
        self._entity_list = TokenSourceList("entity", entity_page)
        self._entity_list.itemDoubleClicked.connect(
            lambda _item: self._place_selected_entity_token()
        )
        entity_layout.addWidget(self._entity_list, 1)
        entity_buttons = QHBoxLayout()
        entity_buttons.setContentsMargins(0, 0, 0, 0)
        entity_buttons.setSpacing(6)
        self._entity_place_button = QPushButton("Place", entity_page)
        self._entity_place_button.clicked.connect(self._place_selected_entity_token)
        entity_buttons.addWidget(self._entity_place_button, 1)
        self._entity_edit_button = QPushButton("Open sheet", entity_page)
        self._entity_edit_button.clicked.connect(self._edit_selected_entity)
        entity_buttons.addWidget(self._entity_edit_button, 1)
        entity_layout.addLayout(entity_buttons)
        self._token_tabs.addTab(entity_page, "Entities")

        self._token_provider_hint = QLabel(container)
        self._token_provider_hint.setWordWrap(True)
        self._token_provider_hint.setObjectName("TokenProviderHint")
        root.addWidget(self._token_provider_hint)

        refresh = QPushButton("Refresh", container)
        refresh.clicked.connect(self.refresh_token_sources)
        root.addWidget(refresh)

        toolbar.addWidget(container)
        self._apply_token_toolbar_style(toolbar)
        self.refresh_token_sources()

    @staticmethod
    def _apply_token_toolbar_style(toolbar: QToolBar) -> None:
        toolbar.setStyleSheet(
            """
            QToolBar#MapTokenTools {
                background: #181825;
                border: none;
                border-left: 1px solid #313244;
            }
            QToolBar#MapTokenTools QWidget {
                color: #cdd6f4;
                background: #181825;
            }
            QLabel#TokenToolbarTitle {
                color: #6c7086;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
                padding: 4px 2px;
            }
            QLabel#TokenProviderHint {
                color: #6c7086;
                font-size: 10px;
                padding: 3px;
            }
            QToolBar#MapTokenTools QLineEdit,
            QToolBar#MapTokenTools QListWidget {
                color: #cdd6f4;
                background: #11111b;
                border: 1px solid #313244;
                border-radius: 6px;
                padding: 5px;
                selection-background-color: #313244;
                selection-color: #89b4fa;
            }
            QToolBar#MapTokenTools QLineEdit:focus,
            QToolBar#MapTokenTools QListWidget:focus {
                border-color: #89b4fa;
            }
            QToolBar#MapTokenTools QTabWidget::pane {
                border: 1px solid #313244;
                border-radius: 7px;
                background: #181825;
            }
            QToolBar#MapTokenTools QTabBar::tab {
                color: #9399b2;
                background: #11111b;
                border: 1px solid #313244;
                padding: 6px 10px;
            }
            QToolBar#MapTokenTools QTabBar::tab:selected {
                color: #89b4fa;
                background: #313244;
            }
            QToolBar#MapTokenTools QPushButton {
                min-height: 28px;
                color: #cdd6f4;
                background: #313244;
                border: 1px solid #45475a;
                border-radius: 6px;
                padding: 4px 7px;
            }
            QToolBar#MapTokenTools QPushButton:hover {
                border-color: #89b4fa;
                color: #89b4fa;
            }
            QToolBar#MapTokenTools QPushButton:disabled {
                color: #585b70;
                background: #1e1e2e;
            }
            """
        )

    def set_token_provider(self, provider: Optional[TokenEntityProvider]) -> None:
        self.token_provider = provider or NullTokenEntityProvider()
        self.canvas.set_token_provider(self.token_provider)
        self.refresh_token_sources()

    @staticmethod
    def _token_source_icon(source: TokenSourceData) -> QIcon:
        """Render a compact token-like portrait used by the resource lists and drag pixmap."""
        width = 72
        height = 86
        portrait_size = 64

        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        portrait_rect = QRectF(
            (width - portrait_size) / 2.0,
            1.0,
            portrait_size,
            portrait_size,
        )

        image = QImage(str(source.portrait)) if source.portrait else QImage()

        if not image.isNull():
            MapCanvas._draw_cover_image(
                painter,
                portrait_rect,
                image,
            )
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#313244"))
            painter.drawRoundedRect(
                portrait_rect,
                8.0,
                8.0,
            )

            initials = "".join(
                part[:1]
                for part in source.name.split()[:2]
            ).upper() or "?"

            font = QFont()
            font.setBold(True)
            font.setPixelSize(20)
            painter.setFont(font)
            painter.setPen(QColor("#cdd6f4"))
            painter.drawText(
                portrait_rect,
                Qt.AlignmentFlag.AlignCenter,
                initials,
            )

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#89b4fa"), 2.0))
        painter.drawRoundedRect(
            portrait_rect,
            8.0,
            8.0,
        )

        # Tiny HP / stamina bars make the thumbnail read as a token preview,
        # rather than as a generic file thumbnail.
        bar_x = 7.0
        bar_w = width - 14.0
        bar_h = 4.0
        hp_y = 69.0
        sta_y = 76.0

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#313244"))
        painter.drawRoundedRect(QRectF(bar_x, hp_y, bar_w, bar_h), 2.0, 2.0)
        painter.drawRoundedRect(QRectF(bar_x, sta_y, bar_w, bar_h), 2.0, 2.0)

        painter.setBrush(QColor("#f38ba8"))
        painter.drawRoundedRect(QRectF(bar_x, hp_y, bar_w, bar_h), 2.0, 2.0)

        painter.setBrush(QColor("#fab387"))
        painter.drawRoundedRect(QRectF(bar_x, sta_y, bar_w, bar_h), 2.0, 2.0)

        painter.end()
        return QIcon(pixmap)

    @classmethod
    def _populate_token_list(
        cls,
        widget: QListWidget,
        sources: list[TokenSourceData],
        query: str,
    ) -> None:
        widget.clear()
        widget.setIconSize(QSize(64, 78))

        query = query.casefold().strip()

        for source in sources:
            haystack = f"{source.name} {source.subtitle}".casefold()
            if query and query not in haystack:
                continue

            text = (
                source.name
                if not source.subtitle
                else f"{source.name}\n{source.subtitle}"
            )

            item = QListWidgetItem(
                cls._token_source_icon(source),
                text,
            )
            item.setData(
                Qt.ItemDataRole.UserRole,
                source.id,
            )
            item.setToolTip(
                (source.subtitle or source.name)
                + "\nDrag onto the map to place this token."
            )
            item.setSizeHint(QSize(0, 88))
            widget.addItem(item)

    def refresh_token_sources(self) -> None:
        try:
            characters = list(self.token_provider.list_characters())
            entities = list(self.token_provider.list_entities())
            error = ""
        except Exception as exc:
            characters = []
            entities = []
            error = str(exc)

        query = self._token_search.text() if hasattr(self, "_token_search") else ""
        if hasattr(self, "_character_list"):
            self._populate_token_list(self._character_list, characters, query)
        if hasattr(self, "_entity_list"):
            # Mark entities that already have a token on this map.
            decorated: list[TokenSourceData] = []
            for source in entities:
                subtitle = source.subtitle
                if self.asset.token_for(source.id) is not None:
                    subtitle = f"{subtitle} • on map" if subtitle else "on map"
                decorated.append(
                    TokenSourceData(
                        source.id,
                        source.name,
                        subtitle,
                        source.portrait,
                    )
                )
            self._populate_token_list(self._entity_list, decorated, query)

        if hasattr(self, "_token_provider_hint"):
            if error:
                self._token_provider_hint.setText(f"Provider error: {error}")
            elif isinstance(self.token_provider, NullTokenEntityProvider):
                self._token_provider_hint.setText(
                    "No current Campaign is available for Characters / Entities."
                )
            else:
                self._token_provider_hint.setText(
                    f"{len(characters)} characters • {len(entities)} entities\n"
                    "Drag a portrait onto the map."
                )

    @staticmethod
    def _selected_source_id(widget: QListWidget) -> Optional[str]:
        item = widget.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return str(value) if value is not None else None

    def _create_selected_character_token(self) -> None:
        character_id = self._selected_source_id(self._character_list)
        if not character_id:
            return
        try:
            entity_id = self.token_provider.create_entity_from_character(character_id)
            if not entity_id:
                return
            # Rule: a Character always becomes an Entity and its editor is opened
            # before a token can be placed.
            if not self.token_provider.edit_entity(entity_id):
                self.refresh_token_sources()
                return
            self.refresh_token_sources()
            self.canvas.begin_token_placement(entity_id)
            self.statusBar().showMessage("Click on the map to place the new Entity token.", 4000)
        except Exception as error:
            QMessageBox.critical(self, "Create Entity token", str(error))

    def _place_selected_entity_token(self) -> None:
        entity_id = self._selected_source_id(self._entity_list)
        if not entity_id:
            return
        self.canvas.begin_token_placement(entity_id)
        existing = self.asset.token_for(entity_id)
        message = "Click on the map to move this Entity token." if existing else "Click on the map to place this Entity token."
        self.statusBar().showMessage(message, 4000)

    def _edit_selected_entity(self) -> None:
        entity_id = self._selected_source_id(self._entity_list)
        if not entity_id:
            return
        self._open_entity_sheet(entity_id)

    def _open_entity_sheet(self, entity_id: str) -> None:
        try:
            self.token_provider.edit_entity(entity_id)
            self.canvas.update()
            self.refresh_token_sources()
        except Exception as error:
            QMessageBox.critical(self, "Entity", str(error))

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("ToolbarSectionLabel")
        return label

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("ToolbarFieldLabel")
        return label

    @staticmethod
    def _apply_editor_toolbar_style(toolbar: QToolBar) -> None:
        toolbar.setStyleSheet(
            """
            QToolBar#MapEditorTools {
                background: #181825;
                border: none;
                border-right: 1px solid #313244;
                spacing: 4px;
                padding: 10px 8px;
            }

            QToolBar#MapEditorTools QToolButton {
                min-width: 158px;
                min-height: 30px;
                padding: 5px 8px;
                margin: 1px 0px;
                color: #cdd6f4;
                background: transparent;
                border: 1px solid transparent;
                border-radius: 7px;
                font-size: 12px;
                text-align: left;
            }

            QToolBar#MapEditorTools QToolButton:hover {
                background: #252536;
                border-color: #313244;
            }

            QToolBar#MapEditorTools QToolButton:checked {
                color: #89b4fa;
                background: #313244;
                border-color: #45475a;
                font-weight: 600;
            }

            QToolBar#MapEditorTools QToolButton:pressed {
                background: #45475a;
            }

            QToolBar#MapEditorTools::separator {
                background: #313244;
                height: 1px;
                margin: 9px 4px;
            }

            QLabel#ToolbarSectionLabel {
                color: #6c7086;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
                padding: 4px 4px 5px 4px;
            }

            QLabel#ToolbarFieldLabel {
                color: #a6adc8;
                font-size: 11px;
                padding: 7px 3px 3px 3px;
            }

            QToolBar#MapEditorTools QDoubleSpinBox,
            QToolBar#MapEditorTools QSpinBox {
                min-height: 28px;
                padding: 2px 7px;
                color: #cdd6f4;
                background: #11111b;
                border: 1px solid #313244;
                border-radius: 6px;
                selection-background-color: #89b4fa;
                selection-color: #11111b;
            }

            QToolBar#MapEditorTools QDoubleSpinBox:hover,
            QToolBar#MapEditorTools QSpinBox:hover {
                border-color: #45475a;
            }

            QToolBar#MapEditorTools QDoubleSpinBox:focus,
            QToolBar#MapEditorTools QSpinBox:focus {
                border-color: #89b4fa;
            }

            QToolBar#MapEditorTools QDoubleSpinBox:disabled,
            QToolBar#MapEditorTools QSpinBox:disabled {
                color: #585b70;
                background: #181825;
            }

            QToolBar#MapEditorTools QCheckBox {
                min-height: 25px;
                padding: 5px 3px 2px 3px;
                color: #cdd6f4;
                spacing: 8px;
            }

            QToolBar#MapEditorTools QCheckBox:disabled {
                color: #585b70;
            }

            QToolBar#MapEditorTools QCheckBox::indicator {
                width: 15px;
                height: 15px;
                border-radius: 4px;
                border: 1px solid #585b70;
                background: #11111b;
            }

            QToolBar#MapEditorTools QCheckBox::indicator:hover {
                border-color: #89b4fa;
            }

            QToolBar#MapEditorTools QCheckBox::indicator:checked {
                background: #89b4fa;
                border-color: #89b4fa;
            }

            QPushButton#ColorSwatch {
                min-width: 158px;
                border-radius: 6px;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 8px;
            }

            QPushButton#ColorSwatch:hover {
                border-width: 2px;
            }

            QPushButton#ColorSwatch:disabled {
                color: #6c7086;
                background: #1e1e2e;
                border-color: #313244;
            }
            """
        )

    @classmethod
    def _tool_icon(cls, tool: EditorTool) -> QIcon:
        icon = QIcon()
        icon.addPixmap(
            cls._tool_icon_pixmap(tool, QColor("#cdd6f4")),
            QIcon.Mode.Normal,
            QIcon.State.Off,
        )
        icon.addPixmap(
            cls._tool_icon_pixmap(tool, QColor("#89b4fa")),
            QIcon.Mode.Normal,
            QIcon.State.On,
        )
        icon.addPixmap(
            cls._tool_icon_pixmap(tool, QColor("#585b70")),
            QIcon.Mode.Disabled,
            QIcon.State.Off,
        )
        return icon

    @staticmethod
    def _tool_icon_pixmap(tool: EditorTool, color: QColor) -> QPixmap:
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if tool is EditorTool.SELECT:
            polygon = QPolygonF([
                QPointF(5.0, 3.0),
                QPointF(17.0, 13.0),
                QPointF(11.5, 14.0),
                QPointF(15.5, 20.0),
                QPointF(12.5, 21.5),
                QPointF(8.5, 15.2),
                QPointF(5.0, 19.0),
            ])
            painter.drawPolygon(polygon)

        elif tool is EditorTool.IMAGE:
            painter.drawRoundedRect(QRectF(3.5, 4.0, 17.0, 16.0), 2.0, 2.0)
            painter.drawEllipse(QPointF(8.0, 8.5), 1.4, 1.4)
            painter.drawPolyline(QPolygonF([
                QPointF(5.5, 17.0),
                QPointF(10.0, 12.5),
                QPointF(13.0, 15.0),
                QPointF(16.0, 11.5),
                QPointF(19.0, 15.0),
            ]))

        elif tool is EditorTool.FREEHAND:
            path = QPainterPath(QPointF(3.0, 17.0))
            path.cubicTo(QPointF(7.0, 5.0), QPointF(10.0, 21.0), QPointF(14.0, 10.0))
            path.cubicTo(QPointF(16.0, 5.0), QPointF(19.0, 7.0), QPointF(21.0, 4.0))
            painter.drawPath(path)

        elif tool is EditorTool.LINE:
            painter.drawLine(QPointF(4.0, 19.0), QPointF(20.0, 5.0))
            painter.drawEllipse(QPointF(4.0, 19.0), 1.2, 1.2)
            painter.drawEllipse(QPointF(20.0, 5.0), 1.2, 1.2)

        elif tool is EditorTool.RECT:
            painter.drawRect(QRectF(4.0, 5.0, 16.0, 14.0))

        elif tool is EditorTool.CIRCLE:
            painter.drawEllipse(QRectF(4.0, 4.0, 16.0, 16.0))

        elif tool is EditorTool.TEXT:
            font = QFont()
            font.setBold(True)
            font.setPixelSize(17)
            painter.setFont(font)
            painter.drawText(QRectF(2.0, 1.0, 20.0, 21.0), Qt.AlignmentFlag.AlignCenter, "T")

        painter.end()
        return pixmap

    def _activate_tool(self, tool: EditorTool) -> None:
        if tool is EditorTool.IMAGE:
            self._choose_image_tool()
            return
        self.canvas.set_tool(tool)

    def _choose_image_file(self, title: str = "Import image") -> Optional[Path]:
        source, _filter = QFileDialog.getOpenFileName(
            self,
            title,
            str(Path.cwd()),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All files (*)",
        )
        return Path(source) if source else None

    def _import_image_file(self, source: Path) -> Optional[tuple[Path, int, int]]:
        map_dir = self._require_map_dir()
        if map_dir is None:
            return None

        relative = import_media(map_dir, source)
        width, height = media_size(map_dir, relative)
        if width <= 0 or height <= 0:
            width, height = 128, 128
        self.canvas.reload_media()
        return relative, width, height

    def _choose_image_tool(self) -> None:
        source = self._choose_image_file("Import image")
        if source is None:
            self._tool_actions[EditorTool.SELECT].setChecked(True)
            self.canvas.set_tool(EditorTool.SELECT)
            return

        try:
            imported = self._import_image_file(source)
            if imported is None:
                self._tool_actions[EditorTool.SELECT].setChecked(True)
                self.canvas.set_tool(EditorTool.SELECT)
                return

            relative, width, height = imported
            self.canvas.set_pending_image(relative, width, height)
            self._tool_actions[EditorTool.IMAGE].setChecked(True)
        except Exception as error:
            QMessageBox.critical(self, "Import image", str(error))
            self._tool_actions[EditorTool.SELECT].setChecked(True)
            self.canvas.set_tool(EditorTool.SELECT)

    def _replace_selected_image(self) -> None:
        element = self.canvas.selected_element()
        if not isinstance(element, ImageElement) or element.locked:
            return

        source = self._choose_image_file("Choose image")
        if source is None:
            return

        try:
            imported = self._import_image_file(source)
            if imported is None:
                return

            relative, _width, _height = imported
            # Replacing the source intentionally preserves the current frame size.
            self.canvas.replace_selected_image_asset(relative)
        except Exception as error:
            QMessageBox.critical(self, "Choose image", str(error))

    def _choose_stroke_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._stroke_color_value), self, "Choose color")
        if color.isValid():
            self._stroke_color_value = color.name(QColor.NameFormat.HexRgb)
            self._refresh_color_buttons()
            self._apply_tool_style()

    def _choose_fill_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._fill_color_value), self, "Choose fill color")
        if color.isValid():
            self._fill_color_value = color.name(QColor.NameFormat.HexRgb)
            self._fill_enabled.setChecked(True)
            self._refresh_color_buttons()
            self._apply_tool_style()

    @staticmethod
    def _swatch_text_color(value: str) -> str:
        color = QColor(value)
        luminance = (
            0.2126 * color.redF()
            + 0.7152 * color.greenF()
            + 0.0722 * color.blueF()
        )
        return "#11111b" if luminance > 0.55 else "#f5e0dc"

    def _refresh_color_buttons(self) -> None:
        stroke_text = self._swatch_text_color(self._stroke_color_value)
        fill_text = self._swatch_text_color(self._fill_color_value)

        self._stroke_color_button.setStyleSheet(
            f"""
            QPushButton#ColorSwatch {{
                color: {stroke_text};
                background: {self._stroke_color_value};
                border: 1px solid #585b70;
                border-radius: 6px;
            }}
            QPushButton#ColorSwatch:hover {{
                border: 2px solid #cdd6f4;
            }}
            QPushButton#ColorSwatch:disabled {{
                color: #6c7086;
                background: #1e1e2e;
                border: 1px solid #313244;
            }}
            """
        )
        self._stroke_color_button.setText(self._stroke_color_value.upper())

        self._fill_color_button.setStyleSheet(
            f"""
            QPushButton#ColorSwatch {{
                color: {fill_text};
                background: {self._fill_color_value};
                border: 1px solid #585b70;
                border-radius: 6px;
            }}
            QPushButton#ColorSwatch:hover {{
                border: 2px solid #cdd6f4;
            }}
            QPushButton#ColorSwatch:disabled {{
                color: #6c7086;
                background: #1e1e2e;
                border: 1px solid #313244;
            }}
            """
        )
        self._fill_color_button.setText(self._fill_color_value.upper())

    def _apply_tool_style(self, *_args) -> None:
        self.canvas.set_creation_style(
            stroke_color=self._stroke_color_value,
            fill_color=self._fill_color_value if self._fill_enabled.isChecked() else None,
            stroke_width=self._stroke_width.value(),
            opacity=self._opacity.value(),
            font_size=float(self._font_size.value()),
        )

    def _sync_tool_action(self, tool: EditorTool) -> None:
        action = self._tool_actions.get(tool)
        if action is not None:
            action.setChecked(True)
        self._update_tool_controls()
        self._update_status()

    def _update_tool_controls(self) -> None:
        tool = self.canvas.active_tool
        drawing = tool in DRAW_TOOLS
        shape = tool in (EditorTool.RECT, EditorTool.CIRCLE)
        colored = drawing or tool is EditorTool.TEXT

        self._stroke_color_button.setEnabled(colored)
        self._stroke_width.setEnabled(drawing)
        self._fill_enabled.setEnabled(shape)
        self._fill_color_button.setEnabled(shape and self._fill_enabled.isChecked())
        self._opacity.setEnabled(tool is not EditorTool.SELECT)
        self._font_size.setEnabled(tool is EditorTool.TEXT)

    def _build_status_bar(self) -> None:
        bar = QStatusBar(self)
        self.setStatusBar(bar)

        self._save_button = QPushButton("Save", bar)
        self._save_button.setObjectName("MapSaveButton")
        self._save_button.setToolTip(
            "Save map.json and compile changed tiles"
        )
        self._save_button.setFixedHeight(24)
        self._save_button.setMinimumWidth(62)
        self._save_button.clicked.connect(self.save_map)

        self._compile_button = QPushButton("Compile", bar)
        self._compile_button.setObjectName("MapCompileButton")
        self._compile_button.setToolTip(
            "Compile tiles without rewriting map.json"
        )
        self._compile_button.setFixedHeight(24)
        self._compile_button.setMinimumWidth(70)
        self._compile_button.clicked.connect(self.compile_map)

        bar.addPermanentWidget(self._save_button)
        bar.addPermanentWidget(self._compile_button)

        self._update_status()

    def _connect_signals(self) -> None:
        self.canvas.mouse_world_moved.connect(self._on_mouse_world_moved)
        self.canvas.zoom_changed.connect(lambda _zoom: self._update_status())
        self.canvas.selection_changed.connect(lambda _element: self._update_status())
        self.canvas.tool_changed.connect(self._sync_tool_action)
        self.canvas.image_replace_requested.connect(self._replace_selected_image)
        self.canvas.token_double_clicked.connect(self._open_entity_sheet)
        self.canvas.token_placed.connect(lambda _entity_id: self.refresh_token_sources())
        self.canvas.history_changed.connect(lambda _undo, _redo: self._update_status())

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    def _update_status(self, world: Optional[Vec2] = None) -> None:
        selected = self.canvas.selected_elements()
        if len(selected) > 1:
            selection_text = f" | selected: {len(selected)} elements"
        elif selected:
            selection = selected[0]
            selection_text = f" | selected: {selection.type_name} ({selection.id[:8]})"
        elif self.canvas.camera_selected:
            state = self.asset.initial_gm_camera
            selection_text = (
                " | selected: initial GM camera"
                f" ({state.zoom:.2f}x)"
            )
        else:
            selection_text = ""
        mouse_text = (
            f" | x={world.x:.1f}, y={world.y:.1f}"
            if world is not None
            else ""
        )

        token_text = (
            f" | placing token: {self.canvas.pending_token_entity_id[:8]}"
            if self.canvas.pending_token_entity_id
            else ""
        )
        self.statusBar().showMessage(
            f"{self.asset.name} | tool: {self.canvas.active_tool.name.lower()} "
            f"| zoom: {self.canvas.camera.zoom:.2f}x"
            f"{mouse_text}{selection_text}{token_text}"
        )

    def _on_mouse_world_moved(self, x: float, y: float) -> None:
        self._update_status(Vec2(x, y))

    # -------------------------------------------------------------------------
    # Map operations
    # -------------------------------------------------------------------------

    def _set_map_loaded(self, loaded: bool) -> None:
        """Enable editing only when the editor owns a persistent map folder."""
        self.canvas.setEnabled(loaded)
        if hasattr(self, "_editor_toolbar"):
            self._editor_toolbar.setEnabled(loaded)
        if hasattr(self, "_token_toolbar"):
            self._token_toolbar.setEnabled(loaded)
        if hasattr(self, "_save_button"):
            self._save_button.setEnabled(loaded)
        if hasattr(self, "_compile_button"):
            self._compile_button.setEnabled(loaded)

    def _activate_map(self, asset: MapAsset, map_dir: Path, *, fit: bool = False) -> None:
        self.map_dir = Path(map_dir)
        self.asset = asset
        self.canvas.set_asset(asset, self.map_dir)
        self._tool_actions[EditorTool.SELECT].setChecked(True)
        self._set_map_loaded(True)
        self.refresh_token_sources()
        self.setWindowTitle(f"RPG Tabletop - Map Editor - {asset.name}")
        if fit:
            self.canvas.fit_map()
        self._update_status()

    def new_map(self) -> None:
        name, accepted = QInputDialog.getText(
            self,
            "New map",
            "Map name:",
            text="New Map",
        )
        if not accepted:
            return

        name = name.strip()
        if not name:
            QMessageBox.warning(self, "New map", "The map name cannot be empty.")
            return

        asset = MapAsset(name=name)

        try:
            map_dir = _unique_map_dir(name)
            asset.initialize(map_dir)
        except Exception as error:
            QMessageBox.critical(self, "New map", str(error))
            return

        self._activate_map(asset, map_dir, fit=True)
        self.statusBar().showMessage(
            f"Created {asset.name} in {map_dir.name}.",
            4000,
        )

    def open_map(self) -> None:
        if self.map_dir is not None:
            start_dir = self.map_dir.parent
        else:
            try:
                start_dir = Path(
                    get_current_campaign().map_path("__map_editor_probe__")
                ).parent
            except Exception:
                start_dir = Path.cwd()
        directory = QFileDialog.getExistingDirectory(
            self,
            "Open map folder",
            str(start_dir),
        )
        if not directory:
            return

        map_dir = Path(directory)

        try:
            asset = MapAsset.load(map_dir)
        except Exception as error:
            QMessageBox.critical(
                self,
                "Open map",
                str(error),
            )
            return

        self._activate_map(asset, map_dir)

    def _require_map_dir(self) -> Optional[Path]:
        if self.map_dir is not None:
            return self.map_dir

        QMessageBox.information(
            self,
            "No map open",
            "Create or open a map first.",
        )
        return None

    def compile_map(self) -> None:
        map_dir = self._require_map_dir()
        if map_dir is None:
            return

        try:
            compiled = self.asset.compile(
                map_dir,
            )
        except MapCompileCancelled:
            self.statusBar().showMessage("Compilation cancelled.", 3000)
            return
        except Exception as error:
            QMessageBox.critical(
                self,
                "Compile map",
                str(error),
            )
            self._update_status()
            return

        self.statusBar().showMessage(
            f"Compiled {compiled.columns} x {compiled.rows} tiles.",
            4000,
        )

    def save(self) -> None:
        """Standard save entry point for host IDE integration."""
        self.save_map()

    def save_map(self) -> None:
        map_dir = self._require_map_dir()
        if map_dir is None:
            return

        try:
            compiled = self.asset.save(
                map_dir,
            )
        except MapCompileCancelled:
            self.statusBar().showMessage("Save cancelled.", 3000)
            return
        except Exception as error:
            QMessageBox.critical(
                self,
                "Save map",
                str(error),
            )
            self._update_status()
            return

        self.statusBar().showMessage(
            f"Saved and compiled ({compiled.columns} x {compiled.rows} tiles).",
            4000,
        )

    def set_initial_camera(self) -> None:
        """Compatibility helper; normal UI edits the camera directly."""
        before = self.canvas._capture_state()
        self.canvas.set_current_as_initial_camera()
        self.canvas._select_camera()
        self.canvas._commit_history(before)
        self.statusBar().showMessage(
            "Current editor view stored as initial GM camera.",
            3000,
        )


# =============================================================================
# Standalone test
# =============================================================================


def main() -> None:
    app = QApplication(sys.argv)

    map_dir: Optional[Path] = None
    asset: Optional[MapAsset] = None

    # Optional command line usage:
    #     python libs/gui/map_editor.py path/to/map_folder
    if len(sys.argv) > 1:
        candidate = Path(sys.argv[1]).expanduser().resolve()
        try:
            asset = MapAsset.load(candidate)
            map_dir = candidate
        except Exception as error:
            QMessageBox.critical(
                None,
                "Open map",
                str(error),
            )

    window = MapEditorWindow(
        asset=asset,
        map_dir=map_dir,
    )
    window.show()

    if map_dir is None:
        window.new_map()

    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
