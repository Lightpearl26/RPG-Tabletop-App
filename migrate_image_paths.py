#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Replace old image-root paths throughout the legacy project.

Default migration:
    C:\\Users\\franc\\Pictures
        ->
    A:\\images

The script handles several textual representations automatically:
    C:\\Users\\franc\\Pictures\\...
    C:/Users/franc/Pictures/...
    C:\\\\Users\\\\franc\\\\Pictures\\\\...   # escaped JSON/Python text

By default this is a DRY RUN: nothing is modified.

Examples
--------
Preview:
    python migrate_image_paths.py "A:\\PyProjects\\JDR-To-Aru-Dmg-dice-calculator"

Apply changes:
    python migrate_image_paths.py "A:\\PyProjects\\JDR-To-Aru-Dmg-dice-calculator" --apply

Apply and create .bak copies of modified files:
    python migrate_image_paths.py "A:\\PyProjects\\JDR-To-Aru-Dmg-dice-calculator" --apply --backup

Custom roots:
    python migrate_image_paths.py PROJECT --old-root "C:\\Users\\franc\\Pictures" --new-root "A:\\images" --apply
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


DEFAULT_OLD_ROOT = r"C:\Users\franc\Pictures"
DEFAULT_NEW_ROOT = r"A:\images"

TEXT_EXTENSIONS = {
    ".json",
    ".py",
    ".txt",
    ".md",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
}

SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "venv",
    ".venv",
    "env",
    ".env",
    "node_modules",
}


def replacement_pairs(old_root: str, new_root: str) -> list[tuple[str, str]]:
    """
    Generate common textual representations of the same Windows path.

    Order matters: replace the most-escaped variants first.
    """
    old_back = old_root.replace("/", "\\")
    new_back = new_root.replace("/", "\\")

    old_forward = old_back.replace("\\", "/")
    new_forward = new_back.replace("\\", "/")

    old_escaped = old_back.replace("\\", "\\\\")
    new_escaped = new_back.replace("\\", "\\\\")

    old_double_forward = old_forward.replace("/", "//")
    new_double_forward = new_forward.replace("/", "//")

    pairs = [
        (old_escaped, new_escaped),
        (old_double_forward, new_double_forward),
        (old_back, new_back),
        (old_forward, new_forward),
    ]

    # Deduplicate while preserving order.
    result: list[tuple[str, str]] = []
    seen: set[str] = set()

    for old, new in pairs:
        if old and old not in seen:
            seen.add(old)
            result.append((old, new))

    return result


def should_skip(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False

    return any(
        part.casefold() in {name.casefold() for name in SKIP_DIR_NAMES}
        for part in relative.parts[:-1]
    )


def iter_candidate_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue

        if should_skip(path, root):
            continue

        if path.suffix.casefold() not in TEXT_EXTENSIONS:
            continue

        yield path


def read_text_file(path: Path) -> tuple[str, str]:
    """
    Read text while tolerating the common encodings used by old Windows projects.

    Returns:
        (text, encoding_used)
    """
    encodings = (
        "utf-8-sig",
        "utf-8",
        "cp1252",
    )

    last_error: Exception | None = None

    for encoding in encodings:
        try:
            return path.read_text(encoding=encoding), encoding
        except UnicodeDecodeError as error:
            last_error = error

    raise UnicodeDecodeError(
        "unknown",
        b"",
        0,
        1,
        f"Could not decode {path}: {last_error}",
    )


def migrate_text(
    text: str,
    pairs: list[tuple[str, str]],
) -> tuple[str, int]:
    changed = text
    total = 0

    for old, new in pairs:
        count = changed.count(old)
        if count:
            changed = changed.replace(old, new)
            total += count

    return changed, total


def write_atomic(
    path: Path,
    text: str,
    encoding: str,
) -> None:
    # utf-8-sig should remain BOM-encoded when it was read that way.
    tmp = path.with_name(f".{path.name}.tmp")

    try:
        tmp.write_text(
            text,
            encoding=encoding,
        )
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replace the old image root path throughout a legacy project."
        )
    )

    parser.add_argument(
        "project_root",
        help="Root directory of the old project.",
    )

    parser.add_argument(
        "--old-root",
        default=DEFAULT_OLD_ROOT,
        help=f'Old image root (default: "{DEFAULT_OLD_ROOT}")',
    )

    parser.add_argument(
        "--new-root",
        default=DEFAULT_NEW_ROOT,
        help=f'New image root (default: "{DEFAULT_NEW_ROOT}")',
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually modify files. Without this flag the script only previews changes.",
    )

    parser.add_argument(
        "--backup",
        action="store_true",
        help="Create <file>.bak before modifying each file. Requires --apply.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    root = Path(args.project_root).expanduser().resolve()

    if not root.is_dir():
        print(
            f"ERROR: project directory not found: {root}",
            file=sys.stderr,
        )
        return 2

    if args.backup and not args.apply:
        print(
            "WARNING: --backup has no effect without --apply."
        )

    pairs = replacement_pairs(
        args.old_root,
        args.new_root,
    )

    print(f"Project : {root}")
    print(f"Old root: {args.old_root}")
    print(f"New root: {args.new_root}")
    print(
        "Mode    : "
        + ("APPLY" if args.apply else "DRY RUN")
    )
    print()

    files_scanned = 0
    files_changed = 0
    replacements = 0
    errors = 0

    for path in iter_candidate_files(root):
        files_scanned += 1

        try:
            original, encoding = read_text_file(path)
        except Exception as error:
            errors += 1
            print(
                f"[ERROR] {path.relative_to(root)}: {error}"
            )
            continue

        migrated, count = migrate_text(
            original,
            pairs,
        )

        if not count:
            continue

        files_changed += 1
        replacements += count

        relative = path.relative_to(root)
        print(
            f"[CHANGE] {relative} "
            f"({count} remplacement{'s' if count != 1 else ''})"
        )

        if not args.apply:
            continue

        try:
            if args.backup:
                backup = path.with_name(
                    path.name + ".bak"
                )
                if not backup.exists():
                    shutil.copy2(
                        path,
                        backup,
                    )

            write_atomic(
                path,
                migrated,
                encoding,
            )

        except Exception as error:
            errors += 1
            print(
                f"  ERROR while writing: {error}"
            )

    print()
    print(
        f"Scanned: {files_scanned} file(s)"
    )
    print(
        f"Changed: {files_changed} file(s)"
    )
    print(
        f"Paths replaced: {replacements}"
    )

    if errors:
        print(
            f"Errors: {errors}"
        )

    if not args.apply and files_changed:
        print()
        print(
            "Nothing was modified. "
            "Re-run with --apply to perform the migration."
        )

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
