# -*- coding: utf-8 -*-

"""Map canvas snapping: grid and angle snapping helpers."""

from __future__ import annotations

from PySide6.QtCore import Qt

from rpg_tabletop.assets.map import (
    DrawingElement,
    ImageElement,
    MapElement,
    TextElement,
    Vec2,
)
from rpg_tabletop.ui.campaign_manager.editors.map.state import (
    TransformSession,
)


class SnappingMixin:
    """Grid and angle snapping helpers. Mixed into MapCanvas."""

    # ----- Transform snapping ------------------------------------------------

    def _grid_snap_active(self, modifiers: Qt.KeyboardModifier) -> bool:
        grid = self.asset.grid
        return (
            self.snap_to_grid
            and grid.enabled
            and grid.cell_size > 0
            and not (modifiers & Qt.KeyboardModifier.AltModifier)
        )

    @staticmethod
    def _snap_value_near(
        value: float,
        offset: float,
        step: float,
        tolerance: float,
    ) -> float:
        """Snap to the nearest grid line only when it is close enough."""
        target = offset + round((value - offset) / step) * step
        return target if abs(value - target) <= tolerance else value

    def _grid_snap_tolerance_world(self) -> float:
        """Convert the screen-space magnetic radius to world pixels."""
        return self.grid_snap_tolerance_px / max(self.camera.zoom, 1e-6)

    def _snap_angle_near(self, angle: float, step: float) -> float:
        """Snap an angle only when it is close to a multiple of ``step``."""
        if step <= 0.0:
            return angle
        target = round(angle / step) * step
        difference = (angle - target + 180.0) % 360.0 - 180.0
        if abs(difference) <= self.rotation_snap_tolerance_deg:
            return target
        return angle

    def _snap_world_point(
        self,
        point: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> Vec2:
        if not self._grid_snap_active(modifiers):
            return Vec2(point.x, point.y)

        grid = self.asset.grid
        step = float(grid.cell_size)
        tolerance = self._grid_snap_tolerance_world()
        return Vec2(
            self._snap_value_near(point.x, grid.offset_x, step, tolerance),
            self._snap_value_near(point.y, grid.offset_y, step, tolerance),
        )

    def _snap_move_delta(
        self,
        element: MapElement,
        session: TransformSession,
        delta: Vec2,
        modifiers: Qt.KeyboardModifier,
    ) -> Vec2:
        if not self._grid_snap_active(modifiers):
            return delta

        grid = self.asset.grid
        step = float(grid.cell_size)
        tolerance = self._grid_snap_tolerance_world()

        # Images and text have a stable model-space top-left anchor. Each axis
        # snaps independently, and only inside the magnetic tolerance.
        if isinstance(element, (ImageElement, TextElement)) and session.snapshot.pos is not None:
            candidate = session.snapshot.pos + delta
            snapped = Vec2(
                self._snap_value_near(candidate.x, grid.offset_x, step, tolerance),
                self._snap_value_near(candidate.y, grid.offset_y, step, tolerance),
            )
            return snapped - session.snapshot.pos

        # Drawings are point clouds. Snap the top-left of their transform frame so
        # the whole drawing moves rigidly instead of snapping every point.
        if isinstance(element, DrawingElement):
            start_left = session.frame.center.x - session.frame.width / 2.0
            start_top = session.frame.center.y - session.frame.height / 2.0
            candidate_left = start_left + delta.x
            candidate_top = start_top + delta.y
            snapped_left = self._snap_value_near(
                candidate_left, grid.offset_x, step, tolerance
            )
            snapped_top = self._snap_value_near(
                candidate_top, grid.offset_y, step, tolerance
            )
            return Vec2(
                snapped_left - start_left,
                snapped_top - start_top,
            )

        return delta
