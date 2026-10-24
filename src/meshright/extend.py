"""Grow a broken end back: continue a snapped-off lug, ear or tab from its flat break.

The break face is the starting ring. Cross-sections just behind it show which
way the part was heading, how fast it bent and how fast it narrowed, and the
ring is swept forward along that path. The new skin shares the break's edge
vertices, so the result is closed with no boolean step.
"""

from __future__ import annotations

import numpy as np
import trimesh
from scipy.spatial import cKDTree

FLAT_ANGLE = np.radians(5.0)  # triangles within this of each other count as one flat break face
SLICE_STEP_MM = 1.0
MAX_BEND_DEGREES = 90.0


class ExtendError(ValueError):
    """The break cannot be rebuilt. The message is shown to the user."""


def _break_face(mesh: trimesh.Trimesh, point) -> np.ndarray:
    """Boolean mask of the flat patch of triangles around the point."""
    _, start = cKDTree(mesh.triangles_center).query(point)
    flat = mesh.face_adjacency[mesh.face_adjacency_angles < FLAT_ANGLE]
    labels = trimesh.graph.connected_component_labels(flat, node_count=len(mesh.faces))
    return labels == labels[start]


def _loops(faces: np.ndarray) -> list[list[int]]:
    """Ordered vertex rings around a patch, each edge in the patch's own direction."""
    directed = np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    undirected = np.sort(directed, axis=1)
    _, inverse, counts = np.unique(undirected, axis=0, return_inverse=True, return_counts=True)
    edges = directed[counts[inverse.reshape(-1)] == 1]
    following = {}
    for a, b in edges:
        if int(a) in following:
            raise ExtendError("The break face touches itself, so its outline cannot be followed.")
        following[int(a)] = int(b)
    loops, seen = [], set()
    for first in following:
        if first in seen:
            continue
        ring, here = [], first
        while here not in seen:
            seen.add(here)
            ring.append(here)
            here = following.get(here)
            if here is None:
                raise ExtendError("The break face is not a closed outline.")
        loops.append(ring)
    return loops


def _slice_stats(mesh, origin, normal, centre, reach):
    """Centre and size of the part's cross-section in a plane, near the axis."""
    segments = trimesh.intersections.mesh_plane(mesh, normal, origin, return_faces=False)
    if segments is None or len(segments) == 0:
        return None
    middles = segments.mean(axis=1)
    lengths = np.linalg.norm(segments[:, 0] - segments[:, 1], axis=1)
    near = np.linalg.norm(middles - origin, axis=1) < reach
    if near.sum() < 6 or lengths[near].sum() <= 0:
        return None
    w = lengths[near] / lengths[near].sum()
    mid = (middles[near] * w[:, None]).sum(axis=0)
    size = float(np.sqrt((w * np.linalg.norm(middles[near] - mid, axis=1) ** 2).sum()))
    return mid, size


def _trend(mesh, centre, normal, e1, e2, patch_points, follow_curve, taper):
    """How the part bends and narrows behind the break: (b, a, g), the first two as 2-vectors in e1/e2."""
    zero = (np.zeros(2), np.zeros(2), 0.0)
    if not (follow_curve or taper):
        return zero
    radius = float(np.linalg.norm(patch_points - centre, axis=1).max())
    size0 = float(np.sqrt((np.linalg.norm(patch_points - centre, axis=1) ** 2).mean()))
    depth_cap = min(float(mesh.extents.max()) * 0.5, radius * 8)
    rows, last_size, last_mid = [], size0, centre
    for d in np.arange(SLICE_STEP_MM, depth_cap, SLICE_STEP_MM):
        origin = centre - normal * d
        found = _slice_stats(mesh, origin, normal, centre, reach=radius * 3)
        if found is None:
            break
        mid, size = found
        if abs(size - last_size) > 0.35 * size0 or np.linalg.norm(mid - last_mid) > 0.5 * radius:
            break  # reached the part's body, not the lug any more
        rows.append((d, (mid - centre) @ e1, (mid - centre) @ e2, size))
        last_size, last_mid = size, mid
    if len(rows) < 3:
        return zero
    d, x1, x2, size = np.array(rows).T
    basis = np.column_stack([d, d * d / 2])
    fit = []
    for x in (x1, x2):
        coef, *_ = np.linalg.lstsq(basis, x, rcond=None)
        if np.abs(basis @ coef - x).max() > 0.15 * size0:
            return zero
        fit.append(coef)
    b = np.array([-fit[0][0], -fit[1][0]]) if follow_curve else np.zeros(2)
    a = np.array([fit[0][1], fit[1][1]]) if follow_curve else np.zeros(2)
    g = 0.0
    if taper:
        slope, _ = np.polyfit(-d, size / size0, 1)  # size grows by slope per mm forward
        g = float(np.clip(slope, -0.05, 0.05))
    return b, a, g


def extend_break(mesh: trimesh.Trimesh, point, length_mm: float, follow_curve: bool = True, taper: bool = True):
    """Rebuild a snapped-off end beyond the flat break face nearest the point."""
    patch = _break_face(mesh, point)
    faces = mesh.faces[patch]
    loops = _loops(faces)
    if not loops:
        raise ExtendError("That is not a flat break face. Click on the flat surface where the piece snapped off.")
    areas = mesh.area_faces[patch]
    normal = (mesh.face_normals[patch] * areas[:, None]).sum(axis=0)
    normal /= np.linalg.norm(normal)
    patch_ids = np.unique(faces)
    points = mesh.vertices[patch_ids]
    centre = (mesh.triangles_center[patch] * areas[:, None]).sum(axis=0) / areas.sum()
    spread = points - centre
    spread -= np.outer(spread @ normal, normal)
    _, _, axes = np.linalg.svd(spread, full_matrices=False)
    e1 = axes[0] - (axes[0] @ normal) * normal
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(normal, e1)

    b, a, g = _trend(mesh, centre, normal, e1, e2, points, follow_curve, taper)
    t0 = normal + b[0] * e1 + b[1] * e2
    t0 /= np.linalg.norm(t0)

    def place(s: float, p: np.ndarray) -> np.ndarray:
        offset = (b + a * s / 2) * s
        tangent = normal + (b + a * s)[0] * e1 + (b + a * s)[1] * e2
        tangent /= np.linalg.norm(tangent)
        turn = trimesh.geometry.align_vectors(t0, tangent)[:3, :3]
        factor = float(np.clip(1 + g * s, 0.4, 1.6))
        return centre + normal * s + offset[0] * e1 + offset[1] * e2 + (p - centre) @ turn.T * factor

    steps = int(np.clip(np.ceil(length_mm / SLICE_STEP_MM), 8, 400))
    ring_ids = sorted({v for loop in loops for v in loop})
    where = {v: i for i, v in enumerate(ring_ids)}
    base = mesh.vertices[ring_ids]
    vertices = [mesh.vertices]
    count = len(mesh.vertices)
    ring_start = [None] * (steps + 1)  # ring k's vertex number for ring vertex i is ring_start[k] + i
    new_faces = []
    for k in range(1, steps + 1):
        s = length_mm * k / steps
        vertices.append(np.array([place(s, p) for p in base]))
        ring_start[k] = count
        count += len(base)

    def at(k: int, v: int) -> int:
        return v if k == 0 else ring_start[k] + where[v]

    for k in range(steps):
        for loop in loops:
            for i, a_v in enumerate(loop):
                b_v = loop[(i + 1) % len(loop)]
                a0, b0, a1, b1 = at(k, a_v), at(k, b_v), at(k + 1, a_v), at(k + 1, b_v)
                new_faces += [[a0, b0, b1], [a0, b1, a1]]
    # the end cap reuses the break face's own triangles, moved to the far end
    inner = [v for v in patch_ids if v not in where]
    inner_new = {}
    if inner:
        vertices.append(np.array([place(length_mm, mesh.vertices[v]) for v in inner]))
        inner_new = {v: count + i for i, v in enumerate(inner)}
        count += len(inner)
    cap = [[at(steps, v) if v in where else inner_new[v] for v in tri] for tri in faces]
    keep = mesh.faces[~patch]
    result = trimesh.Trimesh(np.vstack(vertices), np.vstack([keep, new_faces, cap]), process=False)
    result.remove_unreferenced_vertices()
    if not result.is_watertight:
        raise ExtendError("The rebuilt end did not join up cleanly, so nothing was changed.")
    end = normal + (b + a * length_mm)[0] * e1 + (b + a * length_mm)[1] * e2
    turned = np.degrees(np.arccos(np.clip(normal @ end / np.linalg.norm(end), -1, 1)))
    note = f"Rebuilt the broken end {length_mm:g} mm long"
    if follow_curve and turned > 0.5:
        note += f", following a bend of {turned:.0f} degrees"
    if taper and abs(g) > 1e-4:
        note += f", {'narrowing' if g < 0 else 'widening'} by {abs(g) * 100:.1f}% per mm"
    return result, note
