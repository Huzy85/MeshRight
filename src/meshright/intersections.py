"""Find triangles that cut through other triangles.

Two stages, both plain numpy:
1. Broad phase: put every triangle's bounding box into a grid of cells, and
   only compare triangles that share a cell and whose boxes overlap.
2. Narrow phase: two triangles cross when an edge of one passes through the
   inside of the other (the Moller-Trumbore ray test, limited to the edge).

Triangles that share a corner are skipped, as are exact touches: those are
normal neighbours, not faults.
"""

from __future__ import annotations

import numpy as np

# Compare pairs in batches so memory stays flat on big scans.
BATCH = 500_000
# Give up (and say so) rather than grind through pathological meshes.
MAX_PAIRS = 60_000_000
EPS = 1e-9


class TooComplex(RuntimeError):
    """The mesh has too many overlapping candidates to check in reasonable time."""


def _candidate_pairs(triangles: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lo = triangles.min(axis=1)
    hi = triangles.max(axis=1)
    sizes = (hi - lo).max(axis=1)
    span = float(np.ptp(np.vstack([lo, hi]), axis=0).max()) or 1.0
    cell = max(float(np.mean(sizes)) * 2.0, 1e-9 * span)
    eps = 1e-9 * span

    origin = lo.min(axis=0)
    first = np.floor((lo - origin) / cell).astype(np.int64)
    last = np.floor((hi - origin) / cell).astype(np.int64)
    span = last - first + 1
    counts = span.prod(axis=1)
    if counts.sum() > 4 * MAX_PAIRS:
        raise TooComplex()

    # Every (triangle, cell) combination the triangle's box touches.
    owner = np.repeat(np.arange(len(triangles)), counts)
    local = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
    sy, sz = span[owner, 1], span[owner, 2]
    cx = first[owner, 0] + local // (sy * sz)
    cy = first[owner, 1] + (local // sz) % sy
    cz = first[owner, 2] + local % sz
    base = np.int64(max(last.max() + 2, 2))
    keys = (cx * base + cy) * base + cz

    order = np.argsort(keys, kind="stable")
    keys, owner = keys[order], owner[order]
    cells = np.stack([cx, cy, cz], axis=1)[order]
    starts = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]])
    group_size = np.diff(np.r_[starts, len(keys)])
    rank = np.arange(len(keys)) - np.repeat(starts, group_size)
    partners = np.repeat(group_size, group_size) - rank - 1
    total = int(partners.sum())
    if total > MAX_PAIRS:
        raise TooComplex()

    found_a, found_b = [], []
    positions = np.repeat(np.arange(len(keys)), partners)
    offsets = np.arange(total) - np.repeat(np.cumsum(partners) - partners, partners)
    for start in range(0, total, 4 * BATCH):
        left = positions[start:start + 4 * BATCH]
        right = left + 1 + offsets[start:start + 4 * BATCH]
        a, b = owner[left], owner[right]
        # Boxes must overlap, and each pair is kept only in the one cell that
        # holds the corner of the overlap, so no pair is counted twice.
        overlap_lo = np.maximum(lo[a], lo[b])
        overlap = np.all(overlap_lo <= np.minimum(hi[a], hi[b]) + eps, axis=1)
        home = np.floor((overlap_lo - origin) / cell).astype(np.int64)
        keep = overlap & np.all(home == cells[left], axis=1) & (a != b)
        found_a.append(a[keep])
        found_b.append(b[keep])
    a = np.concatenate(found_a)
    b = np.concatenate(found_b)
    return np.minimum(a, b), np.maximum(a, b)


def _edge_hits(tri_edges: np.ndarray, tri: np.ndarray) -> np.ndarray:
    """Does any of the 3 edges in tri_edges (n,3,3) pass through tri (n,3,3)?"""
    v0, v1, v2 = tri[:, 0], tri[:, 1], tri[:, 2]
    e1 = v1 - v0
    e2 = v2 - v0
    hit = np.zeros(len(tri), dtype=bool)
    for i in range(3):
        p0 = tri_edges[:, i]
        d = tri_edges[:, (i + 1) % 3] - p0
        pvec = np.cross(d, e2)
        det = np.einsum("ij,ij->i", e1, pvec)
        # Relative to the sizes involved, so the check works the same whether
        # the model is in metres or millimetres.
        scale = np.linalg.norm(d, axis=1) * np.linalg.norm(e1, axis=1) * np.linalg.norm(e2, axis=1)
        ok = np.abs(det) > 1e-10 * scale
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        tvec = p0 - v0
        u = np.einsum("ij,ij->i", tvec, pvec) * inv
        qvec = np.cross(tvec, e1)
        v = np.einsum("ij,ij->i", d, qvec) * inv
        t = np.einsum("ij,ij->i", e2, qvec) * inv
        # The edge must pass through the triangle, rims included (a crossing
        # can land exactly on an edge), but not merely end on it.
        inside = (u >= -1e-12) & (v >= -1e-12) & (u + v <= 1 + 1e-12)
        hit |= ok & inside & (t > 1e-7) & (t < 1 - 1e-7)
    return hit


def crossing_pairs(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Pairs of face indices (k, 2) whose triangles cut through each other."""
    if len(faces) < 2:
        return np.zeros((0, 2), dtype=np.int64)
    triangles = np.asarray(vertices, dtype=np.float64)[faces]
    a, b = _candidate_pairs(triangles)

    found = []
    for start in range(0, len(a), BATCH):
        ia, ib = a[start:start + BATCH], b[start:start + BATCH]
        fa, fb = faces[ia], faces[ib]
        shares_corner = (fa[:, :, None] == fb[:, None, :]).any(axis=(1, 2))
        ia, ib = ia[~shares_corner], ib[~shares_corner]
        if len(ia) == 0:
            continue
        ta, tb = triangles[ia], triangles[ib]
        hit = _edge_hits(ta, tb) | _edge_hits(tb, ta)
        found.append(np.stack([ia[hit], ib[hit]], axis=1))
    return np.concatenate(found) if found else np.zeros((0, 2), dtype=np.int64)
