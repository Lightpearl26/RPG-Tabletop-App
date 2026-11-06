# -*- coding: utf-8 -*-
"""Shared types and geometry helpers for the map editor."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from math import atan2, cos, degrees, radians, sin
from typing import Optional

from PySide6.QtCore import QPointF

from ...assets.map import CameraState, DrawTool, Vec2

@dataclass(slots=True)
class EditorCamera:
    """Temporary editor camera. It is not saved until explicitly requested."""

    center: Vec2 = field(default_factory=Vec2)
    zoom: float = 1.0

    MIN_ZOOM: float = 0.05
    MAX_ZOOM: float = 16.0

    def clamp_zoom(self, value: float) -> float:
        return max(self.MIN_ZOOM, min(self.MAX_ZOOM, float(value)))

    def set_zoom(self, value: float) -> None:
        self.zoom = self.clamp_zoom(value)

    def to_state(self) -> CameraState:
        return CameraState(
            center=Vec2(self.center.x, self.center.y),
            zoom=self.zoom,
        )

    def from_state(self, state: CameraState) -> None:
        self.center = Vec2(state.center.x, state.center.y)
        self.zoom = self.clamp_zoom(state.zoom if state.zoom > 0 else 1.0)


# =============================================================================
# Selection / transform frame
# =============================================================================


class TransformMode(Enum):
    MOVE = auto()
    RESIZE = auto()
    ROTATE = auto()


class ResizeHandle(Enum):
    TOP_LEFT = auto()
    TOP = auto()
    TOP_RIGHT = auto()
    RIGHT = auto()
    BOTTOM_RIGHT = auto()
    BOTTOM = auto()
    BOTTOM_LEFT = auto()
    LEFT = auto()


class EditorTool(Enum):
    SELECT = auto()
    IMAGE = auto()
    FREEHAND = auto()
    LINE = auto()
    RECT = auto()
    CIRCLE = auto()
    TEXT = auto()
    WALL = auto()


DRAW_TOOLS: dict[EditorTool, DrawTool] = {
    EditorTool.FREEHAND: DrawTool.FREEHAND,
    EditorTool.LINE: DrawTool.LINE,
    EditorTool.RECT: DrawTool.RECT,
    EditorTool.CIRCLE: DrawTool.CIRCLE,
}


@dataclass(slots=True)
class ElementFrame:
    """Oriented editor frame of an element, in world coordinates."""

    center: Vec2
    width: float
    height: float
    rotation: float = 0.0

    def corners(self) -> list[Vec2]:
        half_w = self.width / 2.0
        half_h = self.height / 2.0
        local = [
            Vec2(-half_w, -half_h),
            Vec2(half_w, -half_h),
            Vec2(half_w, half_h),
            Vec2(-half_w, half_h),
        ]
        return [_rotate_vector(point, self.rotation) + self.center for point in local]


@dataclass(slots=True)
class ElementSnapshot:
    """Mutable geometry copied at the beginning of a transform gesture."""

    pos: Optional[Vec2] = None
    width: Optional[float] = None
    height: Optional[float] = None
    rotation: Optional[float] = None
    font_size: Optional[float] = None
    points: Optional[list[Vec2]] = None


@dataclass(slots=True)
class EditorState:
    """Serializable editor state used by undo / redo."""

    elements: list[dict]
    tokens: list[dict]
    initial_camera: dict
    selected_element_ids: list[str]
    selected_element_id: Optional[str]
    selected_token_ids: list[str]
    selected_token_id: Optional[str]
    camera_selected: bool


@dataclass(slots=True)
class CameraEditSession:
    """One direct manipulation gesture on the initial GM camera."""

    mode: str  # "move" | "zoom"
    start_screen: QPointF
    start_world: Vec2
    start_center: Vec2
    start_zoom: float
    start_handle_distance: float
    history_before: Optional[EditorState] = None


@dataclass(slots=True)
class TokenMoveSession:
    """Initial positions of one or more selected tokens during a drag."""

    token_ids: list[str]
    primary_token_id: str
    start_world: Vec2
    positions: dict[str, Vec2]
    history_before: Optional[EditorState] = None


@dataclass(slots=True)
class TransformSession:
    element_id: str
    mode: TransformMode
    start_world: Vec2
    start_screen: QPointF
    frame: ElementFrame
    snapshot: ElementSnapshot
    history_before: Optional[EditorState] = None
    handle: Optional[ResizeHandle] = None
    start_angle: float = 0.0


@dataclass(slots=True)
class GroupTransformSession:
    """Initial state of a multi-selection transform gesture."""

    element_ids: list[str]
    mode: TransformMode
    start_world: Vec2
    start_screen: QPointF
    frame: ElementFrame
    snapshots: dict[str, ElementSnapshot]
    element_frames: dict[str, ElementFrame]
    history_before: Optional[EditorState] = None
    handle: Optional[ResizeHandle] = None
    start_angle: float = 0.0


HANDLE_SIZE_PX = 10.0
ROTATE_HANDLE_OFFSET_PX = 30.0
MIN_ELEMENT_SIZE_PX = 4.0
ROTATION_SNAP_DEG = 45.0
GRID_SNAP_TOLERANCE_PX = 5.0
ROTATION_SNAP_TOLERANCE_DEG = 5.0
MAX_UNDO_STEPS = 100
PASTE_OFFSET_PX = 20.0
MARQUEE_CLICK_TOLERANCE_PX = 4.0


def _rotate_vector(vector: Vec2, angle_deg: float) -> Vec2:
    if not angle_deg:
        return Vec2(vector.x, vector.y)
    angle = radians(angle_deg)
    c, s = cos(angle), sin(angle)
    return Vec2(
        vector.x * c - vector.y * s,
        vector.x * s + vector.y * c,
    )


def _angle(center: Vec2, point: Vec2) -> float:
    """Angle in map coordinates; positive values are clockwise on screen."""
    return degrees(atan2(point.y - center.y, point.x - center.x))


def _midpoint(a: QPointF, b: QPointF) -> QPointF:
    return QPointF((a.x() + b.x()) / 2.0, (a.y() + b.y()) / 2.0)
