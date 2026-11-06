# -*- coding: utf-8 -*-

"""
Where campaigns live.

A campaign is a self-contained folder holding a `campaign.json` file; it can be
stored anywhere and moved freely. This module keeps track of:

- the default campaign, in the per-user data folder (see `core.paths`);
- the campaigns opened recently, most recent first;
- the one-time migration of the `default_campaign` folder that older versions
  created next to the program.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Optional

from rpg_tabletop.core.paths import (
    adopt_legacy_certificate,
    default_campaign_dir,
    recent_campaigns_file,
)

CAMPAIGN_FILE = "campaign.json"
LEGACY_DEFAULT_DIR_NAME = "default_campaign"
LEGACY_CERTS = Path(".runtime") / "certs"
MAX_RECENT_CAMPAIGNS = 10


def is_campaign_dir(root: Path) -> bool:
    """True if `root` holds a campaign."""
    return (Path(root) / CAMPAIGN_FILE).is_file()


def read_campaign_name(root: Path) -> str:
    """Name stored in `campaign.json`, or the folder name when it has none."""
    root = Path(root)
    try:
        data = json.loads((root / CAMPAIGN_FILE).read_text(encoding="utf-8"))
        name = data.get("name") if isinstance(data, dict) else None
    except (OSError, ValueError):
        name = None
    return str(name) if name else root.name


# ----------------------------------------------------------------- recent campaigns

def _same_folder(a: Path, b: Path) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return Path(a) == Path(b)


def _read_recent() -> list[Path]:
    try:
        data = json.loads(recent_campaigns_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [Path(item) for item in data if isinstance(item, str) and item]


def _write_recent(roots: list[Path]) -> None:
    path = recent_campaigns_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps([str(root) for root in roots], ensure_ascii=False, indent=4),
        encoding="utf-8",
    )
    temporary.replace(path)


def recent_campaigns() -> list[Path]:
    """Campaigns opened recently, most recent first (missing folders are skipped)."""
    return [root for root in _read_recent() if is_campaign_dir(root)]


def remember_campaign(root: Path) -> None:
    """Put `root` at the top of the recent campaigns."""
    root = Path(root).absolute()
    roots = [item for item in _read_recent() if not _same_folder(item, root)]
    _write_recent([root, *roots][:MAX_RECENT_CAMPAIGNS])


def forget_campaign(root: Path) -> None:
    """Remove `root` from the recent campaigns."""
    roots = _read_recent()
    kept = [item for item in roots if not _same_folder(item, root)]
    if len(kept) != len(roots):
        _write_recent(kept)


def last_campaign() -> Optional[Path]:
    """The campaign opened last, if its folder still exists."""
    recent = recent_campaigns()
    return recent[0] if recent else None


# ------------------------------------------------------------------- legacy folder

def legacy_default_campaigns() -> list[Path]:
    """
    `default_campaign` folders created by older versions, which stored it in the
    folder the program was started from (the project folder, or next to the exe).
    """
    candidates = [Path.cwd() / LEGACY_DEFAULT_DIR_NAME]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / LEGACY_DEFAULT_DIR_NAME)
    candidates.append(Path(__file__).resolve().parents[2] / LEGACY_DEFAULT_DIR_NAME)
    found: list[Path] = []
    for candidate in candidates:
        if is_campaign_dir(candidate) and not any(_same_folder(candidate, f) for f in found):
            found.append(candidate)
    return found


def migrate_legacy_default_campaign() -> Optional[Path]:
    """
    Copy the old `default_campaign` folder into the per-user data folder, once.

    When several old folders exist, the one used last (most recent `campaign.json`)
    is copied. The old folders are left untouched. The server certificate is not
    copied into the campaign: it becomes the certificate of this computer.

    Returns:
        The folder that was copied, or None if nothing was done.
    """
    target = default_campaign_dir()
    if target.exists():
        return None
    candidates = legacy_default_campaigns()
    if not candidates:
        return None
    source = max(candidates, key=lambda root: (root / CAMPAIGN_FILE).stat().st_mtime)

    def skip_certificates(directory: str, names: list[str]) -> list[str]:
        if Path(directory).name == LEGACY_CERTS.parent.name and LEGACY_CERTS.name in names:
            return [LEGACY_CERTS.name]
        return []

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.migrating")
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(source, staging, ignore=skip_certificates)
    staging.replace(target)
    adopt_legacy_certificate(source / LEGACY_CERTS)
    return source
