"""Which way is up, laying models flat, and cutting a flat bottom."""

from __future__ import annotations

import numpy as np
import trimesh

DOWN = np.array([0.0, 0.0, -1.0])

# Candidates closer than this are the same suggestion.
SAME_DIRECTION_DEGREES = 15

# A bottom this much of the model's outline or more counts as a real flat base.
MIN_FLAT_SHARE = 0.02


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def rotation_to_down(direction) -> np.ndarray:
    """4x4 rotation that turns ``direction`` to point straight down."""
    direction = _unit(direction)
    if np.allclose(direction, DOWN):
        return np.eye(4)
    if np.allclose(direction, -DOWN):
        return trimesh.transformations.rotation_matrix(np.pi, [1, 0, 0])
    return trimesh.geometry.align_vectors(direction, DOWN)


def fit_plane(points) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares plane through points: (centre, unit normal)."""
    points = np.asarray(points, dtype=np.float64)
    centre = points.mean(axis=0)
    _, _, vt = np.linalg.svd(points - centre)
    return centre, _unit(vt[-1])


def _open_rim_candidates(mesh: trimesh.Trimesh) -> list[dict]:
    """Big, nearly flat openings: where a scan sat on the turntable."""
    unique, counts = np.unique(mesh.edges_sorted, axis=0, return_counts=True)
    open_edges = unique[counts == 1]
    if len(open_edges) == 0:
        return []
    size = float(mesh.extents.max()) or 1.0
    out = []
    for loop in trimesh.graph.connected_components(open_edges, min_len=3):
        points = mesh.vertices[list(loop)]
        span = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
        if span < 0.25 * size:
            continue
        centre, normal = fit_plane(points)
        flatness = float(np.abs((points - centre) @ normal).max())
        if flatness > 0.05 * span:
            continue
        # The opening faces away from the body of the model.
        if (mesh.vertices.mean(axis=0) - centre) @ normal > 0:
            normal = -normal
        # A turntable opening sits at one end: the whole model lies on one
        # side of it. A hole in the side has model on both sides.
        beyond = (mesh.vertices - centre) @ normal > 0.02 * size
        if beyond.mean() > 0.03:
            continue
        # ...and it spans most of the model's width both ways, where a hole in
        # the side (or a slit) is narrow in at least one direction.
        _, _, vt = np.linalg.svd(points - centre)
        if any(np.ptp(points @ axis) < 0.5 * np.ptp(mesh.vertices @ axis) for axis in vt[:2]):
            continue
        out.append({
            "down": normal,
            "reason": "Flat open edge: probably where it sat on the scanner's turntable",
            "score": 3.0 + span / size,
        })
    return out


def _flat_side_candidates(mesh: trimesh.Trimesh) -> list[dict]:
    """Large flat areas of the outer shape that the model can rest on."""
    try:
        hull = mesh.convex_hull
    except Exception:  # a flat or tiny model has no outer shape to rest on
        return []
    if len(hull.facets) == 0:
        return []
    areas = hull.facets_area
    total = float(hull.area) or 1.0
    centre = mesh.center_mass if mesh.is_watertight and mesh.volume > 0 else mesh.centroid
    out = []
    for index in np.argsort(-areas)[:12]:
        share = areas[index] / total
        if share < MIN_FLAT_SHARE:
            break
        normal = hull.facets_normal[index]
        # Stable if the centre sits above the flat area.
        faces = hull.facets[index]
        points = hull.vertices[np.unique(hull.faces[faces])]
        rotation = rotation_to_down(normal)[:3, :3]
        flat2d = (points @ rotation.T)[:, :2]
        c2d = (rotation @ centre)[:2]
        steady = _inside_polygon(c2d, flat2d)
        out.append({
            "down": normal,
            "reason": f"Flat side ({share:.0%} of the outside)" + ("" if steady else ", but may tip over"),
            "score": (2.0 if steady else 0.5) + share * 4,
        })
    return out


def _inside_polygon(point, points) -> bool:
    """Is a 2D point inside the convex hull of 2D points?"""
    from scipy.spatial import ConvexHull, QhullError

    try:
        hull = ConvexHull(points)
    except (QhullError, ValueError):
        return False
    return bool(np.all(hull.equations[:, :2] @ point + hull.equations[:, 2] <= 1e-9))


def up_candidates(mesh: trimesh.Trimesh, limit: int = 3) -> list[dict]:
    """The most likely bottoms, best first, each as a down direction and reason."""
    candidates = _open_rim_candidates(mesh) + _flat_side_candidates(mesh)
    candidates.append({"down": DOWN, "reason": "As it is now", "score": 1.0})

    chosen: list[dict] = []
    for cand in sorted(candidates, key=lambda c: -c["score"]):
        if all(np.degrees(np.arccos(np.clip(_unit(cand["down"]) @ _unit(c["down"]), -1, 1))) > SAME_DIRECTION_DEGREES for c in chosen):
            chosen.append(cand)
        if len(chosen) == limit:
            break
    return [
        {"down": [round(float(v), 6) for v in _unit(c["down"])], "reason": c["reason"]}
        for c in chosen
    ]


# ---------------------------------------------------------------- actions' work

def place_on_bed(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    low = mesh.bounds[0]
    centre = mesh.bounds.mean(axis=0)
    mesh.apply_translation((-centre[0], -centre[1], -low[2]))
    return mesh


def square_up(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Spin the model on the bed so its footprint lines up with the grid."""
    points = mesh.vertices[:, :2]
    if len(points) >= 3:
        try:
            transform, _ = trimesh.bounds.oriented_bounds_2D(points)
        except Exception:  # degenerate footprints (a line or a point)
            return mesh
        angle = np.arctan2(transform[1, 0], transform[0, 0])
        mesh.apply_transform(trimesh.transformations.rotation_matrix(angle, [0, 0, 1]))
    return mesh


def set_bottom(mesh: trimesh.Trimesh, down) -> trimesh.Trimesh:
    mesh.apply_transform(rotation_to_down(down))
    return place_on_bed(square_up(mesh))


def lay_flat_on_points(mesh: trimesh.Trimesh, points) -> trimesh.Trimesh:
    centre, normal = fit_plane(points)
    # The plane's normal should point into the model, so "down" is opposite.
    if (mesh.vertices - centre).mean(axis=0) @ normal < 0:
        normal = -normal
    return set_bottom(mesh, -normal)


def cut_flat_bottom(mesh: trimesh.Trimesh, cut_mm: float) -> trimesh.Trimesh:
    """Slice ``cut_mm`` off the bottom and seal the cut with a flat face."""
    import manifold3d

    solid = manifold3d.Manifold(manifold3d.Mesh(
        vert_properties=np.asarray(mesh.vertices, dtype=np.float32),
        tri_verts=np.asarray(mesh.faces, dtype=np.uint32),
    ))
    if solid.status() != manifold3d.Error.NoError:
        raise ValueError("The model must be closed before it can be cut. Run Clean up first.")
    height = float(mesh.bounds[0][2]) + cut_mm
    kept = solid.trim_by_plane((0.0, 0.0, 1.0), height)
    result = kept.to_mesh()
    out = trimesh.Trimesh(np.asarray(result.vert_properties)[:, :3], np.asarray(result.tri_verts), process=True)
    if len(out.faces) == 0:
        raise ValueError("That cut would remove the whole model.")
    from . import colour

    if colour.has(mesh):
        colour.transfer(mesh, out)  # before it moves down onto the bed
    return place_on_bed(out)


def add_flat_base(mesh: trimesh.Trimesh, depth_mm: float) -> trimesh.Trimesh:
    """Give a rounded bottom a flat foot without removing anything: the
    outline ``depth_mm`` above the lowest point is filled straight down to
    the bed. The model keeps its full height."""
    from .cut import CutError, _to_manifold

    try:
        solid = _to_manifold(mesh)
    except CutError:
        raise ValueError("The model must be closed before a base can be added. Run Clean up first.") from None
    low = float(mesh.bounds[0][2])
    outline = solid.slice(low + depth_mm)
    if outline.is_empty():
        raise ValueError("There is nothing to stand on at that height. Try a thicker base.")
    # Start the foot a hair below the lowest point: a foot bottom touching
    # that point exactly leaves a pinched corner in the joined surface.
    nudge = 0.005
    foot = outline.extrude(depth_mm + nudge).translate((0.0, 0.0, low - nudge))
    joined = (solid + foot).to_mesh()
    # Taken as manifold3d made it: merging near-identical corners here would
    # break the closed surface.
    result = trimesh.Trimesh(np.asarray(joined.vert_properties)[:, :3], np.asarray(joined.tri_verts), process=False)
    from . import colour

    if colour.has(mesh):
        colour.transfer(mesh, result)  # before it moves onto the bed
    return place_on_bed(result)


def footprint(mesh: trimesh.Trimesh, tolerance_mm: float = 0.3) -> tuple[float, bool]:
    """Area touching the bed (mm², from the outline of the touching points)
    and whether the centre of mass sits above it."""
    from scipy.spatial import ConvexHull, QhullError

    low = mesh.bounds[0][2]
    touching = mesh.vertices[mesh.vertices[:, 2] <= low + tolerance_mm][:, :2]
    centre = (mesh.center_mass if mesh.is_watertight and mesh.volume > 0 else mesh.centroid)[:2]
    if len(touching) < 3:
        return 0.0, False
    try:
        hull = ConvexHull(touching)
    except (QhullError, ValueError):
        return 0.0, False
    return float(hull.volume), _inside_polygon(centre, touching)
