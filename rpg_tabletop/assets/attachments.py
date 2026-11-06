#-*- coding: utf-8 -*-

"""
Files stored next to a resource, such as the token of a character.

An attachment is not part of the resource JSON, so renaming a resource has to
move it explicitly. The changes of a save are planned first, then applied
together: when one of them fails, every file is put back as it was.
"""


# import built-in modules
from __future__ import annotations
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path


TOKEN_FOLDER = "tokens"
TOKEN_SUFFIX = ".png"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class FileChange:
    """
    A file to write or to delete.

    Attributes:
        path (Path): The affected file.
        content (bytes | None): The new content of the file, or None to delete it.
    """
    path: Path
    content: bytes | None


def token_path(directory: Path, name: str) -> Path:
    """
    Return where the token of the resource called `name` is stored in `directory`.
    """
    return directory / TOKEN_FOLDER / f"{name}{TOKEN_SUFFIX}"


def is_png(data: bytes) -> bool:
    """
    Return whether `data` starts with the PNG signature.
    """
    return data.startswith(PNG_SIGNATURE)


def plan_token_changes(
    directory: Path,
    old_name: str,
    new_name: str,
    *,
    replacement: bytes | None = None,
    removed: bool = False,
) -> list[FileChange]:
    """
    Plan what saving a resource does to its token, without touching the disk.

    Names are compared as strings: a rename that only changes the case is a move
    too, since the file system may treat both spellings as one file.

    Args:
        directory (Path): The folder of the resource type, which holds the tokens folder.
        old_name (str): The name the saved resource is stored under, empty for a new one.
        new_name (str): The name of the resource once saved.
        replacement (bytes | None): The PNG data that becomes the token, if one was chosen.
        removed (bool): Whether the token was removed.

    Returns:
        list[FileChange]: The changes to apply, empty when the token stays as it is.
    """
    old_path = token_path(directory, old_name) if old_name else None
    has_old_file = old_path is not None and old_path.is_file()
    changes: list[FileChange] = []

    if replacement is not None:
        if old_path is not None and has_old_file and old_name != new_name:
            changes.append(FileChange(old_path, None))
        changes.append(FileChange(token_path(directory, new_name), replacement))
    elif removed:
        if old_path is not None and has_old_file:
            changes.append(FileChange(old_path, None))
    elif old_path is not None and has_old_file and old_name != new_name:
        changes.append(FileChange(old_path, None))
        changes.append(FileChange(token_path(directory, new_name), old_path.read_bytes()))
    return changes


def write_bytes_atomic(path: Path, content: bytes) -> None:
    """
    Write `content` through a temporary file so a reader never sees a partial file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        temporary_path.write_bytes(content)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def apply_changes(changes: list[FileChange]) -> list[FileChange]:
    """
    Apply `changes`, deleting files before writing any, and return how to undo them.

    Deleting first keeps a case-only rename correct on case-insensitive file systems.

    Raises:
        OSError: If a change fails. The files already changed are restored.

    Returns:
        list[FileChange]: The previous state of every changed file, for `restore_changes`.
    """
    ordered = [change for change in changes if change.content is None]
    ordered += [change for change in changes if change.content is not None]
    previous: list[FileChange] = []
    try:
        for change in ordered:
            previous.append(
                FileChange(
                    change.path,
                    change.path.read_bytes() if change.path.is_file() else None,
                )
            )
            if change.content is None:
                change.path.unlink(missing_ok=True)
            else:
                write_bytes_atomic(change.path, change.content)
    except OSError:
        restore_changes(previous)
        raise
    return previous


def restore_changes(previous: list[FileChange]) -> None:
    """
    Put every file of `previous` back as it was, as far as possible.
    """
    for change in reversed(previous):
        with suppress(OSError):
            if change.content is None:
                change.path.unlink(missing_ok=True)
            else:
                write_bytes_atomic(change.path, change.content)
