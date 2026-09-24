"""Sculpting brushes: smooth, flatten, push out and push in, along a stroke.

A stroke is the list of points the brush passed over. Every vertex within
the brush radius of the stroke moves, fully at the middle of the brush and
fading to nothing at its edge, so no step or ridge is left at the rim.
Only the triangles near the stroke are worked on, so big scans stay quick.
"""

from __future__ import annotations

import numpy as np
import trimesh
from scipy.spatial import cKDTree

BRUSHES = ("smooth", "flatten", "push_out", "push_in")
STRENGTHS = {"light": 0.35, "medium": 0.65, "strong": 1.0}
SMOOTH_PASSES = {"light": 4, "medium": 10, "strong": 25}


def _dense(stroke: np.ndarray, spacing: float) -> np.ndarray:
    """Add points along the stroke so no gap is wider than ``spacing``."""
    if len(stroke) < 2:
        return stroke
    points = [stroke[0]]
    for a, b in zip(stroke[:-1], stroke[1:]):
        steps = max(1, int(np.ceil(np.linalg.norm(b - a) / spacing)))
        points.extend(a + (b - a) * (k / steps) for k in range(1, steps + 1))
    return np.asarray(points)


def weights(mesh: trimesh.Trimesh, stroke: np.ndarray, radius: float) -> np.ndarray:
    """How much each vertex moves: 1 on the stroke, easing to 0 at the edge."""
    tree = cKDTree(_dense(stroke, radius / 4))
    distance, _ = tree.query(mesh.vertices, distance_upper_bound=radius)
    t = np.clip(1 - distance / radius, 0, 1)
    t[~np.isfinite(distance)] = 0
    return t * t * (3 - 2 * t)  # smoothstep: no crease at the brush edge


def brush(mesh: trimesh.Trimesh, stroke: np.ndarray, radius: float, kind: str, strength: str) -> tuple[trimesh.Trimesh, str]:
    if kind not in BRUSHES or strength not in STRENGTHS:
        raise ValueError("Unknown brush.")
    w = weights(mesh, np.asarray(stroke, dtype=float), radius)
    moving = np.flatnonzero(w > 0)
    if len(moving) == 0:
        raise ValueError("The brush did not touch the model. Brush over the surface.")
    before = mesh.vertices[moving].copy()
    vertices = mesh.vertices.copy()
    amount = STRENGTHS[strength]

    if kind == "smooth":
        # Smooth the nearby triangles on their own, then blend the result in.
        near = np.flatnonzero(np.isin(mesh.faces, moving).any(axis=1))
        used, local = np.unique(mesh.faces[near], return_inverse=True)
        patch = trimesh.Trimesh(mesh.vertices[used], local.reshape(-1, 3), process=False)
        trimesh.smoothing.filter_taubin(patch, lamb=0.5, nu=0.52, iterations=SMOOTH_PASSES[strength])
        at = np.searchsorted(used, moving).clip(max=len(used) - 1)
        found = used[at] == moving  # vertices no triangle uses stay put
        target = np.where(found[:, None], patch.vertices[at], before)
        vertices[moving] = before + (w[moving] * min(1.0, amount * 1.5))[:, None] * (target - before)
        verb = "Smoothed"
    elif kind == "flatten":
        from .orient import fit_plane

        core = moving[w[moving] > 0.5]
        centre, normal = fit_plane(mesh.vertices[core if len(core) >= 3 else moving])
        offset = (before - centre) @ normal
        vertices[moving] = before - (w[moving] * amount * offset)[:, None] * normal
        verb = "Flattened"
    else:
        sign = 1.0 if kind == "push_out" else -1.0
        step = 0.15 * radius * amount
        normals = mesh.vertex_normals[moving]
        vertices[moving] = before + (sign * step * w[moving])[:, None] * normals
        verb = "Pushed out" if sign > 0 else "Pushed in"

    result = trimesh.Trimesh(vertices, mesh.faces.copy(), process=False)
    moved = float(np.linalg.norm(vertices[moving] - before, axis=1).max())
    return result, f"{verb} an area {2 * radius:.3g} mm wide (moved the surface up to {moved:.2g} mm)"
