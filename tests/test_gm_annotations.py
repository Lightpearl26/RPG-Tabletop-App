# -*- coding: utf-8 -*-
"""GM runtime map: annotations drawn during a session can be selected, moved, erased."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rpg_tabletop.assets.campaign import Campaign  # noqa: E402
from rpg_tabletop.assets.map import DrawingElement, DrawTool, MapAsset, Vec2  # noqa: E402
from rpg_tabletop.session.runtime import SessionRuntime  # noqa: E402
from rpg_tabletop.ui.gm_runtime.map_view import RuntimeMapView, _element_hit  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(app, tmp_path):
    campaign = Campaign("Test", tmp_path / "campaign")
    campaign.ensure_directories()
    MapAsset(width=1024, height=1024).save(campaign.map_path("m"))
    runtime = SessionRuntime.start_new("Partie", campaign, entities=[], initial_map_id="m")
    runtime.add_map_element(DrawingElement(
        tool=DrawTool.RECT, points=[Vec2(100, 100), Vec2(300, 200)], stroke_width=3,
    ))
    widget = RuntimeMapView(runtime)
    widget.resize(800, 600)
    widget.show()
    widget.center = Vec2(400, 300)
    widget.zoom = 1.0
    return widget


def _screen(view, x, y) -> QPoint:
    point = view.world_to_screen(Vec2(x, y))
    return QPoint(int(point.x()), int(point.y()))


def test_strokes_are_hit_on_their_line_not_inside():
    rect = DrawingElement(tool=DrawTool.RECT, points=[Vec2(0, 0), Vec2(100, 100)], stroke_width=2)
    assert _element_hit(rect, Vec2(0, 50), 4)
    assert not _element_hit(rect, Vec2(50, 50), 4)
    rect.fill_color = "#ff0000"
    assert _element_hit(rect, Vec2(50, 50), 4)
    line = DrawingElement(tool=DrawTool.FREEHAND, points=[Vec2(0, 0), Vec2(10, 0), Vec2(10, 10)])
    assert _element_hit(line, Vec2(10, 5), 2)
    assert not _element_hit(line, Vec2(0, 10), 2)
    circle = DrawingElement(tool=DrawTool.CIRCLE, points=[Vec2(0, 0), Vec2(100, 50)], stroke_width=2)
    assert _element_hit(circle, Vec2(100, 25), 3)
    assert not _element_hit(circle, Vec2(50, 25), 3)


def test_select_move_and_delete_an_annotation(view):
    runtime = view.runtime
    element_id = runtime.active_map.elements[0].id

    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=_screen(view, 200, 100))
    assert view.selected_element_id == element_id

    QTest.mousePress(view, Qt.MouseButton.LeftButton, pos=_screen(view, 100, 150))
    QTest.mouseMove(view, _screen(view, 120, 160))
    QTest.mouseMove(view, _screen(view, 150, 200))
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, pos=_screen(view, 150, 200))
    moved = runtime.active_map.elements[0]
    assert moved.id == element_id
    assert (moved.points[0].x, moved.points[0].y) == (150, 150)
    assert (moved.points[-1].x, moved.points[-1].y) == (350, 250)

    # Undoable: the journal holds the move as one event
    events = [event.type.value for event in runtime.session.events]
    assert events[-1] == "map_element_updated"

    QTest.keyClick(view, Qt.Key.Key_Delete)
    assert runtime.active_map.elements == []
    assert view.selected_element_id is None


def test_click_inside_an_empty_rectangle_selects_nothing(view):
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=_screen(view, 200, 150))
    assert view.selected_element_id is None


def test_eraser_removes_only_what_it_touches(view):
    view.set_drawing_tool("eraser")
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=_screen(view, 200, 150))
    assert len(view.runtime.active_map.elements) == 1
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=_screen(view, 300, 150))
    assert view.runtime.active_map.elements == []
