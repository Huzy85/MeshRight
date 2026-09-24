"""Cutting models into parts, with pin holes so the parts line up.

Uses manifold3d (the geometry engine behind OpenSCAD), which always gives
closed, printable parts. The model must be closed first (Clean up does that).

Pins: a hole is made across each cut and a matching pin is printed with the
parts. The hole is a little wider than the pin so it slides in.
"""

from __future__ import annotations

import math

import numpy as np
import trimesh

AXES = {"x": 0, "y": 1, "z": 2}
# Maps each axis to the model's z (a turn of the axes, so nothing is mirrored)
# and back: slices are always taken across z.
_TO_Z = {
    0: np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]], dtype=float),
    1: np.array([[0, 0, 1], [1, 0, 0], [0, 1, 0]], dtype=float),
    2: np.eye(3),
}
PIN_CLEARANCE_MM = 0.3
PART_GAP_MM = 8.0
MIN_WALL_MM = 2.0


class CutError(ValueError):
    """The cut cannot be made; the message is shown to the user."""


def _to_manifold(mesh: trimesh.Trimesh):
    import manifold3d

    solid = manifold3d.Manifold(manifold3d.Mesh(
        vert_properties=np.asarray(mesh.vertices, dtype=np.float32),
        tri_verts=np.asarray(mesh.faces, dtype=np.uint32),
    ))
    if solid.status() != manifold3d.Error.NoError:
        raise CutError("The model must be closed before it can be cut. Run Clean up first.")
    return solid


def _to_mesh(solid) -> trimesh.Trimesh:
    result = solid.to_mesh()
    return trimesh.Trimesh(np.asarray(result.vert_properties)[:, :3], np.asarray(result.tri_verts), process=True)


def _affine(rotation: np.ndarray, offset=(0, 0, 0)) -> list:
    """manifold3d wants a 3x4 matrix (rows)."""
    return np.c_[rotation, np.asarray(offset, dtype=float)].tolist()


def _pin_spots(solid, axis: int, position: float, cells: list[tuple], radius: float) -> list[np.ndarray]:
    """Up to two pin centres per cell of the cut face, far apart and well
    inside the surface. Returns 3D points on the cut plane."""
    import manifold3d

    turn = _TO_Z[axis]
    section = solid.transform(_affine(turn)).slice(position)
    inner = section.offset(-(radius + MIN_WALL_MM), manifold3d.JoinType.Round)
    spots = []
    for (u0, u1, v0, v1) in cells:
        box = manifold3d.CrossSection.square((u1 - u0, v1 - v0)).translate((u0, v0))
        area = inner ^ box
        points = [np.asarray(p) for poly in area.to_polygons() for p in poly]
        if not points:
            continue
        points = np.asarray(points)
        if len(points) == 1 or area.area() < (4 * radius) ** 2:
            chosen = [points.mean(axis=0)] if len(points) else []
        else:
            d = np.linalg.norm(points[:, None] - points[None], axis=2)
            i, j = np.unravel_index(np.argmax(d), d.shape)
            # Pull each pin a little towards the middle so it stays clear of edges.
            centre = points.mean(axis=0)
            chosen = [points[i] * 0.85 + centre * 0.15, points[j] * 0.85 + centre * 0.15]
        for u, v in chosen:
            spots.append(turn.T @ np.array([u, v, position]))
    return spots


def _hole(axis: int, centre: np.ndarray, radius: float, depth: float):
    import manifold3d

    cylinder = manifold3d.Manifold.cylinder(2 * depth, radius, radius, 32, True)
    return cylinder.transform(_affine(_TO_Z[axis].T)).translate(tuple(centre))


def _pins(count: int, diameter: float, depth: float) -> list[trimesh.Trimesh]:
    pins = []
    for _ in range(count):
        pin = trimesh.creation.cylinder(radius=diameter / 2, height=2 * depth - 1.0, sections=32)
        pins.append(pin)
    return pins


def _lay_out(parts: list[trimesh.Trimesh]) -> trimesh.Trimesh:
    """Arrange the parts in tidy rows on the bed, each resting on it."""
    parts = sorted((p.copy() for p in parts), key=lambda p: -p.extents[1])
    total = sum((p.extents[0] + PART_GAP_MM) * (p.extents[1] + PART_GAP_MM) for p in parts)
    row_width = max(max(p.extents[0] for p in parts), math.sqrt(total) * 1.2)
    placed = []
    x = y = row_depth = 0.0
    for part in parts:
        if x > 0 and x + part.extents[0] > row_width:
            x, y, row_depth = 0.0, y + row_depth + PART_GAP_MM, 0.0
        low = part.bounds[0]
        part.apply_translation((x - low[0], y - low[1], -low[2]))
        x += part.extents[0] + PART_GAP_MM
        row_depth = max(row_depth, part.extents[1])
        placed.append(part)
    combined = trimesh.util.concatenate(placed)
    centre = combined.bounds.mean(axis=0)
    combined.apply_translation((-centre[0], -centre[1], 0))
    return combined


def cut(mesh: trimesh.Trimesh, plan: dict[int, list[float]], pins: bool = True, pin_mm: float = 4.0) -> tuple[trimesh.Trimesh, int, int]:
    """Cut along the planned planes ({axis: [positions]}). Returns the laid
    out parts (with pins to print), the number of parts and of pins."""
    solid = _to_manifold(mesh)
    radius = pin_mm / 2 + PIN_CLEARANCE_MM / 2
    depth = max(3.0, 1.5 * pin_mm)

    hole_count = 0
    if pins:
        bounds = mesh.bounds
        for axis, positions in plan.items():
            turn = _TO_Z[axis]
            u_axis, v_axis = int(np.argmax(turn[0])), int(np.argmax(turn[1]))
            u_edges = [bounds[0][u_axis] - 1] + sorted(plan.get(u_axis, [])) + [bounds[1][u_axis] + 1]
            v_edges = [bounds[0][v_axis] - 1] + sorted(plan.get(v_axis, [])) + [bounds[1][v_axis] + 1]
            cells = [(u_edges[i], u_edges[i + 1], v_edges[j], v_edges[j + 1])
                     for i in range(len(u_edges) - 1) for j in range(len(v_edges) - 1)]
            for position in positions:
                for spot in _pin_spots(solid, axis, position, cells, radius):
                    solid = solid - _hole(axis, spot, radius, depth)
                    hole_count += 1

    pieces = [solid]
    for axis, positions in plan.items():
        normal = [0.0, 0.0, 0.0]
        normal[axis] = 1.0
        for position in positions:
            next_pieces = []
            for piece in pieces:
                for part in piece.split_by_plane(normal, position):
                    if not part.is_empty():
                        next_pieces.append(part)
            pieces = next_pieces

    parts = [_to_mesh(p) for p in pieces]
    parts = [p for p in parts if len(p.faces)]
    from . import colour

    coloured = colour.has(mesh)
    if coloured:
        # Colour each part before the parts are moved apart.
        for part in parts:
            colour.transfer(mesh, part)
    if len(plan) == 1:
        # A single cut: rest both halves on their flat cut face.
        axis, positions = next(iter(plan.items()))
        for part in parts:
            below = part.bounds.mean(axis=0)[axis] < positions[0]
            down = np.zeros(3)
            down[axis] = 1.0 if below else -1.0
            part.apply_transform(trimesh.geometry.align_vectors(down, [0, 0, -1]) if not np.allclose(down, [0, 0, -1]) else np.eye(4))
    if len(parts) < 2:
        raise CutError("That cut does not pass through the model. Move it so it crosses the model.")
    extra = _pins(hole_count, pin_mm, depth) if pins and hole_count else []
    if coloured:
        for pin in extra:
            pin.visual = trimesh.visual.ColorVisuals(pin, vertex_colors=np.tile(colour.NEUTRAL, (len(pin.vertices), 1)))
    return _lay_out(parts + extra), len(parts), len(extra)


def cut_in_two(mesh: trimesh.Trimesh, axis: str, position: float, pins: bool, pin_mm: float) -> tuple[trimesh.Trimesh, str]:
    a = AXES[axis]
    low, high = mesh.bounds[0][a], mesh.bounds[1][a]
    if not low < position < high:
        raise CutError(f"The cut must be inside the model: between {low:.4g} and {high:.4g} mm along {axis.upper()}.")
    result, parts, pin_count = cut(mesh, {a: [position]}, pins, pin_mm)
    receipt = f"Cut into {parts} parts along {axis.upper()} at {position:.4g} mm"
    if pin_count:
        receipt += f", with {pin_count} pin holes and {pin_count} pins to print"
    return result, receipt


def split_plan(mesh: trimesh.Trimesh, bed: tuple[float, float, float]) -> dict[int, list[float]]:
    """Evenly spaced cuts so every part fits the bed (with a little room)."""
    plan = {}
    for axis in range(3):
        size = float(mesh.extents[axis])
        limit = bed[axis] * 0.92
        count = math.ceil(size / limit) if size > limit else 1
        if count > 1:
            low = float(mesh.bounds[0][axis])
            plan[axis] = [low + size * k / count for k in range(1, count)]
    return plan


def split_to_fit(mesh: trimesh.Trimesh, bed: tuple[float, float, float], pins: bool, pin_mm: float) -> tuple[trimesh.Trimesh, str]:
    plan = split_plan(mesh, bed)
    if not plan:
        raise CutError("It already fits the printer, so there is nothing to split.")
    if sum(len(p) + 1 for p in plan.values()) > 40:
        raise CutError("That would make too many parts. Shrink the model a little first.")
    result, parts, pin_count = cut(mesh, plan, pins, pin_mm)
    names = " and ".join("XYZ"[a] for a in plan)
    receipt = f"Split into {parts} parts along {names} so each fits the printer"
    if pin_count:
        receipt += f", with {pin_count} pin holes and {pin_count} pins to print"
    return result, receipt
