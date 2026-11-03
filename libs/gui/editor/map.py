# -*- coding: utf-8 -*-
"""
IDE-integrated map resource editor.

This is the only map-editor class the Campaign Manager needs to import.
The heavy interactive implementation lives in the sibling map_* modules.
"""

from __future__ import annotations

import shutil
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QInputDialog,
    QMessageBox,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from ...assets.campaign import Campaign
from ...assets.map import MapAsset, MediaCache
from .base import ResourceEditor
from .map_workspace import MapWorkspace


_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _safe_map_folder_name(name: str) -> str:
    invalid = '<>:"/\\|?*'
    cleaned = "".join(
        "_" if character in invalid else character
        for character in str(name)
    )
    cleaned = " ".join(cleaned.strip().rstrip(". ").split())
    if not cleaned:
        cleaned = "New Map"
    if cleaned.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


class MapEditor(ResourceEditor):
    """
    Map ResourceEditor compatible with CampaignManagerWindow.

    Unlike ordinary resources, a map is a DIRECTORY containing map.json,
    media/, tiles/ and preview.png. This class therefore overrides ResourceEditor
    persistence while keeping its signals / modified-state contract.

    Existing map:
        resource_id == map folder name

    New map:
        a private .map_drafts folder is used while editing, so images can already
        be imported without making an unfinished map visible in the browser.
        The draft is moved into campaign/maps on the first save.
    """

    resource_label = "Map"
    resource_type = "maps"

    # Optional hook for a future IDE-level "open referenced resource" connection.
    resource_open_requested = Signal(str, str)

    def __init__(
        self,
        resource_id: str = "",
        campaign: Campaign | None = None,
        *,
        is_new: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        self.asset: MapAsset
        self.map_dir: Path | None = None
        self._draft_dir: Path | None = None

        super().__init__(
            resource_id,
            campaign,
            is_new=is_new,
            parent=parent,
        )

        if is_new:
            self._draft_dir = (
                self.campaign.root
                / ".map_drafts"
                / uuid4().hex
            )
            self.map_dir = self._draft_dir

        self.workspace = MapWorkspace(
            asset=self.asset,
            map_dir=self.map_dir,
            parent=self,
        )
        self.workspace.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.workspace)

        # The workspace's Save button must go through this ResourceEditor so the
        # IDE receives saved/modified_changed and can refresh the browser/tab.

        # Any committed canvas edit may flip ResourceEditor.is_modified.
        self.workspace.canvas.history_changed.connect(
            lambda _undo, _redo: self._refresh_modified()
        )

        self.title = self.asset.name or resource_id or "New Map"

        if not is_new:
            self._mark_clean()
        else:
            # ResourceEditor.is_modified remains True while is_new is True.
            self._baseline = deepcopy(self.asset.to_dict())
            self._reported_modified = True

    @property
    def directory(self) -> Path:
        return self.campaign.maps_path

    # ------------------------------------------------------------------
    # ResourceEditor data contract
    # ------------------------------------------------------------------

    def _new_data(self) -> dict:
        name = str(self.resource_id or "").strip()
        if not name or name.casefold().startswith("new map"):
            name = "New Map"

        self.asset = MapAsset(name=name)
        return deepcopy(self.asset.to_dict())

    def _load(self, resource_id: str) -> None:
        if not resource_id or Path(resource_id).name != resource_id:
            raise ValueError("Map resource ID must be a folder name.")

        map_dir = Path(self.campaign.map_path(resource_id))
        self.asset = MapAsset.load(map_dir)
        self.map_dir = map_dir
        self.file_path = map_dir / "map.json"
        self._data = deepcopy(self.asset.to_dict())

    def _collect_data(self) -> dict:
        return deepcopy(self.asset.to_dict())

    def _populate_fields(self) -> None:
        # This path is primarily used by ResourceEditor.update_references().
        # Map tokens use Entity UUIDs, so resource renames normally do not alter
        # map data, but keeping the method complete makes the contract robust.
        self.asset = MapAsset.from_dict(
            deepcopy(self._data)
        )
        if hasattr(self, "workspace"):
            self.workspace._activate_map(
                self.asset,
                self.map_dir or self._draft_dir or self.campaign.maps_path,
            )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _first_save_name(self) -> str | None:
        suggested = self.asset.name.strip() or "New Map"

        name, accepted = QInputDialog.getText(
            self,
            "Save map",
            "Map name:",
            text=suggested,
        )
        if not accepted:
            return None

        name = name.strip()
        if not name:
            QMessageBox.warning(
                self,
                "Save map",
                "The map name cannot be empty.",
            )
            return None

        return name

    def _target_for_new_map(self, name: str) -> Path:
        folder_name = _safe_map_folder_name(name)
        target = Path(
            self.campaign.map_path(folder_name)
        )

        if target.exists():
            raise FileExistsError(
                f"A map already uses this folder: {target}"
            )

        return target

    def _promote_draft(
        self,
        target: Path,
    ) -> None:
        draft = self._draft_dir

        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if draft is not None and draft.exists():
            shutil.move(
                str(draft),
                str(target),
            )
        else:
            target.mkdir(
                parents=True,
                exist_ok=False,
            )

        self._draft_dir = None
        self.map_dir = target

        # Keep the current undo/selection state; only retarget the media cache.
        self.workspace.map_dir = target
        self.workspace.canvas.map_dir = target
        self.workspace.canvas.media = MediaCache(target)
        self.workspace.canvas.update()

    def save(self) -> Path:
        if self.is_new:
            name = self._first_save_name()
            if name is None:
                # ResourceEditor.save() normally returns Path; cancellation is
                # represented as a controlled ValueError so IDE save handling
                # does not treat an unsaved draft as successfully persisted.
                raise ValueError("Map save cancelled.")

            target = self._target_for_new_map(name)
            self.asset.name = name
            self._promote_draft(target)
        else:
            if self.map_dir is None:
                raise ValueError("Map folder is not available.")
            target = self.map_dir

        try:
            compiled = self.asset.save(target)
        except Exception:
            # If first save failed after the draft was moved, keep map_dir pointed
            # at the target so a second Save can retry without losing imported media.
            raise

        self.map_dir = target
        self.file_path = target / "map.json"
        self.resource_id = target.name
        self.title = self.asset.name
        self.is_new = False
        self._data = deepcopy(self.asset.to_dict())
        self._mark_clean()

        # IMPORTANT: emit the MAP FOLDER, not ".../map.json".
        # CampaignManagerWindow currently derives resource_id with
        # Path(file_path).stem, which then correctly yields the folder name.
        self.saved.emit(
            str(target),
            self.asset.name,
        )

        self.workspace._status_bar.showMessage(
            f"Saved and compiled "
            f"({compiled.columns} x {compiled.rows} tiles).",
            4000,
        )
        return target

    def compile(self) -> None:
        """Explicit Compile button action; does not mark the source as saved."""
        self.workspace.compile_map()

    # ------------------------------------------------------------------
    # IDE Edit menu compatibility
    # ------------------------------------------------------------------

    def undo(self) -> None:
        self.workspace.canvas.undo()

    def redo(self) -> None:
        self.workspace.canvas.redo()

    def cut(self) -> None:
        self.workspace.canvas.cut()

    def copy(self) -> None:
        self.workspace.canvas.copy()

    def paste(self) -> None:
        self.workspace.canvas.paste()

    def selectAll(self) -> None:  # noqa: N802 - Qt API compatibility
        self.workspace.canvas.selectAll()
