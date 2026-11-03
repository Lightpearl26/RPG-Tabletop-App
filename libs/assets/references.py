#-*- coding: utf-8 -*-

"""
Name references between campaign resources.

Resources point at each other by name: a character lists the spells it knows
and the items it carries, for example. Every JSON field that stores the name
of another resource must be declared in REFERENCES, so that renaming that
resource rewrites the field in every asset instead of leaving it dangling.
Resource editors apply this rule automatically when they save a renamed
resource.
"""


# import built-in modules
from __future__ import annotations
import json
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal


if TYPE_CHECKING:
    from .campaign import Campaign


@dataclass(frozen=True)
class Reference:
    """
    A JSON field of `source` resources that stores the names of `target` resources.

    Attributes:
        source (str): The resource type that holds the field.
        target (str): The resource type the stored names point at.
        fields (tuple[str, ...]): The alternative keys the field may be stored under.
        shape (str): "name" for a string, "names" for a list of strings, and "entries"
                     for a list of `[name, quantity]` pairs or a `{name: quantity}`
                     dictionary.
    """
    source: str
    target: str
    fields: tuple[str, ...]
    shape: Literal["name", "names", "entries"]


@dataclass(frozen=True)
class ReferenceUpdate:
    """
    The references rewritten in one file after a resource was renamed.

    Attributes:
        source (str): The resource type of the rewritten file.
        path (Path): The rewritten file.
        count (int): The number of references that were rewritten in it.
        original (str): The file content before the rewrite.
        updated (str): The file content after the rewrite.
    """
    source: str
    path: Path
    count: int
    original: str
    updated: str


REFERENCES = (
    Reference("entities", "spells", ("spells",), "names"),
    Reference("entities", "items", ("items", "inventory"), "entries"),
)

# Resource types stored as one JSON file each, and the Campaign attribute of their folder
RESOURCE_DIRECTORIES = {
    "characters": "character_path",
    "entities": "entity_path",
    "items": "item_path",
    "spells": "spell_path",
}


def canonical_name(name: str) -> str:
    """
    Return the file stem a name resolves to, as the asset loaders compute it.
    """
    return name.replace(" ", "_").lower()


def json_text(data: Any) -> str:
    """
    Serialize resource data the way the editors store it.
    """
    return json.dumps(data, ensure_ascii=False, indent=4)


def write_text_atomic(path: Path, text: str) -> None:
    """
    Write `text` through a temporary file so a reader never sees a partial file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        temporary_path.write_text(text, encoding="utf-8")
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _matches(value: Any, key: str) -> bool:
    return isinstance(value, str) and canonical_name(value) == key


def _rename_name(value: Any, old_key: str, new_name: str) -> tuple[Any, int]:
    if _matches(value, old_key):
        return new_name, 1
    return value, 0


def _rename_names(value: Any, old_key: str, new_name: str) -> tuple[Any, int]:
    if not isinstance(value, list):
        return value, 0
    count = sum(_matches(entry, old_key) for entry in value)
    if not count:
        return value, 0

    new_key = canonical_name(new_name)
    names: list[Any] = []
    has_new_name = False
    for entry in value:
        entry = new_name if _matches(entry, old_key) else entry
        if _matches(entry, new_key):
            if has_new_name:
                continue
            has_new_name = True
        names.append(entry)
    return names, count


def _add_quantity(entry: list, other: list) -> None:
    quantities = entry[1:2] + other[1:2]
    if len(quantities) == 2 and all(isinstance(quantity, (int, float)) for quantity in quantities):
        entry[1] += other[1]


def _rename_entries(value: Any, old_key: str, new_name: str) -> tuple[Any, int]:
    if isinstance(value, dict):
        entries: list[Any] = [[name, quantity] for name, quantity in value.items()]
    elif isinstance(value, list):
        entries = [
            list(entry) if isinstance(entry, (list, tuple)) else entry
            for entry in value
        ]
    else:
        return value, 0

    count = sum(
        isinstance(entry, list) and bool(entry) and _matches(entry[0], old_key)
        for entry in entries
    )
    if not count:
        return value, 0

    new_key = canonical_name(new_name)
    result: list[Any] = []
    first_with_new_name: list | None = None
    for entry in entries:
        if isinstance(entry, list) and entry:
            if _matches(entry[0], old_key):
                entry[0] = new_name
            if _matches(entry[0], new_key):
                if first_with_new_name is not None:
                    _add_quantity(first_with_new_name, entry)
                    continue
                first_with_new_name = entry
        result.append(entry)

    if isinstance(value, dict):
        return {entry[0]: entry[1] for entry in result}, count
    return result, count


_RENAMERS: dict[str, Callable[[Any, str, str], tuple[Any, int]]] = {
    "name": _rename_name,
    "names": _rename_names,
    "entries": _rename_entries,
}


def rename_in_data(
    source: str,
    data: dict,
    target: str,
    old_stem: str,
    new_name: str,
) -> int:
    """
    Rewrite, in place, the references in `data` that point at a renamed resource.

    A reference matches when it resolves to the same file as `old_stem`. When the
    rename makes two entries of a list or inventory point at the same resource,
    they are merged.

    Args:
        source (str): The resource type of `data`.
        data (dict): The JSON content of a `source` resource.
        target (str): The resource type that was renamed.
        old_stem (str): The file stem of the resource before the rename.
        new_name (str): The name of the resource after the rename.

    Returns:
        int: The number of references that were rewritten.
    """
    old_key = canonical_name(old_stem)
    rewritten = 0
    for reference in REFERENCES:
        if reference.source != source or reference.target != target:
            continue
        for field in reference.fields:
            if field in data:
                data[field], count = _RENAMERS[reference.shape](
                    data[field],
                    old_key,
                    new_name,
                )
                rewritten += count
    return rewritten


def plan_rename(
    campaign: Campaign,
    target: str,
    old_stem: str,
    new_name: str,
    *,
    exclude: Path | None = None,
) -> list[ReferenceUpdate]:
    """
    Compute the file rewrites required by the rename of a resource, without writing.

    Args:
        campaign (Campaign): The campaign whose assets are searched.
        target (str): The resource type that was renamed.
        old_stem (str): The file stem of the resource before the rename.
        new_name (str): The name of the resource after the rename.
        exclude (Path | None): A file to leave out, usually the renamed resource itself.

    Returns:
        list[ReferenceUpdate]: One entry per file that holds references to rewrite.
    """
    sources = sorted({
        reference.source for reference in REFERENCES if reference.target == target
    })
    updates: list[ReferenceUpdate] = []
    for source in sources:
        directory: Path = getattr(campaign, RESOURCE_DIRECTORIES[source])
        for path in sorted(directory.glob("*.json")):
            if path == exclude:
                continue
            try:
                original = path.read_text(encoding="utf-8")
                data = json.loads(original)
            except ValueError:
                # A file that is not valid JSON cannot be loaded, so it holds no usable reference
                continue
            if not isinstance(data, dict):
                continue
            count = rename_in_data(source, data, target, old_stem, new_name)
            if count:
                updates.append(
                    ReferenceUpdate(source, path, count, original, json_text(data))
                )
    return updates


def revert_updates(updates: list[ReferenceUpdate]) -> None:
    """
    Restore the original content of every file in `updates`, as far as possible.
    """
    for update in updates:
        with suppress(OSError):
            write_text_atomic(update.path, update.original)


def apply_updates(updates: list[ReferenceUpdate]) -> None:
    """
    Write every update to disk, leaving the files untouched if one of them fails.

    Raises:
        OSError: If a file cannot be written. Files already written are restored.
    """
    written: list[ReferenceUpdate] = []
    try:
        for update in updates:
            write_text_atomic(update.path, update.updated)
            written.append(update)
    except OSError:
        revert_updates(written)
        raise


def delete_in_data(source: str, data: dict, target: str, old_stem: str) -> int:
    """Remove declared references to a deleted asset; preserve unrelated values."""
    old_key = canonical_name(old_stem)
    removed = 0

    for reference in REFERENCES:
        if reference.source != source or reference.target != target:
            continue

        for field in reference.fields:
            if field not in data:
                continue

            value = data[field]

            if reference.shape == "name":
                if _matches(value, old_key):
                    data[field] = None
                    removed += 1

            elif reference.shape == "names" and isinstance(value, list):
                data[field] = [
                    entry for entry in value
                    if not _matches(entry, old_key)
                ]
                removed += len(value) - len(data[field])

            elif reference.shape == "entries":
                if isinstance(value, dict):
                    data[field] = {
                        name: quantity
                        for name, quantity in value.items()
                        if not _matches(name, old_key)
                    }
                    removed += len(value) - len(data[field])

                elif isinstance(value, list):
                    data[field] = [
                        entry for entry in value
                        if not (
                            isinstance(entry, (list, tuple))
                            and entry
                            and _matches(entry[0], old_key)
                        )
                    ]
                    removed += len(value) - len(data[field])

    return removed


def plan_delete(
    campaign: Campaign,
    target: str,
    old_stem: str,
    *,
    exclude: Path | None = None,
) -> list[ReferenceUpdate]:
    """Calculate all reference removals without changing persisted files."""
    updates = []

    for source in sorted({
        ref.source for ref in REFERENCES
        if ref.target == target
    }):
        directory: Path = getattr(
            campaign, RESOURCE_DIRECTORIES[source]
        )

        for path in sorted(directory.glob("*.json")):
            if path == exclude:
                continue

            original = path.read_text(encoding="utf-8")
            data = json.loads(original)

            if not isinstance(data, dict):
                raise ValueError(
                    f"Invalid resource JSON object: {path}"
                )

            count = delete_in_data(
                source, data, target, old_stem
            )

            if count:
                updates.append(
                    ReferenceUpdate(
                        source,
                        path,
                        count,
                        original,
                        json_text(data),
                    )
                )

    return updates
