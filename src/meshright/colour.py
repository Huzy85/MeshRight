"""Colours: keep the colours of colour scans through every change.

Colours live on the points of the model (one colour per point). Textures are
turned into point colours when the file is opened: scans are dense enough
that this keeps the look, and it means no repair has to know about colours.
When a repair builds a new surface, each new point takes the colour of the
nearest point of the surface before, so colours survive hole filling,
Make Solid, detail reduction and the rest. Points far from the old surface
(a new base, pins) get a neutral grey.
"""

from __future__ import annotations

import numpy as np
import trimesh
from scipy.spatial import cKDTree

NEUTRAL = np.array([200, 200, 200, 255], dtype=np.uint8)
# New points further than this share of the model's size from the old
# surface are new material, not repaired surface.
FAR_SHARE = 0.02


def has(mesh: trimesh.Trimesh) -> bool:
    visual = getattr(mesh, "visual", None)
    return bool(
        visual is not None
        and visual.kind == "vertex"
        and visual.defined
        and len(visual.vertex_colors) == len(mesh.vertices)
    )


def of(mesh: trimesh.Trimesh) -> np.ndarray:
    """Point colours (RGBA, 0-255); neutral grey when the model has none."""
    if has(mesh):
        return np.asarray(mesh.visual.vertex_colors, dtype=np.uint8)
    return np.tile(NEUTRAL, (len(mesh.vertices), 1))


def from_loaded(mesh: trimesh.Trimesh) -> np.ndarray | None:
    """Point colours of a mesh as read from a file (before points are
    merged), from point colours, triangle colours or a texture; None when
    the file has no colours."""
    visual = getattr(mesh, "visual", None)
    if visual is None or not visual.defined or len(mesh.vertices) == 0:
        return None
    try:
        if visual.kind == "texture":
            visual = visual.to_color()
        colours = np.asarray(visual.vertex_colors, dtype=np.uint8)
    except Exception:  # noqa: BLE001 - odd textures: open the shape without colours
        return None
    if colours.ndim != 2 or len(colours) != len(mesh.vertices):
        return None
    if colours.shape[1] == 3:
        colours = np.c_[colours, np.full(len(colours), 255, dtype=np.uint8)]
    if np.all(colours == colours[0]):
        return None  # one flat colour is not a colour scan
    return colours


def transfer(source: trimesh.Trimesh, target: trimesh.Trimesh, colours: np.ndarray | None = None,
             new_material: bool = False) -> trimesh.Trimesh:
    """Colour ``target`` from the nearest points of ``source`` (in place).

    Repaired surface, such as a filled hole, takes the colour of the nearest
    old surface however far it is. With ``new_material`` (a base, pins),
    points away from the old surface are grey instead."""
    if len(target.vertices) == 0 or len(source.vertices) == 0:
        return target
    colours = of(source) if colours is None else colours
    distance, index = cKDTree(source.vertices).query(target.vertices)
    result = colours[index].copy()
    if new_material:
        result[distance > FAR_SHARE * float(np.linalg.norm(source.extents))] = NEUTRAL
    target.visual = trimesh.visual.ColorVisuals(target, vertex_colors=result)
    return target


def carry(before: trimesh.Trimesh, after: trimesh.Trimesh) -> trimesh.Trimesh:
    """After a change: keep colours if the model had them."""
    if not has(before) or has(after):
        return after
    return transfer(before, after)


def face_vertex_rgb(mesh: trimesh.Trimesh) -> bytes:
    """RGB for each corner of each triangle, in triangle order (as the viewer
    draws them)."""
    return np.ascontiguousarray(of(mesh)[mesh.faces][:, :, :3]).tobytes()
