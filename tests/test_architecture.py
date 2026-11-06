"""Guards for the package layout described in the README."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "rpg_tabletop"
LAYERS = ["core", "assets", "session", "net", "ui", "apps"]
# Qt is allowed in the user interface, the entry points and the map renderer
QT_ALLOWED = ("ui", "apps", "assets/map")


def _modules():
    return sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)


def _imports(path: Path):
    """Absolute module names imported by `path`, outside TYPE_CHECKING blocks."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skipped = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
            skipped.update(id(child) for child in ast.walk(node))
    module = ".".join(path.relative_to(PACKAGE.parent).with_suffix("").parts)
    package = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
    for node in ast.walk(tree):
        if id(node) in skipped:
            continue
        if isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                yield ".".join(parts + ([node.module] if node.module else []))
            else:
                yield node.module or ""
        elif isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)


@pytest.mark.parametrize("path", _modules(), ids=lambda p: str(p.relative_to(PACKAGE)))
def test_layers_and_qt(path):
    relative = path.relative_to(PACKAGE).as_posix()
    layer = relative.split("/")[0]
    for target in _imports(path):
        if target.startswith("PySide6"):
            assert relative.startswith(QT_ALLOWED), f"{relative} imports Qt"
        if layer in LAYERS and target.startswith("rpg_tabletop."):
            other = target.split(".")[1]
            if other in LAYERS:
                assert LAYERS.index(other) <= LAYERS.index(layer), f"{relative} imports {target}"
