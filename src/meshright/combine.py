"""Combining models: join two models into one, cut one out of the other, or
keep only where they overlap.

Uses manifold3d (the engine behind OpenSCAD), which needs closed models. Each
model is repaired first when needed, so the operation does not fail on the
small gaps scans usually have.
"""

from __future__ import annotations

import numpy as np
import trimesh

HOW = ("join", "subtract", "overlap")
MAX_SCALE = 100.0
MIN_SCALE = 0.01


def _closed(mesh: trimesh.Trimesh) -> tuple[object, str | None]:
    """The model as a manifold3d solid, repaired first when it is not closed.
    Returns (solid, what was done to it or None)."""
    from . import repair
    from .cut import CutError, _to_manifold

    try:
        return _to_manifold(mesh), None
    except CutError:
        pass
    fixed, _ = repair.cleanup(mesh, close_open_edge=True)
    try:
        return _to_manifold(fixed), "closed the gaps in {}"
    except CutError:
        pass
    try:
        solid, _ = repair.make_solid(mesh)
        return _to_manifold(solid), "rebuilt {} as a solid"
    except (CutError, ValueError):
        raise ValueError("Could not close this model well enough to combine it. Try Make Solid on it first.") from None


def placement(elements: np.ndarray) -> np.ndarray:
    """A 4x4 placement from 16 numbers in column order (as the 3D view keeps
    them), checked so it only moves, turns and resizes."""
    matrix = np.asarray(elements, dtype=float).reshape(4, 4).T
    if not np.allclose(matrix[3], [0, 0, 0, 1]):
        raise ValueError("The placement is not valid.")
    part = matrix[:3, :3]
    sizes = np.linalg.norm(part, axis=0)
    if np.linalg.det(part) <= 0 or sizes.min() < MIN_SCALE or sizes.max() > MAX_SCALE:
        raise ValueError("The added model is sized too small, too big or mirrored.")
    return matrix


def combine(base: trimesh.Trimesh, added: trimesh.Trimesh, name: str, matrix: np.ndarray, how: str) -> tuple[trimesh.Trimesh, str]:
    if how not in HOW:
        raise ValueError(f"how must be one of: {', '.join(HOW)}.")
    added = added.copy()
    added.apply_transform(matrix)

    base_solid, base_note = _closed(base)
    added_solid, added_note = _closed(added)
    touching = not (base_solid ^ added_solid).is_empty()

    if how == "join":
        result = base_solid + added_solid
        receipt = f"Joined {name} to the model"
        if not touching:
            receipt += "; they do not touch, so they will print as separate pieces"
    elif how == "subtract":
        if not touching:
            raise ValueError(f"{name} does not touch the model, so there is nothing to cut away. Move it into the model.")
        result = base_solid - added_solid
        receipt = f"Cut the shape of {name} out of the model"
    else:
        if not touching:
            raise ValueError(f"{name} does not touch the model, so nothing overlaps. Move it into the model.")
        result = base_solid ^ added_solid
        receipt = f"Kept only where the model and {name} overlap"

    from .cut import _to_mesh

    mesh = _to_mesh(result)
    if len(mesh.faces) == 0:
        raise ValueError("That would leave nothing of the model, so it was not done.")
    from .merge import _colour_from_both

    _colour_from_both(base, added, mesh)
    notes = [note.format(what) for note, what in ((base_note, "the model"), (added_note, name)) if note]
    if notes:
        receipt += "; first " + " and ".join(notes)
    return mesh, receipt
