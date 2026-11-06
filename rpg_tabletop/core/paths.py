# -*- coding: utf-8 -*-

"""
Per-user data folder of the GM application.

Everything that belongs to this computer rather than to a campaign lives here:

    <user data>/RPGTabletop/
    ├── default_campaign/        # campaign opened when no other one is known
    ├── certs/                   # TLS certificate of the game server (one per machine)
    └── recent_campaigns.json    # campaigns opened recently, most recent first

<user data> is %LOCALAPPDATA% on Windows, ~/Library/Application Support on macOS and
$XDG_DATA_HOME (~/.local/share) elsewhere. The RPG_TABLETOP_DATA_DIR environment
variable replaces the whole folder (tests, portable installs).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_DIR_NAME = "RPGTabletop"
DATA_DIR_ENV = "RPG_TABLETOP_DATA_DIR"

SERVER_CERT_FILE = "server.crt"
SERVER_KEY_FILE = "server.key"


def user_data_dir() -> Path:
    """Folder holding the per-user data of the GM application."""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / APP_DIR_NAME


def default_campaign_dir() -> Path:
    """Folder of the default campaign."""
    return user_data_dir() / "default_campaign"


def recent_campaigns_file() -> Path:
    """JSON list of the campaigns opened recently."""
    return user_data_dir() / "recent_campaigns.json"


def certificates_dir() -> Path:
    """Folder of the game server TLS certificate, shared by every campaign."""
    return user_data_dir() / "certs"


def server_certificate_files() -> tuple[Path, Path]:
    """Paths of the server certificate and of its private key."""
    directory = certificates_dir()
    return directory / SERVER_CERT_FILE, directory / SERVER_KEY_FILE


def adopt_legacy_certificate(legacy_dir: Path) -> bool:
    """
    Copy a certificate kept by an older version in a campaign folder.

    Older versions stored the server certificate in `<campaign>/.runtime/certs`.
    Reusing it keeps the same fingerprint, so players do not have to trust the
    server again. Nothing happens if this computer already has a certificate.

    Returns:
        True if a certificate was copied.
    """
    cert_file, key_file = server_certificate_files()
    if cert_file.exists() or key_file.exists():
        return False
    legacy_cert = Path(legacy_dir) / SERVER_CERT_FILE
    legacy_key = Path(legacy_dir) / SERVER_KEY_FILE
    if not (legacy_cert.is_file() and legacy_key.is_file()):
        return False
    cert_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(legacy_key, key_file)
    shutil.copy2(legacy_cert, cert_file)
    return True
