"""Repairs: one-click cleanup and Make Solid.

Cleanup always starts with a plan (what would change and why), so the user
sees everything before it happens. The same functions do the work for the
browser, batch mode and scripts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import trimesh

from .analysis import small_pieces

# An opening is the model's open edge (a face scan, a relief, a mask) rather
# than a gap when it spans most of the model and the model is shallow behind
# it. A vase scanned without its bottom is deep behind its opening, so that
# opening is filled.
OPEN_EDGE_SPAN_SHARE = 0.5
OPEN_EDGE_MAX_DEPTH = 0.6

# Slicers stay responsive below this; a 0.4 mm nozzle cannot show more detail.
COMFORTABLE_TRIANGLES = 300_000
REDUCE_ABOVE_TRIANGLES = 500_000
# Repair presets: (reduce above this many triangles, down to this many).
# None keeps every triangle.
PRESETS = {
    "quick": (150_000, 150_000),     # Quick print: slices fastest; fine for most prints
    "balanced": (REDUCE_ABOVE_TRIANGLES, COMFORTABLE_TRIANGLES),
    "detail": None,                  # Keep all detail: for fine resin prints
    "flexible": (REDUCE_ABOVE_TRIANGLES, COMFORTABLE_TRIANGLES),  # TPU: as balanced; walls checked thicker
}


@dataclass
class Hole:
    edges: int
    perimeter_mm: float
    across_mm: float
    fill: bool
    reason: str


@dataclass
class CleanupPlan:
    duplicate_faces: int
    zero_area_faces: int
    debris_pieces: int
    debris_share: float  # share of the model's surface area, 0-1
    holes: list[Hole]
    flipped: bool
    crossing: int  # triangles cutting through their own piece; -1 = not checked
    triangles: int
    reduce_to: int  # 0 = keep all detail

    @property
    def holes_to_fill(self) -> list[Hole]:
        return [h for h in self.holes if h.fill]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["steps"] = self.steps()
        return data

    def steps(self) -> list[dict]:
        """Plain-English list of what cleanup would do, for the preview."""
        steps = []
        tidy = self.duplicate_faces + self.zero_area_faces
        if tidy:
            steps.append({"key": "tidy", "text": f"Remove {tidy:,} duplicate or zero-area triangles"})
        if self.debris_pieces:
            steps.append({
                "key": "remove_debris",
                "text": (
                    f"Delete {self.debris_pieces:,} loose piece{'s' if self.debris_pieces != 1 else ''} "
                    f"({self.debris_share:.1%} of the surface)"
                ),
            })
        fill = self.holes_to_fill
        if fill:
            largest = max(h.across_mm for h in fill)
            steps.append({
                "key": "fill_holes",
                "text": f"Fill {len(fill)} hole{'s' if len(fill) != 1 else ''} (largest about {largest:.3g} mm across)",
            })
        skipped = [h for h in self.holes if not h.fill]
        if skipped:
            largest = max(h.across_mm for h in skipped)
            steps.append({
                "key": "close_open_edge",
                "text": f"Also close the {largest:.3g} mm open edge (it looks like the edge of the model, so it is left open unless you tick this)",
                "checked": False,
            })
        if self.reduce_to:
            steps.append({
                "key": "reduce",
                "text": f"Reduce detail from {self.triangles:,} to about {self.reduce_to:,} triangles so the slicer stays quick",
            })
        if self.crossing > 0:
            steps.append({"key": "fix_crossing", "text": f"Repair {self.crossing:,} triangles where the surface cuts through itself"})
        elif self.crossing < 0:
            steps.append({"key": "fix_crossing", "text": "Check for places where the surface cuts through itself, and repair them"})
        if self.flipped:
            steps.append({"key": "fix_normals", "text": "Turn wrong-way triangles to face outwards"})
        return steps


def _loops(mesh: trimesh.Trimesh) -> list[np.ndarray]:
    """Boundary loops as lists of vertex indices."""
    unique, counts = np.unique(mesh.edges_sorted, axis=0, return_counts=True)
    open_edges = unique[counts == 1]
    if len(open_edges) == 0:
        return []
    return [np.array(sorted(c)) for c in trimesh.graph.connected_components(open_edges, min_len=1)]


def _hole_info(mesh: trimesh.Trimesh) -> list[Hole]:
    unique, counts = np.unique(mesh.edges_sorted, axis=0, return_counts=True)
    open_edges = unique[counts == 1]
    if len(open_edges) == 0:
        return []
    from .orient import fit_plane

    lengths = np.linalg.norm(mesh.vertices[open_edges[:, 0]] - mesh.vertices[open_edges[:, 1]], axis=1)
    diagonal = float(np.linalg.norm(mesh.extents))
    holes = []
    for loop in trimesh.graph.connected_components(open_edges, min_len=1):
        in_loop = np.isin(open_edges[:, 0], list(loop))
        perimeter = float(lengths[in_loop].sum())
        points = mesh.vertices[list(loop)]
        across = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
        is_outline = False
        if across >= OPEN_EDGE_SPAN_SHARE * diagonal and len(points) >= 3:
            _, normal = fit_plane(points)
            depth = float(np.ptp(mesh.vertices @ normal))
            is_outline = depth <= OPEN_EDGE_MAX_DEPTH * across
        holes.append(Hole(
            edges=int(in_loop.sum()),
            perimeter_mm=round(perimeter, 3),
            across_mm=round(across, 3),
            fill=not is_outline,
            reason="it looks like the open edge of the model, not a gap" if is_outline else "",
        ))
    holes.sort(key=lambda h: -h.across_mm)
    return holes


def _debris_mask(mesh: trimesh.Trimesh) -> np.ndarray:
    """True for faces that belong to small loose pieces."""
    labels = trimesh.graph.connected_component_labels(mesh.face_adjacency, node_count=len(mesh.faces))
    return small_pieces(mesh, labels)[labels]


def surface_change(before: trimesh.Trimesh, after: trimesh.Trimesh, samples: int = 20000) -> float:
    """How far the existing surface moved (mm): for points spread over the
    old surface, the distance to the new one, ignoring the farthest 2% (so
    filled holes and removed crumbs do not count)."""
    from scipy.spatial import cKDTree

    if len(before.faces) == 0 or len(after.faces) == 0:
        return 0.0
    old_points, _ = trimesh.sample.sample_surface_even(before, samples, seed=0)
    new_points, _ = trimesh.sample.sample_surface_even(after, samples * 3, seed=1)
    distances, _ = cKDTree(new_points).query(old_points)
    # Point spacing on the new surface sets a floor on what can be measured.
    spacing = float(np.median(cKDTree(new_points).query(new_points[:2000], k=2)[0][:, 1]))
    return max(0.0, float(np.quantile(distances, 0.98)) - spacing / 2)


def describe_change(moved: float) -> str:
    if moved < 0.01:
        return "the rest of the surface did not move"
    return f"the rest of the surface moved less than {moved:.2g} mm"


def plan_cleanup(mesh: trimesh.Trimesh, crossing: np.ndarray | None | bool = True, preset: str = "balanced") -> CleanupPlan:
    """What Clean up would change. ``crossing`` works as in analyze();
    ``preset`` is one of PRESETS."""
    if crossing is True:
        from .analysis import find_crossing_faces

        crossing = find_crossing_faces(mesh)
    sorted_faces = np.sort(mesh.faces, axis=1)
    duplicates = len(mesh.faces) - len(np.unique(sorted_faces, axis=0))
    zero_area = int(np.count_nonzero(mesh.area_faces <= 1e-12))

    debris = _debris_mask(mesh)
    labels = trimesh.graph.connected_component_labels(mesh.face_adjacency, node_count=len(mesh.faces))
    debris_pieces = len(np.unique(labels[debris]))
    area = float(mesh.area) or 1.0
    debris_share = float(mesh.area_faces[debris].sum()) / area

    kept = mesh.submesh([~debris], append=True) if debris.any() and (~debris).any() else mesh
    triangles = len(mesh.faces)

    return CleanupPlan(
        duplicate_faces=int(duplicates),
        zero_area_faces=zero_area,
        debris_pieces=int(debris_pieces),
        debris_share=round(debris_share, 5),
        holes=_hole_info(kept),
        flipped=not (kept.is_winding_consistent and (not kept.is_watertight or kept.volume >= 0)),
        crossing=-1 if crossing is None else int(len(crossing)),
        triangles=triangles,
        reduce_to=_reduce_target(triangles, preset),
    )


def _reduce_target(triangles: int, preset: str) -> int:
    if preset not in PRESETS:
        raise ValueError(f"preset must be one of: {', '.join(PRESETS)}.")
    limits = PRESETS[preset]
    if limits is None or triangles <= limits[0]:
        return 0
    return limits[1]


# ---------------------------------------------------------------- steps

def tidy(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    mesh = mesh.copy()
    mesh.update_faces(mesh.unique_faces() & mesh.nondegenerate_faces(height=1e-9))
    mesh.remove_unreferenced_vertices()
    mesh.merge_vertices()
    return mesh


def _collapse_slivers(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Remove zero-area triangles by collapsing each one's shortest edge. Deleting them
    outright would leave a hole, so the change is kept only if the surface stays closed."""
    flat = np.nonzero(mesh.area_faces <= 1e-12)[0]
    if not len(flat):
        return mesh
    faces = mesh.faces.copy()
    vertices = mesh.vertices
    remap = np.arange(len(vertices))
    for f in flat:
        tri = remap[faces[f]]
        pairs = [(tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])]
        a, b = min(pairs, key=lambda p: np.linalg.norm(vertices[p[0]] - vertices[p[1]]))
        if a != b:
            remap[remap == b] = a
    faces = remap[faces]
    faces = faces[(faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])]
    trial = trimesh.Trimesh(vertices, faces, process=False)
    trial.remove_unreferenced_vertices()
    if trial.is_watertight == mesh.is_watertight and trial.is_winding_consistent and len(trial.faces) > 0:
        return trial
    return mesh


def remove_debris(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    debris = _debris_mask(mesh)
    if not debris.any() or debris.all():
        return mesh
    mesh = mesh.copy()
    mesh.update_faces(~debris)
    mesh.remove_unreferenced_vertices()
    return mesh


def fix_normals(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    mesh = mesh.copy()
    trimesh.repair.fix_normals(mesh, multibody=True)
    return mesh


def _meshfix(mesh: trimesh.Trimesh):
    import pymeshfix

    tin = pymeshfix.PyTMesh()
    tin.set_quiet(True)
    tin.load_array(
        np.ascontiguousarray(mesh.vertices, dtype=np.float64),
        np.ascontiguousarray(mesh.faces, dtype=np.int32),
    )
    return tin


def _from_meshfix(tin) -> trimesh.Trimesh:
    vertices, faces = tin.return_arrays()
    return trimesh.Trimesh(vertices, faces, process=True, validate=False)


def fill_holes(mesh: trimesh.Trimesh, holes: list[Hole]) -> trimesh.Trimesh:
    """Fill the planned holes, and only those.

    MeshFix fills every hole up to a rim size, so openings that must stay
    open are first covered with a temporary cap, which is removed again
    afterwards.
    """
    fill = [h for h in holes if h.fill]
    if not fill:
        return mesh
    keep_open = [h for h in holes if not h.fill]
    work, cap_points = (_cap_openings(mesh, keep_open) if keep_open else (mesh, None))
    tin = _meshfix(work)
    tin.fill_small_boundaries(nbe=max(h.edges for h in fill) + 1, refine=True)
    result = _from_meshfix(tin)
    if cap_points is not None and len(cap_points):
        from scipy.spatial import cKDTree

        distance, _ = cKDTree(cap_points).query(result.vertices)
        is_cap_centre = distance < 1e-9
        result.update_faces(~is_cap_centre[result.faces].any(axis=1))
        result.remove_unreferenced_vertices()
    return result


def _cap_openings(mesh: trimesh.Trimesh, openings: list[Hole]) -> tuple[trimesh.Trimesh, np.ndarray]:
    """Cover each opening with a fan of triangles around a new centre point
    (so hole filling leaves it alone). Returns the capped mesh and the centre
    points, which mark the cap for removal later."""
    unique, counts = np.unique(mesh.edges_sorted, axis=0, return_counts=True)
    open_set = {tuple(e) for e in unique[counts == 1]}
    directed = mesh.edges  # each face's edges, in the face's own direction
    rim = np.array([d for d, k in zip(directed, map(tuple, np.sort(directed, axis=1))) if k in open_set])
    loops = trimesh.graph.connected_components(unique[counts == 1], min_len=1)
    wanted_sizes = {h.edges for h in openings}
    vertices = [mesh.vertices]
    faces = [mesh.faces]
    centres = []
    for loop in loops:
        members = set(loop)
        loop_edges = rim[np.isin(rim[:, 0], list(members))]
        if len(loop_edges) not in wanted_sizes:
            continue
        centre = mesh.vertices[list(members)].mean(axis=0)
        index = len(mesh.vertices) + len(centres)
        centres.append(centre)
        # Reverse each rim edge so the cap faces the same way as the surface.
        faces.append(np.c_[loop_edges[:, 1], loop_edges[:, 0], np.full(len(loop_edges), index)])
    if not centres:
        return mesh, np.zeros((0, 3))
    capped = trimesh.Trimesh(
        np.vstack(vertices + [np.asarray(centres)]), np.vstack(faces), process=False, validate=False
    )
    return capped, np.asarray(centres)


def fill_holes_curved(mesh: trimesh.Trimesh, max_edges: int = 600) -> tuple[trimesh.Trimesh, str]:
    """Fill every open hole with a curvature-following patch.

    The patch is not flat: it drapes over the hole continuing the surface
    curvature from the boundary ring. Works by fan-filling each hole, then
    relaxing the new interior vertices with a cotangent-weighted Laplacian
    (boundary vertices fixed). Small holes are iterated more so they converge
    fully; large ones get fewer passes but still improve on flat fill.
    """
    from scipy.sparse import lil_matrix
    from scipy.sparse.linalg import spsolve

    unique_sorted, counts = np.unique(mesh.edges_sorted, axis=0, return_counts=True)
    boundary_set = set(map(tuple, unique_sorted[counts == 1]))
    if not boundary_set:
        return mesh, "No holes found."

    # Build directed boundary adjacency list from face winding.
    adj: dict[int, list[int]] = {}
    for a, b in mesh.edges:
        key = (min(int(a), int(b)), max(int(a), int(b)))
        if key in boundary_set:
            adj.setdefault(int(a), []).append(int(b))

    # Trace loops, marking each directed edge as used so non-manifold
    # junctions (where two holes share a vertex) are handled correctly.
    used: set[tuple[int, int]] = set()
    loops: list[list[int]] = []
    for start in list(adj):
        for first in adj.get(start, []):
            ek = (start, first)
            if ek in used:
                continue
            ring = [start]
            used.add(ek)
            cur = first
            while cur != start:
                ring.append(cur)
                moved = False
                for nxt_v in adj.get(cur, []):
                    ek2 = (cur, nxt_v)
                    if ek2 not in used:
                        used.add(ek2)
                        cur = nxt_v
                        moved = True
                        break
                if not moved:
                    break
            if cur == start and len(ring) >= 3:
                loops.append(ring)

    small = [lp for lp in loops if len(lp) <= max_edges]
    if not small:
        return mesh, f"Found {len(loops)} hole(s) but all are larger than {max_edges} edges (too complex for curvature fill; use Clean up instead)."

    verts = list(mesh.vertices)
    faces = list(mesh.faces)
    n_orig = len(mesh.vertices)
    new_interior: list[int] = []

    for ring in small:
        ring_pos = mesh.vertices[np.array(ring)]
        centroid = ring_pos.mean(axis=0)
        ci = len(verts)
        verts.append(centroid)
        new_interior.append(ci)

        # For larger holes add intermediate rings so the Laplacian has
        # interior vertices to smooth between, not just a single centroid.
        n_inner = max(0, len(ring) // 20)  # one extra ring per 20 boundary edges
        if n_inner:
            inner_rings: list[list[int]] = []
            for k in range(1, n_inner + 1):
                t = k / (n_inner + 1)
                inner_pos = (1 - t) * ring_pos + t * centroid
                ring_start = len(verts)
                verts.extend(inner_pos.tolist())
                new_interior.extend(range(ring_start, ring_start + len(ring)))
                inner_rings.append(list(range(ring_start, ring_start + len(ring))))

            # Connect boundary → inner_0 → ... → inner_N → centroid
            all_rings = [ring] + inner_rings + [[ci] * len(ring)]
            for ri in range(len(all_rings) - 1):
                outer = all_rings[ri]
                inner = all_rings[ri + 1]
                for i in range(len(outer)):
                    a0, a1 = outer[i], outer[(i + 1) % len(outer)]
                    b0, b1 = inner[i % len(inner)], inner[(i + 1) % len(inner)]
                    if b0 != b1:
                        faces.extend([[a0, b0, b1], [a0, b1, a1]])
                    else:
                        faces.append([a0, a1, b0])
        else:
            for i, v0 in enumerate(ring):
                v1 = ring[(i + 1) % len(ring)]
                faces.append([v0, ci, v1])

    filled = trimesh.Trimesh(np.array(verts), np.array(faces), process=False)
    # DO NOT call remove_unreferenced_vertices here — that reindexes vertices
    # and would invalidate new_interior before the Laplacian step.

    if not new_interior:
        return mesh, "Could not build fill patches."

    # --- cotangent Laplacian relaxation on new interior vertices ---------------
    v = np.asarray(filled.vertices, dtype=np.float64)
    f = np.asarray(filled.faces, dtype=np.int64)
    n_v = len(v)
    # cotangent weights: for each face, cot of each corner
    # w(i,j) += 0.5 * cot(angle at k) for the third vertex k
    e0 = v[f[:, 2]] - v[f[:, 1]]
    e1 = v[f[:, 0]] - v[f[:, 2]]
    e2 = v[f[:, 1]] - v[f[:, 0]]
    # cot(angle at v0) = dot(e1,-e2) / |e1 x -e2|
    def _cot(a, b):
        dot = (a * b).sum(axis=1)
        cross = np.linalg.norm(np.cross(a, b), axis=1)
        cross = np.where(cross < 1e-12, 1e-12, cross)
        return dot / cross

    cot0 = _cot(-e1, e2)
    cot1 = _cot(-e2, e0)
    cot2 = _cot(-e0, e1)

    W = lil_matrix((n_v, n_v))
    for fi in range(len(f)):
        i, j, k = int(f[fi, 0]), int(f[fi, 1]), int(f[fi, 2])
        w = max(0.0, cot0[fi]) * 0.5
        W[j, k] += w; W[k, j] += w
        w = max(0.0, cot1[fi]) * 0.5
        W[i, k] += w; W[k, i] += w
        w = max(0.0, cot2[fi]) * 0.5
        W[i, j] += w; W[j, i] += w

    interior_set = set(new_interior)
    interior = [vid for vid in range(n_v) if vid in interior_set]
    if not interior:
        return mesh, "Fill completed (no interior vertices to smooth)."

    interior = np.array(interior, dtype=np.int64)
    boundary = np.array([i for i in range(n_v) if i not in interior_set], dtype=np.int64)

    # Build the Laplacian for interior vertices only; solve Lx = -L_boundary * x_boundary
    from scipy.sparse import csr_matrix
    Wc = W.tocsr()
    row_sum = np.asarray(Wc.sum(axis=1)).ravel()
    # Diagonal = sum of weights
    from scipy.sparse import diags
    L = diags(row_sum) - Wc
    L_int = L[np.ix_(interior, interior)]
    L_bd = L[np.ix_(interior, boundary)]
    rhs = -(L_bd @ v[boundary])
    # Solve for each coordinate
    new_v = v.copy()
    for col in range(3):
        try:
            sol = spsolve(L_int.tocsr(), rhs[:, col])
            if np.all(np.isfinite(sol)):
                new_v[interior, col] = sol
        except Exception:
            pass  # keep fan fill if solver fails

    result = trimesh.Trimesh(new_v, f, process=True, validate=False)
    trimesh.repair.fix_normals(result, multibody=True)
    result.remove_unreferenced_vertices()

    # Remove faces at non-manifold edges (an edge shared by 3+ faces means
    # two fill patches both covered the same junction edge).  Prefer keeping
    # original-mesh faces by removing the fill faces (highest indices).
    unique_e, inv_e, counts_e = np.unique(result.edges_sorted, axis=0,
                                          return_inverse=True, return_counts=True)
    if (counts_e > 2).any():
        bad_edge_mask = counts_e[inv_e.reshape(-1, 3)] > 2  # (F, 3)
        bad_face = bad_edge_mask.any(axis=1)
        if bad_face.any():
            n_orig_faces = len(mesh.faces)
            # Prefer removing fill faces (index >= n_orig_faces) over original
            fill_bad = bad_face & (np.arange(len(result.faces)) >= n_orig_faces)
            if fill_bad.any():
                result.update_faces(~fill_bad)
            else:
                result.update_faces(~bad_face)
            result.remove_unreferenced_vertices()

    result = tidy(result)

    # Final fallback: if multi-ring faces created self-intersections, let
    # pymeshfix resolve them (it only touches the intersecting triangles).
    if not result.is_watertight:
        try:
            import pymeshfix
            tin = pymeshfix.PyTMesh()
            tin.load_array(np.asarray(result.vertices, dtype=np.float64),
                           np.asarray(result.faces, dtype=np.int32))
            tin.clean()
            vf, ff = tin.return_arrays()
            result = trimesh.Trimesh(vf, ff, process=True)
        except Exception:
            pass  # leave as-is if pymeshfix is unavailable

    filled_count = len(small)
    return result, f"Filled {filled_count} hole{'s' if filled_count != 1 else ''} with curvature-following patches"


def mean_curvature(mesh: trimesh.Trimesh) -> np.ndarray:
    """Discrete mean curvature at each vertex, in mm^-1.

    Uses the cotangent-weighted Laplacian (Meyer et al. 2003).  Positive =
    convex, negative = concave.  Returns a 1-D array of length n_vertices.
    """
    v = np.asarray(mesh.vertices, dtype=np.float64)
    f = np.asarray(mesh.faces, dtype=np.int64)

    # Cotangent weights
    e0 = v[f[:, 2]] - v[f[:, 1]]
    e1 = v[f[:, 0]] - v[f[:, 2]]
    e2 = v[f[:, 1]] - v[f[:, 0]]

    def _cot(a, b):
        c = np.linalg.norm(np.cross(a, b), axis=1)
        c = np.where(c < 1e-12, 1e-12, c)
        return (a * b).sum(axis=1) / c

    cot0 = _cot(-e1, e2)   # at vertex f[:,0]
    cot1 = _cot(-e2, e0)   # at vertex f[:,1]
    cot2 = _cot(-e0, e1)   # at vertex f[:,2]

    n_v = len(v)
    Lv = np.zeros_like(v)  # Laplacian * v, accumulate weighted position sums
    W = np.zeros(n_v)       # sum of weights per vertex

    for fi in range(len(f)):
        i, j, k = int(f[fi, 0]), int(f[fi, 1]), int(f[fi, 2])
        w0, w1, w2 = max(0.0, cot0[fi]), max(0.0, cot1[fi]), max(0.0, cot2[fi])
        Lv[j] += w2 * (v[k] - v[j])
        Lv[k] += w2 * (v[j] - v[k])
        Lv[i] += w1 * (v[k] - v[i])
        Lv[k] += w1 * (v[i] - v[k])
        Lv[i] += w0 * (v[j] - v[i])
        Lv[j] += w0 * (v[i] - v[j])
        W[i] += w1 + w0
        W[j] += w2 + w0
        W[k] += w2 + w1

    W = np.where(W < 1e-12, 1e-12, W)
    H_vec = Lv / W[:, None]  # (mean curvature normal) per vertex
    H_mag = np.linalg.norm(H_vec, axis=1)

    # Sign convention: for a convex surface the Laplacian (mean curvature normal)
    # points INWARD (toward the centre of curvature), opposite to the outward
    # vertex normal.  So convex → H_vec · n < 0.  Negate so that convex = positive.
    normals = np.asarray(mesh.vertex_normals, dtype=np.float64)
    sign = -np.sign((H_vec * normals).sum(axis=1))
    sign = np.where(sign == 0, 1.0, sign)
    return (sign * H_mag).astype(np.float64)


def surface_deviation(mesh: trimesh.Trimesh, reference: trimesh.Trimesh) -> np.ndarray:
    """Per-vertex distance from ``mesh`` to the nearest face of ``reference``.

    Returns a 1-D array of distances in mm, one per vertex in ``mesh``.
    """
    from scipy.spatial import cKDTree

    _, dist_v, _ = trimesh.proximity.closest_point(reference, mesh.vertices)
    return dist_v.astype(np.float64)


def self_intersecting_faces(mesh: trimesh.Trimesh) -> np.ndarray:
    """Indices of faces that cut through faces of the same piece.

    Separate pieces that overlap each other are left out: slicers merge
    those without trouble.
    """
    from .intersections import crossing_pairs

    pairs = crossing_pairs(mesh.vertices, mesh.faces)
    if len(pairs) == 0:
        return np.zeros(0, dtype=np.int64)
    labels = trimesh.graph.connected_component_labels(mesh.face_adjacency, node_count=len(mesh.faces))
    same_piece = labels[pairs[:, 0]] == labels[pairs[:, 1]]
    return np.unique(pairs[same_piece])


def fix_self_intersections(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh, int]:
    bad = self_intersecting_faces(mesh)
    if len(bad) == 0:
        return mesh, 0
    labels = trimesh.graph.connected_component_labels(mesh.face_adjacency, node_count=len(mesh.faces))
    pieces = []
    fixed_count = 0
    for label in np.unique(labels):
        members = np.flatnonzero(labels == label)
        piece = mesh.submesh([members], append=True)
        in_piece = np.flatnonzero(np.isin(members, bad))
        if len(in_piece):
            repaired = _fix_piece(piece, in_piece)
            if repaired is not piece:
                fixed_count += len(in_piece)
            piece = repaired
        pieces.append(piece)
    return trimesh.util.concatenate(pieces), fixed_count


def _similar(before: trimesh.Trimesh, after: trimesh.Trimesh, bad: np.ndarray) -> bool:
    """A repair may only change the shape about as much as the surface was
    broken: repairing 7 triangles must not move the outline or lose area,
    while a badly folded model may change a lot."""
    if len(after.faces) == 0 or before.area <= 0:
        return False
    broken = float(before.area_faces[bad].sum() / before.area)
    diagonal = float(np.linalg.norm(before.extents)) or 1.0
    moved = float(np.abs(after.bounds - before.bounds).max())
    change = abs(after.area / before.area - 1)
    return moved <= (0.02 + broken) * diagonal and change <= 0.1 + 2 * broken


def _fix_piece(piece: trimesh.Trimesh, bad: np.ndarray) -> trimesh.Trimesh:
    """Repair the triangles of one piece that cut through it. MeshFix's own
    repair is tried first; it sometimes throws away a big part of the shape,
    so the result is checked. Then a local repair (remove the crossing
    triangles and their neighbours, fill the gap) is tried. If both change
    the shape, the piece is left as it is: a few crossing triangles are far
    better than a lost shoulder."""
    tin = _meshfix(piece)
    tin.clean(max_iters=10, inner_loops=3)
    whole = _from_meshfix(tin)
    if _similar(piece, whole, bad):
        return whole
    for rings in (2, 4, 6):  # a wider patch when the first one cannot be sewn up cleanly
        local = _local_fix(piece, bad, rings)
        if local is not None and _similar(piece, local, bad):
            return local
    return piece


def _local_fix(piece: trimesh.Trimesh, bad: np.ndarray, rings: int = 2) -> trimesh.Trimesh | None:
    region = np.zeros(len(piece.faces), dtype=bool)
    region[bad] = True
    adjacency = piece.face_adjacency
    for _ in range(rings):
        touching = region[adjacency[:, 0]] | region[adjacency[:, 1]]
        region[adjacency[touching].ravel()] = True
    if region.all():
        return None
    kept = piece.submesh([np.flatnonzero(~region)], append=True)
    holes = [Hole(h.edges, h.perimeter_mm, h.across_mm, True, "") for h in _hole_info(kept)]
    try:
        filled = fill_holes(kept, holes)
    except Exception:  # noqa: BLE001 - MeshFix can fail on odd gaps; the caller keeps the piece
        return None
    if len(self_intersecting_faces(filled)):
        return None
    return filled


def reduce_detail(mesh: trimesh.Trimesh, target: int) -> trimesh.Trimesh:
    if not target or len(mesh.faces) <= target:
        return mesh
    import fast_simplification

    was_closed = mesh.is_watertight
    vertices, faces = fast_simplification.simplify(
        np.asarray(mesh.vertices, dtype=np.float32),
        np.asarray(mesh.faces, dtype=np.int32),
        target_reduction=1 - target / len(mesh.faces),
    )
    reduced = trimesh.Trimesh(vertices, faces, process=True, validate=False)
    return heal_small_faults(reduced) if was_closed else reduced


def heal_small_faults(mesh: trimesh.Trimesh, attempts: int = 3) -> trimesh.Trimesh:
    """Mend the odd pinch or fold that edge-collapsing leaves in a closed mesh.

    Back-to-back triangle pairs and every triangle touching an edge shared by
    more than two triangles are removed (a wider ring each attempt, so the
    gap becomes a simple hole), crumbs are dropped and the gaps filled.
    """
    for ring in range(1, attempts + 1):
        _, inverse, counts = np.unique(np.sort(mesh.faces, axis=1), axis=0, return_inverse=True, return_counts=True)
        bad = counts[inverse.ravel()] > 1
        edges, edge_counts = np.unique(mesh.edges_sorted, axis=0, return_counts=True)
        pinched = edges[edge_counts > 2]
        if not bad.any() and len(pinched) == 0 and mesh.is_watertight:
            return mesh
        if len(pinched):
            near = np.zeros(len(mesh.vertices), dtype=bool)
            near[pinched.ravel()] = True
            for _ in range(ring - 1):  # grow the ring on later attempts
                near[mesh.faces[near[mesh.faces].any(axis=1)].ravel()] = True
            bad |= near[mesh.faces].any(axis=1)
        mesh = mesh.copy()
        mesh.update_faces(~bad)
        labels = trimesh.graph.connected_component_labels(mesh.face_adjacency, node_count=len(mesh.faces))
        sizes = np.bincount(labels)
        mesh.update_faces(sizes[labels] > 4)
        mesh.remove_unreferenced_vertices()
        if not mesh.is_watertight:
            mesh = fill_holes(mesh, [Hole(h.edges, h.perimeter_mm, h.across_mm, True, "") for h in _hole_info(mesh)])
    return mesh


def cleanup(
    mesh: trimesh.Trimesh,
    remove_loose_pieces: bool = True,
    fill: bool = True,
    fix_crossing: bool = True,
    reduce: bool = True,
    close_open_edge: bool = False,
    preset: str = "balanced",
) -> tuple[trimesh.Trimesh, str]:
    """The one-click cleanup. Returns the new mesh and a receipt."""
    # The self-crossing repair below finds its own faults, so the plan
    # does not need to look for them.
    plan = plan_cleanup(mesh, crossing=None, preset=preset)
    done = []

    tidied = plan.duplicate_faces + plan.zero_area_faces
    mesh = tidy(mesh)
    if tidied:
        done.append(f"removed {tidied:,} duplicate or zero-area triangles")

    if remove_loose_pieces and plan.debris_pieces:
        mesh = remove_debris(mesh)
        done.append(f"deleted {plan.debris_pieces:,} loose piece{'s' if plan.debris_pieces != 1 else ''}")
    reference = mesh  # what the surface looked like before any reshaping
    crossing_fixed = False

    if fill:
        holes = _hole_info(mesh)
        if close_open_edge:
            holes = [Hole(h.edges, h.perimeter_mm, h.across_mm, True, "") for h in holes]
        to_fill = [h for h in holes if h.fill]
        if to_fill:
            mesh = fill_holes(mesh, holes)
            largest = max(h.across_mm for h in to_fill)
            done.append(f"filled {len(to_fill)} hole{'s' if len(to_fill) != 1 else ''} (largest {largest:.3g} mm)")

    # Reduce before the self-crossing repair: it is far quicker on fewer
    # triangles, and reducing can itself create crossings to repair.
    if reduce and plan.reduce_to:
        count = len(mesh.faces)
        mesh = tidy(reduce_detail(mesh, plan.reduce_to))
        done.append(f"reduced detail from {count:,} to {len(mesh.faces):,} triangles")

    if fix_crossing:
        from .intersections import TooComplex

        try:
            mesh, crossing = fix_self_intersections(mesh)
        except TooComplex:
            crossing = 0
            done.append("skipped the self-crossing repair (model too complex)")
        if crossing:
            crossing_fixed = True
            done.append(f"repaired {crossing:,} triangles that cut through the surface")
            # The repair can leave crumbs, flattened triangles and small gaps
            # of its own: tidy those up so cleanup finishes clean.
            mesh = tidy(mesh)
            if remove_loose_pieces:
                mesh = remove_debris(mesh)
            if fill:
                gaps = [h for h in _hole_info(mesh) if h.fill or close_open_edge]
                mesh = fill_holes(mesh, [Hole(h.edges, h.perimeter_mm, h.across_mm, True, "") for h in gaps])

    if fill and (np.unique(mesh.edges_sorted, axis=0, return_counts=True)[1] > 2).any():
        mesh = heal_small_faults(mesh)  # a big cap can pinch where it meets the outline

    before = mesh.copy()
    mesh = fix_normals(mesh)
    if not np.array_equal(before.faces, mesh.faces):
        done.append("turned wrong-way triangles outwards")

    mesh = _collapse_slivers(mesh)
    if reduce and plan.reduce_to or crossing_fixed:
        done.append(describe_change(surface_change(reference, mesh)))
    if fill and not close_open_edge:
        left = [h for h in _hole_info(mesh) if not h.fill]
        if left:
            widest = max(h.across_mm for h in left)
            done.append(
                f"left a {widest:.3g} mm opening alone because it looks like the model's own open edge "
                "(tick 'Also close a big opening' to close it)"
            )
    if not done:
        return mesh, "Cleanup found nothing to change"
    return mesh, "Cleanup: " + "; ".join(done)


# ---------------------------------------------------------------- make solid

def _voxelize(mesh: trimesh.Trimesh, pitch: float, pad: int) -> tuple[np.ndarray, np.ndarray]:
    """Mark every grid cell the surface passes through. Sampling the surface
    densely is much faster than exact voxelisation and just as good here."""
    count = int(min(2e7, max(1e5, mesh.area / (pitch * pitch) * 6)))
    points, _ = trimesh.sample.sample_surface(mesh, count, seed=0)
    points = np.vstack([points, mesh.vertices])
    origin = mesh.bounds[0] - pad * pitch
    index = np.floor((points - origin) / pitch).astype(np.int64)
    grid = np.zeros(index.max(axis=0) + pad + 1, dtype=bool)
    grid[tuple(index.T)] = True
    return grid, origin


def make_solid(mesh: trimesh.Trimesh, detail_mm: float = 0.0) -> tuple[trimesh.Trimesh, str]:
    """Rebuild the model as one closed solid from a voxel grid.

    Used when normal repair cannot cope. Holes are closed first, small gaps
    close up, hidden inner walls disappear, and the result is always
    watertight. Detail finer than the grid is softened, so the grid size is
    picked from the model: 0.2 mm (finer than a 0.4 mm nozzle) or coarser for
    big models so memory stays reasonable. A model under 30 mm gets at least
    150 cells across whatever its size, so a scan saved in metres (a 0.15
    "mm" bunny) is not reduced to a single voxel and refused.
    """
    from scipy import ndimage

    span = float(mesh.extents.max())
    if span <= 0:
        raise ValueError("The model has no size.")
    pitch = max(detail_mm, span / 300) if detail_mm else max(span / 300, min(0.2, span / 150))

    # Close every opening first, including a big open edge: otherwise there
    # is no inside to fill.
    closed = tidy(mesh)
    holes = [Hole(h.edges, h.perimeter_mm, h.across_mm, True, "") for h in _hole_info(closed)]
    closed = fill_holes(closed, holes)

    pad = 3
    surface, origin = _voxelize(closed, pitch, pad)
    grid = ndimage.binary_dilation(surface, iterations=1)
    grid = ndimage.binary_fill_holes(grid)
    grid = ndimage.binary_erosion(grid, iterations=1)
    if grid.sum() < 1.2 * surface.sum():
        raise ValueError(
            "Make Solid could not find the inside of this model, probably because "
            "it is a single open surface. Try Give it thickness instead."
        )

    solid = grid_to_mesh(grid, origin, pitch)
    moved = describe_change(surface_change(closed, solid))
    return solid, f"Rebuilt as a closed solid with {pitch:.2g} mm detail ({len(solid.faces):,} triangles); {moved}"


def _survives_stl(mesh: trimesh.Trimesh) -> bool:
    """Is the mesh still closed once its numbers are stored as 32-bit, as an STL does?"""
    v32 = mesh.vertices.astype(np.float32)
    unique, inverse = np.unique(v32, axis=0, return_inverse=True)
    faces = inverse.reshape(-1)[mesh.faces]
    if (faces[:, 0] == faces[:, 1]).any() or (faces[:, 1] == faces[:, 2]).any() or (faces[:, 0] == faces[:, 2]).any():
        return False
    return trimesh.Trimesh(unique.astype(np.float64), faces, process=False).is_watertight


def _stl_safe(mesh: trimesh.Trimesh) -> trimesh.Trimesh | None:
    """The mesh as an STL file would store it, or None if that opens it. Triangles that
    shrink to nothing at 32-bit are dropped: removing a collapsed edge keeps a mesh closed."""
    unique, inverse = np.unique(mesh.vertices.astype(np.float32), axis=0, return_inverse=True)
    faces = inverse.reshape(-1)[mesh.faces]
    keep = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
    out = trimesh.Trimesh(unique.astype(np.float64), faces[keep], process=False)
    return out if out.is_watertight else None


def grid_to_mesh(grid: np.ndarray, origin: np.ndarray, pitch: float) -> trimesh.Trimesh:
    """Smooth closed surface around the filled cells of a voxel grid."""
    import mcubes
    from scipy import ndimage

    field = ndimage.gaussian_filter(grid.astype(np.float32), 1.0)
    # A level that lands almost exactly on a grid value makes hairline triangles that
    # an STL file cannot store, so move the level a touch until the surface is sound.
    for level in (0.5, 0.503, 0.497, 0.508, 0.492):
        vertices, faces = mcubes.marching_cubes(field, level)
        vertices = vertices * pitch + origin + pitch / 2
        solid = trimesh.Trimesh(vertices, faces, process=True)
        # Marching cubes now and then leaves a stray triangle pinched onto the
        # surface where the shape is very thin.
        solid = tidy(heal_small_faults(solid))
        trimesh.repair.fix_normals(solid, multibody=True)
        if len(solid.faces) > COMFORTABLE_TRIANGLES:
            solid = tidy(reduce_detail(solid, COMFORTABLE_TRIANGLES))
            trimesh.repair.fix_normals(solid, multibody=True)
        if _survives_stl(solid):
            break
    return solid


# ---------------------------------------------------------------- thickness

def thicken(mesh: trimesh.Trimesh, thickness_mm: float, outward: bool = False, gap_mm: float = 0.0) -> trimesh.Trimesh:
    """Turn an open surface into a closed shell ``thickness_mm`` thick.

    Normally the scanned surface stays exactly where it is and the new back
    surface is built behind it (against the direction the surface faces).
    With ``outward`` the shell is built in front of it instead, starting
    ``gap_mm`` away, so it fits over the scanned surface like a cover. The
    edges are joined with a thin wall.
    """
    mesh = tidy(mesh)
    trimesh.repair.fix_winding(mesh)
    unique, counts = np.unique(mesh.edges_sorted, axis=0, return_counts=True)
    if not np.any(counts == 1):
        raise ValueError("This model is already closed, so it has no open surface to thicken.")

    n = len(mesh.vertices)
    normals = mesh.vertex_normals
    if outward:
        front = mesh.vertices + normals * gap_mm
        back = mesh.vertices + normals * (gap_mm + thickness_mm)
    else:
        front = mesh.vertices
        back = mesh.vertices - normals * thickness_mm
    vertices = np.vstack([front, back])
    front_faces = mesh.faces
    back_faces = mesh.faces[:, ::-1] + n

    # Open edges in the direction their own triangle uses them.
    directed = mesh.edges  # (faces * 3, 2), in face order
    key = np.sort(directed, axis=1)
    lookup = {tuple(e) for e in unique[counts == 1]}
    rim = np.array([d for d, k in zip(directed, map(tuple, key)) if k in lookup])
    a, b = rim[:, 0], rim[:, 1]
    walls = np.vstack([np.c_[b, a, a + n], np.c_[b, a + n, b + n]])

    shell = trimesh.Trimesh(vertices, np.vstack([front_faces, back_faces, walls]), process=True)
    trimesh.repair.fix_normals(shell, multibody=True)
    return shell


# ---------------------------------------------------------------- hollowing

def hollow(mesh: trimesh.Trimesh, wall_mm: float, drain_holes: bool, hole_mm: float) -> tuple[trimesh.Trimesh, str]:
    """Hollow a closed model for resin printing, leaving walls ``wall_mm``
    thick, with drain holes through the bottom so resin can run out."""
    import manifold3d
    from scipy import ndimage

    from .cut import CutError, _affine, _to_manifold, _to_mesh

    try:
        solid = _to_manifold(mesh)
    except CutError:
        raise ValueError("The model must be closed before it can be hollowed. Run Clean up first.") from None

    span = float(mesh.extents.max())
    pitch = max(wall_mm / 4, span / 250)
    pad = 3
    surface, origin = _voxelize(mesh, pitch, pad)
    inside = ndimage.binary_fill_holes(ndimage.binary_dilation(surface, iterations=1))
    # Distance to the real surface, not to the voxel edge: voxel distances are off by
    # up to a cell, which left walls thinner than asked (down to nothing at thin tips).
    from scipy.spatial import cKDTree

    count = int(min(6e6, max(2e5, mesh.area / (pitch / 4) ** 2)))
    samples = np.vstack([trimesh.sample.sample_surface(mesh, count, seed=1)[0], mesh.vertices])
    tree = cKDTree(samples)
    # Distance to the nearest surface of any kind, so inner shells keep their own wall too.
    coarse = ndimage.distance_transform_edt(~surface) * pitch
    # Only cells near the wall line need the exact distance; deeper ones are cavity for sure.
    band = inside & (coarse > wall_mm * 0.5)
    near = np.argwhere(band & (coarse <= wall_mm + 3 * pitch))
    distance = np.where(band, np.float32(1e6), np.float32(0))
    if len(near):
        distance[tuple(near.T)] = tree.query(origin + (near + 0.5) * pitch, workers=-1)[0]

    margin = 0.35 * pitch  # marching cubes pulls the surface a little off the cell edge
    for _ in range(5):
        cavity = inside & (distance >= wall_mm + margin)
        cavity = _drop_small_pockets(cavity, pitch, 100.0)
        if cavity.sum() < 27:
            raise ValueError(f"The model is too thin to hollow with {wall_mm:g} mm walls.")
        inner = grid_to_mesh(cavity, origin, pitch)
        thinnest = float(tree.query(inner.vertices, workers=-1)[0].min())
        if thinnest >= 0.97 * wall_mm:
            break
        margin += wall_mm - thinnest + 0.02 * pitch
    # The outer skin plus the cavity turned inside out. No boolean is needed, so no
    # new points appear and the shell saves cleanly as STL.
    result = trimesh.Trimesh(
        np.vstack([mesh.vertices, inner.vertices]),
        np.vstack([mesh.faces, inner.faces[:, ::-1] + len(mesh.vertices)]),
        process=False,
    )

    spots = _drain_spots(cavity, origin, pitch, hole_mm) if drain_holes else []
    holes = 0
    bottom = float(mesh.bounds[0][2])
    skin = result

    def drill_all(spot_list: list) -> trimesh.Trimesh | None:
        """Drill every spot straight up from the bottom. Cutting makes points that can land
        closer together than an STL file can tell apart (32-bit numbers), which opens the
        shell on reload, so raise manifold's merge tolerance step by step and keep the first
        cut that saves cleanly and does not cross itself."""
        try:
            shell = _to_manifold(skin)
        except CutError:
            return None
        fallback = None
        for tolerance in (0.0, 0.01, 0.03, 0.06, 0.1):
            carved = shell.set_tolerance(tolerance) if tolerance else shell
            for spot in spot_list:
                length = spot[2] - bottom + 2 * pitch + 1.0
                drill = manifold3d.Manifold.cylinder(length, hole_mm / 2, hole_mm / 2, 32, False)
                if tolerance:
                    drill = drill.set_tolerance(tolerance)
                carved = carved - drill.transform(_affine(np.eye(3), (spot[0], spot[1], bottom - 1.0)))
            candidate = _stl_safe(_to_mesh(carved))
            if candidate is None:
                continue
            if fallback is None:
                fallback = candidate
            if not len(self_intersecting_faces(candidate)):
                return candidate
        return fallback

    def sealed_pockets(shape: trimesh.Trimesh) -> list[trimesh.Trimesh]:
        # Sealed pockets are voids (negative volume). Specks under 1 mm3 are rounding debris.
        return [part for part in shape.split(only_watertight=False) if part.volume < -1.0]

    if spots:
        drilled = drill_all(spots)
        # A pocket the first holes missed (a spout, a leg) gets its own hole at its lowest point.
        for _ in range(3):
            if drilled is None:
                break
            missed = sealed_pockets(drilled)
            if not missed:
                break
            extra = []
            for pocket in missed:
                low = pocket.vertices[pocket.vertices[:, 2] < pocket.bounds[0][2] + pitch]
                extra.append(np.array([low[:, 0].mean(), low[:, 1].mean(), pocket.bounds[0][2] + 0.5 * pitch]))
            more = drill_all(spots + extra)
            if more is None:
                break
            drilled, spots = more, spots + extra
        if drilled is not None:
            result, holes = drilled, len(spots)

    undrained = len(sealed_pockets(result)) if holes else 0

    saved = 1 - result.volume / mesh.volume if mesh.volume > 0 else 0
    receipt = f"Hollowed with {wall_mm:g} mm walls, using about {saved:.0%} less resin"
    if holes:
        receipt += f"; added {holes} drain hole{'s' if holes != 1 else ''} ({hole_mm:g} mm) at the bottom"
    if undrained:
        receipt += (f". {undrained} closed pocket{'s' if undrained != 1 else ''} inside could not be drained: "
                    "resin would be trapped there")
    if drain_holes and not holes:
        receipt += (". Drain holes could not be cut cleanly on this model, so the hollow is sealed: "
                    "add a drain hole in your slicer before printing")
    return result, receipt


def _drop_small_pockets(cavity: np.ndarray, pitch: float, min_mm3: float) -> np.ndarray:
    """Leave pockets under ``min_mm3`` solid: they are not worth a drain hole. Pockets are
    counted on the smoothed grid the hollow is cut from, where a thin neck pinches off
    the tip of a bigger pocket into a speck of its own."""
    from scipy import ndimage

    for _ in range(2):
        smooth = ndimage.gaussian_filter(cavity.astype(np.float32), 1.0) > 0.5
        labels, count = ndimage.label(smooth)
        if count == 0:
            return cavity
        volumes = np.bincount(labels.ravel(), minlength=count + 1) * pitch**3
        small = (volumes < min_mm3)[labels] & (labels > 0)
        if count > 1 and small.any() and not small.all():
            cavity = cavity & ~ndimage.binary_dilation(small, iterations=2)
        else:
            break
    return cavity


def _drain_spots(cavity: np.ndarray, origin: np.ndarray, pitch: float, hole_mm: float) -> list[np.ndarray]:
    """Where to drill up into the hollow. Each separate pocket gets a spot at
    its lowest level, kept away from its edge so the drill is sure to reach
    it; the biggest pocket gets two, far apart, so air can get in."""
    from scipy import ndimage

    # The hollow is cut from the smoothed grid (see grid_to_mesh), where a thin
    # neck can pinch a pocket off, so find the pockets in that same grid.
    cavity = ndimage.gaussian_filter(cavity.astype(np.float32), 1.0) > 0.5
    labels, count = ndimage.label(cavity)
    if count == 0:
        return []
    sizes = np.bincount(labels.ravel())[1:]
    reach = max(1, int(np.ceil(hole_mm / 2 / pitch)))
    spots: list[np.ndarray] = []
    for rank, pocket in enumerate(np.argsort(sizes)[::-1][:4]):
        if sizes[pocket] < 27:
            continue
        region = labels == pocket + 1
        floor = int(np.nonzero(region.any(axis=(0, 1)))[0][0])
        layer = region[:, :, floor:floor + 3].any(axis=2)
        core = ndimage.binary_erosion(layer, iterations=reach)
        xs, ys = np.nonzero(core if core.any() else layer)
        picks = [np.argmin(xs), np.argmax(xs)] if rank == 0 else [int(len(xs) // 2)]
        for i in picks:
            spot = origin + (np.array([xs[i], ys[i], floor + 1]) + 0.5) * pitch
            if all(np.linalg.norm(spot[:2] - other[:2]) >= 3 * hole_mm for other in spots):
                spots.append(spot)
    return spots
