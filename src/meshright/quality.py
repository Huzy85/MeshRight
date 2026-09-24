"""Surface quality: where a scan is rough.

Two signs of scanner noise, and a point must show both to count as rough:

1. Noise makes the surface zig-zag, bending outwards across some edges and
   inwards across others. Real shape bends one way at a point: every edge of
   a cube or a cylinder bends outwards. Sharp designed creases (steeper than
   about 57 degrees) are left out, so the inner corner where two blocks join
   does not count.
2. Noise bulges differently from the points right next to it, while a smooth
   curve (even a saddle, like the inside of a ring) bulges about the same.

The result is blurred a little so rough areas show as patches, not speckles.
"""

from __future__ import annotations

import numpy as np
import trimesh
from scipy import sparse

CREASE = 1.0          # radians; steeper bends are designed edges, not noise
FULL_BEND = 0.08      # average bend (radians) shown as fully rough
FULL_BULGE = 0.12     # bulge difference (share of edge length) shown as fully rough
ROUGH = 0.4           # levels above this count as rough in the summary


def _mean(adjacency, degree, values):
    return (adjacency @ values) / degree.reshape(-1, *([1] * (values.ndim - 1)))


def vertex_roughness(mesh: trimesh.Trimesh) -> np.ndarray:
    """0 (clean) to 1 (rough) for each vertex."""
    n = len(mesh.vertices)
    if n == 0 or len(mesh.faces) < 2:
        return np.zeros(n)
    edges = mesh.face_adjacency_edges
    angles = np.where(mesh.face_adjacency_angles < CREASE, mesh.face_adjacency_angles, 0.0)
    convex = np.repeat(mesh.face_adjacency_convex, 2)
    ends = edges.ravel()
    twice = np.repeat(angles, 2)
    outward = np.bincount(ends, weights=np.where(convex, twice, 0), minlength=n)
    inward = np.bincount(ends, weights=np.where(convex, 0, twice), minlength=n)
    count = np.maximum(np.bincount(ends, minlength=n), 1)
    bend = np.minimum(outward, inward) / count

    unique = mesh.edges_unique
    rows = np.concatenate([unique[:, 0], unique[:, 1]])
    cols = np.concatenate([unique[:, 1], unique[:, 0]])
    adjacency = sparse.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, n))
    degree = np.maximum(np.asarray(adjacency.sum(axis=1)).ravel(), 1)
    lengths = mesh.edges_unique_length
    edge_mean = np.maximum(np.bincount(rows, weights=np.concatenate([lengths, lengths]), minlength=n) / degree, 1e-12)
    bulge = np.einsum("ij,ij->i", _mean(adjacency, degree, mesh.vertices) - mesh.vertices, mesh.vertex_normals) / edge_mean
    wobble = np.abs(bulge - _mean(adjacency, degree, bulge))

    rough = np.minimum(np.clip(bend / FULL_BEND, 0, 1), np.clip(wobble / FULL_BULGE, 0, 1))
    for _ in range(2):
        rough = 0.5 * rough + 0.5 * _mean(adjacency, degree, rough)
    return rough


FULL_CHANGE_MM = 2.0  # moved this far or more shows fully red


def change_levels(before: trimesh.Trimesh, after: trimesh.Trimesh, samples: int = 200_000) -> tuple[np.ndarray, float, float]:
    """How far each triangle of ``after`` lies from the surface of ``before``:
    0..255 per triangle (green to red), the largest distance in mm, and the
    share of the surface that moved more than 0.1 mm. Distances are measured
    to dense points spread over ``before``, which is accurate to a fraction
    of their spacing."""
    from scipy.spatial import cKDTree

    if len(after.faces) == 0 or len(before.faces) == 0:
        return np.zeros(len(after.faces), dtype=np.uint8), 0.0, 0.0
    points, _ = trimesh.sample.sample_surface_even(before, samples, seed=0)
    points = np.vstack([points, before.vertices])
    spacing = np.sqrt(before.area / max(len(points), 1))
    distance, _ = cKDTree(points).query(after.vertices)
    distance = np.maximum(distance - spacing / 2, 0.0)  # sampling gaps are not movement
    per_face = distance[after.faces].max(axis=1)
    area = after.area_faces
    moved = float(area[per_face > 0.1].sum() / area.sum()) if area.sum() > 0 else 0.0
    levels = np.round(np.clip(per_face / FULL_CHANGE_MM, 0, 1) * 255).astype(np.uint8)
    return levels, float(per_face.max()), moved


def face_levels(mesh: trimesh.Trimesh) -> tuple[np.ndarray, float]:
    """Per triangle 0..255 (clean to rough) and the share of the surface that
    is rough."""
    if len(mesh.faces) == 0:
        return np.zeros(0, dtype=np.uint8), 0.0
    levels = vertex_roughness(mesh)[mesh.faces].mean(axis=1)
    area = mesh.area_faces
    share = float(area[levels > ROUGH].sum() / area.sum()) if area.sum() > 0 else 0.0
    return np.round(levels * 255).astype(np.uint8), share
