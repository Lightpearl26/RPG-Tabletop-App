#-*- coding: utf-8 -*-

"""
Duplicate and delete campaign resources.

A resource is a JSON file (characters, entities, items, spells) or a folder
(maps). Sessions are folders too but live in `libs.runtime.session_store`.

Files stored next to a resource (the `tokens` folder) follow it, and the changes
are applied together: when one of them fails, everything is put back.

Deleting goes through the reference module, so no asset keeps pointing at a
resource that no longer exists. A character cannot be deleted while an entity is
based on it, since an entity without a base character is invalid.
"""


# import built-in modules
from __future__ import annotations
import json
import shutil
from pathlib import Path
from uuid import uuid4

# import local modules
from .attachments import TOKEN_FOLDER, FileChange, apply_changes, restore_changes
from .campaign import Campaign
from .references import (
    RESOURCE_DIRECTORIES,
    ReferenceUpdate,
    apply_updates,
    canonical_name,
    json_text,
    plan_delete,
    revert_updates,
    write_text_atomic,
)


INVALID_FILENAME_CHARACTERS = '<>:"/\\|?*'
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


# =============================================================================
# Paths and names
# =============================================================================


def _directory(campaign: Campaign, resource_type: str) -> Path:
    if resource_type in RESOURCE_DIRECTORIES:
        return getattr(campaign, RESOURCE_DIRECTORIES[resource_type])
    if resource_type == "maps":
        return campaign.maps_path
    raise ValueError(f"Unsupported resource type: {resource_type!r}")


def resource_path(campaign: Campaign, resource_type: str, resource_id: str) -> Path:
    """
    Return the file (or the folder, for a map) of a resource.

    Raises:
        ValueError: If `resource_id` is not a plain file or folder name.
    """
    if not resource_id or Path(resource_id).name != resource_id:
        raise ValueError("A resource id must be a file or folder name.")
    directory = _directory(campaign, resource_type)
    return directory / (resource_id if resource_type == "maps" else f"{resource_id}.json")


def _safe_folder_name(name: str) -> str:
    cleaned = "".join("_" if c in INVALID_FILENAME_CHARACTERS else c for c in name)
    cleaned = " ".join(cleaned.strip().rstrip(". ").split())
    if not cleaned:
        cleaned = "New Map"
    if cleaned.upper() in RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def _clean_name(name: str, resource_type: str) -> str:
    name = " ".join(str(name).split())
    if not name:
        raise ValueError("The name cannot be empty.")
    if resource_type != "maps" and any(c in INVALID_FILENAME_CHARACTERS for c in name):
        raise ValueError("The name contains a character invalid in file names.")
    return name


def _target_path(campaign: Campaign, resource_type: str, name: str) -> Path:
    directory = _directory(campaign, resource_type)
    if resource_type == "maps":
        return directory / _safe_folder_name(name)
    return directory / f"{canonical_name(name)}.json"


def _display_name(path: Path, resource_type: str, resource_id: str) -> str:
    file = path / "map.json" if resource_type == "maps" else path
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    name = data.get("name") if isinstance(data, dict) else None
    return str(name).strip() if name else resource_id.replace("_", " ")


def suggest_copy_name(campaign: Campaign, resource_type: str, resource_id: str) -> str:
    """
    Return `<name> (copy)`, or `<name> (copy 2)`... the first one no resource uses.
    """
    base = _display_name(
        resource_path(campaign, resource_type, resource_id), resource_type, resource_id
    )
    candidate, index = f"{base} (copy)", 2
    while _target_path(campaign, resource_type, candidate).exists():
        candidate = f"{base} (copy {index})"
        index += 1
    return candidate


# =============================================================================
# Attachments
# =============================================================================


def _token_files(directory: Path, stem: str) -> list[Path]:
    """
    The files of the `tokens` folder that belong to the resource called `stem`.

    Names are compared once canonicalized, so a token saved under the display name
    ("Goblin Chief.png") is found as well as one saved under the file stem.
    """
    folder = directory / TOKEN_FOLDER
    if not folder.is_dir():
        return []
    key = canonical_name(stem)
    return [
        path for path in folder.iterdir()
        if path.is_file() and canonical_name(path.stem) == key
    ]


# =============================================================================
# Duplicate
# =============================================================================


def duplicate_resource(
    campaign: Campaign,
    resource_type: str,
    resource_id: str,
    new_name: str,
) -> Path:
    """
    Copy a resource under a new name and return the path of the copy.

    - An entity gets a new id (tokens point at the id) and no runtime state.
    - A map gets a new id; its compiled tiles are copied as they are.
    - Nothing points at the copy yet, so no reference has to change.

    Raises:
        FileNotFoundError: If the resource does not exist.
        FileExistsError: If the new name is already used.
        ValueError: If the name is not valid or the resource cannot be read.
    """
    source = resource_path(campaign, resource_type, resource_id)
    if not source.exists():
        raise FileNotFoundError(f"Resource not found: {source}")
    name = _clean_name(new_name, resource_type)
    target = _target_path(campaign, resource_type, name)
    if target.exists():
        raise FileExistsError(f"A resource already uses this name: {target}")

    if resource_type == "maps":
        _duplicate_map(source, target, name)
    else:
        _duplicate_file(resource_type, source, target, name)
    return target


def _duplicate_file(resource_type: str, source: Path, target: Path, name: str) -> None:
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{source.name} must contain a JSON object.")
    data["name"] = name
    if resource_type == "entities":
        data["id"] = str(uuid4())
        data["spell_events"] = []
        data["spell_effects"] = []

    tokens = [
        FileChange(
            path.with_name(
                f"{target.stem if path.stem == canonical_name(path.stem) else name}{path.suffix}"
            ),
            path.read_bytes(),
        )
        for path in _token_files(source.parent, source.stem)
    ]
    write_text_atomic(target, json_text(data))
    try:
        apply_changes(tokens)
    except OSError:
        target.unlink(missing_ok=True)
        raise


def _duplicate_map(source: Path, target: Path, name: str) -> None:
    staging = target.with_name(f".{target.name}.tmp")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        shutil.copytree(source, staging)
        map_file = staging / "map.json"
        data = json.loads(map_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("map.json must contain a JSON object.")
        new_id = uuid4().hex
        data["id"] = new_id
        data["name"] = name
        compiled = data.get("compiled")
        if isinstance(compiled, dict):
            compiled["source_map_id"] = new_id
        write_text_atomic(map_file, json_text(data))
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


# =============================================================================
# Delete
# =============================================================================


def delete_impact(
    campaign: Campaign,
    resource_type: str,
    resource_id: str,
) -> list[ReferenceUpdate]:
    """
    Return the reference rewrites a deletion would make, without writing anything.

    Raises:
        ValueError: If the resource cannot be deleted because others depend on it.
    """
    path = resource_path(campaign, resource_type, resource_id)
    updates = plan_delete(campaign, resource_type, resource_id, exclude=path)
    if resource_type == "characters" and updates:
        users = ", ".join(sorted(update.path.stem for update in updates))
        raise ValueError(
            f"This character is the base of entities ({users}): "
            "delete them or change their base character first."
        )
    return updates


def _remove(path: Path) -> None:
    if path.is_dir():
        # Moved aside first: a failure leaves the map where it was
        trash = path.with_name(f".{path.name}.deleted")
        shutil.rmtree(trash, ignore_errors=True)
        path.rename(trash)
        shutil.rmtree(trash, ignore_errors=True)
    else:
        path.unlink()


def delete_resource(
    campaign: Campaign,
    resource_type: str,
    resource_id: str,
) -> list[ReferenceUpdate]:
    """
    Delete a resource, its tokens, and the references other assets hold to it.

    Returns:
        list[ReferenceUpdate]: The files whose references were removed.

    Raises:
        FileNotFoundError: If the resource does not exist.
        ValueError: If others depend on it (see `delete_impact`).
        OSError: If a change fails. Everything already changed is restored.
    """
    path = resource_path(campaign, resource_type, resource_id)
    if not path.exists():
        raise FileNotFoundError(f"Resource not found: {path}")
    updates = delete_impact(campaign, resource_type, resource_id)
    deletions = (
        []
        if resource_type == "maps"
        else [FileChange(token, None) for token in _token_files(path.parent, resource_id)]
    )

    apply_updates(updates)
    previous: list[FileChange] = []
    try:
        previous = apply_changes(deletions)
        _remove(path)
    except OSError:
        restore_changes(previous)
        revert_updates(updates)
        raise
    return updates
