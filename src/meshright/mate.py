"""Join a broken-off piece back on by matching the two fracture surfaces.

The user clicks three matching spots on the piece and on the main model for a
rough fit. Then the piece is moved until its break surface sits against the
break surface of the main model. Only triangles that face each other count
(the piece's break faces point the opposite way to the main model's), so the
outer faces that both models share cannot slide the piece out of place.
"""

from __future__ import annotations

import numpy as np
import trimesh
from scipy.spatial import cKDTree

from . import merge

SAMPLE_POINTS = 12000
OPPOSED = -0.5  # normals must point at least this far against each other
KEEP_BEST = 0.8  # use the closest share of matches in each step
MIN_MATCHES = 30
SEAM_SHARE = 0.0025  # slit width sealed, as a share of the model's overall size
CRUMB_SHARE = 0.001  # loose pieces and sealed gaps smaller than this share of the model are dropped


def mate_pieces(base: trimesh.Trimesh, piece: trimesh.Trimesh, added_points, base_points) -> tuple[np.ndarray, float]:
    """The 4x4 placing ``piece`` against ``base``, and the average gap in mm."""
    rough = merge.pair_transform(added_points, base_points)
    target, target_face = trimesh.sample.sample_surface_even(base, SAMPLE_POINTS, seed=0)
    target_normals = base.face_normals[target_face]
    tree = cKDTree(target)
    spacing = float(np.median(tree.query(target, k=2)[0][:, 1]))
    source, source_face = trimesh.sample.sample_surface_even(piece, SAMPLE_POINTS, seed=1)
    source_normals = piece.face_normals[source_face]
    matrix, gap = rough, np.inf
    for reach in (12, 8, 5, 3, 2, 1.5):
        for _ in range(30):
            moved = trimesh.transform_points(source, matrix)
            normals = source_normals @ matrix[:3, :3].T
            distance, index = tree.query(moved)
            facing = (distance < reach * spacing) & ((normals * target_normals[index]).sum(axis=1) < OPPOSED)
            if facing.sum() < MIN_MATCHES:
                break
            facing &= distance <= np.quantile(distance[facing], KEEP_BEST)
            matrix = merge._kabsch(moved[facing], target[index[facing]]) @ matrix
            gap = float(distance[facing].mean())
    if not np.isfinite(gap):
        raise ValueError("The break surfaces did not meet. Check that the matching spots are on the same break.")
    return matrix, gap


def _close_seam(joined, around: trimesh.Trimesh, radius: float):
    """Seal hairline cracks where the two break surfaces meet.

    The model is grown by ``radius`` and shrunk back, which fills any slit
    narrower than twice that and leaves everything else as it was. Only a
    block around the joined piece is treated, because the grow step is slow.
    """
    import manifold3d

    margin = radius * 6
    low, high = around.bounds[0] - margin, around.bounds[1] + margin
    block = manifold3d.Manifold.cube(tuple(high - low), False).translate(tuple(low))
    ball = manifold3d.Manifold.sphere(radius, 4)
    inside = (joined ^ block).minkowski_sum(ball).minkowski_difference(ball)
    return (joined - block) + inside


def join_piece(base: trimesh.Trimesh, piece: trimesh.Trimesh, name: str, added_points, base_points) -> tuple[trimesh.Trimesh, str]:
    from .cut import CutError, _to_manifold, _to_mesh

    matrix, gap = mate_pieces(base, piece, added_points, base_points)
    moved = piece.copy()
    moved.apply_transform(matrix)
    radius = float(np.clip(SEAM_SHARE * np.linalg.norm(base.extents), 0.2, 1.0))
    try:
        joined = _to_manifold(base) + _to_manifold(moved)
        joined = _close_seam(joined, moved, radius)
        result = _to_mesh(joined)
    except CutError as exc:
        raise ValueError(str(exc)) from exc
    parts = result.split(only_watertight=False)
    if len(parts) > 1:
        largest = max(abs(p.volume) for p in parts)
        keep = [p for p in parts if abs(p.volume) >= CRUMB_SHARE * largest]
        result = trimesh.util.concatenate(keep)
    return result, f"Joined {name} back on and sealed the seam (break surfaces matched to within {gap:.2g} mm)"
