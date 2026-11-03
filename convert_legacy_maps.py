#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Convert old monolithic JDR-To-Aru map JSON files to the current
RPG Tabletop App MapAsset folder format.

OLD FORMAT (one JSON per map)
=============================
Typical keys:
    scene_name
    show_token_bars
    background
        color
        image_asset
        image_size_px
    grid
        enabled
        cell_size_px
        color
        opacity
    decor_images[]
        id
        asset
        pos {x, y}          # GRID / WORLD CELLS
        size_px [w, h]      # PIXELS
        rotation_deg
        locked
    decor_drawings[]
        id
        tool
        points [{x, y}, ...] # GRID / WORLD CELLS
        stroke_color
        stroke_width
        fill_color
    decor_texts[]
        id
        text
        pos {x, y}           # GRID / WORLD CELLS, baseline
        color
        font_size            # Qt point size in the old renderer
    debug_overlays[]
    pnj_tokens[]
        id
        display_name
        character_name
        pos {x, y}           # GRID / WORLD CELLS, token center
        size_cells
        visible
        ...

NEW FORMAT
==========
    maps/<map name>/
        map.json
        media/

All new MapAsset coordinates are PIXELS.

V3 automatically computes the required map bounds from every legacy image,
drawing, text and token. A generic 4096x4096 old editing canvas is no
longer forced onto maps that do not actually need that size.

The critical conversion is therefore:
    old_world_x * grid.cell_size_px -> new_pixel_x
    old_world_y * grid.cell_size_px -> new_pixel_y

Images keep their old size_px unchanged.

Old PNJ tokens become persistent Entity resources and the map token references
the new Entity UUID, matching RPG Tabletop App's current token rule.

The converter does NOT compile tiles or preview.png. Open/save the converted
map in the current editor afterwards.

Usage examples
==============
Convert one map:
    python convert_legacy_maps_v2.py ^
        "A:/PyProjects/JDR-To-Aru-Dmg-dice-calculator/maps/Labo Mother.json" ^
        "A:/PyProjects/RPG Tabletop App/campaigns/My Campaign" ^
        --legacy-root "A:/PyProjects/JDR-To-Aru-Dmg-dice-calculator"

Convert every old JSON map:
    python convert_legacy_maps_v2.py ^
        "A:/PyProjects/JDR-To-Aru-Dmg-dice-calculator/maps" ^
        "A:/PyProjects/RPG Tabletop App/campaigns/My Campaign" ^
        --legacy-root "A:/PyProjects/JDR-To-Aru-Dmg-dice-calculator"

Preview only:
    ... --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable, Optional
from uuid import uuid4


FORMAT_VERSION = 1
MAP_FILE = "map.json"
MEDIA_DIR = "media"

DEFAULT_MAP_SIZE = (4096, 4096)
DEFAULT_CELL_SIZE = 64

IMAGE_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"
}


# =============================================================================
# Report
# =============================================================================


@dataclass
class ConversionReport:
    source: Path
    destination: Path
    images: int = 0
    drawings: int = 0
    texts: int = 0
    debug_overlays: int = 0
    tokens: int = 0
    entities_created: list[Path] = field(default_factory=list)
    entities_updated: list[Path] = field(default_factory=list)
    media_copied: set[Path] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)

    old_canvas_size: tuple[int, int] = (0, 0)
    content_bounds: Optional[tuple[float, float, float, float]] = None
    applied_shift: tuple[float, float] = (0.0, 0.0)
    final_map_size: tuple[int, int] = (0, 0)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


# =============================================================================
# JSON / primitive helpers
# =============================================================================


def load_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return raw


def write_json(path: Path, data: dict[str, Any], dry_run: bool) -> None:
    if dry_run:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=4) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().casefold() not in {
            "", "0", "false", "no", "off", "none"
        }
    return bool(value)


def vec_xy(value: Any) -> tuple[float, float]:
    if isinstance(value, dict):
        return (
            as_float(value.get("x"), 0.0),
            as_float(value.get("y"), 0.0),
        )

    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return (
            as_float(value[0], 0.0),
            as_float(value[1], 0.0),
        )

    return (0.0, 0.0)


def world_to_pixels(value: Any, cell_size: float) -> list[float]:
    x, y = vec_xy(value)
    return [x * cell_size, y * cell_size]


def safe_name(value: str, fallback: str = "Map") -> str:
    text = str(value).strip() or fallback
    text = re.sub(r'[<>:"/\\|?*]', "_", text)
    text = text.rstrip(" .")
    return text or fallback


def resource_slug(name: str) -> str:
    # Matches current Entity.save() convention.
    return str(name).strip().lower().replace(" ", "_")


def color_or_default(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text or default


# =============================================================================
# Current campaign Entity resources
# =============================================================================


@dataclass
class EntityRecord:
    path: Path
    data: dict[str, Any]

    @property
    def name(self) -> str:
        return str(
            self.data.get("name")
            or self.path.stem.replace("_", " ")
        ).strip()

    @property
    def id(self) -> str:
        return str(self.data.get("id") or "").strip()


class EntityStore:
    """
    Tiny JSON-only bridge to RPG Tabletop App's persistent Entity resources.

    It intentionally does not import the app, so the converter can be executed
    without starting PySide6 or setting the current campaign singleton.
    """

    def __init__(
        self,
        campaign_root: Path,
        *,
        dry_run: bool,
    ) -> None:
        self.campaign_root = campaign_root
        self.character_dir = campaign_root / "characters"
        self.entity_dir = campaign_root / "entities"
        self.dry_run = dry_run

        self.characters: dict[str, str] = {}
        self.entities: list[EntityRecord] = []

        self._scan()

    def _scan(self) -> None:
        self.characters.clear()
        self.entities.clear()

        if self.character_dir.is_dir():
            for path in sorted(self.character_dir.glob("*.json")):
                try:
                    data = load_json(path)
                except Exception:
                    continue

                name = str(
                    data.get("name")
                    or path.stem.replace("_", " ")
                ).strip()

                keys = {
                    name.casefold(),
                    path.stem.casefold(),
                    resource_slug(name).casefold(),
                }
                for key in keys:
                    self.characters.setdefault(key, name)

        if self.entity_dir.is_dir():
            for path in sorted(self.entity_dir.glob("*.json")):
                try:
                    data = load_json(path)
                except Exception:
                    continue
                self.entities.append(
                    EntityRecord(path=path, data=data)
                )

    def character_name(self, identifier: str) -> Optional[str]:
        raw = str(identifier).strip()
        if not raw:
            return None

        return (
            self.characters.get(raw.casefold())
            or self.characters.get(resource_slug(raw).casefold())
        )

    def existing_entity_by_name(
        self,
        name: str,
    ) -> Optional[EntityRecord]:
        target = str(name).strip().casefold()
        for entity in self.entities:
            if entity.name.casefold() == target:
                return entity
        return None

    def existing_entity_by_id(
        self,
        entity_id: str,
    ) -> Optional[EntityRecord]:
        target = str(entity_id).strip()
        for entity in self.entities:
            if entity.id == target:
                return entity
        return None

    def unique_entity_name(self, preferred: str) -> str:
        base = str(preferred).strip() or "Entity"
        existing = {
            entity.name.casefold()
            for entity in self.entities
        }

        if base.casefold() not in existing:
            return base

        index = 2
        while f"{base} ({index})".casefold() in existing:
            index += 1
        return f"{base} ({index})"

    def _unique_entity_path(self, entity_name: str) -> Path:
        base = resource_slug(entity_name) or "entity"
        path = self.entity_dir / f"{base}.json"

        if not path.exists():
            return path

        index = 2
        while True:
            candidate = self.entity_dir / f"{base}_{index}.json"
            if not candidate.exists():
                return candidate
            index += 1

    def create_from_legacy_token(
        self,
        token: dict[str, Any],
        report: ConversionReport,
    ) -> Optional[str]:
        """
        Old pnj_tokens are instances, not Character assets. Each one therefore
        becomes its own persistent Entity.
        """
        character_raw = str(
            token.get("character_name")
            or ""
        ).strip()

        character_name = self.character_name(character_raw)
        if character_name is None:
            report.warn(
                "Token "
                f"{token.get('display_name') or token.get('id')!r}: "
                f"Character {character_raw!r} does not exist in "
                f"{self.character_dir}; token skipped."
            )
            return None

        display_name = str(
            token.get("display_name")
            or character_name
            or "Entity"
        ).strip()

        entity_name = self.unique_entity_name(display_name)
        entity_id = str(uuid4())

        data: dict[str, Any] = {
            "name": entity_name,
            "character": character_name,
            "id": entity_id,
            "stat_modifier": {
                "hp": 0,
                "stamina": 0,
                "mental_health": 0,
                "drug_health": 0,
                "str": 0,
                "con": 0,
                "dex": 0,
                "agi": 0,
                "int": 0,
                "wis": 0,
                "cha": 0,
                "luc": 0,
                "sur": 0,
                "per": 0,
            },
            "items": [],
            "spells": [],
            "spell_events": [],
            "spell_effects": [],

            # Kept only as migration metadata because the current Entity model
            # does not expose a separate "current HP" field.
            "_legacy_token_state": {
                "old_token_id": token.get("id"),
                "bars": token.get("bars", {}),
                "effects": token.get("effects", []),
                "owner_username": token.get("owner_username"),
                "show_name": token.get("show_name", True),
                "show_bars": token.get("show_bars", True),
                "image_asset": token.get("image_asset", ""),
            },
        }

        path = self._unique_entity_path(entity_name)
        write_json(path, data, self.dry_run)

        record = EntityRecord(path=path, data=data)
        self.entities.append(record)
        report.entities_created.append(path)

        return entity_id


# =============================================================================
# Legacy asset resolution
# =============================================================================


def normalized_legacy_parts(raw: str) -> list[str]:
    """
    Split both Windows and POSIX paths reliably, even if the converter happens
    to run on another OS.
    """
    text = str(raw).strip().replace("\\", "/")
    return [part for part in text.split("/") if part and not part.endswith(":")]


class AssetResolver:
    def __init__(
        self,
        legacy_root: Optional[Path],
        extra_roots: list[Path],
    ) -> None:
        self.legacy_root = legacy_root
        self.extra_roots = extra_roots
        self._basename_index: Optional[dict[str, list[Path]]] = None

    def _direct_candidates(
        self,
        raw: str,
        source_json: Path,
    ) -> Iterable[Path]:
        value = str(raw).strip()
        if not value:
            return

        # Native absolute path (important when running on the original Windows PC).
        native = Path(value)
        if native.is_absolute():
            yield native

        # Relative to the legacy map JSON.
        yield source_json.parent / value

        parts = normalized_legacy_parts(value)

        # Re-root paths containing ".../assets/...".
        lower_parts = [p.casefold() for p in parts]
        if "assets" in lower_parts:
            index = lower_parts.index("assets")
            suffix = Path(*parts[index:])

            if self.legacy_root is not None:
                yield self.legacy_root / suffix

            for root in self.extra_roots:
                yield root / suffix

        # Basename in common image folders.
        basename = parts[-1] if parts else Path(value).name
        if basename:
            if self.legacy_root is not None:
                yield self.legacy_root / "assets" / "images" / basename
                yield self.legacy_root / "images" / basename

            for root in self.extra_roots:
                yield root / "assets" / "images" / basename
                yield root / "images" / basename

    def _build_basename_index(self) -> dict[str, list[Path]]:
        if self._basename_index is not None:
            return self._basename_index

        index: dict[str, list[Path]] = {}

        roots: list[Path] = []
        if self.legacy_root is not None:
            roots.append(self.legacy_root)
        roots.extend(self.extra_roots)

        seen_roots: set[Path] = set()
        for root in roots:
            try:
                root = root.resolve()
            except OSError:
                continue

            if root in seen_roots or not root.is_dir():
                continue
            seen_roots.add(root)

            for path in root.rglob("*"):
                if (
                    path.is_file()
                    and path.suffix.casefold() in IMAGE_SUFFIXES
                ):
                    index.setdefault(
                        path.name.casefold(),
                        [],
                    ).append(path)

        self._basename_index = index
        return index

    def resolve(
        self,
        raw: Any,
        source_json: Path,
    ) -> Optional[Path]:
        value = str(raw or "").strip()
        if not value:
            return None

        seen: set[str] = set()

        for candidate in self._direct_candidates(
            value,
            source_json,
        ):
            key = os.path.normcase(str(candidate))
            if key in seen:
                continue
            seen.add(key)

            if candidate.is_file():
                return candidate

        parts = normalized_legacy_parts(value)
        basename = parts[-1].casefold() if parts else ""

        if basename:
            matches = self._build_basename_index().get(
                basename,
                [],
            )
            if matches:
                return matches[0]

        return None


def copy_into_map_media(
    source: Path,
    map_dir: Path,
    *,
    dry_run: bool,
) -> tuple[str, Path]:
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)

    suffix = source.suffix.lower()
    relative = Path(MEDIA_DIR) / (
        digest.hexdigest()[:16] + suffix
    )
    target = map_dir / relative

    if not dry_run and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(
            f".{target.name}.tmp"
        )
        try:
            shutil.copyfile(source, tmp)
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)

    return relative.as_posix(), target


def migrate_asset(
    raw: Any,
    *,
    source_json: Path,
    map_dir: Path,
    resolver: AssetResolver,
    report: ConversionReport,
    dry_run: bool,
    context: str,
) -> Optional[str]:
    if not str(raw or "").strip():
        return None

    source = resolver.resolve(
        raw,
        source_json,
    )

    if source is None:
        report.warn(
            f"{context}: image introuvable: {raw!r}"
        )
        return None

    relative, target = copy_into_map_media(
        source,
        map_dir,
        dry_run=dry_run,
    )
    report.media_copied.add(target)
    return relative


# =============================================================================
# Legacy -> current MapAsset conversion
# =============================================================================


def old_map_geometry(
    data: dict[str, Any],
) -> tuple[int, int, float]:
    background = (
        data.get("background")
        if isinstance(data.get("background"), dict)
        else {}
    )
    grid = (
        data.get("grid")
        if isinstance(data.get("grid"), dict)
        else {}
    )

    raw_size = background.get(
        "image_size_px",
        DEFAULT_MAP_SIZE,
    )

    if isinstance(raw_size, (list, tuple)) and len(raw_size) >= 2:
        width = max(1, as_int(raw_size[0], DEFAULT_MAP_SIZE[0]))
        height = max(1, as_int(raw_size[1], DEFAULT_MAP_SIZE[1]))
    else:
        width, height = DEFAULT_MAP_SIZE

    cell_size = max(
        1.0,
        as_float(
            grid.get("cell_size_px"),
            DEFAULT_CELL_SIZE,
        ),
    )

    return width, height, cell_size


# =============================================================================
# Automatic map sizing / fitting
# =============================================================================


Bounds = tuple[float, float, float, float]


def union_bounds(
    current: Optional[Bounds],
    new: Optional[Bounds],
) -> Optional[Bounds]:
    if new is None:
        return current
    if current is None:
        return new

    return (
        min(current[0], new[0]),
        min(current[1], new[1]),
        max(current[2], new[2]),
        max(current[3], new[3]),
    )


def points_bounds(
    points: list[list[float]],
    margin: float = 0.0,
) -> Optional[Bounds]:
    if not points:
        return None

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]

    return (
        min(xs) - margin,
        min(ys) - margin,
        max(xs) + margin,
        max(ys) + margin,
    )


def rotated_rect_bounds(
    x: float,
    y: float,
    width: float,
    height: float,
    rotation_deg: float,
) -> Bounds:
    """
    Exact axis-aligned bounds of the rectangle after rotating it around its
    center, matching the current ImageElement renderer.
    """
    if not rotation_deg:
        return (
            x,
            y,
            x + width,
            y + height,
        )

    center_x = x + width / 2.0
    center_y = y + height / 2.0

    angle = math.radians(rotation_deg)
    cosine = math.cos(angle)
    sine = math.sin(angle)

    corners = (
        (x, y),
        (x + width, y),
        (x + width, y + height),
        (x, y + height),
    )

    rotated: list[tuple[float, float]] = []

    for corner_x, corner_y in corners:
        dx = corner_x - center_x
        dy = corner_y - center_y
        rotated.append((
            center_x + dx * cosine - dy * sine,
            center_y + dx * sine + dy * cosine,
        ))

    xs = [point[0] for point in rotated]
    ys = [point[1] for point in rotated]

    return (
        min(xs),
        min(ys),
        max(xs),
        max(ys),
    )


def legacy_text_bounds(
    old: dict[str, Any],
    *,
    cell_size: float,
    old_point_dpi: float,
) -> Bounds:
    """
    Estimate the same conservative bounds used by the current TextElement.

    The legacy renderer stores `pos` as the text baseline. The current renderer
    stores the top-left position, so this mirrors convert_texts().
    """
    text = str(old.get("text") or "")
    old_font_pt = max(
        1.0,
        as_float(
            old.get("font_size"),
            14.0,
        ),
    )
    font_px = old_font_pt * (
        old_point_dpi / 72.0
    )

    x_cells, y_cells = vec_xy(
        old.get("pos")
    )
    baseline_x = x_cells * cell_size
    baseline_y = y_cells * cell_size

    top_left_x = baseline_x
    top_left_y = baseline_y - (
        font_px * 0.8
    )

    lines = text.split("\\n")
    if not lines:
        lines = [""]

    # This intentionally follows the conservative TextElement.bounds() model,
    # not a platform-dependent font-metric calculation.
    width = max(
        (len(line) for line in lines),
        default=0,
    ) * font_px
    height = max(
        1,
        len(lines),
    ) * font_px * 1.5
    margin = font_px * 0.5

    return (
        top_left_x - margin,
        top_left_y - margin,
        top_left_x + width + margin,
        top_left_y + height + margin,
    )


def legacy_content_bounds(
    data: dict[str, Any],
    *,
    cell_size: float,
    old_width: int,
    old_height: int,
    old_point_dpi: float,
) -> Optional[Bounds]:
    """
    Compute scene bounds from the LEGACY JSON itself.

    This is important: sizing must not depend on whether an external image was
    successfully found during conversion. Even a temporarily missing image still
    contributes its known pos/size/rotation to the required canvas bounds.
    """
    bounds: Optional[Bounds] = None

    background = (
        data.get("background")
        if isinstance(data.get("background"), dict)
        else {}
    )

    # An actual background image is real content and anchors the legacy
    # 0..image_size_px canvas. A null image with image_size_px=4096x4096 does
    # NOT: in old maps this was commonly just a generic editing canvas size.
    if str(background.get("image_asset") or "").strip():
        bounds = union_bounds(
            bounds,
            (
                0.0,
                0.0,
                float(old_width),
                float(old_height),
            ),
        )

    for old in data.get("decor_images") or []:
        if not isinstance(old, dict):
            continue

        x_cells, y_cells = vec_xy(
            old.get("pos")
        )
        x = x_cells * cell_size
        y = y_cells * cell_size

        raw_size = old.get(
            "size_px",
            [128, 128],
        )
        if (
            isinstance(raw_size, (list, tuple))
            and len(raw_size) >= 2
        ):
            width = max(
                0.01,
                as_float(raw_size[0], 128.0),
            )
            height = max(
                0.01,
                as_float(raw_size[1], 128.0),
            )
        else:
            width = height = 128.0

        bounds = union_bounds(
            bounds,
            rotated_rect_bounds(
                x,
                y,
                width,
                height,
                as_float(
                    old.get("rotation_deg"),
                    0.0,
                ),
            ),
        )

    for old in data.get("decor_drawings") or []:
        if not isinstance(old, dict):
            continue

        raw_points = old.get("points") or []
        if not isinstance(raw_points, list):
            continue

        points = [
            world_to_pixels(
                point,
                cell_size,
            )
            for point in raw_points
        ]

        stroke_margin = max(
            0.0,
            as_float(
                old.get("stroke_width"),
                2.0,
            ),
        ) / 2.0 + 1.0

        bounds = union_bounds(
            bounds,
            points_bounds(
                points,
                stroke_margin,
            ),
        )

    for old in data.get("decor_texts") or []:
        if not isinstance(old, dict):
            continue

        bounds = union_bounds(
            bounds,
            legacy_text_bounds(
                old,
                cell_size=cell_size,
                old_point_dpi=old_point_dpi,
            ),
        )

    for old in data.get("debug_overlays") or []:
        if not isinstance(old, dict):
            continue

        raw_points = old.get("points") or []
        if not isinstance(raw_points, list):
            continue

        points = [
            world_to_pixels(
                point,
                cell_size,
            )
            for point in raw_points
        ]

        bounds = union_bounds(
            bounds,
            points_bounds(
                points,
                2.0,
            ),
        )

    for old in data.get("pnj_tokens") or []:
        if not isinstance(old, dict):
            continue

        center_x_cells, center_y_cells = vec_xy(
            old.get("pos")
        )
        center_x = center_x_cells * cell_size
        center_y = center_y_cells * cell_size

        size = max(
            0.01,
            as_float(
                old.get("size_cells"),
                1.0,
            ),
        ) * cell_size
        half = size / 2.0

        bounds = union_bounds(
            bounds,
            (
                center_x - half,
                center_y - half,
                center_x + half,
                center_y + half,
            ),
        )

    return bounds


def ceil_to_grid(
    value: float,
    cell_size: float,
) -> int:
    return max(
        1,
        int(
            math.ceil(
                max(1.0, value)
                / cell_size
            )
            * cell_size
        ),
    )


def compute_autofit(
    data: dict[str, Any],
    *,
    old_width: int,
    old_height: int,
    cell_size: float,
    old_point_dpi: float,
    margin_cells: float,
) -> tuple[int, int, float, float, Optional[Bounds]]:
    """
    Return:
        final_width, final_height, shift_x, shift_y, original_content_bounds

    No background image:
        tightly crop around real content + margin.

    Real background image:
        keep the original background canvas intact and only EXPAND it if
        content extends outside it. This prevents stretching/cropping the old
        background composition.
    """
    bounds = legacy_content_bounds(
        data,
        cell_size=cell_size,
        old_width=old_width,
        old_height=old_height,
        old_point_dpi=old_point_dpi,
    )

    if bounds is None:
        return (
            ceil_to_grid(
                old_width,
                cell_size,
            ),
            ceil_to_grid(
                old_height,
                cell_size,
            ),
            0.0,
            0.0,
            None,
        )

    min_x, min_y, max_x, max_y = bounds
    margin = max(
        0.0,
        float(margin_cells),
    ) * cell_size

    background = (
        data.get("background")
        if isinstance(data.get("background"), dict)
        else {}
    )
    has_background_image = bool(
        str(
            background.get("image_asset")
            or ""
        ).strip()
    )

    if has_background_image:
        # Preserve [0, 0] as the old background origin whenever possible.
        left = min(
            0.0,
            min_x,
        )
        top = min(
            0.0,
            min_y,
        )
        right = max(
            float(old_width),
            max_x,
        )
        bottom = max(
            float(old_height),
            max_y,
        )

        # Only add safety margin on sides where scene content actually escaped
        # the original background rectangle.
        if min_x < 0.0:
            left -= margin
        if min_y < 0.0:
            top -= margin
        if max_x > old_width:
            right += margin
        if max_y > old_height:
            bottom += margin

    else:
        # Generic image_size_px was merely the old editor canvas. Crop around
        # the actual scene instead.
        left = min_x - margin
        top = min_y - margin
        right = max_x + margin
        bottom = max_y + margin

    shift_x = -left
    shift_y = -top

    final_width = ceil_to_grid(
        right - left,
        cell_size,
    )
    final_height = ceil_to_grid(
        bottom - top,
        cell_size,
    )

    return (
        final_width,
        final_height,
        shift_x,
        shift_y,
        bounds,
    )


def shift_elements(
    elements: list[dict[str, Any]],
    shift_x: float,
    shift_y: float,
) -> None:
    if not shift_x and not shift_y:
        return

    for element in elements:
        kind = str(
            element.get("type")
            or ""
        )

        if kind in {
            "image",
            "text",
        }:
            pos = element.get("pos")
            if isinstance(pos, list) and len(pos) >= 2:
                pos[0] = float(pos[0]) + shift_x
                pos[1] = float(pos[1]) + shift_y

        elif kind == "drawing":
            points = element.get("points")
            if not isinstance(points, list):
                continue

            for point in points:
                if (
                    isinstance(point, list)
                    and len(point) >= 2
                ):
                    point[0] = float(point[0]) + shift_x
                    point[1] = float(point[1]) + shift_y


def shift_tokens(
    tokens: list[dict[str, Any]],
    shift_x: float,
    shift_y: float,
) -> None:
    if not shift_x and not shift_y:
        return

    for token in tokens:
        pos = token.get("pos")
        if isinstance(pos, list) and len(pos) >= 2:
            pos[0] = float(pos[0]) + shift_x
            pos[1] = float(pos[1]) + shift_y


def shifted_grid_offset(
    shift_x: float,
    shift_y: float,
    cell_size: float,
) -> list[float]:
    """
    Moving all scene content also moves the old grid relative to the scene.
    Preserve that relationship in the current GridSettings.
    """
    if cell_size <= 0:
        return [0.0, 0.0]

    return [
        shift_x % cell_size,
        shift_y % cell_size,
    ]


def convert_background(
    data: dict[str, Any],
    *,
    source_json: Path,
    map_dir: Path,
    resolver: AssetResolver,
    report: ConversionReport,
    dry_run: bool,
) -> dict[str, Any]:
    """
    Current MapBackground stretches its image to the FULL map rectangle.

    Because V3 can enlarge the map to fit outlying elements, retaining the old
    image here would deform it. The legacy background image is therefore
    converted separately into a locked ImageElement of its original pixel size.
    """
    old = (
        data.get("background")
        if isinstance(data.get("background"), dict)
        else {}
    )

    return {
        "color": color_or_default(
            old.get("color"),
            "#1e1e2e",
        ),
        "image_asset": None,
    }


def convert_background_image_element(
    data: dict[str, Any],
    *,
    old_width: int,
    old_height: int,
    source_json: Path,
    map_dir: Path,
    resolver: AssetResolver,
    report: ConversionReport,
    dry_run: bool,
) -> Optional[dict[str, Any]]:
    old = (
        data.get("background")
        if isinstance(data.get("background"), dict)
        else {}
    )

    raw_asset = old.get(
        "image_asset"
    )
    if not str(
        raw_asset or ""
    ).strip():
        return None

    asset = migrate_asset(
        raw_asset,
        source_json=source_json,
        map_dir=map_dir,
        resolver=resolver,
        report=report,
        dry_run=dry_run,
        context="background",
    )
    if asset is None:
        return None

    return {
        "type": "image",
        "id": (
            "legacy_background_"
            + uuid4().hex
        ),
        "visible": True,
        "locked": True,
        "opacity": 1.0,
        "asset": asset,
        "pos": [0.0, 0.0],
        "width": float(old_width),
        "height": float(old_height),
        "rotation": 0.0,
    }


def convert_grid(
    data: dict[str, Any],
    cell_size: float,
    *,
    offset: Optional[list[float]] = None,
) -> dict[str, Any]:
    old = (
        data.get("grid")
        if isinstance(data.get("grid"), dict)
        else {}
    )

    return {
        "enabled": as_bool(
            old.get("enabled"),
            True,
        ),
        "cell_size": max(1, round(cell_size)),
        "offset": (
            list(offset)
            if offset is not None
            else [0.0, 0.0]
        ),
        "color": color_or_default(
            old.get("color"),
            "#4a4a4a",
        ),
        "opacity": as_float(
            old.get("opacity"),
            0.35,
        ),
    }


def convert_images(
    data: dict[str, Any],
    *,
    cell_size: float,
    source_json: Path,
    map_dir: Path,
    resolver: AssetResolver,
    report: ConversionReport,
    dry_run: bool,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    rows = data.get("decor_images") or []
    if not isinstance(rows, list):
        report.warn("decor_images n'est pas une liste; ignoré.")
        return result

    for old in rows:
        if not isinstance(old, dict):
            continue

        element_id = str(
            old.get("id")
            or uuid4().hex
        )

        asset = migrate_asset(
            old.get("asset"),
            source_json=source_json,
            map_dir=map_dir,
            resolver=resolver,
            report=report,
            dry_run=dry_run,
            context=f"decor image {element_id}",
        )

        # Do not create a broken ImageElement when its source image really
        # cannot be found; keep a warning instead.
        if asset is None:
            continue

        raw_size = old.get(
            "size_px",
            [128, 128],
        )
        if (
            isinstance(raw_size, (list, tuple))
            and len(raw_size) >= 2
        ):
            width = max(
                0.01,
                as_float(raw_size[0], 128.0),
            )
            height = max(
                0.01,
                as_float(raw_size[1], 128.0),
            )
        else:
            width = height = 128.0

        result.append({
            "type": "image",
            "id": element_id,
            "visible": True,
            "locked": as_bool(
                old.get("locked"),
                False,
            ),
            "opacity": 1.0,
            "asset": asset,

            # CRITICAL: old position is expressed in grid cells.
            "pos": world_to_pixels(
                old.get("pos"),
                cell_size,
            ),

            # size_px is already pixels; do NOT multiply it.
            "width": width,
            "height": height,
            "rotation": as_float(
                old.get("rotation_deg"),
                0.0,
            ),
        })

    report.images = len(result)
    return result


def convert_drawings(
    data: dict[str, Any],
    *,
    cell_size: float,
    report: ConversionReport,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    rows = data.get("decor_drawings") or []
    if not isinstance(rows, list):
        report.warn("decor_drawings n'est pas une liste; ignoré.")
        return result

    valid_tools = {
        "freehand",
        "line",
        "rect",
        "circle",
    }

    for old in rows:
        if not isinstance(old, dict):
            continue

        tool = str(
            old.get("tool")
            or "freehand"
        ).strip().casefold()

        if tool not in valid_tools:
            report.warn(
                f"Drawing {old.get('id')}: outil inconnu {tool!r}; ignoré."
            )
            continue

        raw_points = old.get("points") or []
        if not isinstance(raw_points, list):
            continue

        points = [
            world_to_pixels(
                point,
                cell_size,
            )
            for point in raw_points
        ]

        if not points:
            continue

        result.append({
            "type": "drawing",
            "id": str(
                old.get("id")
                or uuid4().hex
            ),
            "visible": True,
            "locked": False,
            "opacity": 1.0,
            "tool": tool,
            "points": points,
            "stroke_color": color_or_default(
                old.get("stroke_color"),
                "#f9e2af",
            ),

            # Old QPen width was already a screen-pixel value at 100% zoom.
            # Keeping it unchanged is the closest visual mapping.
            "stroke_width": max(
                0.0,
                as_float(
                    old.get("stroke_width"),
                    2.0,
                ),
            ),
            "fill_color": (
                str(old.get("fill_color"))
                if old.get("fill_color") not in (None, "")
                else None
            ),
        })

    report.drawings = len(result)
    return result


def convert_texts(
    data: dict[str, Any],
    *,
    cell_size: float,
    report: ConversionReport,
    old_point_dpi: float,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    rows = data.get("decor_texts") or []
    if not isinstance(rows, list):
        report.warn("decor_texts n'est pas une liste; ignoré.")
        return result

    for old in rows:
        if not isinstance(old, dict):
            continue

        old_font_pt = max(
            1.0,
            as_float(
                old.get("font_size"),
                14.0,
            ),
        )

        # Old renderer used QFont.setPointSize().
        # New renderer uses QFont.setPixelSize().
        font_px = old_font_pt * (
            old_point_dpi / 72.0
        )

        x_cells, y_cells = vec_xy(
            old.get("pos")
        )
        baseline_x = x_cells * cell_size
        baseline_y = y_cells * cell_size

        # Old drawText(QPointF, text) used pos as the text baseline.
        # New TextElement.pos is top-left. Without importing Qt/font metrics in
        # this standalone converter, 0.8 * font_px is a close ascent estimate.
        top_left = [
            baseline_x,
            baseline_y - (font_px * 0.8),
        ]

        result.append({
            "type": "text",
            "id": str(
                old.get("id")
                or uuid4().hex
            ),
            "visible": True,
            "locked": False,
            "opacity": 1.0,
            "text": str(
                old.get("text")
                or ""
            ),
            "pos": top_left,
            "font_size": font_px,
            "font_color": color_or_default(
                old.get("color"),
                "#f9e2af",
            ),
            "font_family": None,
            "rotation": 0.0,
            "bold": False,
            "italic": False,
            "underline": False,
        })

    report.texts = len(result)
    return result


def convert_debug_overlays(
    data: dict[str, Any],
    *,
    cell_size: float,
    report: ConversionReport,
) -> list[dict[str, Any]]:
    """
    Current MapAsset has no persisted debug-overlay layer.

    Instead of silently losing a visible old overlay, convert it to a regular
    DrawingElement. This preserves appearance in the static map.
    """
    result: list[dict[str, Any]] = []

    rows = data.get("debug_overlays") or []
    if not isinstance(rows, list):
        return result

    for old in rows:
        if not isinstance(old, dict):
            continue

        raw_points = old.get("points") or []
        if not isinstance(raw_points, list):
            continue

        points = [
            world_to_pixels(
                point,
                cell_size,
            )
            for point in raw_points
        ]

        if not points:
            continue

        result.append({
            "type": "drawing",
            "id": str(
                old.get("id")
                or uuid4().hex
            ),
            "visible": True,
            "locked": False,
            "opacity": 1.0,
            "tool": (
                "line"
                if len(points) == 2
                else "freehand"
            ),
            "points": points,
            "stroke_color": color_or_default(
                old.get("color"),
                "#89b4fa",
            ),
            "stroke_width": 2.0,
            "fill_color": None,
        })

    report.debug_overlays = len(result)
    return result


def convert_tokens(
    data: dict[str, Any],
    *,
    cell_size: float,
    entity_store: EntityStore,
    report: ConversionReport,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    rows = data.get("pnj_tokens") or []
    if not isinstance(rows, list):
        report.warn("pnj_tokens n'est pas une liste; ignoré.")
        return result

    for old in rows:
        if not isinstance(old, dict):
            continue

        entity_id = entity_store.create_from_legacy_token(
            old,
            report,
        )
        if entity_id is None:
            continue

        result.append({
            "id": str(
                old.get("id")
                or uuid4().hex
            ),
            "entity_id": entity_id,

            # Old TokenData.pos is already the center in grid/world cells.
            "pos": world_to_pixels(
                old.get("pos"),
                cell_size,
            ),
            "size_cells": max(
                0.01,
                as_float(
                    old.get("size_cells"),
                    1.0,
                ),
            ),
            "rotation": 0.0,
            "opacity": 1.0,
            "hidden": not as_bool(
                old.get("visible"),
                True,
            ),
        })

    report.tokens = len(result)
    return result


def convert_map(
    source_json: Path,
    target_campaign: Path,
    *,
    resolver: AssetResolver,
    entity_store: EntityStore,
    dry_run: bool,
    overwrite: bool,
    old_point_dpi: float,
    margin_cells: float,
) -> ConversionReport:
    old = load_json(source_json)

    # Strict legacy signature: prevents accidentally feeding the converter a
    # current MapAsset JSON.
    if not any(
        key in old
        for key in (
            "decor_images",
            "decor_drawings",
            "decor_texts",
            "pnj_tokens",
        )
    ):
        raise ValueError(
            f"{source_json} ne ressemble pas à une ancienne map "
            "(decor_images/decor_drawings/decor_texts/pnj_tokens absents)."
        )

    width, height, cell_size = old_map_geometry(
        old
    )

    map_name = source_json.stem
    map_dir = (
        target_campaign
        / "maps"
        / safe_name(map_name, "Map")
    )
    destination = map_dir / MAP_FILE

    report = ConversionReport(
        source=source_json,
        destination=destination,
        old_canvas_size=(
            width,
            height,
        ),
    )

    if destination.exists() and not overwrite:
        raise FileExistsError(
            f"{destination} existe déjà. Utilise --overwrite pour le remplacer."
        )

    background_element = convert_background_image_element(
        old,
        old_width=width,
        old_height=height,
        source_json=source_json,
        map_dir=map_dir,
        resolver=resolver,
        report=report,
        dry_run=dry_run,
    )

    images = convert_images(
        old,
        cell_size=cell_size,
        source_json=source_json,
        map_dir=map_dir,
        resolver=resolver,
        report=report,
        dry_run=dry_run,
    )
    drawings = convert_drawings(
        old,
        cell_size=cell_size,
        report=report,
    )
    texts = convert_texts(
        old,
        cell_size=cell_size,
        report=report,
        old_point_dpi=old_point_dpi,
    )
    debug_drawings = convert_debug_overlays(
        old,
        cell_size=cell_size,
        report=report,
    )

    tokens = convert_tokens(
        old,
        cell_size=cell_size,
        entity_store=entity_store,
        report=report,
    )

    elements = (
        (
            [background_element]
            if background_element is not None
            else []
        )
        + images
        + drawings
        + texts
        + debug_drawings
    )

    (
        final_width,
        final_height,
        shift_x,
        shift_y,
        content_bounds,
    ) = compute_autofit(
        old,
        old_width=width,
        old_height=height,
        cell_size=cell_size,
        old_point_dpi=old_point_dpi,
        margin_cells=margin_cells,
    )

    shift_elements(
        elements,
        shift_x,
        shift_y,
    )
    shift_tokens(
        tokens,
        shift_x,
        shift_y,
    )

    report.content_bounds = content_bounds
    report.applied_shift = (
        shift_x,
        shift_y,
    )
    report.final_map_size = (
        final_width,
        final_height,
    )

    result = {
        "version": FORMAT_VERSION,
        "id": uuid4().hex,
        # Use the JSON filename. scene_name was often just "Main Scene".
        "name": map_name,
        "size": [
            final_width,
            final_height,
        ],
        "background": convert_background(
            old,
            source_json=source_json,
            map_dir=map_dir,
            resolver=resolver,
            report=report,
            dry_run=dry_run,
        ),
        "grid": convert_grid(
            old,
            cell_size,
            offset=shifted_grid_offset(
                shift_x,
                shift_y,
                cell_size,
            ),
        ),

        # Old renderer order was:
        #   background image -> images -> drawings -> texts.
        "elements": elements,
        "initial_tokens": tokens,
        "initial_gm_camera": {
            "center": [
                final_width / 2.0,
                final_height / 2.0,
            ],
            "zoom": 1.0,
        },
    }

    if destination.exists() and overwrite and not dry_run:
        backup = destination.with_name(
            "map.before_legacy_conversion.json"
        )
        if not backup.exists():
            shutil.copy2(
                destination,
                backup,
            )

    write_json(
        destination,
        result,
        dry_run,
    )

    return report


# =============================================================================
# CLI
# =============================================================================


def discover_source_maps(
    source: Path,
) -> list[Path]:
    if source.is_file():
        return [source]

    if not source.is_dir():
        raise FileNotFoundError(
            f"Source introuvable: {source}"
        )

    # Old maps were direct *.json files in maps/.
    return sorted(
        path
        for path in source.glob("*.json")
        if path.is_file()
    )


def print_report(
    report: ConversionReport,
    target_campaign: Path,
) -> None:
    try:
        destination = report.destination.relative_to(
            target_campaign
        )
    except ValueError:
        destination = report.destination

    print(f"  -> {destination}")
    print(
        "     "
        f"{report.images} images, "
        f"{report.drawings} dessins, "
        f"{report.texts} textes, "
        f"{report.debug_overlays} overlays, "
        f"{report.tokens} tokens"
    )

    print(
        "     taille canvas legacy : "
        f"{report.old_canvas_size[0]}x{report.old_canvas_size[1]} px"
    )
    print(
        "     taille MapAsset finale: "
        f"{report.final_map_size[0]}x{report.final_map_size[1]} px"
    )

    if report.content_bounds is not None:
        min_x, min_y, max_x, max_y = report.content_bounds
        print(
            "     bounds contenu       : "
            f"({min_x:.1f}, {min_y:.1f}) -> "
            f"({max_x:.1f}, {max_y:.1f})"
        )

    shift_x, shift_y = report.applied_shift
    if shift_x or shift_y:
        print(
            "     décalage auto-fit    : "
            f"({shift_x:.1f}, {shift_y:.1f}) px"
        )

    if report.media_copied:
        print(
            f"     {len(report.media_copied)} média(s) importé(s)"
        )

    if report.entities_created:
        print(
            f"     {len(report.entities_created)} Entity(s) créée(s)"
        )

    for warning in report.warnings:
        print(
            f"     WARNING: {warning}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convertit les anciennes maps JSON monolithiques "
            "JDR-To-Aru vers le MapAsset actuel de RPG Tabletop App."
        )
    )

    parser.add_argument(
        "source",
        help=(
            "Ancien fichier map JSON ou ancien dossier maps/."
        ),
    )
    parser.add_argument(
        "target_campaign",
        help=(
            "Racine de la campagne RPG Tabletop App "
            "(contient characters/, entities/, maps/)."
        ),
    )
    parser.add_argument(
        "--legacy-root",
        help=(
            "Racine de l'ancien projet. Sert à résoudre les chemins "
            "assets/images enregistrés dans les JSON."
        ),
    )
    parser.add_argument(
        "--asset-search-root",
        action="append",
        default=[],
        help=(
            "Dossier supplémentaire où rechercher les images. "
            "Option répétable."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyse/conversion simulée sans écrire de fichier.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Remplace map.json si la destination existe déjà.",
    )
    parser.add_argument(
        "--old-point-dpi",
        type=float,
        default=96.0,
        help=(
            "DPI utilisés pour convertir les anciens font_size Qt "
            "en pixels (défaut: 96)."
        ),
    )

    parser.add_argument(
        "--margin-cells",
        type=float,
        default=1.0,
        help=(
            "Marge ajoutée autour du contenu lors de l'auto-fit, "
            "en nombre de cellules (défaut: 1)."
        ),
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    source = Path(
        args.source
    ).expanduser().resolve()

    target_campaign = Path(
        args.target_campaign
    ).expanduser().resolve()

    if not target_campaign.is_dir():
        print(
            f"ERROR: campagne cible introuvable: {target_campaign}",
            file=sys.stderr,
        )
        return 2

    # Expected current campaign folders.
    for folder in (
        target_campaign / "maps",
        target_campaign / "entities",
    ):
        if not args.dry_run:
            folder.mkdir(
                parents=True,
                exist_ok=True,
            )

    legacy_root = (
        Path(args.legacy_root).expanduser().resolve()
        if args.legacy_root
        else (
            source.parent.parent
            if source.is_file() and source.parent.name.casefold() == "maps"
            else (
                source.parent
                if source.is_dir() and source.name.casefold() == "maps"
                else None
            )
        )
    )

    extra_roots = [
        Path(value).expanduser().resolve()
        for value in args.asset_search_root
    ]

    resolver = AssetResolver(
        legacy_root,
        extra_roots,
    )
    entity_store = EntityStore(
        target_campaign,
        dry_run=args.dry_run,
    )

    try:
        sources = discover_source_maps(
            source
        )
    except Exception as error:
        print(
            f"ERROR: {error}",
            file=sys.stderr,
        )
        return 2

    if not sources:
        print(
            "Aucune ancienne map JSON trouvée."
        )
        return 0

    print(
        f"Source : {source}"
    )
    print(
        f"Campagne cible : {target_campaign}"
    )
    print(
        f"Ancienne racine : {legacy_root or '(non définie)'}"
    )
    if args.dry_run:
        print(
            "DRY RUN : aucun fichier ne sera écrit."
        )
    print()

    success = 0
    failure = 0

    for source_json in sources:
        print(
            f"[MAP] {source_json.name}"
        )

        try:
            report = convert_map(
                source_json,
                target_campaign,
                resolver=resolver,
                entity_store=entity_store,
                dry_run=args.dry_run,
                overwrite=args.overwrite,
                old_point_dpi=max(
                    1.0,
                    float(args.old_point_dpi),
                ),
                margin_cells=max(
                    0.0,
                    float(args.margin_cells),
                ),
            )
        except Exception as error:
            failure += 1
            print(
                f"  ERROR: {error}"
            )
            print()
            continue

        success += 1
        print_report(
            report,
            target_campaign,
        )
        print()

    print(
        f"Terminé : {success} map(s) convertie(s), "
        f"{failure} erreur(s)."
    )

    if success and not args.dry_run:
        print(
            "\nOuvre ensuite les maps dans le nouvel éditeur et sauvegarde-les "
            "pour générer tiles/ et preview.png."
        )

    return 1 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
