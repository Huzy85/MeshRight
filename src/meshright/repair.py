"""Repairs: one-click cleanup and Make Solid.

Cleanup always starts with a plan (what would change and why), so the user
sees everything before it happens. The same functions do the work for the
browser, batch mode and scripts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import trimesh

from .analysis import DEBRIS_FACE_SHARE

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
    sizes = np.bincount(labels)
    small = sizes < max(1, DEBRIS_FACE_SHARE * sizes.max())
    return small[labels]


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
    local = _local_fix(piece, bad)
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

    before = mesh.copy()
    mesh = fix_normals(mesh)
    if not np.array_equal(before.faces, mesh.faces):
        done.append("turned wrong-way triangles outwards")

    if not done:
        return mesh, "Cleanup found nothing to change"
    if reduce and plan.reduce_to or crossing_fixed:
        done.append(describe_change(surface_change(reference, mesh)))
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
    big models so memory stays reasonable.
    """
    from scipy import ndimage

    span = float(mesh.extents.max())
    if span <= 0:
        raise ValueError("The model has no size.")
    pitch = max(detail_mm or 0.2, span / 300)

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


def grid_to_mesh(grid: np.ndarray, origin: np.ndarray, pitch: float) -> trimesh.Trimesh:
    """Smooth closed surface around the filled cells of a voxel grid."""
    import mcubes
    from scipy import ndimage

    field = ndimage.gaussian_filter(grid.astype(np.float32), 1.0)
    vertices, faces = mcubes.marching_cubes(field, 0.5)
    vertices = vertices * pitch + origin + pitch / 2
    solid = trimesh.Trimesh(vertices, faces, process=True)
    # Marching cubes now and then leaves a stray triangle pinched onto the
    # surface where the shape is very thin.
    solid = tidy(heal_small_faults(solid))
    trimesh.repair.fix_normals(solid, multibody=True)
    if len(solid.faces) > COMFORTABLE_TRIANGLES:
        solid = tidy(reduce_detail(solid, COMFORTABLE_TRIANGLES))
        trimesh.repair.fix_normals(solid, multibody=True)
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
    depth = ndimage.distance_transform_edt(inside) * pitch
    cavity = depth > wall_mm + pitch
    if cavity.sum() < 27:
        raise ValueError(f"The model is too thin to hollow with {wall_mm:g} mm walls.")
    inner = grid_to_mesh(cavity, origin, pitch)
    try:
        hollowed = solid - _to_manifold(inner)
    except CutError:
        raise ValueError("Could not hollow this model. Try Make Solid first.") from None

    holes = 0
    if drain_holes:
        # Drain from the lowest points of the hollow, straight down through the bottom.
        low = inner.vertices[inner.vertices[:, 2] <= inner.bounds[0][2] + max(wall_mm, 2 * pitch)]
        spots = [low[np.argmin(low[:, 0])], low[np.argmax(low[:, 0])]]
        if np.linalg.norm(spots[0] - spots[1]) < 3 * hole_mm:
            spots = spots[:1]
        bottom = float(mesh.bounds[0][2])
        for spot in spots:
            length = spot[2] - bottom + 2 * pitch + 1.0
            drill = manifold3d.Manifold.cylinder(length, hole_mm / 2, hole_mm / 2, 32, False)
            hollowed = hollowed - drill.transform(_affine(np.eye(3), (spot[0], spot[1], bottom - 1.0)))
            holes += 1

    result = _to_mesh(hollowed)
    saved = 1 - result.volume / mesh.volume if mesh.volume > 0 else 0
    receipt = f"Hollowed with {wall_mm:g} mm walls, using about {saved:.0%} less resin"
    if holes:
        receipt += f"; added {holes} drain hole{'s' if holes != 1 else ''} ({hole_mm:g} mm) at the bottom"
    return result, receipt
