# -*- coding: utf-8 -*-

"""
Map coordinates: points (Vec2) and axis-aligned rectangles (Rect).
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, radians, sin
from typing import Any

from PySide6.QtCore import QRectF

# =============================================================================
# Geometry
# =============================================================================


@dataclass(slots=True)
class Vec2:
    """A point or a vector, in map pixels."""

    x: float = 0.0
    y: float = 0.0

    def __add__(self, other: Vec2) -> Vec2:
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Vec2) -> Vec2:
        return Vec2(self.x - other.x, self.y - other.y)

    def to_list(self) -> list[float]:
        """
        Serialize the point as `[x, y]`.
        """
        return [self.x, self.y]

    @classmethod
    def from_value(cls, value: Any) -> Vec2:
        """
        Read a point stored as `[x, y]`.

        Raises:
            ValueError: If the value is not a pair of numbers.
        """
        try:
            x, y = value
            return cls(float(x), float(y))
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid point: {value!r}") from error


@dataclass(slots=True)
class Rect:
    """An axis-aligned rectangle, in map pixels."""

    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0

    @property
    def right(self) -> float:
        """The x coordinate of the right edge."""
        return self.x + self.width

    @property
    def bottom(self) -> float:
        """The y coordinate of the bottom edge."""
        return self.y + self.height

    def intersects(self, other: Rect) -> bool:
        """
        Whether the two rectangles overlap or touch.

        Touching counts on purpose: filtering must never miss an element.
        """
        return not (
            self.right < other.x
            or other.right < self.x
            or self.bottom < other.y
            or other.bottom < self.y
        )

    def inflate(self, margin: float) -> Rect:
        """
        Return a rectangle grown by `margin` on every side.
        """
        return Rect(
            self.x - margin,
            self.y - margin,
            self.width + 2 * margin,
            self.height + 2 * margin,
        )

    def to_qrectf(self) -> QRectF:
        """
        Convert to a Qt rectangle.
        """
        return QRectF(self.x, self.y, self.width, self.height)

    @classmethod
    def from_points(cls, points: list[Vec2]) -> Rect:
        """
        Return the bounding box of `points`, empty if there is none.
        """
        if not points:
            return cls()
        xs = [point.x for point in points]
        ys = [point.y for point in points]
        return cls(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def _rotated_bounds(rect: Rect, angle_deg: float) -> Rect:
    """
    Bounding box of `rect` once rotated around its center.
    """
    if not angle_deg:
        return Rect(rect.x, rect.y, rect.width, rect.height)
    center = Vec2(rect.x + rect.width / 2, rect.y + rect.height / 2)
    cosine, sine = cos(radians(angle_deg)), sin(radians(angle_deg))
    corners = [
        Vec2(rect.x, rect.y),
        Vec2(rect.right, rect.y),
        Vec2(rect.right, rect.bottom),
        Vec2(rect.x, rect.bottom),
    ]
    rotated = []
    for corner in corners:
        dx, dy = corner.x - center.x, corner.y - center.y
        rotated.append(
            Vec2(
                center.x + dx * cosine - dy * sine,
                center.y + dx * sine + dy * cosine,
            )
        )
    return Rect.from_points(rotated)
