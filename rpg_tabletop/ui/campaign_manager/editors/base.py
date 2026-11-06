"""Shared persistence for campaign resource editors."""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from rpg_tabletop.assets.campaign import Campaign, get_current_campaign
from rpg_tabletop.assets.references import (
    ReferenceUpdate,
    apply_updates,
    canonical_name,
    json_text,
    plan_rename,
    rename_in_data,
    revert_updates,
    write_text_atomic,
    delete_in_data,
)


INVALID_FILENAME_CHARACTERS = '<>:"/\\|?*'


class ResourceEditor(QWidget):
    """
    Editor for one campaign resource stored as a single JSON object.

    Renaming a resource rewrites the references to it in every other asset (see
    `rpg_tabletop.assets.references`), so subclasses only have to set `resource_type`.

    Unsaved changes are reported through `is_modified` and `modified_changed`: a
    subclass connects every widget signal that edits the content to
    `_refresh_modified` and calls `_mark_clean` once its widgets are populated.
    """

    saved = Signal(str, str)
    # Previous file stem, new name and the reference updates written to other assets
    renamed = Signal(str, str, object)
    modified_changed = Signal(bool)
    save_requested = Signal()

    resource_label = "Resource"
    resource_type = ""

    def __init__(
        self,
        resource_id: str = "",
        campaign: Campaign | None = None,
        *,
        is_new: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not self.resource_type:
            raise NotImplementedError(
                f"{type(self).__name__} must define resource_type."
            )

        self.campaign = campaign or get_current_campaign()
        self.resource_id = resource_id
        # Name shown on the document tab, without the unsaved marker
        self.title = resource_id
        self.is_new = is_new
        self.file_path: Path | None = None
        self._data: dict = {}
        self._baseline: dict | None = None
        self._reported_modified = False
        self._tracking_paused = False

        if is_new:
            self._data = self._new_data()
        else:
            self._load(resource_id)

    @property
    def directory(self) -> Path:
        """Campaign folder that holds this resource type's JSON files."""
        raise NotImplementedError

    def _new_data(self) -> dict:
        return {}

    def _collect_data(self) -> dict:
        """Return the resource data as currently shown by the widgets."""
        raise NotImplementedError

    def _populate_fields(self) -> None:
        """Fill the widgets from `self._data`, replacing what they currently show."""
        raise NotImplementedError

    @property
    def has_changes(self) -> bool:
        """Whether the widgets differ from the content last loaded or saved."""
        return self._baseline is None or self._collect_data() != self._baseline

    @property
    def is_modified(self) -> bool:
        """Whether the content is not saved to disk; always true before the first save."""
        return self.is_new or self.has_changes

    def _mark_clean(self) -> None:
        """Take the content shown by the widgets as the saved state."""
        self._baseline = self._collect_data()
        self._refresh_modified()

    def _refresh_modified(self, *_args: object) -> None:
        """Emit `modified_changed` when the unsaved state flipped; slot for the edit signals."""
        if self._baseline is None or self._tracking_paused:
            return
        modified = self.is_modified
        if modified != self._reported_modified:
            self._reported_modified = modified
            self.modified_changed.emit(modified)

    def _reload_fields(self) -> None:
        """Refill the widgets from `self._data` without reporting the states in between."""
        self._tracking_paused = True
        try:
            self._populate_fields()
        finally:
            self._tracking_paused = False

    def reload_from_disk(self) -> None:
        """
        Show the file as it is now on disk, dropping what the widgets held.

        Used when something else wrote the file (a runtime saving its session).
        """
        if self.is_new:
            return
        self._load(self.resource_id)
        self._reload_fields()
        self._mark_clean()

    def save(self) -> Path:
        """Write the editor content to disk and return the file path."""
        data = self._collect_data()
        return self._write(data, str(data.get("name", "")))

    def update_references(
        self,
        resource_type: str,
        old_stem: str,
        new_name: str,
    ) -> bool:
        """
        Apply the rename of another resource to the content shown by the widgets.

        This keeps unsaved edits while making sure a later save does not write the
        old name back over the references that were rewritten on disk.

        Returns:
            bool: Whether the content or the saved state referenced the renamed resource.
        """
        data = self._collect_data()
        in_widgets = rename_in_data(self.resource_type, data, resource_type, old_stem, new_name)
        # The file on disk was rewritten too, so the saved state follows the rename
        in_saved_file = self._baseline is not None and rename_in_data(
            self.resource_type, self._baseline, resource_type, old_stem, new_name
        )
        if in_widgets:
            self._data = data
            self._reload_fields()
        self._refresh_modified()
        return bool(in_widgets or in_saved_file)

    def remove_references(
        self,
        resource_type: str,
        old_stem: str,
    ) -> bool:
        """Remove deleted resource references from editor widgets and baseline."""
        data = self._collect_data()

        in_widgets = delete_in_data(
            self.resource_type,
            data,
            resource_type,
            old_stem,
        )

        in_saved = (
            self._baseline is not None
            and delete_in_data(
                self.resource_type,
                self._baseline,
                resource_type,
                old_stem,
            )
        )

        if in_widgets:
            self._data = data
            self._reload_fields()

        self._refresh_modified()
        return bool(in_widgets or in_saved)


    def _load(self, resource_id: str) -> None:
        label = self.resource_label
        if not resource_id or Path(resource_id).name != resource_id:
            raise ValueError(f"{label} resource ID must be a file name.")

        file_path = self.directory / f"{resource_id}.json"
        if not file_path.is_file():
            raise FileNotFoundError(f"{label} file not found: {file_path}")

        data = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{label} file must contain a JSON object: {file_path}")

        self.file_path = file_path
        self._data = data

    def _new_file_path(self, name: str) -> Path:
        filename_stem = name.strip().replace(" ", "_").lower()
        return self.directory / f"{filename_stem}.json"

    def _write(self, data: dict, name: str) -> Path:
        """
        Persist `data` under `name`, renaming the file when the name changed.

        A rename also rewrites the references to the resource in the other assets:
        either everything is written or nothing is.
        """
        label = self.resource_label
        if not name:
            raise ValueError(f"{label} name is required.")
        if any(character in INVALID_FILENAME_CHARACTERS for character in name):
            raise ValueError(f"{label} name contains a character invalid in file names.")

        old_file_path = self.file_path
        new_file_path = self._new_file_path(name)
        target_path = (
            old_file_path
            if old_file_path is not None
            and old_file_path.stem.lower() == new_file_path.stem
            else new_file_path
        )
        is_renaming = old_file_path is not None and target_path != old_file_path
        if target_path.exists() and (self.is_new or is_renaming):
            raise FileExistsError(
                f"A {label.lower()} already uses this file name: {target_path}"
            )

        # References resolve through the file stem, so only a different stem breaks them
        renamed_from = (
            old_file_path.stem
            if old_file_path is not None
            and canonical_name(old_file_path.stem) != canonical_name(target_path.stem)
            else None
        )
        updates: list[ReferenceUpdate] = []
        has_self_reference = False
        if renamed_from is not None:
            has_self_reference = bool(
                rename_in_data(self.resource_type, data, self.resource_type, renamed_from, name)
            )
            updates = plan_rename(
                self.campaign,
                self.resource_type,
                renamed_from,
                name,
                exclude=old_file_path,
            )
        text = json_text(data)

        apply_updates(updates)
        try:
            write_text_atomic(target_path, text)
            if is_renaming and old_file_path is not None:
                old_file_path.unlink()
        except OSError:
            if is_renaming:
                with suppress(OSError):
                    target_path.unlink(missing_ok=True)
            revert_updates(updates)
            raise

        self._data = data
        self.file_path = target_path
        self.resource_id = target_path.stem
        self.title = name
        self.is_new = False
        if has_self_reference:
            self._reload_fields()
        self._mark_clean()
        self.saved.emit(str(target_path), name)
        if renamed_from is not None:
            self.renamed.emit(renamed_from, name, updates)
        return target_path
