"""Symmetry repair: detect the best symmetry plane and replace a damaged half
with a mirror of the intact half.

Works for any roughly-symmetric part where one side is broken or missing.
"""

from __future__ import annotations

import numpy as np
import trimesh
from scipy.spatial import cKDTree


class SymmetryError(ValueError):
    """Raised when symmetry repair cannot be carried out."""


def _reflect_vertices(verts: np.ndarray, normal: np.ndarray, point: np.ndarray) -> np.ndarray:
    """Reflect vertex positions through a plane (normal, point)."""
    n = normal / np.linalg.norm(normal)
    d = (verts - point) @ n
    return verts - 2.0 * d[:, None] * n


def _open_half(mesh: trimesh.Trimesh, normal: np.ndarray, offset: float, tol: float = 1e-3
               ) -> trimesh.Trimesh:
    """Cut mesh by plane and return the open half (cap face removed).

    Uses manifold3d split_by_plane for a clean cut with new seam vertices.
    The plane cap is removed so the half has an open boundary that can be
    mated with its reflection.

    If the mesh is not watertight, falls back to face-centroid splitting.
    """
    from .cut import _to_manifold, _to_mesh, CutError
    n = normal / np.linalg.norm(normal)
    # Try clean manifold cut first
    try:
        m = _to_manifold(mesh)
        half_m, _ = m.split_by_plane(n, offset)
        half = _to_mesh(half_m)
        verts = np.asarray(half.vertices, dtype=np.float64)
        on_plane = np.abs((verts @ n) - offset) < tol
        cap_face = on_plane[half.faces].all(axis=1)
        half.update_faces(~cap_face)
        half.remove_unreferenced_vertices()
        return half
    except (CutError, Exception):
        pass
    # Fallback: face-centroid split for open/damaged meshes
    face_centres = mesh.triangles_center
    side = (face_centres @ n) - offset
    mask = side >= 0
    if mask.sum() < 3:
        raise SymmetryError("Symmetry plane cuts off fewer than 3 faces on the positive side.")
    m = trimesh.Trimesh(mesh.vertices, mesh.faces[mask], process=False)
    m.remove_unreferenced_vertices()
    return m


def _symmetry_score(verts: np.ndarray, normal: np.ndarray, point: np.ndarray) -> float:
    """Lower = more symmetric.  Mean nearest-neighbour distance after reflection."""
    reflected = _reflect_vertices(verts, normal, point)
    tree = cKDTree(verts)
    dists, _ = tree.query(reflected, workers=-1)
    return float(np.percentile(dists, 90))


def detect_symmetry_plane(mesh: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray]:
    """Detect the repair symmetry plane.

    Strategy: find the plane that gives the LARGEST difference in damage scores
    between the two halves — that reveals which axis the damage lies along.
    Falls back to the most geometrically symmetric plane if all differences are small.

    Returns (normal, point).
    """
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    centre = verts.mean(axis=0)
    centred = verts - centre

    # PCA: singular vectors give the three principal axes
    _, _, Vt = np.linalg.svd(centred, full_matrices=False)
    axes = Vt  # rows = principal axes

    best_diff = -1.0
    best_sym_score = np.inf
    best_normal_diff = axes[0]
    best_normal_sym = axes[0]
    best_point = centre

    for axis in axes:
        try:
            pos_h, neg_h, pos_d, neg_d = _split_at_plane(mesh, axis, centre)
        except SymmetryError:
            continue
        diff = abs(pos_d - neg_d)
        if diff > best_diff:
            best_diff = diff
            best_normal_diff = axis.copy()

        sym_s = _symmetry_score(verts, axis, centre)
        if sym_s < best_sym_score:
            best_sym_score = sym_s
            best_normal_sym = axis.copy()

    # Use the damage-difference plane if it clearly identifies the wound;
    # otherwise fall back to the most geometrically symmetric plane.
    chosen = best_normal_diff if best_diff > 0.1 else best_normal_sym
    return chosen, best_point


def _split_at_plane(mesh: trimesh.Trimesh, normal: np.ndarray, point: np.ndarray
                    ) -> tuple[trimesh.Trimesh, trimesh.Trimesh, float, float]:
    """Split mesh by plane (face-centroid method) for damage scoring only.

    Returns (positive_half, negative_half, pos_damage, neg_damage).
    Damage is boundary-edge perimeter / sqrt(area) — higher = more holes.
    """
    n = normal / np.linalg.norm(normal)
    offset = float(np.asarray(point) @ n)
    face_centres = mesh.triangles_center
    side = (face_centres @ n) - offset

    def _half(mask: np.ndarray) -> trimesh.Trimesh:
        m = trimesh.Trimesh(mesh.vertices, mesh.faces[mask], process=False)
        m.remove_unreferenced_vertices()
        return m

    pos_mask, neg_mask = side >= 0, side < 0
    if pos_mask.sum() < 3 or neg_mask.sum() < 3:
        raise SymmetryError("The symmetry plane cuts off less than 3 triangles on one side.")

    pos_half = _half(pos_mask)
    neg_half = _half(neg_mask)

    def _damage(m: trimesh.Trimesh) -> float:
        if len(m.faces) == 0:
            return 1e9
        unique_e, counts = np.unique(m.edges_sorted, axis=0, return_counts=True)
        boundary_edges = unique_e[counts == 1]
        if len(boundary_edges) == 0:
            return 0.0
        verts = np.asarray(m.vertices, dtype=np.float64)
        edge_lengths = np.linalg.norm(verts[boundary_edges[:, 0]] - verts[boundary_edges[:, 1]], axis=1)
        perimeter = float(edge_lengths.sum())
        return perimeter / float(np.sqrt(m.area + 1e-9))

    return pos_half, neg_half, _damage(pos_half), _damage(neg_half)


def symmetry_repair(
    mesh: trimesh.Trimesh,
    keep: str = "auto",
) -> tuple[trimesh.Trimesh, str]:
    """Replace the damaged half with a mirror of the intact half.

    Parameters
    ----------
    mesh : trimesh.Trimesh
        The broken, roughly-symmetric mesh.
    keep : 'auto' | 'positive' | 'negative'
        Which half to keep as the intact reference.  'auto' picks the half
        with the lower damage score (fewer holes, smoother surface).
    """
    from . import repair as rep

    normal, point = detect_symmetry_plane(mesh)
    pos_half, neg_half, pos_dmg, neg_dmg = _split_at_plane(mesh, normal, point)

    n = normal / np.linalg.norm(normal)
    offset = float(np.asarray(point) @ n)

    if keep == "positive" or (keep == "auto" and pos_dmg <= neg_dmg):
        intact_sign = 1.0
        intact_label = "positive"
    else:
        intact_sign = -1.0
        intact_label = "negative"

    # Get a clean open half using manifold split (or centroid fallback)
    intact_open = _open_half(mesh, intact_sign * n, intact_sign * offset)

    # Reflect the intact open half through the symmetry plane
    vr = _reflect_vertices(np.asarray(intact_open.vertices, dtype=np.float64), n, point)
    reflected = trimesh.Trimesh(vr, intact_open.faces[:, ::-1], process=False)

    # Merge and weld the seam
    combined = trimesh.util.concatenate([intact_open, reflected])
    combined.merge_vertices()
    combined = rep.tidy(combined)

    # Close any small gaps from floating-point seam mismatch
    if not combined.is_watertight:
        try:
            combined, _ = rep.fill_holes_curved(combined)
        except Exception:
            pass

    note = (
        f"Symmetry repair: kept the {intact_label} half "
        f"(damage score {pos_dmg:.2f} vs {neg_dmg:.2f}), "
        f"mirrored it across the detected symmetry plane."
    )
    return combined, note
