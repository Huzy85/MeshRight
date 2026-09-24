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


# ---------------------------------------------------------------- part numbers

# Seven-segment digits: simple strokes, so no font is needed.
_SEGMENTS = {"0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc",
             "5": "afgcd", "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg"}
NUMBER_DEPTH_MM = 0.6


def _digits(text: str, height: float):
    """The number as a flat shape, centred on (0, 0)."""
    import manifold3d

    w, t = 0.6 * height, 0.17 * height
    gap = 0.35 * w
    boxes = {
        "a": (0, height - t, w, height), "d": (0, 0, w, t), "g": (0, height / 2 - t / 2, w, height / 2 + t / 2),
        "b": (w - t, height / 2, w, height), "c": (w - t, 0, w, height / 2),
        "e": (0, 0, t, height / 2), "f": (0, height / 2, t, height),
    }
    shapes = []
    for i, digit in enumerate(text):
        x = i * (w + gap)
        for seg in _SEGMENTS[digit]:
            u0, v0, u1, v1 = boxes[seg]
            shapes.append(manifold3d.CrossSection.square((u1 - u0, v1 - v0)).translate((x + u0, v0)))
    width = len(text) * w + (len(text) - 1) * gap
    return manifold3d.CrossSection.batch_boolean(shapes, manifold3d.OpType.Add).translate((-width / 2, -height / 2))


def _engrave(piece, axis: int, position: float, side: int, text: str):
    """Engrave ``text`` into the piece's cut face on this plane, away from
    the edges and pin holes. ``side`` is +1 when the piece lies above the
    plane. Returns the engraved piece, or None when there is no room."""
    import manifold3d

    turn = _TO_Z[axis]
    section = piece.transform(_affine(turn)).slice(position + side * 0.05)
    if section.is_empty():
        return None
    x0, y0, x1, y1 = section.bounds()
    for wanted in (10.0, 7.0, 5.0, 3.5):
        height = min(wanted, 0.35 * min(x1 - x0, y1 - y0))
        if height < 3.0:
            return None
        label = _digits(text, height)
        lx0, ly0, lx1, ly1 = label.bounds()
        # Offsetting inwards by half the label keeps it clear of the edge
        # and of pin holes (which are holes in the cut face).
        inner = section.offset(-(max(lx1 - lx0, ly1 - ly0) / 2 + 1.0), manifold3d.JoinType.Round)
        if inner.is_empty():
            continue
        region = max(inner.decompose(), key=lambda r: r.area())
        rx0, ry0, rx1, ry1 = region.bounds()
        spot = ((rx0 + rx1) / 2, (ry0 + ry1) / 2)
        probe = manifold3d.CrossSection.square((0.2, 0.2), True).translate(spot)
        if (region ^ probe).area() < 0.03:  # the middle is outside (an odd shape): use a corner of the region
            spot = tuple(region.to_polygons()[0][0])
        if side > 0:
            label = label.mirror((1.0, 0.0))  # read from below, it must not appear mirrored
        z0 = position - 0.1 if side > 0 else position - NUMBER_DEPTH_MM
        cutter = label.translate(spot).extrude(NUMBER_DEPTH_MM + 0.1).translate((0.0, 0.0, z0))
        return piece - cutter.transform(_affine(turn.T))
    return None


def _number(pieces: list, plan: dict[int, list[float]]) -> tuple[list, list[tuple[int, int]], int]:
    """Number the pieces in assembly order (the lowest first, then its
    neighbours) and engrave each number on a cut face. Returns the pieces in
    that order, which numbered parts join, and how many were engraved."""
    boxes = [np.array(p.bounding_box()).reshape(2, 3) for p in pieces]
    planes = [(axis, pos) for axis, positions in plan.items() for pos in positions]
    touching = []
    for low, high in boxes:
        faces = []
        for axis, pos in planes:
            if abs(low[axis] - pos) < 1e-3:
                faces.append((axis, pos, 1))
            elif abs(high[axis] - pos) < 1e-3:
                faces.append((axis, pos, -1))
        touching.append(faces)

    def overlap(i, j, axis):
        others = [a for a in range(3) if a != axis]
        return all(min(boxes[i][1][a], boxes[j][1][a]) - max(boxes[i][0][a], boxes[j][0][a]) > 0.1 for a in others)

    neighbours = {i: set() for i in range(len(pieces))}
    for i in range(len(pieces)):
        for j in range(i + 1, len(pieces)):
            for axis, pos, side in touching[i]:
                if (axis, pos, -side) in touching[j] and overlap(i, j, axis):
                    neighbours[i].add(j)
                    neighbours[j].add(i)

    order, seen = [], set()
    # Start from the lowest piece; among equals, an end piece (fewest
    # neighbours), so a row of parts is numbered along the row.
    by_height = sorted(range(len(pieces)), key=lambda i: (round(boxes[i][0][2], 3), len(neighbours[i]), -pieces[i].volume()))
    for start in by_height:
        if start in seen:
            continue
        queue = [start]
        seen.add(start)
        while queue:
            i = queue.pop(0)
            order.append(i)
            for j in sorted(neighbours[i] - seen, key=lambda j: round(boxes[j][0][2], 3)):
                seen.add(j)
                queue.append(j)
    number = {piece: n + 1 for n, piece in enumerate(order)}

    engraved = 0
    result = []
    for i in order:
        piece = pieces[i]
        for axis, pos, side in touching[i]:
            marked = _engrave(piece, axis, pos, side, str(number[i]))
            if marked is not None:
                piece = marked
                engraved += 1
                break
        result.append(piece)
    joins = sorted({tuple(sorted((number[i], number[j]))) for i in neighbours for j in neighbours[i]})
    return result, joins, engraved


def _joins_text(joins: list[tuple[int, int]]) -> str:
    shown = "; ".join(f"{a} joins {b}" for a, b in joins[:8])
    return shown + ("; …" if len(joins) > 8 else "")


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


def cut(mesh: trimesh.Trimesh, plan: dict[int, list[float]], pins: bool = True, pin_mm: float = 4.0,
        numbers: bool = True) -> tuple[trimesh.Trimesh, int, int, list[tuple[int, int]] | None]:
    """Cut along the planned planes ({axis: [positions]}). Returns the laid
    out parts (with pins to print), the number of parts and of pins, and
    which numbered parts join (None when the parts are not numbered)."""
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

    joins = None
    if numbers and len(pieces) > 1:
        pieces, joins, _ = _number(pieces, plan)

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
    return _lay_out(parts + extra), len(parts), len(extra), joins


def cut_in_two(mesh: trimesh.Trimesh, axis: str, position: float, pins: bool, pin_mm: float,
               numbers: bool = True) -> tuple[trimesh.Trimesh, str]:
    a = AXES[axis]
    low, high = mesh.bounds[0][a], mesh.bounds[1][a]
    if not low < position < high:
        raise CutError(f"The cut must be inside the model: between {low:.4g} and {high:.4g} mm along {axis.upper()}.")
    result, parts, pin_count, joins = cut(mesh, {a: [position]}, pins, pin_mm, numbers)
    receipt = f"Cut into {parts} {'numbered ' if joins is not None else ''}parts along {axis.upper()} at {position:.4g} mm"
    if pin_count:
        receipt += f", with {pin_count} pin holes and {pin_count} pins to print"
    if joins:
        receipt += f". Assembly: {_joins_text(joins)}"
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


def split_to_fit(mesh: trimesh.Trimesh, bed: tuple[float, float, float], pins: bool, pin_mm: float,
                 numbers: bool = True) -> tuple[trimesh.Trimesh, str]:
    plan = split_plan(mesh, bed)
    if not plan:
        raise CutError("It already fits the printer, so there is nothing to split.")
    if sum(len(p) + 1 for p in plan.values()) > 40:
        raise CutError("That would make too many parts. Shrink the model a little first.")
    result, parts, pin_count, joins = cut(mesh, plan, pins, pin_mm, numbers)
    names = " and ".join("XYZ"[a] for a in plan)
    receipt = f"Split into {parts} {'numbered ' if joins is not None else ''}parts along {names} so each fits the printer"
    if pin_count:
        receipt += f", with {pin_count} pin holes and {pin_count} pins to print"
    if joins:
        receipt += f". Assembly: {_joins_text(joins)}"
    return result, receipt
