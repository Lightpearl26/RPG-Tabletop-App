"""Launcher of the installed applications (packaging/launcher.py), without Qt."""

from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

_SOURCE = Path(__file__).resolve().parents[1] / "packaging" / "launcher.py"
_spec = importlib.util.spec_from_file_location("rpg_launcher", _SOURCE)
launcher = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = launcher
_spec.loader.exec_module(launcher)


def _init(version):
    return f'"""Package."""\n\n__version__ = "{version}"\n'


def _archive(path: Path, files: dict[str, str], top="RPG-Tabletop-App-main") -> Path:
    """GitHub-like branch archive: every file under one top folder."""
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr(f"{top}/", "")
        for name, text in files.items():
            bundle.writestr(f"{top}/{name}", text)
    return path


def _install(root: Path, version="1.0.0", requirements="PySide6==6.11.2\n") -> launcher.Layout:
    """Installed layout with code of `version`."""
    layout = launcher.Layout(root)
    package = layout.app_dir / "rpg_tabletop"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(_init(version))
    (layout.app_dir / "requirements.txt").write_text(requirements)
    layout.launcher.write_text("# old launcher")
    return layout


# -- versions ------------------------------------------------------------------

def test_read_version():
    assert launcher.read_version(_init("0.2.0")) == "0.2.0"
    assert launcher.read_version("__version__ = '1.0'  # comment") == "1.0"
    assert launcher.read_version("x = 1") is None


def test_installed_version(tmp_path):
    assert launcher.installed_version(tmp_path) is None
    assert launcher.installed_version(_install(tmp_path, "3.1").app_dir) == "3.1"


def test_branch_and_repository_can_be_overridden(monkeypatch):
    assert launcher.version_url().endswith("/Lightpearl26/RPG-Tabletop-App/main/rpg_tabletop/__init__.py")
    monkeypatch.setenv(launcher.BRANCH_ENV, "test-update")
    monkeypatch.setenv(launcher.REPOSITORY_ENV, "me/fork")
    assert launcher.archive_url() == "https://codeload.github.com/me/fork/zip/refs/heads/test-update"


def test_skipped_version_is_shared_with_the_app_data(user_data):
    assert launcher.skipped_version() == ""
    launcher.skip_version("2.0")
    assert launcher.skipped_version() == "2.0"
    assert (user_data / "update.json").is_file()


# -- archive -------------------------------------------------------------------

def test_extract_update_keeps_only_the_code(tmp_path):
    archive = _archive(tmp_path / "code.zip", {
        "rpg_tabletop/__init__.py": _init("2.0"),
        "rpg_tabletop/apps/gm.py": "print('gm')",
        "rpg_tabletop/__pycache__/x.pyc": "junk",
        "requirements.txt": "PySide6==6.12\n",
        "packaging/launcher.py": "# new launcher",
        "packaging/build.ps1": "not needed",
        "default_campaign/campaign.json": "{}",
        "README.md": "readme",
    })
    destination = tmp_path / "app.new"
    assert launcher.extract_update(archive, destination) == "2.0"
    files = sorted(p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file())
    assert files == [
        "launcher.py", "requirements.txt", "rpg_tabletop/__init__.py", "rpg_tabletop/apps/gm.py",
    ]


def test_extract_update_refuses_archive_without_code(tmp_path):
    archive = _archive(tmp_path / "code.zip", {"README.md": "x"})
    with pytest.raises(launcher.UpdateError, match="__init__"):
        launcher.extract_update(archive, tmp_path / "app.new")


def test_extract_update_refuses_paths_outside(tmp_path):
    archive = _archive(tmp_path / "code.zip", {
        "rpg_tabletop/__init__.py": _init("2.0"),
        "rpg_tabletop/../../evil.py": "x",
    })
    with pytest.raises(launcher.UpdateError, match="refusé"):
        launcher.extract_update(archive, tmp_path / "out" / "app.new")
    assert not (tmp_path / "evil.py").exists()


def test_extract_update_refuses_broken_archive(tmp_path):
    broken = tmp_path / "code.zip"
    broken.write_text("not a zip")
    with pytest.raises(launcher.UpdateError, match="illisible"):
        launcher.extract_update(broken, tmp_path / "app.new")


def test_requirements_changed(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    (old / "requirements.txt").write_text("PySide6==6.11.2\ncryptography>=42\n")
    (new / "requirements.txt").write_text("# deps\ncryptography >= 42\r\nPySide6==6.11.2\n\n")
    assert not launcher.requirements_changed(old, new)
    (new / "requirements.txt").write_text("PySide6==6.12.0\ncryptography>=42\n")
    assert launcher.requirements_changed(old, new)


# -- installation of the new code ------------------------------------------------

def test_swap_in_replaces_code_and_launcher(tmp_path):
    layout = _install(tmp_path, "1.0")
    archive = _archive(tmp_path / "code.zip", {
        "rpg_tabletop/__init__.py": _init("2.0"),
        "requirements.txt": "PySide6==6.11.2\n",
        "packaging/launcher.py": "# new launcher",
    })
    launcher.extract_update(archive, layout.new_dir)
    launcher.swap_in(layout)
    assert launcher.installed_version(layout.app_dir) == "2.0"
    assert layout.launcher.read_text() == "# new launcher"
    assert not layout.new_dir.exists() and not layout.old_dir.exists()


def test_swap_in_without_new_code_keeps_the_old_one(tmp_path):
    layout = _install(tmp_path, "1.0")
    with pytest.raises(launcher.UpdateError):
        launcher.swap_in(layout)
    assert launcher.installed_version(layout.app_dir) == "1.0"


def test_download_and_cancel(tmp_path):
    source = tmp_path / "code.zip"
    source.write_bytes(b"z" * 700_000)
    seen = []
    target = launcher.download(source.as_uri(), tmp_path / "dl" / "code.zip",
                               progress=lambda done, total: seen.append(done))
    assert target.read_bytes() == source.read_bytes() and seen[-1] == 700_000
    with pytest.raises(launcher.UpdateCancelled):
        launcher.download(source.as_uri(), tmp_path / "dl2" / "code.zip", cancelled=lambda: True)
    assert list((tmp_path / "dl2").iterdir()) == []


# -- starting -----------------------------------------------------------------

def test_start_app_uses_the_embedded_python_and_tells_the_app_where_the_launcher_is(tmp_path):
    layout = _install(tmp_path)
    calls = []
    launcher.start_app(layout, "player", popen=lambda cmd, **kw: calls.append((cmd, kw)))
    command, options = calls[0]
    assert command == [str(tmp_path / "python" / "pythonw.exe"), "-m", "rpg_tabletop.apps.player"]
    assert options["env"][launcher.LAUNCHER_ENV] == str(layout.launcher)
    assert options["cwd"] == str(tmp_path)


def _fake_run(returncode, calls):
    class Result:
        pass

    def run(command, **_):
        calls.append(command)
        result = Result()
        result.returncode = returncode
        return result

    return run


def _prepare_update(tmp_path):
    layout = _install(tmp_path, "1.0")
    archive = _archive(tmp_path / "code.zip", {
        "rpg_tabletop/__init__.py": _init("2.0"),
        "requirements.txt": "PySide6==6.12.0\n",
    })
    launcher.extract_update(archive, layout.new_dir)
    return layout


def test_finish_update_runs_pip_then_swaps(tmp_path, monkeypatch):
    layout = _prepare_update(tmp_path)
    started, pip_calls = [], []
    monkeypatch.setattr(launcher, "start_app", lambda lay, key: started.append(key))
    assert launcher.finish_update(layout, "gm", None, run=_fake_run(0, pip_calls)) == 0
    assert pip_calls[0][1:6] == ["-m", "pip", "install", "-r", str(layout.new_dir / "requirements.txt")]
    assert launcher.installed_version(layout.app_dir) == "2.0"
    assert started == ["gm"]


def test_finish_update_keeps_old_code_when_pip_fails(tmp_path, monkeypatch):
    layout = _prepare_update(tmp_path)
    started = []
    monkeypatch.setattr(launcher, "start_app", lambda lay, key: started.append(key))
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert launcher.finish_update(layout, "player", None, run=_fake_run(1, [])) == 1
    assert launcher.installed_version(layout.app_dir) == "1.0"
    assert not layout.new_dir.exists()
    assert started == ["player"]
    assert "pip" in (launcher.data_dir() / "launcher.log").read_text(encoding="utf-8")
