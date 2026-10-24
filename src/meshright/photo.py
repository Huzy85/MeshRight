"""Photo-guided repair: estimate camera pose from 2D-3D correspondences,
project the scan onto the photo, and find regions where the scan is missing
geometry the photo shows as intact.

No OpenCV required — uses DLT camera pose estimation with numpy/scipy and
PIL for image I/O.
"""

from __future__ import annotations

import numpy as np
import trimesh
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image as PilImage


# ---------------------------------------------------------------- camera math


def _normalise_2d(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Isotropic normalisation for DLT stability. Returns (pts_norm, T) where T
    is the 3×3 normalisation matrix such that pts_norm = (T @ hom(pts).T).T."""
    c = pts.mean(axis=0)
    scale = np.sqrt(2) / (np.linalg.norm(pts - c, axis=1).mean() + 1e-9)
    T = np.array([[scale, 0, -scale * c[0]],
                  [0, scale, -scale * c[1]],
                  [0, 0, 1]], dtype=np.float64)
    hom = np.column_stack([pts, np.ones(len(pts))])
    return (T @ hom.T).T[:, :2], T


def _normalise_3d(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Isotropic normalisation in 3D. Returns (pts_norm, T) where T is 4×4."""
    c = pts.mean(axis=0)
    scale = np.sqrt(3) / (np.linalg.norm(pts - c, axis=1).mean() + 1e-9)
    T = np.eye(4, dtype=np.float64)
    T[:3, :3] = scale * np.eye(3)
    T[:3, 3] = -scale * c
    return (pts - c) * scale, T


def camera_pose_from_correspondences(
    pts2d: np.ndarray,
    pts3d: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Estimate camera intrinsics + pose from ≥4 2D-3D point pairs.

    Parameters
    ----------
    pts2d : (N, 2) image pixel coordinates [x, y]
    pts3d : (N, 3) world / model coordinates [X, Y, Z]

    Returns
    -------
    P   : (3, 4) full projection matrix  (x_h = P @ X_h)
    K   : (3, 3) intrinsic matrix (camera)
    Rt  : (3, 4) extrinsic matrix [R | t]
    """
    pts2d = np.asarray(pts2d, dtype=np.float64)
    pts3d = np.asarray(pts3d, dtype=np.float64)
    assert len(pts2d) >= 4 and len(pts2d) == len(pts3d), \
        "Need at least 4 matching 2D-3D point pairs."

    # Normalise for numerical stability
    p2n, T2 = _normalise_2d(pts2d)
    p3n, T3 = _normalise_3d(pts3d)

    # Build the DLT constraint matrix A (2N × 12)
    N = len(pts2d)
    A = np.zeros((2 * N, 12), dtype=np.float64)
    for i, ((u, v), (X, Y, Z)) in enumerate(zip(p2n, p3n)):
        r = np.array([X, Y, Z, 1.0])
        A[2 * i,     :4]  = r
        A[2 * i,     8:]  = -u * r
        A[2 * i + 1, 4:8] = r
        A[2 * i + 1, 8:]  = -v * r

    # Least-squares solution: last column of V in SVD
    _, _, Vt = np.linalg.svd(A)
    p = Vt[-1].reshape(3, 4)

    # Denormalise: P_orig = T2_inv @ p @ T3
    T3_hom = T3  # already 4×4
    # T2 applied to image: T2_inv is the inverse
    T2_inv = np.linalg.inv(T2)
    P = T2_inv @ p @ T3_hom

    # Decompose P into K and [R | t] via RQ decomposition of the left 3×3
    M = P[:3, :3]
    # RQ decomposition using QR on the reversed matrix
    Q, R = np.linalg.qr(M[::-1, ::-1].T)
    K = R[::-1, ::-1].T
    Rmat = Q[::-1, ::-1].T

    # Enforce positive diagonal on K (canonical form)
    signs = np.sign(np.diag(K))
    K = K * signs[:, None]
    Rmat = Rmat * signs[:, None]

    # Normalise K so K[2,2] == 1
    K /= K[2, 2]

    # Recover t
    t = np.linalg.inv(K) @ P[:3, 3]
    Rt = np.column_stack([Rmat, t])

    return P, K, Rt


def project_vertices(vertices: np.ndarray, P: np.ndarray) -> np.ndarray:
    """Project 3D vertices to 2D image coordinates using projection matrix P."""
    verts_h = np.column_stack([vertices, np.ones(len(vertices))])
    proj_h = (P @ verts_h.T).T  # (N, 3)
    w = proj_h[:, 2:3]
    w = np.where(np.abs(w) < 1e-9, 1e-9, w)
    return proj_h[:, :2] / w


# ---------------------------------------------------------------- silhouette


def extract_object_mask(image: "PilImage.Image", threshold: int = 30) -> np.ndarray:
    """Return a boolean mask (H, W) where True = object (non-background).

    Simple approach: assumes the background is near the image border.
    Works well on a uniform or neutral background (typical product/object photo).
    """
    import numpy as np
    grey = np.asarray(image.convert("L"), dtype=np.uint8)
    # Background sample from the four corners
    h, w = grey.shape
    margin = max(5, min(h, w) // 20)
    corners = np.concatenate([
        grey[:margin, :margin].ravel(),
        grey[:margin, -margin:].ravel(),
        grey[-margin:, :margin].ravel(),
        grey[-margin:, -margin:].ravel(),
    ])
    bg_val = float(np.median(corners))
    diff = np.abs(grey.astype(float) - bg_val)
    # Dilate with a simple box to close small gaps
    from scipy.ndimage import binary_fill_holes, uniform_filter
    soft = uniform_filter(diff, size=5)
    mask = soft > threshold
    return binary_fill_holes(mask)


def silhouette_hull_2d(projected_pts: np.ndarray) -> np.ndarray:
    """Convex hull of the 2D projected scan points (N, 2) → hull vertices."""
    from scipy.spatial import ConvexHull
    try:
        hull = ConvexHull(projected_pts)
        return projected_pts[hull.vertices]
    except Exception:
        return projected_pts


def point_in_hull(pts: np.ndarray, hull_verts: np.ndarray) -> np.ndarray:
    """Return bool array: which of pts (N,2) are inside the convex hull_verts (M,2)."""
    from scipy.spatial import ConvexHull, Delaunay
    try:
        tri = Delaunay(hull_verts)
        return tri.find_simplex(pts) >= 0
    except Exception:
        return np.ones(len(pts), dtype=bool)


# ---------------------------------------------------------------- gap detection


def find_gap_boundary_edges(
    mesh: trimesh.Trimesh,
    P: np.ndarray,
    photo_mask: np.ndarray,
    img_w: int,
    img_h: int,
) -> list[int]:
    """Return indices of boundary-edge vertices that fall inside the photo object
    but are on the open wound boundary of the scan.

    These are the vertices that need to be filled: the scan is intact elsewhere
    but these edges sit inside a region the photo shows as solid material.
    """
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    proj = project_vertices(verts, P)  # (N, 2)

    # Find boundary vertices (appear on only-once edges)
    unique_e, counts = np.unique(mesh.edges_sorted, axis=0, return_counts=True)
    boundary_edges = unique_e[counts == 1]
    if len(boundary_edges) == 0:
        return []
    boundary_verts = np.unique(boundary_edges)

    # Project boundary vertices to image
    bv_proj = proj[boundary_verts]

    # Clamp to image bounds
    px_x = np.clip(bv_proj[:, 0].astype(int), 0, img_w - 1)
    px_y = np.clip(bv_proj[:, 1].astype(int), 0, img_h - 1)

    # Check which boundary vertices land inside the photo's object mask
    inside_mask = photo_mask[px_y, px_x]
    gap_verts = boundary_verts[inside_mask]
    return gap_verts.tolist()
