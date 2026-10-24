"""Actions: every change MeshRight can make to a model.

Each action is a named function with simple, checked inputs. The browser
buttons, batch mode, scripts and an optional AI assistant all go through this
one list, so they can only ever do what a person could do by clicking.

An action takes a mesh and returns a new mesh plus a short plain-English
receipt. It never changes the mesh it was given, which is what makes undo safe.
"""

from __future__ import annotations

import logging

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import trimesh

AXES = {"x": 0, "y": 1, "z": 2}
UNIT_FACTORS = {"inches": 25.4, "centimetres": 10.0, "metres": 1000.0, "micrometres": 0.001}


class ActionError(ValueError):
    """The action cannot run with these inputs. The message is shown to the user."""


@dataclass
class Param:
    name: str
    kind: str  # "number", "choice", "boolean", "text", "points" (a list of [x, y, z]) or "numbers" (a flat list)
    description: str
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    default: Any = None


@dataclass
class Action:
    name: str
    title: str
    description: str
    params: list[Param]
    run: Callable[..., tuple[trimesh.Trimesh, str]]
    # True when the action only moves, turns, mirrors or scales the model, so
    # its triangles keep their order and any surface faults stay the same.
    keeps_shape: bool = False

    def describe(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "params": [
                {k: v for k, v in vars(p).items() if v not in (None, ())} for p in self.params
            ],
        }


REGISTRY: dict[str, Action] = {}
log = logging.getLogger("meshright")


def action(name: str, title: str, description: str, params: list[Param] | None = None, keeps_shape: bool = False):
    def register(func):
        REGISTRY[name] = Action(name, title, description, params or [], func, keeps_shape)
        return func

    return register


# No number sent to an action, and no point of a result, may be further than
# this from the centre (a kilometre). Larger values are typing mistakes and
# would break the export (infinite coordinates).
LIMIT_MM = 1_000_000.0


def _clean_params(act: Action, raw: dict) -> dict:
    unknown = set(raw) - {p.name for p in act.params}
    if unknown:
        raise ActionError(f"'{act.title}' does not take: {', '.join(sorted(unknown))}.")
    clean = {}
    for p in act.params:
        value = raw.get(p.name, p.default)
        if value is None:
            raise ActionError(f"'{act.title}' needs a value for {p.name}.")
        if p.kind == "number":
            try:
                value = float(value)
            except (TypeError, ValueError, OverflowError):
                raise ActionError(f"{p.name} must be a number.") from None
            if not np.isfinite(value) or abs(value) > LIMIT_MM:
                raise ActionError(f"{p.name} must be a normal number, between -{LIMIT_MM:g} and {LIMIT_MM:g}.")
            if p.minimum is not None and value < p.minimum:
                raise ActionError(f"{p.name} must be at least {p.minimum:g}.")
            if p.maximum is not None and value > p.maximum:
                raise ActionError(f"{p.name} must be at most {p.maximum:g}.")
        elif p.kind == "points":
            try:
                value = np.asarray(value, dtype=np.float64)
            except (TypeError, ValueError, OverflowError):
                raise ActionError(f"{p.name} must be a list of [x, y, z] points.") from None
            if value.ndim != 2 or value.shape[1] != 3 or not np.all(np.isfinite(value)) or np.abs(value).max(initial=0) > LIMIT_MM:
                raise ActionError(f"{p.name} must be a list of [x, y, z] points.")
            if p.minimum is not None and len(value) < p.minimum:
                raise ActionError(f"Pick at least {p.minimum:g} points.")
            if p.maximum is not None and len(value) > p.maximum:
                raise ActionError(f"Pick at most {p.maximum:g} points.")
        elif p.kind == "numbers":
            try:
                value = np.asarray(value, dtype=np.float64).ravel()
            except (TypeError, ValueError, OverflowError):
                raise ActionError(f"{p.name} must be a list of numbers.") from None
            if not np.all(np.isfinite(value)) or np.abs(value).max(initial=0) > LIMIT_MM:
                raise ActionError(f"{p.name} must be a list of normal numbers.")
            if p.minimum is not None and len(value) < p.minimum:
                raise ActionError(f"{p.name} needs at least {p.minimum:g} numbers.")
            if p.maximum is not None and len(value) > p.maximum:
                raise ActionError(f"{p.name} can have at most {p.maximum:g} numbers.")
        elif p.kind == "text":
            value = str(value)
            if not value or len(value) > (p.maximum or 200):
                raise ActionError(f"{p.name} is missing or too long.")
        elif p.kind == "boolean":
            if isinstance(value, str):
                value = value.strip().lower() in ("1", "true", "yes", "on")
            value = bool(value)
        elif p.kind == "choice":
            value = str(value).lower()
            if value not in p.choices:
                raise ActionError(f"{p.name} must be one of: {', '.join(p.choices)}.")
        clean[p.name] = value
    return clean


def run_action(mesh: trimesh.Trimesh, name: str, params: dict | None = None) -> tuple[trimesh.Trimesh, str]:
    act = REGISTRY.get(name)
    if act is None:
        raise ActionError(f"Unknown action '{name}'.")
    clean = _clean_params(act, params or {})
    try:
        result, receipt = act.run(mesh.copy(), **clean)
    except (ActionError, MemoryError):
        raise
    except Exception as exc:  # geometry libraries fail in many ways on odd shapes
        log.warning("Action %s failed: %r", name, exc)
        raise ActionError(
            f"'{act.title}' could not be done on this model. It may be too thin, too small or too damaged "
            "for this step. Try Clean up or Make Solid first. Your model is unchanged."
        ) from exc
    if len(result.faces) == 0:
        raise ActionError("That would leave nothing of the model, so it was not done.")
    verts = np.asarray(result.vertices)
    if not np.isfinite(verts).all() or np.abs(verts).max(initial=0) > LIMIT_MM * 10:
        raise ActionError("That would put the model impossibly far away or make it impossibly big, so it was not done.")
    from .colour import carry

    if "scanner" in mesh.metadata:  # where the scan came from stays known
        result.metadata["scanner"] = mesh.metadata["scanner"]
    return carry(mesh, result), receipt


def _mm(value: float) -> str:
    return f"{value:.4g} mm"


def _size(mesh: trimesh.Trimesh) -> str:
    return " × ".join(f"{v:.4g}" for v in mesh.extents) + " mm"


# ---------------------------------------------------------------- transforms

AXIS = Param("axis", "choice", "Which direction: x (left-right), y (front-back) or z (up-down).", choices=("x", "y", "z"))


@action(
    "move",
    "Move",
    "Moves the model to a different position. Enter how far to shift it in each direction.",
    [
        Param("x", "number", "How far to move it left (negative) or right (positive), in mm.", default=0.0),
        Param("y", "number", "How far to move it towards you (negative) or away (positive), in mm.", default=0.0),
        Param("z", "number", "How far to move it down (negative) or up (positive), in mm.", default=0.0),
    ],
    keeps_shape=True,
)
def move(mesh, x, y, z):
    mesh.apply_translation((x, y, z))
    return mesh, f"Moved by {x:g}, {y:g}, {z:g} mm"


@action(
    "rotate",
    "Rotate",
    "Spins the model around one of its three axes, turning it in place.",
    [AXIS, Param("degrees", "number", "How many degrees to turn it. Positive spins anticlockwise, negative spins clockwise.", minimum=-360, maximum=360)],
    keeps_shape=True,
)
def rotate(mesh, axis, degrees):
    direction = np.zeros(3)
    direction[AXES[axis]] = 1.0
    matrix = trimesh.transformations.rotation_matrix(np.radians(degrees), direction, mesh.bounds.mean(axis=0))
    mesh.apply_transform(matrix)
    return mesh, f"Rotated {degrees:g}° around {axis.upper()}"


@action(
    "scale",
    "Scale",
    "Makes the model bigger or smaller by the same amount in every direction, so it keeps its shape.",
    [Param("factor", "number", "How much bigger or smaller — 2 doubles it, 0.5 halves it, 1 leaves it the same.", minimum=1e-6, maximum=1e6)],
    keeps_shape=True,
)
def scale(mesh, factor):
    before = _size(mesh)
    mesh.apply_scale(factor)
    return mesh, f"Scaled ×{factor:g}: {before} → {_size(mesh)}"


@action(
    "resize",
    "Set size",
    "Sets the model to an exact size along one side. Everything else scales with it to keep the shape.",
    [AXIS, Param("size_mm", "number", "The exact size you want that side to be, in millimetres.", minimum=1e-3, maximum=1e6)],
    keeps_shape=True,
)
def resize(mesh, axis, size_mm):
    current = float(mesh.extents[AXES[axis]])
    if current <= 0:
        raise ActionError(f"The model has no size along {axis.upper()}, so it cannot be resized that way.")
    factor = size_mm / current
    mesh.apply_scale(factor)
    return mesh, f"Set {axis.upper()} size to {_mm(size_mm)} (×{factor:.4g}); now {_size(mesh)}"


@action(
    "convert_units",
    "Fix units",
    "Fixes a model that looks the wrong size because it was saved in the wrong units. Pick what units the file used and MeshRight rescales it to the correct real-world size.",
    [Param("from_units", "choice", "The units the file was saved in — pick whichever one makes the model the wrong size.", choices=tuple(UNIT_FACTORS))],
    keeps_shape=True,
)
def convert_units(mesh, from_units):
    factor = UNIT_FACTORS[from_units]
    mesh.apply_scale(factor)
    return mesh, f"Converted from {from_units} to millimetres (×{factor:g}); now {_size(mesh)}"


@action(
    "mirror",
    "Mirror",
    "Flips the model to create its exact opposite side — like holding it up to a mirror. Useful for making a left part from a right one.",
    [AXIS],
    keeps_shape=True,
)
def mirror(mesh, axis):
    matrix = np.eye(4)
    matrix[AXES[axis], AXES[axis]] = -1.0
    centre = mesh.bounds.mean(axis=0)
    mesh.apply_translation(-centre)
    # trimesh flips the triangles of a mirrored mesh so it keeps facing outwards
    mesh.apply_transform(matrix)
    mesh.apply_translation(centre)
    return mesh, f"Mirrored along {axis.upper()}"


@action(
    "place_on_bed",
    "Put on bed",
    "Sits the model flat on the ground and centres it, ready to send to the slicer.",
    keeps_shape=True,
)
def place_on_bed(mesh):
    from . import orient

    return orient.place_on_bed(mesh), "Centred on the bed and placed on its surface"


@action(
    "set_bottom",
    "Set the bottom",
    "Turns the model so the side you choose faces down, then sits it on the bed.",
    [
        Param("down_x", "number", "Which direction should face down — x part of the direction."),
        Param("down_y", "number", "Which direction should face down — y part of the direction."),
        Param("down_z", "number", "Which direction should face down — z part of the direction."),
    ],
    keeps_shape=True,
)
def set_bottom(mesh, down_x, down_y, down_z):
    from . import orient

    if np.linalg.norm([down_x, down_y, down_z]) == 0:
        raise ActionError("The down direction cannot be zero.")
    return orient.set_bottom(mesh, (down_x, down_y, down_z)), "Turned to the chosen bottom and put on the bed"


@action(
    "lay_flat",
    "Lay flat on points",
    "Click three or more spots on the bottom of the model and MeshRight rotates it so those spots all touch the bed. More points give a more accurate result when the surface is rough.",
    [Param("points", "points", "Click spots on the part of the model that should touch the bed.", minimum=3, maximum=500)],
    keeps_shape=True,
)
def lay_flat(mesh, points):
    from . import orient

    spread = np.linalg.svd(points - points.mean(axis=0), compute_uv=False)
    if len(spread) < 2 or spread[1] < 1e-6 * max(spread[0], 1e-12):
        raise ActionError("The points are in a line. Pick points spread out over the bottom.")
    return orient.lay_flat_on_points(mesh, points), f"Laid flat on a plane through {len(points)} points and put on the bed"


@action(
    "cut_flat_bottom",
    "Cut a flat bottom",
    "Slices a thin layer off the bottom and seals it flat, so the model stands steadily and sticks to the print bed.",
    [Param("cut_mm", "number", "How much to slice off the bottom, in mm. A small amount (0.5–2 mm) is usually enough.", minimum=0.05, maximum=10000)],
)
def cut_flat_bottom(mesh, cut_mm):
    from . import orient

    if cut_mm >= mesh.extents[2]:
        raise ActionError("That cut would remove the whole model.")
    try:
        result = orient.cut_flat_bottom(mesh, cut_mm)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc
    return result, f"Cut {cut_mm:g} mm off the bottom and sealed it flat"


@action(
    "add_flat_base",
    "Add a flat base",
    "Adds a flat foot to a rounded bottom so the model stands steadily on the print bed, without cutting anything off.",
    [Param("depth_mm", "number", "How tall the flat foot is, in mm. Higher gives a more stable base.", minimum=0.05, maximum=10000)],
)
def add_flat_base(mesh, depth_mm):
    from . import orient

    if depth_mm >= mesh.extents[2]:
        raise ActionError("The base cannot be as tall as the model.")
    try:
        result = orient.add_flat_base(mesh, depth_mm)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc
    return result, f"Added a {depth_mm:g} mm flat base; nothing was cut off"


# ---------------------------------------------------------------- repairs

@action(
    "cleanup",
    "Clean up",
    "One-click repair that tidies the model and gets it ready to print — deletes stray bits, fills holes, fixes surfaces that poke through themselves, and removes unnecessary detail.",
    [
        Param("remove_loose_pieces", "boolean", "Remove tiny floating bits that are not part of the main model.", default=True),
        Param("fill", "boolean", "Fill in holes on the surface.", default=True),
        Param("fix_crossing", "boolean", "Fix areas where the surface folds back through itself.", default=True),
        Param("reduce", "boolean", "Reduce the number of triangles on very large scans so the slicer stays fast.", default=True),
        Param(
            "close_open_edge", "boolean",
            "Also close a large opening that looks intentional, like the open back of a face scan. Leave off unless you want everything closed.",
            default=False,
        ),
        Param(
            "preset", "choice",
            "quick = fewest triangles (fastest to slice), balanced = best all-round, detail = keeps every triangle, flexible = for bendy filament like TPU.",
            choices=("quick", "balanced", "detail", "flexible"), default="balanced",
        ),
    ],
)
def cleanup(mesh, remove_loose_pieces, fill, fix_crossing, reduce, close_open_edge, preset):
    from . import repair

    return repair.cleanup(mesh, remove_loose_pieces, fill, fix_crossing, reduce, close_open_edge, preset)


@action(
    "make_solid",
    "Make Solid",
    "Rebuilds a heavily broken model as one clean, solid shell. Use this when Clean up is not enough — it starts fresh rather than patching.",
    [
        Param(
            "detail_mm", "number",
            "Smallest detail to keep, in mm. 0 lets MeshRight decide. Higher values smooth out fine detail but give a cleaner result on very rough scans.",
            minimum=0, maximum=50, default=0.0,
        ),
    ],
)
def make_solid(mesh, detail_mm):
    from . import repair

    try:
        return repair.make_solid(mesh, detail_mm)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc


# ---------------------------------------------------------------- editing

def _inside_loop(points: np.ndarray, loop: np.ndarray) -> np.ndarray:
    """Even-odd test: which 2D points are inside the closed loop."""
    x, y = points[:, 0], points[:, 1]
    inside = np.zeros(len(points), dtype=bool)
    x1, y1 = loop[:, 0], loop[:, 1]
    x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
    for ax, ay, bx, by in zip(x1, y1, x2, y2):
        crosses = (ay > y) != (by > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            at = ax + (y - ay) * (bx - ax) / (by - ay)
        inside ^= crosses & (x < at)
    return inside


def lasso_faces(mesh: trimesh.Trimesh, lasso, view, eye, facing_only: bool) -> np.ndarray:
    matrix = np.asarray(view, dtype=np.float64).reshape(4, 4).T  # three.js is column-major
    centres = mesh.triangles_center
    clip = np.c_[centres, np.ones(len(centres))] @ matrix.T
    in_front = clip[:, 3] > 1e-9
    screen = clip[:, :2] / np.where(in_front, clip[:, 3], 1.0)[:, None]
    selected = in_front & _inside_loop(screen, np.asarray(lasso, dtype=np.float64).reshape(-1, 2))
    if facing_only:
        towards_eye = np.asarray(eye, dtype=np.float64) - centres
        selected &= np.einsum("ij,ij->i", mesh.face_normals, towards_eye) > 0
    return np.flatnonzero(selected)


@action(
    "erase_area",
    "Erase an area",
    "Draw a loop around any part of the surface and delete everything inside it — like cutting out a section with scissors. Use Clean up afterwards to close the opening.",
    [
        Param("lasso", "numbers", "The loop you drew on screen, as x, y pairs.", minimum=6, maximum=4000),
        Param("view", "numbers", "The viewer's camera matrix (sent automatically by the browser).", minimum=16, maximum=16),
        Param("eye", "numbers", "Where the camera is in the model (sent automatically by the browser).", minimum=3, maximum=3),
        Param("facing_only", "boolean", "Only erase the side facing you — leaves the back side untouched.", default=True),
    ],
)
def erase_area(mesh, lasso, view, eye, facing_only):
    if len(lasso) % 2:
        raise ActionError("The loop must be a list of x, y pairs.")
    chosen = lasso_faces(mesh, lasso, view, eye, facing_only)
    if len(chosen) == 0:
        raise ActionError("Nothing is inside that loop. Draw it around part of the model.")
    if len(chosen) == len(mesh.faces):
        raise ActionError("That would erase the whole model.")
    keep = np.ones(len(mesh.faces), dtype=bool)
    keep[chosen] = False
    mesh.update_faces(keep)
    mesh.remove_unreferenced_vertices()
    return mesh, f"Erased {len(chosen):,} triangles"


@action(
    "part_from_area",
    "Make a part from an area",
    "Draw a loop around any area and MeshRight builds a solid piece that fits it exactly — a pad, a grip, a cover or a snap-on shell.",
    [
        Param("lasso", "numbers", "The loop you drew on screen, as x, y pairs.", minimum=6, maximum=4000),
        Param("view", "numbers", "The viewer's camera matrix (sent automatically by the browser).", minimum=16, maximum=16),
        Param("eye", "numbers", "Where the camera is in the model (sent automatically by the browser).", minimum=3, maximum=3),
        Param("facing_only", "boolean", "Only use the side facing you.", default=True),
        Param("thickness_mm", "number", "How thick the new part should be, in mm.", minimum=0.4, maximum=100, default=3.0),
        Param("side", "choice", "outside = sits on top of the surface; inside = sits behind it.", choices=("outside", "inside"), default="outside"),
        Param("gap_mm", "number", "Small gap between the part and the surface so it can slide on and off, in mm. 0.2 suits most prints.", minimum=0, maximum=5, default=0.2),
    ],
)
def part_from_area(mesh, lasso, view, eye, facing_only, thickness_mm, side, gap_mm):
    from . import repair

    if len(lasso) % 2:
        raise ActionError("The loop must be a list of x, y pairs.")
    chosen = lasso_faces(mesh, lasso, view, eye, facing_only)
    if len(chosen) == 0:
        raise ActionError("Nothing is inside that loop. Draw it around part of the model.")
    patch = mesh.submesh([chosen], append=True)
    # Keep the biggest connected patch: stray bits caught by the loop would
    # become loose crumbs.
    labels = trimesh.graph.connected_component_labels(patch.face_adjacency, node_count=len(patch.faces))
    biggest = np.argmax(np.bincount(labels, weights=patch.area_faces))
    patch.update_faces(labels == biggest)
    patch.remove_unreferenced_vertices()
    outside = side == "outside"
    try:
        part = repair.thicken(patch, thickness_mm, outward=outside, gap_mm=gap_mm if outside else 0.0)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc
    where = f"on the outside with a {gap_mm:g} mm gap" if outside else "behind the surface"
    return part, f"Made a {thickness_mm:g} mm part from the selected area, fitting {where}"


@action(
    "erase_piece",
    "Erase a piece",
    "Removes a separate chunk that is not connected to the main model — for example the table or stand the object was sitting on when scanned. The main model is never removed this way.",
    [Param("point", "numbers", "Click anywhere on the piece you want to remove.", minimum=3, maximum=3)],
)
def erase_piece(mesh, point):
    from scipy.spatial import cKDTree

    _, face = cKDTree(mesh.triangles_center).query(point)
    labels = trimesh.graph.connected_component_labels(mesh.face_adjacency, node_count=len(mesh.faces))
    piece = labels == labels[face]
    sizes = np.bincount(labels)
    if len(sizes) == 1 or piece.sum() == sizes.max():
        raise ActionError("That is the main model. To remove part of it, use Erase an area.")
    mesh.update_faces(~piece)
    mesh.remove_unreferenced_vertices()
    return mesh, f"Erased a separate piece ({int(piece.sum()):,} triangles)"


@action(
    "thicken",
    "Give it thickness",
    "Turns a flat or open surface — like a face scan or relief — into a solid shell you can print. The front surface stays as it is; the thickness is added to the back.",
    [Param("thickness_mm", "number", "How thick to make it, in mm. 2 mm suits most face scans and reliefs.", minimum=0.2, maximum=100, default=2.0)],
)
def thicken(mesh, thickness_mm):
    from . import repair

    try:
        shell = repair.thicken(mesh, thickness_mm)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc
    return shell, f"Gave the surface {thickness_mm:g} mm of thickness"


@action(
    "merge_scan",
    "Merge another scan",
    "Combines two scans of the same object into one. Click three or more matching spots on each scan and MeshRight lines them up and joins them together.",
    [
        Param("scan", "text", "The id of the second scan you uploaded.", maximum=64),
        Param("added_points", "points", "The matching spots on the scan you are adding.", minimum=3, maximum=50),
        Param("base_points", "points", "The same spots on this model, in the same order.", minimum=3, maximum=50),
        Param("fuse", "boolean", "Join both scans into one solid surface after aligning.", default=True),
    ],
)
def merge_scan(mesh, scan, added_points, base_points, fuse):
    from . import merge

    entry = merge.PENDING.get(scan)
    if entry is None:
        raise ActionError("That scan is no longer open. Please add it again.")
    name, added = entry
    try:
        return merge.merge(mesh, added, name, added_points, base_points, fuse)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc


@action(
    "join_piece",
    "Join a broken piece back",
    "Sticks a broken-off piece back onto the main model. Click three matching spots on the break surface of each part and MeshRight snaps them together.",
    [
        Param("scan", "text", "The id of the broken piece you uploaded.", maximum=64),
        Param("added_points", "points", "Three or more spots on the break surface of the piece.", minimum=3, maximum=50),
        Param("base_points", "points", "The matching spots on the break surface of the main model, in the same order.", minimum=3, maximum=50),
    ],
)
def join_piece(mesh, scan, added_points, base_points):
    from . import mate, merge

    entry = merge.PENDING.get(scan)
    if entry is None:
        raise ActionError("That piece is no longer open. Please add it again.")
    name, piece = entry
    try:
        return mate.join_piece(mesh, piece, name, added_points, base_points)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc


@action(
    "combine_models",
    "Combine with another model",
    "Brings two models together — add one on top of the other, cut the shape of one out of the other, or keep only the part where they overlap.",
    [
        Param("scan", "text", "The id of the second model you uploaded.", maximum=64),
        Param("placement", "numbers", "Where to position the second model (a 4×4 placement matrix, sent by the browser).", minimum=16, maximum=16),
        Param("how", "choice", "join = add them together, subtract = cut the second one's shape out of the first, overlap = keep only where they share space.", choices=("join", "subtract", "overlap"), default="join"),
    ],
)
def combine_models(mesh, scan, placement, how):
    from . import combine, merge

    entry = merge.PENDING.get(scan)
    if entry is None:
        raise ActionError("That model is no longer open. Please add it again.")
    name, added = entry
    try:
        return combine.combine(mesh, added, name, combine.placement(placement), how)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc


@action(
    "make_bust",
    "Make a bust",
    "Turns a head or head-and-shoulders scan into a finished bust — closes the neck, trims the bottom flat, and adds a display base.",
    [
        Param("height_mm", "number", "How tall the finished bust should be, including the base, in mm.", minimum=10, maximum=2000, default=100),
        Param("base", "choice", "The shape of the base: round, square, or no base.", choices=("round", "square", "none"), default="round"),
    ],
)
def make_bust(mesh, height_mm, base):
    from . import bust

    try:
        return bust.make_bust(mesh, height_mm, base)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc


@action(
    "sculpt",
    "Sculpt",
    "Paint over the surface with a brush to smooth bumps, flatten an area, or push the surface in or out. The effect fades away at the edge of the brush.",
    [
        Param("stroke", "points", "The path the brush took across the surface.", minimum=1, maximum=2000),
        Param("radius_mm", "number", "How wide the brush is, in mm. Bigger affects a larger area.", minimum=0.05, maximum=10000),
        Param("brush", "choice", "smooth = soften rough spots, flatten = level an area, push_out = raise the surface, push_in = dent it inward.", choices=("smooth", "flatten", "push_out", "push_in"), default="smooth"),
        Param("strength", "choice", "How strong the effect is — light, medium or strong.", choices=("light", "medium", "strong"), default="medium"),
    ],
)
def sculpt(mesh, stroke, radius_mm, brush, strength):
    from . import sculpt as sculpting

    try:
        return sculpting.brush(mesh, stroke, radius_mm, brush, strength)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc


@action(
    "reduce_detail",
    "Reduce detail",
    "Cuts down the number of triangles so the file is smaller and slicers stay fast. The shape stays the same — only detail too fine for a printer to show is lost.",
    [Param("triangles", "number", "About how many triangles to keep. 100,000–300,000 suits most prints; go higher to keep more surface detail.", minimum=1000, maximum=10_000_000, default=300_000)],
)
def reduce_detail(mesh, triangles):
    from . import repair

    before = len(mesh.faces)
    target = int(triangles)
    if before <= target:
        return mesh, f"Already {before:,} triangles; nothing to reduce"
    reduced = repair.reduce_detail(mesh, target)
    moved = repair.describe_change(repair.surface_change(mesh, reduced))
    return reduced, f"Reduced detail from {before:,} to {len(reduced.faces):,} triangles; {moved}"


@action(
    "smooth_scan",
    "Smooth scan noise",
    "Removes the rough, speckled texture that scanners and photogrammetry leave on the surface. Heavy smoothing gives a cleaner result but can soften fine details.",
    [Param("strength", "choice", "How much smoothing to apply — light keeps the most detail, heavy gives the cleanest surface.", choices=("light", "medium", "heavy"), default="medium")],
)
def smooth_scan(mesh, strength):
    import trimesh.smoothing as sm

    iters = {"light": 5, "medium": 12, "heavy": 25}[strength]
    result = mesh.copy()
    sm.filter_taubin(result, lamb=0.5, nu=0.53, iterations=iters)
    from . import repair
    moved = repair.describe_change(repair.surface_change(mesh, result))
    return result, f"Applied {strength} smoothing ({iters} passes); {moved}"


@action(
    "remove_base",
    "Remove scan table",
    "Detects and removes the flat surface the object was sitting on when it was scanned — the table, turntable or sheet of paper that shows up at the bottom of the model.",
    [Param("margin_mm", "number", "How high above the table surface to make the cut, in mm. 0.5 mm avoids accidentally cutting into the model.", minimum=0.0, maximum=10.0, default=0.5)],
)
def remove_base(mesh, margin_mm):
    z_lo, z_hi = mesh.bounds[0][2], mesh.bounds[1][2]
    z_range = z_hi - z_lo
    if z_range < 2:
        raise ActionError("The model is thinner than 2 mm; cannot detect a base plane.")
    # Find faces in the bottom 25% that are nearly horizontal (normal ≈ ±Z).
    bot = mesh.triangles_center[:, 2] < z_lo + z_range * 0.25
    horiz = np.abs(mesh.face_normals[:, 2]) > 0.85
    base_mask = bot & horiz
    if base_mask.sum() < 20:
        raise ActionError("No flat base detected. If the object is not sitting on a flat surface, this action is not needed.")
    base_z = float(mesh.triangles_center[base_mask, 2].mean())
    cut_z = base_z + float(margin_mm)
    # Keep only faces whose centre is above the cut.
    keep = mesh.triangles_center[:, 2] > cut_z
    if keep.sum() == 0:
        raise ActionError("The cut would remove the whole model. Is the scan the right way up?")
    kept = trimesh.Trimesh(mesh.vertices, mesh.faces[keep], process=False)
    kept.remove_unreferenced_vertices()
    from . import repair
    kept = repair.tidy(kept)
    removed = len(mesh.faces) - len(kept.faces)
    return kept, (
        f"Detected base at Z = {base_z:.1f} mm; cut at {cut_z:.1f} mm; "
        f"removed {removed:,} triangles ({removed * 100 // len(mesh.faces)}% of model)"
    )


@action(
    "seam_polish",
    "Polish seam after joining",
    "Cleans up the rough, tangled surface left on the join line after two broken pieces are put back together. Run this straight after Join piece.",
    [Param("detail_mm", "number", "How fine a detail to preserve at the join, in mm. 0.3 works for most prints; use a higher number on large or very rough models.", minimum=0.1, maximum=2.0, default=0.3)],
)
def seam_polish(mesh, detail_mm):
    from . import repair

    before = len(mesh.faces)
    cleaned = repair.tidy(mesh)
    result, note = repair.make_solid(cleaned, float(detail_mm))
    return result, f"Polished seam at {detail_mm} mm detail; {len(result.faces):,} triangles (was {before:,}); {note.split(';', 1)[-1].strip()}"


@action(
    "fill_curved",
    "Fill holes",
    "Fills in holes so the patch follows the shape of the surface around it — not flat, but curved the same way the rest of the model is.",
    [Param("max_hole_edges", "number", "The largest hole to fill. Higher = fill bigger holes too. Leave very large openings unfilled if they look intentional.", minimum=10, maximum=1000, default=300)],
)
def fill_curved(mesh, max_hole_edges):
    from . import repair

    result, note = repair.fill_holes_curved(mesh, max_edges=int(max_hole_edges))
    return result, note


@action(
    "repair_wound",
    "Repair wound (missing piece)",
    "For when the broken piece is gone and you are left with a ragged open hole. Smooths the jagged edges and fills the hole, following the shape of the intact surface around it.",
    [
        Param("smooth_passes", "number",
              "How much to smooth the rough wound edges before filling. More gives a softer join; less keeps a sharper edge. 5–15 suits most breaks.",
              minimum=0, maximum=50, default=8),
        Param("max_hole_edges", "number",
              "The largest wound to fill. Raise this if the hole is not being filled.",
              minimum=10, maximum=2000, default=600),
    ],
)
def repair_wound(mesh, smooth_passes, max_hole_edges):
    from . import repair

    # Light Taubin smooth to settle the jagged wound edges before filling
    working = mesh.copy()
    if int(smooth_passes) > 0:
        import trimesh.smoothing as sm
        sm.filter_taubin(working, lamb=0.5, nu=0.53, iterations=int(smooth_passes))

    result, fill_note = repair.fill_holes_curved(working, max_edges=int(max_hole_edges))

    # Report boundary edge count change as a proxy for how much was filled
    def _boundary(m: trimesh.Trimesh) -> int:
        if len(m.faces) == 0:
            return 0
        _, counts = np.unique(m.edges_sorted, axis=0, return_counts=True)
        return int((counts == 1).sum())

    before_b = _boundary(mesh)
    after_b = _boundary(result)
    healed = max(0, before_b - after_b)
    note = (
        f"Wound repair: smoothed wound edges ({smooth_passes} passes), "
        f"closed {healed} boundary edges. "
        f"{'Model is now watertight.' if result.is_watertight else 'Some wound edges remain — try Seam polish or Make solid to close them.'}"
    )
    return result, note


def _load_reference(scan_id: str) -> trimesh.Trimesh:
    """Look up a reference mesh by its scan_id from the pending scan store."""
    from . import merge

    entry = merge.PENDING.get(scan_id)
    if entry is None:
        raise ActionError("That reference file is no longer open. Please upload it again via Add scan.")
    return entry[1]


@action(
    "deviation_map",
    "Show repair accuracy",
    "Colours the model to show exactly what changed after a repair. Upload the original file via Add scan first, then run this. Green = matches the original within 0.5 mm, yellow = moved slightly, red = moved the most.",
    [Param("reference", "text", "The scan id of the original file you uploaded via Add scan.", maximum=64)],
)
def deviation_map(mesh, reference):
    from . import repair

    ref_mesh = _load_reference(reference)
    distances = repair.surface_deviation(mesh, ref_mesh)
    # Colour scale: 0 mm = green, 0.5–2 mm = yellow→orange, >2 mm = red
    greens = np.array([0.2, 0.8, 0.3])
    yellows = np.array([1.0, 0.85, 0.1])
    reds = np.array([0.9, 0.1, 0.1])
    t1 = np.clip(distances / 2.0, 0, 1)
    rgb = np.where(
        distances[:, None] < 0.5,
        greens + (yellows - greens) * (distances / 0.5)[:, None],
        yellows + (reds - yellows) * np.clip((distances - 0.5) / 1.5, 0, 1)[:, None],
    )
    rgb = np.clip(rgb, 0, 1)
    vertex_colors = (rgb * 255).astype(np.uint8)
    result = mesh.copy()
    result.visual = trimesh.visual.ColorVisuals(mesh=result, vertex_colors=vertex_colors)
    p50 = float(np.percentile(distances, 50))
    p95 = float(np.percentile(distances, 95))
    worst = float(distances.max())
    return result, (
        f"Deviation map: median {p50:.2f} mm, 95th-percentile {p95:.2f} mm, worst {worst:.2f} mm — "
        "green = accurate, yellow = 0.5–2 mm, red = over 2 mm"
    )


@action(
    "scan_to_print",
    "Scan to print",
    "One button that does the full clean-up chain automatically — removes the scan table, smooths the surface, cuts the triangle count down to a printable size, and produces a solid ready to print. Use this on a raw scanner export straight off the device.",
    [Param("target_triangles", "number", "How many triangles to keep after simplifying. 100,000 is right for most prints; raise it to keep more surface detail.", minimum=10000, maximum=500000, default=100000)],
)
def scan_to_print(mesh, target_triangles):
    from . import repair

    steps = []
    result = mesh

    # Step 1: remove scan table if one is detectable
    z_lo, z_hi = result.bounds[0][2], result.bounds[1][2]
    z_range = z_hi - z_lo
    if z_range >= 2:
        bot = result.triangles_center[:, 2] < z_lo + z_range * 0.25
        horiz = np.abs(result.face_normals[:, 2]) > 0.85
        if (bot & horiz).sum() >= 20:
            result, note = run_action(result, "remove_base", {"margin_mm": 0.5})
            steps.append("removed scan table")

    # Step 2: smooth scan noise
    import trimesh.smoothing as sm
    sm.filter_taubin(result, lamb=0.5, nu=0.53, iterations=8)
    steps.append("smoothed noise")

    # Step 3: reduce detail
    target = int(target_triangles)
    if len(result.faces) > target:
        result = repair.reduce_detail(result, target)
        steps.append(f"reduced to {len(result.faces):,} triangles")

    # Step 4: cleanup
    result = repair.tidy(result)
    steps.append("cleaned up")

    # Step 5: make solid if more than 5% of faces are on open surfaces
    unique, counts = np.unique(result.edges_sorted, axis=0, return_counts=True)
    boundary_edge_count = (counts == 1).sum()
    if boundary_edge_count > len(result.faces) * 0.05 or not result.is_watertight:
        result, _ = repair.make_solid(result, 0.0)
        steps.append("made solid")

    return result, "Scan to print: " + ", ".join(steps)


@action(
    "curvature_map",
    "Curvature map",
    "Colours the model by its shape — orange on bumps and rounded edges, blue in dips and concave areas, grey on flat surfaces. Useful for spotting dents, bumps and scanner artefacts that the eye misses.",
    [],
)
def curvature_map(mesh):
    from . import repair

    kappa = repair.mean_curvature(mesh)
    # Normalise: map negative (concave) to blue, zero to grey, positive (convex) to orange/red.
    # Use a symmetric range around 0 based on the 95th percentile of |kappa|.
    p95 = float(np.percentile(np.abs(kappa), 95)) or 1e-6
    t = np.clip(kappa / p95, -1.0, 1.0)  # -1=concave, 0=flat, +1=convex
    grey = np.array([0.55, 0.55, 0.55])
    warm = np.array([0.95, 0.45, 0.1])   # convex = orange
    cool = np.array([0.1, 0.45, 0.85])   # concave = blue
    rgb = np.where(
        t[:, None] >= 0,
        grey + (warm - grey) * t[:, None],
        grey + (cool - grey) * (-t[:, None]),
    )
    rgb = np.clip(rgb, 0, 1)
    result = mesh.copy()
    result.visual = trimesh.visual.ColorVisuals(mesh=result, vertex_colors=(rgb * 255).astype(np.uint8))
    flat_pct = int((np.abs(kappa) < p95 * 0.15).sum() * 100 / len(kappa))
    return result, (
        f"Curvature map: {flat_pct}% of surface is flat — orange = convex, blue = concave, grey = flat. "
        "Dents appear blue, bumps appear orange."
    )


@action(
    "symmetry_repair",
    "Symmetry repair",
    "For a part that should be the same on both sides but has one side missing or damaged. MeshRight finds the mirror line, takes the good side, and rebuilds the damaged side as its reflection.",
    [Param("keep", "text", "Which side to use as the template: auto = MeshRight picks the less-damaged side, positive or negative = you choose.",
           default="auto")],
)
def symmetry_repair_action(mesh, keep):
    if keep not in ("auto", "positive", "negative"):
        raise ActionError("'keep' must be 'auto', 'positive', or 'negative'.")
    from .symmetry import SymmetryError, symmetry_repair as _sym_repair
    try:
        return _sym_repair(mesh, keep=keep)
    except SymmetryError as e:
        raise ActionError(str(e)) from e


@action(
    "photo_repair",
    "Photo-guided repair",
    "Take a photo of the intact version of your broken part, click 4 matching spots on the photo and on the scan, and MeshRight figures out where the camera was, spots the missing geometry, and fills it in.",
    [
        Param("photo_data", "text",
              "The photo of the original part, encoded as base64. The frontend encodes the uploaded image before sending it here.", maximum=4_000_000),
        Param("pts2d", "text",
              "Matching spots on the photo — pixel positions separated by commas: x1,y1,x2,y2,x3,y3,x4,y4 (at least 4 pairs, no spaces).", maximum=512),
        Param("pts3d", "text",
              "The same spots on the 3D model — coordinates separated by commas: x1,y1,z1,x2,y2,z2,x3,y3,z3,x4,y4,z4 (at least 4 sets, no spaces).", maximum=512),
        Param("bg_threshold", "number",
              "How different the object needs to be from the background for MeshRight to tell them apart. Lower = picks up more; raise it if the background bleeds into the object.",
              minimum=5, maximum=100, default=30),
    ],
)
def photo_repair(mesh, photo_data, pts2d, pts3d, bg_threshold):
    from . import repair
    from .photo import (
        camera_pose_from_correspondences,
        extract_object_mask,
        find_gap_boundary_edges,
    )
    from PIL import Image as PilImage
    import io, base64

    # Decode the base64 image
    try:
        img_bytes = base64.b64decode(photo_data)
    except Exception as e:
        raise ActionError(f"Could not decode the photo — make sure it was encoded as base64: {e}") from e
    try:
        image = PilImage.open(io.BytesIO(img_bytes))
        img_w, img_h = image.size
    except Exception as e:
        raise ActionError(f"Could not open the photo — make sure it is a JPEG or PNG file: {e}") from e

    # Parse point correspondences
    def _parse_floats(s: str, expected_cols: int) -> np.ndarray:
        try:
            vals = [float(x.strip()) for x in s.split(",") if x.strip()]
        except ValueError as e:
            raise ActionError(f"Could not parse coordinates: {e}") from e
        if len(vals) % expected_cols != 0 or len(vals) < expected_cols * 4:
            raise ActionError(
                f"Need at least 4 points × {expected_cols} values each "
                f"(got {len(vals)} values)."
            )
        return np.array(vals, dtype=np.float64).reshape(-1, expected_cols)

    p2d = _parse_floats(pts2d, 2)
    p3d = _parse_floats(pts3d, 3)
    if len(p2d) != len(p3d):
        raise ActionError(
            f"Number of 2D points ({len(p2d)}) must equal 3D points ({len(p3d)})."
        )

    # Estimate camera pose
    try:
        P, _K, _Rt = camera_pose_from_correspondences(p2d, p3d)
    except Exception as e:
        raise ActionError(f"Camera pose estimation failed: {e}") from e

    # Extract object mask from the photo
    try:
        photo_mask = extract_object_mask(image, threshold=int(bg_threshold))
    except Exception as e:
        raise ActionError(f"Could not extract object silhouette from photo: {e}") from e

    # Find boundary vertices that land inside the photo object
    gap_verts = find_gap_boundary_edges(mesh, P, photo_mask, img_w, img_h)

    if not gap_verts:
        return mesh, (
            "Photo-guided repair: no gap detected. The scan already covers everything "
            "visible in the photo from this view, or the camera pose did not align correctly. "
            "Try re-clicking the correspondences on a wider view."
        )

    # Fill holes using the existing curvature repair
    result, fill_note = repair.fill_holes_curved(mesh, max_edges=600)
    filled = mesh.is_watertight != result.is_watertight or len(result.faces) > len(mesh.faces)
    return result, (
        f"Photo-guided repair: {len(gap_verts)} gap-boundary vertices found in the photo region, "
        f"{'filled successfully' if filled else 'fill attempted'}. {fill_note}"
    )


# ---------------------------------------------------------------- mold making


@action(
    "analyse_mold",
    "Check if a mold can be made",
    "Before making a mold, check whether this part will cast cleanly. "
    "MeshRight looks at the shape and tells you how many mold pieces it needs, "
    "whether any tiny details will be lost, and whether the cast part will pop out easily. "
    "Simple shapes get a one-click result. Complex shapes show a checklist you can adjust.",
    [
        Param("nozzle_mm", "number",
              "Your printer's nozzle width in mm. Details finer than this will blur in the mold cavity.",
              minimum=0.1, maximum=1.2, default=0.4),
        Param("min_wall_mm", "number",
              "Thinnest wall the mold should have, in mm. Thinner than this and the mold may crack.",
              minimum=1.0, maximum=5.0, default=1.5),
    ],
)
def analyse_mold_action(mesh, nozzle_mm, min_wall_mm):
    from .mold import analyse
    a = analyse(mesh, nozzle_mm=float(nozzle_mm), min_wall_mm=float(min_wall_mm))

    lines = []
    if a.is_simple:
        lines.append(f"Ready to cast in a {a.n_parts}-piece mold. No issues found.")
    else:
        lines.append(f"Mold analysis: {a.n_parts}-piece mold recommended.")
        for issue in a.issues:
            fix = "Fix recommended" if issue.default_fix else "Optional fix"
            lines.append(f"  [{fix}] {issue.message} — {issue.detail}")

    axis_name = ["X", "Y", "Z"][int(np.argmax(np.abs(a.split_axis)))]
    lines.append(
        f"Recommended: wall thickness {a.wall_mm:.1f} mm, "
        f"clearance {a.clearance_mm:.1f} mm, pull direction {axis_name} axis."
    )
    return mesh, "\n".join(lines)


@action(
    "make_mold",
    "Make a casting mold",
    "Builds a split mold around your part so you can cast copies in silicone or resin. "
    "The result is two (or more) mold halves, ready to print — with a hole to pour the casting "
    "material in, small channels so air can escape, alignment pins so the halves line up, "
    "and bolt holes around the edge for clamping. "
    "Run 'Check if a mold can be made' first to see if anything needs adjusting. "
    "Simple shapes work with one click.",
    [
        Param("wall_mm", "number",
              "How thick the mold walls are in mm. Thicker = stronger mold, uses more filament. "
              "0 = MeshRight chooses automatically based on the part size.",
              minimum=0, maximum=30, default=0),
        Param("clearance_mm", "number",
              "Tiny gap between the mold cavity and the part, in mm. "
              "Smaller = more accurate cast. Larger = easier to remove the cast part. "
              "0.2 mm suits most materials.",
              minimum=0.0, maximum=1.0, default=0.2),
        Param("smooth_fine_details", "boolean",
              "Smooth away details smaller than the nozzle before building the mold. "
              "Gives a cleaner cavity surface. Turn off only if you need every tiny detail captured.",
              default=True),
        Param("include_clamps", "boolean",
              "Also build a printable C-clamp that holds the two mold halves together while the "
              "material sets. Print two copies — one for each end of the mold.",
              default=True),
        Param("nozzle_mm", "number",
              "Your printer nozzle width in mm — used to detect details that won't print in the cavity.",
              minimum=0.1, maximum=1.2, default=0.4),
    ],
)
def make_mold_action(mesh, wall_mm, clearance_mm, smooth_fine_details, include_clamps, nozzle_mm):
    from .mold import analyse, build, MoldError

    try:
        a = analyse(mesh, nozzle_mm=float(nozzle_mm))
    except Exception as e:
        raise ActionError(f"Could not analyse part for mold making: {e}") from e

    try:
        parts = build(
            mesh, a,
            smooth=bool(smooth_fine_details),
            add_clamps=bool(include_clamps),
            clearance_mm=float(clearance_mm),
            wall_mm=float(wall_mm) if float(wall_mm) > 0 else None,
        )
    except MoldError as e:
        raise ActionError(str(e)) from e

    top = parts["top"]
    gap = 10.0
    is_three_part = "side_a" in parts

    if is_three_part:
        side_a = parts["side_a"]
        side_b = parts["side_b"]
        top_w = float(top.bounding_box.extents[0])
        sa_w = float(side_a.bounding_box.extents[0])
        sb_w = float(side_b.bounding_box.extents[0])
        top.apply_translation([-sa_w / 2 - top_w / 2 - gap, 0, 0])
        side_b.apply_translation([sb_w / 2 + sa_w / 2 + gap, 0, 0])
        combined_parts = [top, side_a, side_b]
    else:
        bot = parts["bottom"]
        top_w = float(top.bounding_box.extents[0])
        bot_w = float(bot.bounding_box.extents[0])
        top.apply_translation([-top_w / 2 - gap / 2, 0, 0])
        bot.apply_translation([bot_w / 2 + gap / 2, 0, 0])
        combined_parts = [top, bot]

    clamp_note = ""
    if "clamp" in parts:
        clamp = parts["clamp"]
        cur_max = max(p.bounding_box.bounds[1][0] for p in combined_parts)
        clamp_w = float(clamp.bounding_box.extents[0])
        clamp.apply_translation([cur_max + gap + clamp_w / 2, 0, 0])
        combined_parts.append(clamp)
        clamp_note = " Clamp shown on the right — print two copies."

    combined = trimesh.util.concatenate(combined_parts)

    piece_desc = "3-piece (top + two side pieces)" if is_three_part else "2-piece"
    note_lines = [
        f"Mold built: {piece_desc} mold with alignment pins, pour hole and air vents.",
    ]
    if is_three_part:
        note_lines.append(
            "  This part has undercuts. The bottom is split into two side pieces that pull out sideways."
        )
    for issue in a.issues:
        if issue.default_fix:
            note_lines.append(f"  Fixed automatically: {issue.message}")
        else:
            note_lines.append(f"  Note: {issue.message}")
    note_lines.append(
        f"Wall thickness: {a.wall_mm:.1f} mm. Cavity clearance: {clearance_mm} mm. "
        f"To save each piece separately, use Erase piece then export."
        + clamp_note
    )
    return combined, "\n".join(note_lines)


# ---------------------------------------------------------------- remesh


@action(
    "remesh",
    "Even out triangles",
    "Rebuilds the triangle layout so all triangles are roughly the same size. Useful after scanning (where triangles can be tiny in some spots and huge in others), before printing detailed surfaces, or before sculpting. The model shape stays the same — only the triangle pattern changes.",
    [
        Param(
            "target_triangles",
            "number",
            "How many triangles to aim for in the result. 50,000 is a good starting point for most prints. Raise it to keep more detail.",
            minimum=5000,
            maximum=500000,
            default=50000,
        ),
        Param(
            "smooth",
            "boolean",
            "Apply a light smoothing pass after remeshing to reduce any tiny bumps introduced by the triangle layout change.",
            default=True,
        ),
    ],
)
def remesh(mesh, target_triangles, smooth):
    import fast_simplification as _fs
    import trimesh.remesh as _tr
    import trimesh.smoothing as _ts

    orig_count = len(mesh.faces)
    target = max(1000, int(target_triangles))

    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    current = len(faces)

    if current < target:
        # Mesh needs more triangles: subdivide for uniformity then simplify back.
        target_edge = float(np.sqrt(mesh.area / target) * 1.4)
        sub_v, sub_f = _tr.subdivide_to_size(verts, faces, max_edge=target_edge, max_iter=8)
    else:
        # Mesh already has enough triangles: skip inflate, simplify directly.
        sub_v, sub_f = verts, faces

    # Multi-pass simplification: fast_simplification caps reduction per pass at ~60%.
    out_v, out_f = sub_v, sub_f
    for _ in range(6):
        current = len(out_f)
        if current <= target * 1.05:
            break
        reduction = min(0.92, 1.0 - target / current)
        if reduction < 0.02:
            break
        prev = current
        out_v, out_f = _fs.simplify(out_v, out_f, target_reduction=float(reduction))
        if len(out_f) >= prev * 0.97:
            break  # diminishing returns

    result = trimesh.Trimesh(out_v, out_f, process=False)

    if smooth:
        _ts.filter_taubin(result, lamb=0.5, nu=0.53, iterations=5)

    after_count = len(result.faces)
    uniformity = "more uniform"
    return result, (
        f"Remesh: {orig_count:,} triangles → {after_count:,} triangles, {uniformity} layout. "
        + ("Light smoothing applied." if smooth else "No smoothing.")
    )


# ---------------------------------------------------------------- cutting

PIN_PARAMS = [
    Param("pins", "boolean", "Add alignment pins so the parts line up perfectly when glued.", default=True),
    Param("pin_mm", "number", "How thick the pins are, in mm. Thicker pins are stronger.", minimum=1.5, maximum=20, default=4.0),
    Param("numbers", "boolean", "Engrave a number on each cut face so you know which part goes where.", default=True),
    Param("clearance_mm", "number", "How much wiggle room each pin has in its hole, in mm. 0.3 suits most printers — raise it if the parts are stiff to push together, lower it if they wobble.", minimum=0.1, maximum=0.6, default=0.3),
]


@action(
    "cut_in_two",
    "Cut in two",
    "Cuts the model in half along a flat plane, seals both sides, and adds alignment pins so the halves line up when you glue them back.",
    [AXIS, Param("position_mm", "number", "Where along that axis to make the cut, in mm."), *PIN_PARAMS],
)
def cut_in_two(mesh, axis, position_mm, pins, pin_mm, numbers, clearance_mm):
    from . import cut

    try:
        return cut.cut_in_two(mesh, axis, position_mm, pins, pin_mm, numbers, clearance_mm)
    except cut.CutError as exc:
        raise ActionError(str(exc)) from exc


@action(
    "split_to_fit",
    "Split to fit the printer",
    "Cuts a model that is too big for the printer into smaller parts that each fit the bed, with alignment pins so they glue back together in the right order.",
    [
        Param("bed_x", "number", "Your printer's bed width, in mm.", minimum=10, maximum=5000),
        Param("bed_y", "number", "Your printer's bed depth, in mm.", minimum=10, maximum=5000),
        Param("bed_z", "number", "Your printer's maximum print height, in mm.", minimum=10, maximum=5000),
        *PIN_PARAMS,
    ],
)
def split_to_fit(mesh, bed_x, bed_y, bed_z, pins, pin_mm, numbers, clearance_mm):
    from . import cut

    try:
        return cut.split_to_fit(mesh, (bed_x, bed_y, bed_z), pins, pin_mm, numbers, clearance_mm)
    except cut.CutError as exc:
        raise ActionError(str(exc)) from exc


@action(
    "add_hole",
    "Add a hole",
    "Drills a round hole into the surface — for a screw, magnet, cable or any other insert.",
    [
        Param("point", "numbers", "Where to drill — click on the surface where you want the hole.", minimum=3, maximum=3),
        Param("direction", "numbers", "Which way the hole goes into the model (set automatically when you click).", minimum=3, maximum=3),
        Param("diameter_mm", "number", "How wide the hole is, in mm. 3.2 mm fits an M3 screw.", minimum=0.5, maximum=200, default=3.2),
        Param("depth_mm", "number", "How deep the hole is, in mm. Set to 0 to go all the way through.", minimum=0, maximum=5000, default=0.0),
    ],
)
def add_hole(mesh, point, direction, diameter_mm, depth_mm):
    import manifold3d

    from .cut import CutError, _affine, _to_manifold, _to_mesh

    length = float(np.linalg.norm(direction))
    if length == 0:
        raise ActionError("The hole needs a direction.")
    direction = direction / length
    try:
        solid = _to_manifold(mesh)
    except CutError as exc:
        raise ActionError(str(exc)) from exc
    through = depth_mm <= 0
    depth = float(np.linalg.norm(mesh.extents)) * 2 if through else depth_mm
    lead = 0.5 + diameter_mm * 0.05  # start just outside the surface
    cylinder = manifold3d.Manifold.cylinder(depth + lead, diameter_mm / 2, diameter_mm / 2, 48, False)
    turn = trimesh.geometry.align_vectors([0, 0, 1], direction)[:3, :3]
    start = np.asarray(point) - direction * lead
    drilled = solid - cylinder.transform(_affine(turn, start))
    result = _to_mesh(drilled)
    if abs(result.volume - mesh.volume) < 1e-9 * max(mesh.volume, 1):
        raise ActionError("The hole missed the model. Click on the model's surface.")
    what = "all the way through" if through else f"{depth_mm:g} mm deep"
    return result, f"Added a {diameter_mm:g} mm hole, {what}"


@action(
    "extend_break",
    "Rebuild a broken end",
    "Rebuilds a snapped-off tab, ear or lug by growing it forward from the flat break surface, following the curve and taper it had before it broke.",
    [
        Param("point", "numbers", "Click on the flat break surface where the piece snapped off.", minimum=3, maximum=3),
        Param("length_mm", "number", "How long the rebuilt end should be, in mm.", minimum=0.5, maximum=1000, default=10.0),
        Param("follow_curve", "boolean", "Follow the bend the part had before it broke, rather than growing straight.", default=True),
        Param("taper", "boolean", "Follow the way the part was getting narrower or wider before the break.", default=True),
    ],
)
def extend_break(mesh, point, length_mm, follow_curve, taper):
    from . import extend

    try:
        return extend.extend_break(mesh, point, length_mm, follow_curve, taper)
    except extend.ExtendError as exc:
        raise ActionError(str(exc)) from exc


@action(
    "hollow",
    "Hollow for resin",
    "Makes the inside of the model hollow so resin printers use less resin. Adds drain holes at the bottom so liquid resin can pour out after printing. Not needed for filament printers — the slicer handles that.",
    [
        Param("wall_mm", "number", "How thick the outer shell should be, in mm. Thicker = stronger but uses more resin.", minimum=0.8, maximum=20, default=2.0),
        Param("drain_holes", "boolean", "Add holes at the bottom so liquid resin can drain out after printing.", default=True),
        Param("hole_mm", "number", "How wide the drain holes are, in mm.", minimum=1, maximum=20, default=3.0),
    ],
)
def hollow(mesh, wall_mm, drain_holes, hole_mm):
    from . import repair

    try:
        return repair.hollow(mesh, wall_mm, drain_holes, hole_mm)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc
