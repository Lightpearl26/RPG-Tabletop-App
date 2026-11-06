# -*- coding: utf-8 -*-

"""
Grid geometry for combat: walls, line of sight, areas and lines.

Positions are map pixels; cells are integer (column, row) pairs of the map grid.
Distances between cells are Chebyshev distances (a diagonal step counts as 1),
like movement and vision.

Line of sight between two cells is the straight segment between their centers:
    - a wall segment it touches blocks it;
    - so does a token standing in a cell it crosses (not the two end cells).
A segment passing exactly through a grid corner crosses neither side cell.

No Qt and no runtime objects here: everything takes plain values.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor, hypot, isinf
from typing import Iterable, Optional, Sequence

Cell = tuple[int, int]
Point = tuple[float, float]
Segment = tuple[Point, Point]

EPSILON = 1e-9


# =============================================================================
# Grid
# =============================================================================


@dataclass(frozen=True)
class GridGeometry:
    """The map grid, in pixels."""

    cell_size: float
    offset_x: float = 0.0
    offset_y: float = 0.0

    def __post_init__(self) -> None:
        if self.cell_size <= 0:
            raise ValueError("Grid cell size must be positive.")

    def cell_of(self, x: float, y: float) -> Cell:
        return (
            floor((float(x) - self.offset_x) / self.cell_size),
            floor((float(y) - self.offset_y) / self.cell_size),
        )

    def center(self, cell: Cell) -> Point:
        return (
            self.offset_x + (cell[0] + 0.5) * self.cell_size,
            self.offset_y + (cell[1] + 0.5) * self.cell_size,
        )

    def footprint(self, x: float, y: float, size_cells: float = 1.0) -> frozenset[Cell]:
        """
        Cells under a token centered on (x, y): those whose center lies inside it.
        A token always covers at least the cell of its center.
        """
        half = max(0.5, float(size_cells) / 2.0) * self.cell_size
        first = self.cell_of(x - half + EPSILON, y - half + EPSILON)
        last = self.cell_of(x + half - EPSILON, y + half - EPSILON)
        cells = {
            (column, row)
            for column in range(first[0], last[0] + 1)
            for row in range(first[1], last[1] + 1)
            if abs(self.center((column, row))[0] - x) <= half
            and abs(self.center((column, row))[1] - y) <= half
        }
        cells.add(self.cell_of(x, y))
        return frozenset(cells)


def chebyshev(first: Cell, second: Cell) -> int:
    return max(abs(second[0] - first[0]), abs(second[1] - first[1]))


def footprint_distance(first: Iterable[Cell], second: Iterable[Cell]) -> int:
    """Smallest Chebyshev distance between two sets of cells."""
    second = list(second)
    return min(chebyshev(a, b) for a in first for b in second)


# =============================================================================
# Segments
# =============================================================================


def _orientation(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: Point, b: Point, p: Point) -> bool:
    return (
        min(a[0], b[0]) - EPSILON <= p[0] <= max(a[0], b[0]) + EPSILON
        and min(a[1], b[1]) - EPSILON <= p[1] <= max(a[1], b[1]) + EPSILON
    )


def segments_intersect(p1: Point, p2: Point, q1: Point, q2: Point) -> bool:
    """Whether two closed segments share at least one point."""
    d1 = _orientation(q1, q2, p1)
    d2 = _orientation(q1, q2, p2)
    d3 = _orientation(p1, p2, q1)
    d4 = _orientation(p1, p2, q2)
    scale = max(1.0, abs(p1[0]) + abs(p1[1]) + abs(q1[0]) + abs(q1[1]))
    eps = EPSILON * scale * scale
    if ((d1 > eps and d2 < -eps) or (d1 < -eps and d2 > eps)) and (
        (d3 > eps and d4 < -eps) or (d3 < -eps and d4 > eps)
    ):
        return True
    return (
        (abs(d1) <= eps and _on_segment(q1, q2, p1))
        or (abs(d2) <= eps and _on_segment(q1, q2, p2))
        or (abs(d3) <= eps and _on_segment(p1, p2, q1))
        or (abs(d4) <= eps and _on_segment(p1, p2, q2))
    )


def blocked_by_walls(start: Point, end: Point, walls: Sequence[Segment]) -> bool:
    return any(segments_intersect(start, end, wall[0], wall[1]) for wall in walls)


# =============================================================================
# Cells crossed by a segment
# =============================================================================


def cells_crossed(grid: GridGeometry, start: Point, end: Point) -> list[Cell]:
    """
    Cells whose inside the segment crosses, in order, both ends included.

    Exact grid corners are stepped over diagonally: the two side cells are not
    crossed (a line can pass between two diagonal tokens).
    """
    x0 = (start[0] - grid.offset_x) / grid.cell_size
    y0 = (start[1] - grid.offset_y) / grid.cell_size
    x1 = (end[0] - grid.offset_x) / grid.cell_size
    y1 = (end[1] - grid.offset_y) / grid.cell_size
    cell = (floor(x0), floor(y0))
    last = (floor(x1), floor(y1))
    dx, dy = x1 - x0, y1 - y0
    step_x = 1 if dx > 0 else -1 if dx < 0 else 0
    step_y = 1 if dy > 0 else -1 if dy < 0 else 0
    t_delta_x = abs(1.0 / dx) if dx else float("inf")
    t_delta_y = abs(1.0 / dy) if dy else float("inf")
    if step_x > 0:
        t_max_x = (floor(x0) + 1 - x0) * t_delta_x
    elif step_x < 0:
        t_max_x = (x0 - floor(x0)) * t_delta_x
    else:
        t_max_x = float("inf")
    if step_y > 0:
        t_max_y = (floor(y0) + 1 - y0) * t_delta_y
    elif step_y < 0:
        t_max_y = (y0 - floor(y0)) * t_delta_y
    else:
        t_max_y = float("inf")

    cells = [cell]
    guard = abs(last[0] - cell[0]) + abs(last[1] - cell[1]) + 2
    while cell != last and guard > 0:
        guard -= 1
        if not isinf(t_max_x) and not isinf(t_max_y) and abs(t_max_x - t_max_y) <= EPSILON:
            cell = (cell[0] + step_x, cell[1] + step_y)
            t_max_x += t_delta_x
            t_max_y += t_delta_y
        elif t_max_x < t_max_y:
            cell = (cell[0] + step_x, cell[1])
            t_max_x += t_delta_x
        else:
            cell = (cell[0], cell[1] + step_y)
            t_max_y += t_delta_y
        cells.append(cell)
    return cells


# =============================================================================
# Line of sight, areas, lines
# =============================================================================


def wall_between(grid: GridGeometry, walls: Sequence[Segment], first: Cell, second: Cell) -> bool:
    """Whether a wall cuts the segment between the centers of two cells."""
    if first == second or not walls:
        return False
    return blocked_by_walls(grid.center(first), grid.center(second), walls)


def line_of_sight(
    grid: GridGeometry,
    walls: Sequence[Segment],
    first: Cell,
    second: Cell,
    blockers: Iterable[Cell] = (),
) -> bool:
    """
    Whether `second` can be seen or shot from `first`: no wall on the way and no
    blocking cell crossed between them (the two end cells never block).
    """
    if first == second:
        return True
    if wall_between(grid, walls, first, second):
        return False
    blocking = set(blockers) - {first, second}
    if not blocking:
        return True
    return not any(
        cell in blocking for cell in cells_crossed(grid, grid.center(first), grid.center(second))
    )


def square_area(center: Cell, radius: int) -> list[Cell]:
    """Every cell at Chebyshev distance <= radius from `center`."""
    radius = max(0, int(radius))
    return [
        (center[0] + dx, center[1] + dy)
        for dy in range(-radius, radius + 1)
        for dx in range(-radius, radius + 1)
    ]


def area_cells(
    grid: GridGeometry,
    walls: Sequence[Segment],
    center: Cell,
    radius: int,
) -> list[Cell]:
    """The square area, minus the cells a wall hides from its center."""
    return [cell for cell in square_area(center, radius) if not wall_between(grid, walls, center, cell)]


def line_cells(
    grid: GridGeometry,
    walls: Sequence[Segment],
    origin: Cell,
    toward: Cell,
    length: int,
) -> list[Cell]:
    """
    A one-cell-wide line from `origin` (excluded) toward `toward`, continued up to
    `length` cells away (Chebyshev), stopping before the first wall.
    """
    if origin == toward or length <= 0:
        return []
    start = grid.center(origin)
    aim = grid.center(toward)
    dx, dy = aim[0] - start[0], aim[1] - start[1]
    # Far enough to leave the range in any direction
    scale = (length + 1) * grid.cell_size * 1.5 / max(EPSILON, hypot(dx, dy))
    end = (start[0] + dx * scale, start[1] + dy * scale)
    result: list[Cell] = []
    for cell in cells_crossed(grid, start, end)[1:]:
        if chebyshev(origin, cell) > length:
            break
        if wall_between(grid, walls, origin, cell):
            break
        result.append(cell)
    return result


def wall_segments(points_lists: Iterable[Sequence[Point]]) -> list[Segment]:
    """Turn wall polylines into their segments."""
    segments: list[Segment] = []
    for points in points_lists:
        for first, second in zip(points, points[1:]):
            if first != second:
                segments.append((tuple(first), tuple(second)))  # type: ignore[arg-type]
    return segments


def nearest_corner(grid: GridGeometry, x: float, y: float) -> Point:
    """The grid intersection closest to a point (walls follow cell edges)."""
    size = grid.cell_size
    return (
        grid.offset_x + round((x - grid.offset_x) / size) * size,
        grid.offset_y + round((y - grid.offset_y) / size) * size,
    )


def nearest(cells: Iterable[Cell], target: Cell) -> Optional[Cell]:
    cells = list(cells)
    return min(cells, key=lambda cell: chebyshev(cell, target)) if cells else None
