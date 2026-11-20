# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build for the RPGTabletop executable.
# Usage (from the project root): pyinstaller packaging/gm.spec
#
# One-folder build (dist/RPGTabletop/RPGTabletop.exe + _internal/): it starts faster than a
# one-file build and is what packaging/installer.iss installs.

import os

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

a = Analysis(
    [os.path.join(ROOT, "rpg_tabletop", "apps", "gm.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RPGTabletop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="RPGTabletop",
)
