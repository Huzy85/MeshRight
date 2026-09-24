"""Bust and head mode: turn a head or head-and-shoulders scan into a bust
ready to print.

The steps a person would do by hand: close the opening at the neck or
shoulders, cut the ragged bottom off flat, add a base, and size it. The model
should be standing upright first (Which way is up? does that).
"""

from __future__ import annotations

import numpy as np
import trimesh

# An opening counts as the neck when it reaches this low (share of the height).
NECK_ZONE = 0.35
BASE_SHARE = 0.12       # base height as a share of the whole bust
BASE_WIDER = 1.15       # the base reaches this much past the cut
OVERLAP_MM = 0.5        # bust sinks this far into the base so they fuse


def neck_level(mesh: trimesh.Trimesh) -> float | None:
    """Height just above the highest point of the lowest big opening (the
    neck or shoulders), so cutting there removes every ragged edge. None when
    there is no opening near the bottom."""
    from .repair import _loops

    low, high = float(mesh.bounds[0][2]), float(mesh.bounds[1][2])
    height = high - low
    best = None
    for loop in _loops(mesh):
        z = mesh.vertices[loop][:, 2]
        points = mesh.vertices[loop]
        across = float(np.linalg.norm(np.ptp(points[:, :2], axis=0)))
        if z.min() > low + NECK_ZONE * height or across < 0.15 * float(np.linalg.norm(mesh.extents[:2])):
            continue
        level = float(z.max())
        if best is None or level > best:
            best = level
    if best is None:
        return None
    return min(best + 0.01 * height, low + 0.5 * height)


def make_bust(mesh: trimesh.Trimesh, height_mm: float, base: str) -> tuple[trimesh.Trimesh, str]:
    import manifold3d

    from .combine import _closed
    from .cut import _affine, _to_mesh

    if height_mm <= 0:
        raise ValueError("The height must be more than 0.")
    done = []
    original = mesh
    level = neck_level(mesh)
    if level is None:
        # No opening at the bottom: just take a sliver off so it stands flat.
        level = float(mesh.bounds[0][2]) + 0.03 * float(mesh.extents[2])
    else:
        # Cut the ragged opening off first, so what is left to close is a
        # flat outline: filling a ragged, bent opening gives a warped patch.
        vertices, faces = trimesh.intersections.slice_faces_plane(
            mesh.vertices, mesh.faces, np.array([0.0, 0.0, 1.0]), np.array([0.0, 0.0, level]))[:2]
        mesh = trimesh.Trimesh(vertices, faces, process=True)
        if len(mesh.faces) == 0:
            raise ValueError("Cutting at the neck would remove the whole model. Is it standing upright?")
    solid, note = _closed(mesh)
    if note:
        done.append("closed the neck" if "gaps" in note else note.format("the model"))
    solid = solid.trim_by_plane((0.0, 0.0, 1.0), level)
    if solid.is_empty():
        raise ValueError("Cutting at the neck would remove the whole model. Is it standing upright?")
    done.append("cut the bottom flat")

    from . import colour

    bust = _to_mesh(solid)
    if colour.has(original):
        colour.transfer(original, bust)  # before it is resized and moved
    base_mm = max(3.0, round(height_mm * BASE_SHARE, 1)) if base != "none" else 0.0
    bust_mm = height_mm - base_mm + (OVERLAP_MM if base_mm else 0.0)
    if bust_mm <= 0:
        raise ValueError("That height is too small for a bust with a base.")
    scale = bust_mm / float(bust.extents[2])
    bust.apply_scale(scale)
    bust.apply_translation((0, 0, base_mm - (OVERLAP_MM if base_mm else 0.0) - bust.bounds[0][2]))

    if base_mm:
        # Size the base from the cut face (the bust's footprint).
        section = bust.vertices[bust.vertices[:, 2] <= bust.bounds[0][2] + 1e-3 * height_mm][:, :2]
        if len(section) < 3:
            section = bust.vertices[:, :2]
        lo, hi = section.min(axis=0), section.max(axis=0)
        centre = (lo + hi) / 2
        size = (hi - lo) * BASE_WIDER + 2 * base_mm * 0.3
        if base == "round":
            radius = float(size.max()) / 2
            plinth = manifold3d.Manifold.cylinder(base_mm, radius, radius * 0.94, 96, False)
        else:
            plinth = manifold3d.Manifold.cube((float(size[0]), float(size[1]), base_mm), False).translate(
                (-float(size[0]) / 2, -float(size[1]) / 2, 0.0))
        plinth = plinth.transform(_affine(np.eye(3), (float(centre[0]), float(centre[1]), 0.0)))
        from .cut import _to_manifold

        coloured = bust if colour.has(bust) else None
        bust = _to_mesh(_to_manifold(bust) + plinth)
        if coloured is not None:
            colour.transfer(coloured, bust, new_material=True)
        done.append(f"added a {base_mm:g} mm {base} base")

    bust.apply_translation((0, 0, -bust.bounds[0][2]))
    return bust, f"Made a bust {height_mm:g} mm tall: " + ", ".join(done)
