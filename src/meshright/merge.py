"""Merging scans: line up a second scan with the first and fuse them.

The method CloudCompare users rely on: the user clicks three or more matching
spots on both scans for a rough fit, then ICP (iterative closest point)
fine-tunes it by repeatedly matching points to their nearest neighbours.
Fully automatic lining-up was tried and dropped: on symmetric shapes it can
be confidently wrong. The fit is checked before anything is merged.
"""

from __future__ import annotations

import numpy as np
import trimesh

SAMPLE_POINTS = 4000
# Share of the added scan's points that must lie on the first scan.
MIN_OVERLAP = 0.1
# Use the closest share of matches in each step, so the parts that do not
# overlap do not pull the fit away.
TRIM = 0.7

# Scans waiting to be merged, by id (uploaded before the merge action runs).
PENDING: dict[str, tuple[str, trimesh.Trimesh]] = {}


def _kabsch(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Best rotation + translation (4x4) moving source points onto target."""
    cs, ct = source.mean(axis=0), target.mean(axis=0)
    u, _, vt = np.linalg.svd((source - cs).T @ (target - ct))
    d = np.sign(np.linalg.det(vt.T @ u.T))
    rotation = vt.T @ np.diag([1, 1, d]) @ u.T
    matrix = np.eye(4)
    matrix[:3, :3] = rotation
    matrix[:3, 3] = ct - rotation @ cs
    return matrix


def icp(source: np.ndarray, tree, target: np.ndarray, start: np.ndarray, steps: int = 40,
        anchors: tuple[np.ndarray, np.ndarray] | None = None, max_gap: float | None = None) -> tuple[np.ndarray, float]:
    """Refine ``start`` so ``source`` sits on ``target``. Returns (4x4, error).

    ``anchors`` are (source spots, target spots) the user matched by hand.
    They are weighted in on every step, so the fit cannot slide along flat or
    symmetric areas away from what the user picked. ``max_gap`` limits
    matches to points that really lie close to the other scan, so the parts
    that do not overlap cannot pull the fit (otherwise the closest share of
    matches is used).
    """
    matrix = start.copy()
    error = np.inf
    for _ in range(steps):
        moved = trimesh.transform_points(source, matrix)
        distances, index = tree.query(moved)
        if max_gap is not None:
            keep = distances <= max_gap
            if keep.sum() < 30:
                keep = distances <= np.quantile(distances, 0.3)
        else:
            keep = distances <= np.quantile(distances, TRIM)
        src, dst = moved[keep], target[index[keep]]
        if anchors is not None:
            repeat = max(1, int(0.05 * keep.sum()))
            src = np.vstack([src, np.repeat(trimesh.transform_points(anchors[0], matrix), repeat, axis=0)])
            dst = np.vstack([dst, np.repeat(anchors[1], repeat, axis=0)])
        step = _kabsch(src, dst)
        matrix = step @ matrix
        new_error = float(np.sqrt(np.mean(distances[keep] ** 2)))
        if abs(error - new_error) < 1e-6 * max(1.0, new_error):
            error = new_error
            break
        error = new_error
    return matrix, error


def pair_transform(added_points, base_points) -> np.ndarray:
    """Rough fit from matching spots clicked on each scan (3 or more pairs)."""
    added_points = np.asarray(added_points, dtype=np.float64)
    base_points = np.asarray(base_points, dtype=np.float64)
    if len(added_points) != len(base_points) or len(added_points) < 3:
        raise ValueError("Pick at least 3 matching spots on each scan.")
    spread = np.linalg.svd(added_points - added_points.mean(axis=0), compute_uv=False)
    if spread[1] < 1e-6 * max(spread[0], 1e-12):
        raise ValueError("The spots are in a line. Pick spots spread out over the scans.")
    return _kabsch(added_points, base_points)


def merge(base: trimesh.Trimesh, added: trimesh.Trimesh, name: str, added_points, base_points, fuse: bool = True) -> tuple[trimesh.Trimesh, str]:
    """Line ``added`` up with ``base`` from matching spots, fine-tune with ICP,
    check the fit and fuse the two into one surface."""
    from scipy.spatial import cKDTree

    rough = pair_transform(added_points, base_points)
    target, _ = trimesh.sample.sample_surface_even(base, SAMPLE_POINTS, seed=0)
    source, _ = trimesh.sample.sample_surface_even(added, SAMPLE_POINTS, seed=1)
    tree = cKDTree(target)
    spacing = float(np.median(tree.query(target, k=2)[0][:, 1]))

    def fit(matrix):
        distances, _ = tree.query(trimesh.transform_points(source, matrix))
        close = distances <= max(3 * spacing, 1e-9)
        return float(close.mean()), (float(distances[close].mean()) if close.any() else float("inf"))

    anchors = (np.asarray(added_points, dtype=np.float64), np.asarray(base_points, dtype=np.float64))
    matrix, _ = icp(source, tree, target, rough, steps=60, anchors=anchors, max_gap=3 * spacing)
    overlap, gap = fit(matrix)
    if overlap < MIN_OVERLAP:
        raise ValueError(
            f"The scans barely overlap after lining them up ({overlap:.0%}). "
            "Check that each pair of spots marks the same place on both scans."
        )
    moved = added.copy()
    moved.apply_transform(matrix)
    note = f"Lined up {name} (average gap {gap:.2g} mm, {overlap:.0%} overlap)"
    if not fuse:
        joined = trimesh.util.concatenate([base, moved])
        _colour_from_both(base, moved, joined)
        return joined, note + " and added it"

    from .pointcloud import points_to_mesh

    points = np.vstack([
        base.vertices,
        moved.vertices,
        trimesh.sample.sample_surface_even(base, 60000, seed=2)[0],
        trimesh.sample.sample_surface_even(moved, 60000, seed=3)[0],
    ])
    fused, _ = points_to_mesh(points)
    _colour_from_both(base, moved, fused)
    return fused, note + " and fused both into one surface"


def _colour_from_both(base: trimesh.Trimesh, added: trimesh.Trimesh, result: trimesh.Trimesh) -> None:
    """Colour the result from both scans, when either has colours."""
    from . import colour

    if colour.has(base) or colour.has(added):
        both = trimesh.Trimesh(np.vstack([base.vertices, added.vertices]), process=False)
        colour.transfer(both, result, np.vstack([colour.of(base), colour.of(added)]), new_material=True)
