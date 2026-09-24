"""Mesh analysis: find printability problems and compute a print-readiness score.

Everything here is plain, well-known geometry. No settings are needed: load a
mesh, call :func:`analyze`, get back a report that a beginner can read.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import trimesh

from . import formats

SUPPORTED_EXTENSIONS = (".stl", ".obj", ".ply", ".3mf", ".glb", ".gltf", ".off", ".amf", ".xyz", ".asc", ".pts")

# Highlight data sent to the viewer is capped so huge broken scans stay usable.
MAX_HIGHLIGHT_SEGMENTS = 100_000

# A disconnected piece smaller than this share of the model's faces counts as
# "floating debris" (scan noise, stray triangles) rather than a real part.
DEBRIS_FACE_SHARE = 0.01

# Highest score a model with a blocking error can get.
UNPRINTABLE_SCORE_CAP = 59

# Outside this range the units are probably wrong (the file stores none).
TINY_MODEL_MM = 10

# Above this the self-crossing check is skipped when a file opens (it would
# take too long); Clean up reduces the detail and checks afterwards.
MAX_CROSSING_CHECK_TRIANGLES = 600_000

PLA_DENSITY_G_CM3 = 1.24

# Above this, slicers get slow; reducing to REDUCED_TRIANGLES loses nothing a
# 0.4 mm nozzle can print.
HEAVY_MESH_TRIANGLES = 1_000_000
REDUCED_TRIANGLES = 300_000

# Bed contact: below this (in mm², or as a share of the model's footprint) a
# model is likely to wobble or come loose. A flat cut aims for the "good" size.
MIN_CONTACT_MM2 = 20
SMALL_CONTACT_SHARE = 0.02
GOOD_CONTACT_SHARE = 0.08

# Offered next to problems that one-click cleanup repairs. "preview" asks the
# viewer to show the cleanup plan first.
CLEANUP_FIX = {"label": "Clean up…", "action": "cleanup", "params": {}, "preview": True}

# Closer to the bed than this counts as sitting on it.
ON_BED_TOLERANCE_MM = 0.01
HUGE_MODEL_MM = 1000


class MeshLoadError(ValueError):
    """The file could not be read as a triangle mesh."""


@dataclass
class Issue:
    code: str
    severity: str  # "error" (will not print well), "warning" (may cause trouble), "info"
    title: str
    detail: str
    count: int
    penalty: int
    # One-click fixes the viewer can offer: {"label", "action", "params"}
    fixes: list[dict] = field(default_factory=list)


@dataclass
class Report:
    score: int
    verdict: str
    printable: bool
    stats: dict
    issues: list[Issue] = field(default_factory=list)
    highlights: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def load_mesh(path: str | Path, file_type: str | None = None) -> trimesh.Trimesh:
    """Load a mesh file in millimetres, Z up, with vertices merged by position.

    UVs and normals are dropped on purpose: they split vertices along texture
    seams, which would show up as false "holes". Colours are kept as one
    colour per point (textures are turned into point colours first).
    """
    path = Path(path)
    file_type = (file_type or path.suffix).lower().lstrip(".")
    if f".{file_type}" not in SUPPORTED_EXTENSIONS:
        raise MeshLoadError(
            f"Unsupported file type '.{file_type}'. Supported: {', '.join(SUPPORTED_EXTENSIONS)}"
        )
    from . import pointcloud

    points = None
    colours = None
    try:
        if file_type == "amf":
            vertices, faces = formats.read_amf(path)
        elif f".{file_type}" in pointcloud.POINT_EXTENSIONS:
            points = pointcloud.read_text_points(path)
            vertices, faces = points, np.zeros((0, 3), dtype=np.int64)
        else:
            loaded = trimesh.load(path, file_type=file_type, process=False)
            if isinstance(loaded, trimesh.PointCloud):
                loaded = trimesh.Trimesh(vertices=loaded.vertices, faces=np.zeros((0, 3), dtype=np.int64), process=False)
            elif isinstance(loaded, trimesh.Scene):
                loaded = _scene_to_mesh(loaded)
            if not isinstance(loaded, trimesh.Trimesh):
                raise MeshLoadError("The file does not contain any triangles.")
            vertices, faces = np.asarray(loaded.vertices, dtype=np.float64), loaded.faces
            if len(faces):
                from .colour import from_loaded

                colours = from_loaded(loaded)
            if len(faces) == 0 and len(vertices):
                points = np.asarray(vertices, dtype=np.float64)  # a PLY of points only
            if file_type in ("glb", "gltf"):
                vertices = trimesh.transform_points(vertices, formats.GLTF_TO_PRINT)
            elif file_type == "3mf":
                units = getattr(loaded, "units", None) or "millimeter"
                vertices = vertices * trimesh.units.unit_conversion(units, "millimeters")
    except MeshLoadError:
        raise
    except Exception as exc:  # the readers raise many different exception types
        raise MeshLoadError(f"Could not read the file: {exc}") from exc

    if points is not None:
        try:
            mesh, note = pointcloud.points_to_mesh(points)
        except ValueError as exc:
            raise MeshLoadError(str(exc)) from exc
        mesh.metadata["notice"] = note
        return mesh

    if len(faces) == 0:
        raise MeshLoadError("The file does not contain any triangles.")
    faces = np.asarray(faces, dtype=np.int64)
    vertices = np.asarray(vertices, dtype=np.float64)
    if faces.ndim != 2 or faces.shape[1] != 3 or faces.min() < 0 or faces.max() >= len(vertices):
        raise MeshLoadError("The file is damaged: some triangles point to corners that do not exist.")
    if not np.all(np.isfinite(vertices)):
        raise MeshLoadError("The file is damaged: some corners have no valid position.")
    try:
        return trimesh.Trimesh(vertices=vertices, faces=faces, vertex_colors=colours, process=True, validate=False)
    except Exception as exc:
        raise MeshLoadError(f"Could not read the file: {exc}") from exc


def _scene_to_mesh(scene: trimesh.Scene) -> trimesh.Trimesh | None:
    """All the parts of a scene (GLB, 3MF) as one mesh, keeping colours:
    each part's texture or colours become point colours first."""
    from .colour import NEUTRAL, from_loaded

    parts = [g for g in scene.dump(concatenate=False) if isinstance(g, trimesh.Trimesh) and len(g.faces)]
    if not parts:
        return None
    colours = [from_loaded(p) for p in parts]
    vertices, faces, offset = [], [], 0
    for part in parts:
        vertices.append(np.asarray(part.vertices, dtype=np.float64))
        faces.append(np.asarray(part.faces) + offset)
        offset += len(part.vertices)
    mesh = trimesh.Trimesh(np.vstack(vertices), np.vstack(faces), process=False)
    if any(c is not None for c in colours):
        mesh.visual = trimesh.visual.ColorVisuals(mesh, vertex_colors=np.vstack([
            c if c is not None else np.tile(NEUTRAL, (len(p.vertices), 1)) for c, p in zip(colours, parts)
        ]))
    return mesh


def _unit_fixes(largest: float, units: tuple[str, ...]) -> list[dict]:
    from .actions import UNIT_FACTORS

    return [
        {
            "label": f"Saved in {unit}: make it {largest * UNIT_FACTORS[unit]:.4g} mm",
            "action": "convert_units",
            "params": {"from_units": unit},
        }
        for unit in units
    ]


def _section_outline_area(mesh: trimesh.Trimesh, height: float) -> float:
    """Area inside the outline of a horizontal slice (convex outline, which is
    close enough for judging how well a base will stand)."""
    from scipy.spatial import ConvexHull, QhullError

    segments = trimesh.intersections.mesh_plane(mesh, plane_normal=(0, 0, 1), plane_origin=(0, 0, height))
    if len(segments) < 2:
        return 0.0
    try:
        return float(ConvexHull(segments.reshape(-1, 3)[:, :2]).volume)
    except (QhullError, ValueError):
        return 0.0


def suggest_flat_cut(mesh: trimesh.Trimesh) -> float | None:
    """Smallest cut (mm) that gives the bottom a reasonable flat area, or None."""
    height = float(mesh.extents[2])
    low = float(mesh.bounds[0][2])
    wanted = max(MIN_CONTACT_MM2, GOOD_CONTACT_SHARE * float(mesh.extents[0] * mesh.extents[1]))
    for cut in np.unique(np.r_[0.2, 0.5, np.linspace(0.5, 0.25 * height, 24)]):
        if cut >= 0.25 * height:
            break
        if _section_outline_area(mesh, low + cut) >= wanted:
            return float(round(cut, 1 if cut >= 1 else 2))
    return None


def _stability_issues(mesh: trimesh.Trimesh) -> list[Issue]:
    from .orient import footprint

    area, steady = footprint(mesh)
    base = float(mesh.extents[0] * mesh.extents[1]) or 1.0
    if steady and area >= min(MIN_CONTACT_MM2, SMALL_CONTACT_SHARE * base):
        return []
    cut = suggest_flat_cut(mesh)
    fixes = [{"label": f"Cut {cut:g} mm flat", "action": "cut_flat_bottom", "params": {"cut_mm": cut}}] if cut else []
    if not steady:
        return [Issue(
            code="may_tip_over",
            severity="warning",
            title="May tip over on the bed",
            detail=(
                "Its weight is not above the part that touches the bed. Choose a "
                "different bottom, or cut a flat base."
            ),
            count=1,
            penalty=5,
            fixes=fixes,
        )]
    return [Issue(
        code="small_contact",
        severity="warning",
        title=f"Only {area:.2g} mm² touches the bed",
        detail=(
            "A tiny contact spot can wobble or come loose while printing. Cutting a "
            "thin flat base gives it something to stand on."
        ),
        count=1,
        penalty=5,
        fixes=fixes,
    )]


def unique_rows(rows: np.ndarray, base: int) -> tuple[np.ndarray, np.ndarray]:
    """Unique rows of small non-negative integers, and how often each occurs.

    Much faster than ``np.unique(axis=0)`` on big meshes: each row is packed
    into one 64-bit number first, when that fits.
    """
    rows = np.asarray(rows, dtype=np.int64)
    width = rows.shape[1]
    if len(rows) == 0 or float(base) ** width >= 2**62:
        return np.unique(rows, axis=0, return_counts=True)
    keys = np.zeros(len(rows), dtype=np.int64)
    for column in range(width):
        keys = keys * base + rows[:, column]
    unique_keys, first, counts = np.unique(keys, return_index=True, return_counts=True)
    return rows[first], counts


def _scaled_penalty(count: int, per_item: float, cap: int) -> int:
    if count <= 0:
        return 0
    return int(min(cap, max(1, round(count * per_item))))


def _segments(mesh: trimesh.Trimesh, edges: np.ndarray) -> tuple[list[float], bool]:
    truncated = len(edges) > MAX_HIGHLIGHT_SEGMENTS
    edges = edges[:MAX_HIGHLIGHT_SEGMENTS]
    coords = mesh.vertices[edges].reshape(-1)
    return [round(float(c), 5) for c in coords], truncated


def _count_loops(edges: np.ndarray) -> int:
    """Number of separate holes = connected groups of open edges."""
    if len(edges) == 0:
        return 0
    return len(trimesh.graph.connected_components(edges, min_len=1))


def _open_surface_fixes(mesh: trimesh.Trimesh) -> list[dict]:
    """A face scan, mask or relief is an open surface: offer thickness."""
    from .repair import _hole_info

    if any(not h.fill for h in _hole_info(mesh)):
        return [
            {"label": "Give it 2 mm thickness", "action": "thicken", "params": {"thickness_mm": 2}},
            CLEANUP_FIX,
        ]
    return []


def find_crossing_faces(mesh: trimesh.Trimesh) -> np.ndarray | None:
    """Faces that cut through their own piece, or None if the mesh is too
    complex to check in reasonable time."""
    from .intersections import TooComplex
    from .repair import self_intersecting_faces

    if len(mesh.faces) > MAX_CROSSING_CHECK_TRIANGLES:
        return None
    try:
        return self_intersecting_faces(mesh)
    except TooComplex:
        return None


def _fit_issues(mesh: trimesh.Trimesh, printer) -> list[Issue]:
    """Does the model fit the printer as it stands (turning on the bed is fine)?"""
    bx, by, bz = printer.bed_x, printer.bed_y, printer.bed_z

    def fit_factor(extents) -> float:
        ex, ey, ez = (float(v) for v in extents)
        flat = max(min(bx / ex, by / ey), min(bx / ey, by / ex)) if ex > 0 and ey > 0 else np.inf
        return min(flat, bz / ez if ez > 0 else np.inf)

    factor = fit_factor(mesh.extents)
    if factor >= 1:
        return []
    ex, ey, ez = (float(v) for v in mesh.extents)
    # Parts laid out side by side (after a split) may be printed one at a time.
    labels = trimesh.graph.connected_component_labels(mesh.face_adjacency, node_count=len(mesh.faces))
    if labels.max() > 0:
        parts = [mesh.vertices[np.unique(mesh.faces[labels == k])] for k in range(labels.max() + 1)]
        if all(fit_factor(np.ptp(p, axis=0)) >= 1 for p in parts):
            return [Issue(
                code="parts_fit_separately",
                severity="info",
                title="Every part fits your printer on its own",
                detail="Together they are wider than the bed, so print them in a few goes (most slicers can split the parts onto several plates).",
                count=1,
                penalty=0,
            )]
    fits_turned = all(np.sort([ex, ey, ez]) <= np.sort([bx, by, bz]))
    shrink = max(float(f"{factor * 0.98:.3g}"), 1e-6)
    detail = (
        f"It is {ex:.4g} × {ey:.4g} × {ez:.4g} mm, and the {printer.name} prints up to "
        f"{bx:g} × {by:g} × {bz:g} mm."
    )
    if fits_turned:
        detail += " It would fit lying a different way: try Which way is up?"
    return [Issue(
        code="too_big",
        severity="error",
        title="Too big for your printer",
        detail=detail,
        count=1,
        penalty=20,
        fixes=[
            {"label": f"Shrink to fit (×{shrink:g})", "action": "scale", "params": {"factor": shrink}},
            {"label": "Split into parts that fit", "action": "split_to_fit",
             "params": {"bed_x": bx, "bed_y": by, "bed_z": bz}},
        ],
    )]


def analyze(mesh: trimesh.Trimesh, crossing: np.ndarray | None | bool = True, printer=None) -> Report:
    """Check a mesh. ``crossing`` can pass in self-intersecting faces already
    found (they do not change when a model is only moved, turned or scaled);
    True means find them now, None means the check was skipped. ``printer``
    (from settings) adds a fits-the-bed check and sets the material."""
    faces_total = len(mesh.faces)
    issues: list[Issue] = []
    if faces_total == 0:
        return Report(
            score=0,
            verdict="The model is empty",
            printable=False,
            stats={"triangles": 0, "vertices": 0, "size_mm": [0, 0, 0], "volume_mm3": None,
                   "surface_area_mm2": 0, "pieces": 0, "watertight": False, "filament_g": None,
                   "material": printer.material if printer is not None else "PLA"},
            issues=[Issue("empty", "error", "The model is empty", "There are no triangles left. Undo, or open the file again.", 1, 100)],
            highlights={"open_edges": [], "non_manifold_edges": [], "crossing_edges": [], "truncated": False},
        )
    if crossing is True:
        crossing = find_crossing_faces(mesh)

    # --- Edge usage: every edge of a solid is shared by exactly two triangles.
    unique_edges, edge_use = unique_rows(mesh.edges_sorted, len(mesh.vertices))
    open_edges = unique_edges[edge_use == 1]
    nonmanifold_edges = unique_edges[edge_use > 2]

    holes = _count_loops(open_edges)
    if holes:
        issues.append(Issue(
            code="holes",
            severity="error",
            title=f"{holes} hole{'s' if holes != 1 else ''} in the surface",
            detail=(
                "The surface is not closed, so the slicer cannot tell inside from "
                "outside. Open edges are shown in red."
            ),
            count=holes,
            penalty=_scaled_penalty(holes, 5, 40),
            fixes=_open_surface_fixes(mesh) if holes <= 50 else [],
        ))

    if len(nonmanifold_edges):
        n = len(nonmanifold_edges)
        issues.append(Issue(
            code="non_manifold",
            severity="error",
            title=f"{n} non-manifold edge{'s' if n != 1 else ''}",
            detail=(
                "Some edges are shared by more than two triangles, like pages "
                "glued to one spine. Slicers often fail or guess here. Shown in orange."
            ),
            count=n,
            penalty=_scaled_penalty(n, 1, 25),
        ))

    # --- Faces that add nothing or double up.
    degenerate = int(np.count_nonzero(mesh.area_faces <= 1e-12))
    if degenerate:
        issues.append(Issue(
            code="degenerate_faces",
            severity="warning",
            title=f"{degenerate} zero-area triangle{'s' if degenerate != 1 else ''}",
            detail="Triangles squashed flat into a line or point. Usually harmless but untidy.",
            count=degenerate,
            penalty=_scaled_penalty(degenerate, 0.1, 5),
        ))

    duplicate = faces_total - len(unique_rows(np.sort(mesh.faces, axis=1), len(mesh.vertices))[0])
    if duplicate:
        issues.append(Issue(
            code="duplicate_faces",
            severity="warning",
            title=f"{duplicate} duplicate triangle{'s' if duplicate != 1 else ''}",
            detail="The same triangle appears more than once, which confuses inside/outside checks.",
            count=duplicate,
            penalty=_scaled_penalty(duplicate, 0.5, 10),
        ))

    # --- Separate pieces and floating debris.
    labels = trimesh.graph.connected_component_labels(mesh.face_adjacency, node_count=faces_total)
    piece_sizes = np.bincount(labels)
    pieces = len(piece_sizes)
    debris = int(np.count_nonzero(piece_sizes < max(1, DEBRIS_FACE_SHARE * piece_sizes.max())))
    if debris:
        issues.append(Issue(
            code="floating_debris",
            severity="warning",
            title=f"{debris} small floating piece{'s' if debris != 1 else ''}",
            detail=(
                "Tiny disconnected bits, often scan noise. They print as loose "
                "blobs or spaghetti. Safe to delete in most cases."
            ),
            count=debris,
            penalty=_scaled_penalty(debris, 1, 10),
        ))

    # --- Orientation: triangles must all face outwards.
    winding_ok = bool(mesh.is_winding_consistent)
    if not winding_ok:
        issues.append(Issue(
            code="inconsistent_winding",
            severity="warning",
            title="Some triangles face the wrong way",
            detail="Neighbouring triangles disagree about which side is outside.",
            count=1,
            penalty=10,
        ))

    if crossing is None:
        issues.append(Issue(
            code="crossing_not_checked",
            severity="info",
            title="Self-crossing check skipped",
            detail=(
                "This model is too detailed to check quickly for places where the "
                "surface cuts through itself. Clean up reduces the detail and checks it."
            ),
            count=1,
            penalty=0,
            fixes=[CLEANUP_FIX],
        ))
    elif len(crossing):
        n = len(crossing)
        issues.append(Issue(
            code="self_intersections",
            severity="error",
            title=f"The surface cuts through itself ({n:,} triangle{'s' if n != 1 else ''})",
            detail=(
                "Parts of the surface pass through each other, so the slicer can "
                "get inside and outside mixed up and leave gaps or extra walls. "
                "Shown in purple."
            ),
            count=n,
            penalty=_scaled_penalty(n, 0.5, 25),
            fixes=[CLEANUP_FIX],
        ))

    watertight = holes == 0 and len(nonmanifold_edges) == 0
    volume = float(mesh.volume) if watertight and winding_ok else None
    if volume is not None and volume < 0:
        issues.append(Issue(
            code="inside_out",
            severity="error",
            title="The model is inside out",
            detail="All triangles point inwards. Slicers may print it as empty space.",
            count=1,
            penalty=15,
        ))

    # --- Will it stand on the bed?
    if watertight and winding_ok and volume is not None and volume > 0:
        issues.extend(_stability_issues(mesh))

    if printer is not None:
        issues.extend(_fit_issues(mesh, printer))

    # --- Sanity checks that cost nothing.
    extents = mesh.extents
    size_mm = [round(float(v), 3) for v in extents]
    largest = float(extents.max()) if len(extents) else 0.0
    if pieces > 1 and largest > HUGE_MODEL_MM:
        # Parts laid out side by side: judge the units by the biggest part.
        largest = max(float(np.ptp(mesh.vertices[np.unique(mesh.faces[labels == k])], axis=0).max()) for k in range(pieces))
    if 0 < largest < TINY_MODEL_MM:
        issues.append(Issue(
            code="tiny_model",
            severity="info",
            title=f"Very small: only {largest:.3g} mm across",
            detail=(
                "STL files do not store units, so a model saved in inches, "
                "centimetres or metres opens far too small. If it should be "
                "bigger, pick the units it was made in."
            ),
            count=1,
            penalty=0,
            fixes=_unit_fixes(largest, ("inches", "centimetres", "metres")),
        ))
    elif largest > HUGE_MODEL_MM:
        issues.append(Issue(
            code="huge_model",
            severity="info",
            title=f"Very large: {largest / 1000:.3g} m across",
            detail=(
                "STL files do not store units, so a model saved in micrometres "
                "opens far too big. If it should be smaller, fix the units."
            ),
            count=1,
            penalty=0,
            fixes=_unit_fixes(largest, ("micrometres",)),
        ))

    lowest = float(mesh.bounds[0][2])
    if abs(lowest) > ON_BED_TOLERANCE_MM:
        below = lowest < 0
        issues.append(Issue(
            code="off_bed",
            severity="info",
            title=(
                f"Part of the model is below the bed ({-lowest:.3g} mm)"
                if below else f"Floating {lowest:.3g} mm above the bed"
            ),
            detail=(
                "Most slicers drop models onto the bed for you, but putting it "
                "there now shows the model exactly as it will print."
            ),
            count=1,
            penalty=0,
            fixes=[{"label": "Put on bed", "action": "place_on_bed", "params": {}}],
        ))

    if faces_total > HEAVY_MESH_TRIANGLES:
        issues.append(Issue(
            code="heavy_mesh",
            severity="warning",
            title=f"Too detailed for most slicers ({faces_total:,} triangles)",
            detail=(
                "Far finer than a printer can show, and slicers get slow or freeze on "
                "meshes this big. Reducing it keeps every detail a 0.4 mm nozzle can print."
            ),
            count=faces_total,
            penalty=5,
            fixes=[{
                "label": f"Reduce to {REDUCED_TRIANGLES:,} triangles",
                "action": "reduce_detail",
                "params": {"triangles": REDUCED_TRIANGLES},
            }],
        ))

    printable = not any(i.severity == "error" for i in issues)
    score = max(0, 100 - sum(i.penalty for i in issues))
    if not printable:
        # Any blocking problem keeps the score out of the "looks fine" range.
        score = min(score, UNPRINTABLE_SCORE_CAP)
    if printable and score >= 90:
        verdict = "Ready to print"
    elif printable:
        verdict = "Printable, with minor issues"
    else:
        verdict = "Needs repair before printing"

    severity_order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: (severity_order[i.severity], -i.penalty))

    material = printer.material if printer is not None else "PLA"
    density = printer.density if printer is not None else PLA_DENSITY_G_CM3

    open_coords, open_truncated = _segments(mesh, open_edges)
    nm_coords, nm_truncated = _segments(mesh, nonmanifold_edges)
    crossing_faces = crossing if crossing is not None else np.zeros(0, dtype=np.int64)
    cross_edges = mesh.faces[crossing_faces][:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
    cross_coords, cross_truncated = _segments(mesh, cross_edges)

    for issue in issues:
        if issue.code in ("holes", "non_manifold", "floating_debris", "degenerate_faces",
                          "duplicate_faces", "inconsistent_winding", "inside_out") and not issue.fixes:
            issue.fixes = [CLEANUP_FIX]

    stats = {
        "triangles": faces_total,
        "vertices": len(mesh.vertices),
        "size_mm": size_mm,
        "volume_mm3": round(abs(volume), 3) if volume is not None else None,
        "surface_area_mm2": round(float(mesh.area), 3),
        "pieces": pieces,
        "watertight": watertight,
        # Weight if printed solid: an upper bound, since infill uses less.
        "filament_g": round(abs(volume) / 1000 * density, 1) if volume is not None else None,
        "material": material,
    }

    return Report(
        score=score,
        verdict=verdict,
        printable=printable,
        stats=stats,
        issues=issues,
        highlights={
            "open_edges": open_coords,
            "non_manifold_edges": nm_coords,
            "crossing_edges": cross_coords,
            "truncated": open_truncated or nm_truncated or cross_truncated,
        },
    )


def analyze_file(path: str | Path, file_type: str | None = None) -> Report:
    return analyze(load_mesh(path, file_type))
