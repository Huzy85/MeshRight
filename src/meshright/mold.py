"""Split mold generation for casting.

Analyses a part for mold-making feasibility (undercuts, fine details, draft angles),
then builds printable mold halves with alignment pins, a pour spout and optional
printable C-clamps.

Designed for silicone and rigid resin casting from FDM-printed molds.
"""
from __future__ import annotations

import numpy as np
import trimesh
from dataclasses import dataclass, field


class MoldError(Exception):
    pass


# ---------------------------------------------------------------- analysis


@dataclass
class MoldIssue:
    code: str
    message: str       # plain English, shown to the user
    detail: str        # extra context / what the fix does
    default_fix: bool  # whether the recommended action is to fix it


@dataclass
class MoldAnalysis:
    split_axis: np.ndarray   # unit pull direction (extraction axis)
    split_offset: float      # parting plane position along split_axis
    n_parts: int             # 2 or 3 — build() produces matching piece count
    fine_feature_count: int
    needs_smoothing: bool
    needs_draft: bool
    wall_mm: float
    clearance_mm: float
    issues: list = field(default_factory=list)
    is_simple: bool = True   # True = no user decisions needed


def _undercut_fraction(mesh: trimesh.Trimesh, axis: np.ndarray) -> float:
    """Fraction of faces that create undercuts in a 2-part mold along axis.

    A face above the parting plane that faces downward is trapped by the upper half.
    A face below the parting plane that faces upward is trapped by the lower half.
    A plain box has zero undercuts — each face faces away from the parting plane.
    """
    n = axis / (np.linalg.norm(axis) + 1e-9)
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    offset = float((verts @ n).mean())
    face_dots = mesh.face_normals @ n
    face_pos = mesh.triangles_center @ n
    above = face_pos > offset
    upper_undercuts = above & (face_dots < -0.10)
    lower_undercuts = (~above) & (face_dots > 0.10)
    return float((upper_undercuts | lower_undercuts).mean())


def _find_split_axis(mesh: trimesh.Trimesh):
    """Try Z, −Z, X, Y and 2 PCA axes; pick the one with fewest undercuts."""
    candidates = [
        np.array([0.0, 0.0, 1.0]),
        np.array([0.0, 0.0, -1.0]),
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
    ]
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    centred = verts - verts.mean(axis=0)
    try:
        _, _, Vt = np.linalg.svd(centred, full_matrices=False)
        for v in Vt:
            n = v / (np.linalg.norm(v) + 1e-9)
            candidates.extend([n.copy(), -n.copy()])
    except Exception:
        pass

    best_axis, best_frac = candidates[0], float("inf")
    for ax in candidates:
        frac = _undercut_fraction(mesh, ax)
        if frac < best_frac:
            best_frac = frac
            best_axis = ax.copy()
    return best_axis, best_frac


def analyse(
    mesh: trimesh.Trimesh,
    nozzle_mm: float = 0.4,
    min_wall_mm: float = 1.5,
) -> MoldAnalysis:
    """Analyse a part and return everything needed to build or describe its mold."""
    split_axis, undercut_frac = _find_split_axis(mesh)

    verts = np.asarray(mesh.vertices, dtype=np.float64)
    dots = verts @ split_axis
    split_offset = float((dots.max() + dots.min()) / 2)

    # Fine feature count: faces smaller than one nozzle-width circle
    areas = trimesh.triangles.area(mesh.triangles)
    threshold_area = np.pi * (nozzle_mm / 2) ** 2
    fine_count = int((areas < threshold_area).sum())

    # Draft angle: faces with <1° angle to the parting plane are near-vertical
    face_dots = np.abs(mesh.face_normals @ split_axis)
    near_vertical_frac = float((face_dots < np.sin(np.radians(1.0))).mean())
    needs_draft = near_vertical_frac > 0.05

    # Recommended wall thickness
    extents = np.asarray(mesh.bounding_box.extents)
    wall_mm = float(np.clip(extents.max() * 0.15, 8.0, 20.0))

    issues: list[MoldIssue] = []
    n_parts = 2

    if undercut_frac > 0.02:
        n_parts = 3
        pct = undercut_frac * 100
        issues.append(MoldIssue(
            code="undercuts",
            message=f"About {pct:.0f}% of this part would get stuck in a 2-piece mold.",
            detail=(
                "These are areas where the mold can't open without tearing. "
                "A 3rd mold piece would let everything release cleanly — "
                "but this adds cost and complexity. Many parts still cast fine "
                "in a 2-piece mold with flexible silicone."
            ),
            default_fix=False,
        ))

    if fine_count > 50:
        issues.append(MoldIssue(
            code="fine_features",
            message=f"{fine_count} details are finer than the printer nozzle ({nozzle_mm} mm).",
            detail=(
                "These tiny features will blur or disappear in the mold cavity. "
                "Smoothing them slightly gives a cleaner mold — "
                "the cast part looks better because the cavity surface is clean, "
                "not because details were added."
            ),
            default_fix=True,
        ))

    if needs_draft:
        issues.append(MoldIssue(
            code="draft",
            message="Some walls are perfectly straight up-and-down — the cast part may grip the mold.",
            detail=(
                "A tiny outward taper (called a draft angle) lets the cast part "
                "pop out cleanly. Silicone molds are flexible enough to not need this, "
                "but rigid molds almost always do."
            ),
            default_fix=False,
        ))

    is_simple = not any(i.code != "fine_features" for i in issues)

    return MoldAnalysis(
        split_axis=split_axis,
        split_offset=split_offset,
        n_parts=n_parts,
        fine_feature_count=fine_count,
        needs_smoothing=fine_count > 50,
        needs_draft=needs_draft,
        wall_mm=wall_mm,
        clearance_mm=0.2,
        issues=issues,
        is_simple=is_simple,
    )


# ---------------------------------------------------------------- geometry helpers


def _inflate(mesh: trimesh.Trimesh, clearance_mm: float) -> trimesh.Trimesh:
    """Uniform outward scale from centroid — approximate surface offset for small clearances."""
    if clearance_mm <= 0:
        return mesh.copy()
    m = mesh.copy()
    c = np.asarray(m.centroid)
    extents = np.asarray(m.bounding_box.extents)
    # Per-axis scale so clearance is roughly uniform
    scale = (extents + 2 * clearance_mm) / np.where(extents > 0, extents, 1.0)
    scale_factor = float(scale.mean())
    m.vertices = (np.asarray(m.vertices) - c) * scale_factor + c
    return m


def _mold_box(mesh: trimesh.Trimesh, wall_mm: float) -> trimesh.Trimesh:
    center = np.asarray(mesh.bounds).mean(axis=0)
    extents = np.asarray(mesh.bounding_box.extents) + 2 * wall_mm
    box = trimesh.creation.box(extents=extents)
    box.apply_translation(center)
    return box


def _prep(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Merge vertices so manifold3d accepts the mesh."""
    m = trimesh.Trimesh(
        np.asarray(mesh.vertices, dtype=np.float64),
        np.asarray(mesh.faces, dtype=np.int64),
        process=False,
    )
    m.merge_vertices()
    return m


def _to_manifold(mesh: trimesh.Trimesh):
    from .cut import _to_manifold as _cut_to_manifold
    return _cut_to_manifold(_prep(mesh))


def _to_mesh(m) -> trimesh.Trimesh:
    from .cut import _to_mesh as _cut_to_mesh
    return _cut_to_mesh(m)


def _boolean_subtract(box: trimesh.Trimesh, part: trimesh.Trimesh) -> trimesh.Trimesh:
    try:
        result = _to_manifold(box) - _to_manifold(part)
        return _to_mesh(result)
    except Exception as e:
        raise MoldError(f"Could not hollow out the mold cavity: {e}") from e


def _split_halves(
    mold: trimesh.Trimesh,
    axis: np.ndarray,
    offset: float,
) -> tuple[trimesh.Trimesh, trimesh.Trimesh]:
    """Split mold at the parting plane. Each half keeps its flat parting face — that is the
    interface surface the two halves press against each other."""
    n = axis / np.linalg.norm(axis)
    try:
        pos_m, neg_m = _to_manifold(mold).split_by_plane(n, offset)
        return _to_mesh(pos_m), _to_mesh(neg_m)
    except Exception as e:
        raise MoldError(f"Could not split mold at parting plane: {e}") from e


def _add_bolt_holes(
    half: trimesh.Trimesh,
    axis: np.ndarray,
    offset: float,
    bolt_dia: float = 4.5,
    n_bolts: int = 4,
) -> trimesh.Trimesh:
    """Drill bolt holes through the parting-face region for clamping."""
    n = axis / np.linalg.norm(axis)
    verts = np.asarray(half.vertices, dtype=np.float64)
    on_plane = np.abs((verts @ n) - offset) < 2.0
    if not on_plane.any():
        return half

    plane_verts = verts[on_plane]
    u = np.array([1.0, 0.0, 0.0]) if abs(n[2]) > 0.9 else np.cross(n, [0, 0, 1.0])
    u /= np.linalg.norm(u)
    v = np.cross(n, u)

    pu = plane_verts @ u
    pv = plane_verts @ v
    u_mid = (pu.max() + pu.min()) / 2
    v_mid = (pv.max() + pv.min()) / 2
    u_r = (pu.max() - pu.min()) * 0.38
    v_r = (pv.max() - pv.min()) * 0.38

    corners = [
        u_mid + u_r * su + 0 * v_mid for su in (-1, 1)
    ]
    bolt_locs = [
        plane_verts.mean(axis=0) + u * u_r * su + v * v_r * sv
        for su in (-1, 1) for sv in (-1, 1)
    ][:n_bolts]

    mold_h = np.asarray(half.vertices, dtype=np.float64) @ n
    mold_depth = mold_h.max() - mold_h.min() + 2.0

    half_m = _to_manifold(half)
    for loc in bolt_locs:
        hole = trimesh.creation.cylinder(radius=bolt_dia / 2, height=mold_depth, sections=12)
        hole_center = loc - n * (mold_h.max() - mold_h.min()) / 2 + n * (mold_h.max() - offset)
        # Position: run through the mold half perpendicular to parting plane
        hole.apply_translation(loc + n * (mold_h.min() + mold_depth / 2 - 1.0))
        try:
            half_m = half_m - _to_manifold(hole)
        except Exception:
            pass

    try:
        return _to_mesh(half_m)
    except Exception:
        return half


def _add_alignment_pins(
    pos: trimesh.Trimesh,
    neg: trimesh.Trimesh,
    axis: np.ndarray,
    offset: float,
    pin_dia: float = 4.0,
    pin_height: float = 6.0,
) -> tuple[trimesh.Trimesh, trimesh.Trimesh]:
    """Add alignment pin stubs to pos half, matching holes to neg half."""
    n = axis / np.linalg.norm(axis)
    verts = np.asarray(pos.vertices, dtype=np.float64)
    on_plane = np.abs((verts @ n) - offset) < 2.0
    if not on_plane.any():
        return pos, neg

    plane_verts = verts[on_plane]
    u = np.array([1.0, 0.0, 0.0]) if abs(n[2]) > 0.9 else np.cross(n, [0, 0, 1.0])
    u /= np.linalg.norm(u)
    v = np.cross(n, u)

    pu = plane_verts @ u
    pv = plane_verts @ v
    pin_locs = [
        plane_verts.mean(axis=0) + u * (pu.max() - pu.min()) * 0.35 * su
                                 + v * (pv.max() - pv.min()) * 0.35 * sv
        for su, sv in ((-1, -1), (1, 1))
    ]

    pos_m = _to_manifold(pos)
    neg_m = _to_manifold(neg)

    for loc in pin_locs:
        # Pin on the pos half: stub pointing in -n direction (into neg half)
        pin = trimesh.creation.cylinder(radius=pin_dia / 2, height=pin_height, sections=16)
        pin.apply_translation(loc - n * (pin_height / 2 + 0.1))

        # Hole in the neg half: slightly wider for clearance
        hole = trimesh.creation.cylinder(radius=pin_dia / 2 + 0.15, height=pin_height + 1.0, sections=16)
        hole.apply_translation(loc + n * (pin_height / 2 + 0.1))

        try:
            pos_m = pos_m | _to_manifold(pin)
        except Exception:
            pass
        try:
            neg_m = neg_m - _to_manifold(hole)
        except Exception:
            pass

    try:
        pos = _to_mesh(pos_m)
    except Exception:
        pass
    try:
        neg = _to_mesh(neg_m)
    except Exception:
        pass

    return pos, neg


def _add_pour_spout(
    top: trimesh.Trimesh,
    axis: np.ndarray,
    offset: float,
    top_dia: float = 10.0,
    bot_dia: float = 4.0,
) -> trimesh.Trimesh:
    """Drill a tapered pour hole through the top of the positive mold half."""
    n = axis / np.linalg.norm(axis)
    verts = np.asarray(top.vertices, dtype=np.float64)
    top_z = float((verts @ n).max())
    bot_z = float((verts @ n).min())
    height = top_z - bot_z + 1.0

    # Center the spout over the part cavity
    center_xy = np.asarray(top.bounds).mean(axis=0)
    center_xy = center_xy - n * (center_xy @ n)
    spout_center = center_xy + n * ((top_z + bot_z) / 2)

    spout = trimesh.creation.cone(radius=top_dia / 2, height=height, sections=20)
    spout.apply_translation(spout_center)

    try:
        return _to_mesh(_to_manifold(top) - _to_manifold(spout))
    except Exception:
        return top


def _add_vent_channels(
    half: trimesh.Trimesh,
    axis: np.ndarray,
    offset: float,
    channel_w: float = 1.5,
    channel_d: float = 0.8,
) -> trimesh.Trimesh:
    """Carve small air-vent grooves in the parting face so trapped air escapes when pouring."""
    n = axis / np.linalg.norm(axis)
    verts = np.asarray(half.vertices, dtype=np.float64)
    on_plane = np.abs((verts @ n) - offset) < 1.0
    if not on_plane.any():
        return half

    plane_verts = verts[on_plane]
    u = np.array([1.0, 0.0, 0.0]) if abs(n[2]) > 0.9 else np.cross(n, [0, 0, 1.0])
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    pu = plane_verts @ u
    pv = plane_verts @ v
    center = plane_verts.mean(axis=0)

    # Two vents: one along each in-plane axis, from center to edge
    half_m = _to_manifold(half)
    for direction, length in [(u, (pu.max() - pu.min()) / 2), (v, (pv.max() - pv.min()) / 2)]:
        groove = trimesh.creation.box([channel_w, length + 2, channel_d * 2])
        vent_center = center + direction * (length / 2) - n * channel_d
        # Rotate groove to face in direction
        rot = trimesh.transformations.rotation_matrix(
            np.arccos(np.clip(direction @ np.array([0.0, 1.0, 0.0]), -1, 1)),
            np.cross(np.array([0.0, 1.0, 0.0]), direction) + 1e-9,
        )
        groove.apply_transform(rot)
        groove.apply_translation(vent_center)
        try:
            half_m = half_m - _to_manifold(groove)
        except Exception:
            pass

    try:
        return _to_mesh(half_m)
    except Exception:
        return half


# ---------------------------------------------------------------- clamp


def make_clamp(
    mold_half: trimesh.Trimesh,
    flange_thickness: float = 5.0,
    arm_thickness: float = 8.0,
    bolt_dia: float = 4.5,
) -> trimesh.Trimesh:
    """Printable C-clamp that grips both mold halves at the parting flange.

    Slot width = flange_thickness × 2 + 3 mm tolerance.
    Bolt holes for M4 bolts through both arms.
    """
    lo, hi = np.asarray(mold_half.bounds[0]), np.asarray(mold_half.bounds[1])
    span_xy = max(hi[0] - lo[0], hi[1] - lo[1])

    slot_h = flange_thickness * 2 + 3.0   # gap for two mold flanges
    clamp_h = slot_h + arm_thickness * 2
    clamp_w = span_xy + 10.0
    clamp_d = 20.0  # depth of the C

    outer = trimesh.creation.box([clamp_w, clamp_h, clamp_d])
    inner = trimesh.creation.box([clamp_w - arm_thickness * 2, slot_h, clamp_d + 2])
    inner.apply_translation([arm_thickness / 2, 0, 0])

    try:
        clamp_m = _to_manifold(outer) - _to_manifold(inner)
        clamp = _to_mesh(clamp_m)
    except Exception:
        clamp = outer

    # Bolt holes through both clamp arms at each end
    for x_off in [-clamp_w * 0.35, clamp_w * 0.35]:
        for y_off in [-(slot_h / 2 + arm_thickness / 2), (slot_h / 2 + arm_thickness / 2)]:
            hole = trimesh.creation.cylinder(radius=bolt_dia / 2, height=clamp_d + 2, sections=12)
            hole.apply_translation([x_off, y_off, 0])
            try:
                clamp_m = _to_manifold(clamp) - _to_manifold(hole)
                clamp = _to_mesh(clamp_m)
            except Exception:
                pass

    return clamp


# ---------------------------------------------------------------- 3-part helpers


def _perp_axis(axis: np.ndarray) -> np.ndarray:
    """Return a unit vector perpendicular to axis, using the most orthogonal cardinal."""
    n = axis / np.linalg.norm(axis)
    for candidate in [np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]), np.array([0.0, 0.0, 1.0])]:
        p = candidate - n * (candidate @ n)
        if np.linalg.norm(p) > 0.3:
            return p / np.linalg.norm(p)
    return np.array([1.0, 0.0, 0.0])


# ---------------------------------------------------------------- build


def build(
    mesh: trimesh.Trimesh,
    analysis: MoldAnalysis,
    smooth: bool = True,
    add_clamps: bool = True,
    clearance_mm: float = 0.2,
    wall_mm: float | None = None,
) -> dict[str, trimesh.Trimesh]:
    """Build the mold.

    Returns a dict with keys: 'top', 'bottom', and (if add_clamps) 'clamp'.
    All are printable trimesh.Trimesh objects.
    'top' and 'bottom' are aligned to each other — stack them at the parting plane.
    """
    if wall_mm is None:
        wall_mm = analysis.wall_mm

    working = mesh.copy()
    if smooth and analysis.needs_smoothing:
        import trimesh.smoothing as sm
        sm.filter_taubin(working, lamb=0.5, nu=0.53, iterations=5)

    part_inflated = _inflate(working, clearance_mm)
    box = _mold_box(part_inflated, wall_mm)
    mold_full = _boolean_subtract(box, part_inflated)

    top, bot = _split_halves(mold_full, analysis.split_axis, analysis.split_offset)

    top, bot = _add_alignment_pins(top, bot, analysis.split_axis, analysis.split_offset)
    top = _add_pour_spout(top, analysis.split_axis, analysis.split_offset)
    top = _add_vent_channels(top, analysis.split_axis, analysis.split_offset)
    top = _add_bolt_holes(top, analysis.split_axis, analysis.split_offset)
    bot = _add_bolt_holes(bot, analysis.split_axis, analysis.split_offset)

    result: dict[str, trimesh.Trimesh] = {"top": top, "bottom": bot}

    # For 3-part molds: split the bottom half along a perpendicular axis so each
    # side piece extracts sideways, releasing undercut features that face inward.
    if analysis.n_parts == 3:
        perp = _perp_axis(analysis.split_axis)
        verts_bot = np.asarray(bot.vertices, dtype=np.float64)
        perp_offset = float((verts_bot @ perp).mean())
        try:
            side_a, side_b = _split_halves(bot, perp, perp_offset)
            result = {"top": top, "side_a": side_a, "side_b": side_b}
        except MoldError:
            pass  # keep 2-part if the split fails

    if add_clamps:
        half = result.get("top", top)
        result["clamp"] = make_clamp(half)

    return result
