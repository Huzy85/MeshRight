"""Actions: every change MeshRight can make to a model.

Each action is a named function with simple, checked inputs. The browser
buttons, batch mode, scripts and an optional AI assistant all go through this
one list, so they can only ever do what a person could do by clicking.

An action takes a mesh and returns a new mesh plus a short plain-English
receipt. It never changes the mesh it was given, which is what makes undo safe.
"""

from __future__ import annotations

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


def action(name: str, title: str, description: str, params: list[Param] | None = None, keeps_shape: bool = False):
    def register(func):
        REGISTRY[name] = Action(name, title, description, params or [], func, keeps_shape)
        return func

    return register


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
            if not np.isfinite(value):
                raise ActionError(f"{p.name} must be a normal number.")
            if p.minimum is not None and value < p.minimum:
                raise ActionError(f"{p.name} must be at least {p.minimum:g}.")
            if p.maximum is not None and value > p.maximum:
                raise ActionError(f"{p.name} must be at most {p.maximum:g}.")
        elif p.kind == "points":
            try:
                value = np.asarray(value, dtype=np.float64)
            except (TypeError, ValueError, OverflowError):
                raise ActionError(f"{p.name} must be a list of [x, y, z] points.") from None
            if value.ndim != 2 or value.shape[1] != 3 or not np.all(np.isfinite(value)):
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
            if not np.all(np.isfinite(value)):
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
    result, receipt = act.run(mesh.copy(), **_clean_params(act, params or {}))
    if len(result.faces) == 0:
        raise ActionError("That would leave nothing of the model, so it was not done.")
    from .colour import carry

    return carry(mesh, result), receipt


def _mm(value: float) -> str:
    return f"{value:.4g} mm"


def _size(mesh: trimesh.Trimesh) -> str:
    return " × ".join(f"{v:.4g}" for v in mesh.extents) + " mm"


# ---------------------------------------------------------------- transforms

AXIS = Param("axis", "choice", "Which axis: x (left-right), y (front-back) or z (up-down).", choices=("x", "y", "z"))


@action(
    "move",
    "Move",
    "Move the model by a distance in millimetres.",
    [
        Param("x", "number", "Millimetres to move left (-) or right (+).", default=0.0),
        Param("y", "number", "Millimetres to move towards (-) or away (+).", default=0.0),
        Param("z", "number", "Millimetres to move down (-) or up (+).", default=0.0),
    ],
    keeps_shape=True,
)
def move(mesh, x, y, z):
    mesh.apply_translation((x, y, z))
    return mesh, f"Moved by {x:g}, {y:g}, {z:g} mm"


@action(
    "rotate",
    "Rotate",
    "Turn the model around one axis, about its own centre.",
    [AXIS, Param("degrees", "number", "Angle in degrees; positive turns anticlockwise.", minimum=-360, maximum=360)],
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
    "Make the model bigger or smaller by a factor, keeping its proportions.",
    [Param("factor", "number", "For example 2 doubles the size, 0.5 halves it.", minimum=1e-6, maximum=1e6)],
    keeps_shape=True,
)
def scale(mesh, factor):
    before = _size(mesh)
    mesh.apply_scale(factor)
    return mesh, f"Scaled ×{factor:g}: {before} → {_size(mesh)}"


@action(
    "resize",
    "Set size",
    "Scale the model so one side is an exact length, keeping its proportions.",
    [AXIS, Param("size_mm", "number", "The length that side should have, in millimetres.", minimum=1e-3, maximum=1e6)],
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
    "The file was saved in other units: convert it to millimetres.",
    [Param("from_units", "choice", "The units the file was saved in.", choices=tuple(UNIT_FACTORS))],
    keeps_shape=True,
)
def convert_units(mesh, from_units):
    factor = UNIT_FACTORS[from_units]
    mesh.apply_scale(factor)
    return mesh, f"Converted from {from_units} to millimetres (×{factor:g}); now {_size(mesh)}"


@action(
    "mirror",
    "Mirror",
    "Flip the model to its mirror image along one axis, for example to make a left part from a right one.",
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
    "Centre the model on the print bed and set its lowest point on the bed surface.",
    keeps_shape=True,
)
def place_on_bed(mesh):
    from . import orient

    return orient.place_on_bed(mesh), "Centred on the bed and placed on its surface"


@action(
    "set_bottom",
    "Set the bottom",
    "Turn the model so the given direction points down, then put it on the bed.",
    [
        Param("down_x", "number", "Down direction, x part."),
        Param("down_y", "number", "Down direction, y part."),
        Param("down_z", "number", "Down direction, z part."),
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
    "Fit a flat plane through three or more points picked on the bottom of the "
    "model, turn the model so that plane rests on the bed, and put it there. "
    "More points average out scan noise.",
    [Param("points", "points", "Points on the bottom of the model, in mm.", minimum=3, maximum=500)],
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
    "Slice a thin layer off the bottom and seal it with a flat face, so the "
    "model stands steadily and sticks to the bed. The model must be closed.",
    [Param("cut_mm", "number", "How much to cut off the bottom, in mm.", minimum=0.05, maximum=10000)],
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


# ---------------------------------------------------------------- repairs

@action(
    "cleanup",
    "Clean up",
    "One-click repair: tidy the triangles, delete loose pieces, fill holes, "
    "repair surfaces that cut through themselves, turn triangles outwards, and "
    "reduce needless detail. Check the plan first to see what it will change.",
    [
        Param("remove_loose_pieces", "boolean", "Delete small loose pieces such as scan noise.", default=True),
        Param("fill", "boolean", "Fill holes in the surface.", default=True),
        Param("fix_crossing", "boolean", "Repair places where the surface cuts through itself.", default=True),
        Param("reduce", "boolean", "Reduce detail on very heavy scans so slicers stay quick.", default=True),
        Param(
            "close_open_edge", "boolean",
            "Also close a big opening that looks like the model's own open edge (a face scan or relief).",
            default=False,
        ),
        Param(
            "preset", "choice",
            "quick (fewest triangles, slices fastest), balanced, or detail (keep every triangle).",
            choices=("quick", "balanced", "detail"), default="balanced",
        ),
    ],
)
def cleanup(mesh, remove_loose_pieces, fill, fix_crossing, reduce, close_open_edge, preset):
    from . import repair

    return repair.cleanup(mesh, remove_loose_pieces, fill, fix_crossing, reduce, close_open_edge, preset)


@action(
    "make_solid",
    "Make Solid",
    "Rebuild a badly broken model as one closed solid. Closes small gaps and "
    "removes hidden inner walls, but softens detail finer than the chosen size. "
    "Use it when Clean up is not enough.",
    [
        Param(
            "detail_mm", "number",
            "Smallest detail to keep in mm. 0 picks a size from the model (at least 0.2 mm).",
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
    "Delete the part of the surface inside a loop drawn on the screen, for example "
    "a table or turntable the scanner picked up. Leaves an opening that Clean up can close.",
    [
        Param("lasso", "numbers", "The loop as x, y pairs in screen coordinates from -1 to 1.", minimum=6, maximum=4000),
        Param("view", "numbers", "The viewer's 4x4 view-projection matrix, column by column.", minimum=16, maximum=16),
        Param("eye", "numbers", "The camera position in model coordinates.", minimum=3, maximum=3),
        Param("facing_only", "boolean", "Only erase the side facing the camera.", default=True),
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
    "Turn the part of the surface inside a loop into a solid piece that fits it: "
    "a pad, a grip, a cover or a mask shell. Outside builds it on top of the "
    "surface (with a small gap so it fits); inside builds it behind the surface.",
    [
        Param("lasso", "numbers", "The loop as x, y pairs in screen coordinates from -1 to 1.", minimum=6, maximum=4000),
        Param("view", "numbers", "The viewer's 4x4 view-projection matrix, column by column.", minimum=16, maximum=16),
        Param("eye", "numbers", "The camera position in model coordinates.", minimum=3, maximum=3),
        Param("facing_only", "boolean", "Only use the side facing the camera.", default=True),
        Param("thickness_mm", "number", "How thick the part is, in mm.", minimum=0.4, maximum=100, default=3.0),
        Param("side", "choice", "outside (on top of the surface) or inside (behind it).", choices=("outside", "inside"), default="outside"),
        Param("gap_mm", "number", "Room left between the part and the surface when outside, in mm.", minimum=0, maximum=5, default=0.2),
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
    "Delete one whole separate piece, picked by a point on it: for example a "
    "scanned table or stand. The main model cannot be erased this way.",
    [Param("point", "numbers", "A point on the piece, in model coordinates.", minimum=3, maximum=3)],
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
    "Turn an open surface, such as a face scan, mask or relief, into a solid shell "
    "you can print. The scanned surface stays as it is; the thickness is added behind it.",
    [Param("thickness_mm", "number", "How thick the shell should be, in mm.", minimum=0.2, maximum=100, default=2.0)],
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
    "Line up another scan of the same object using three or more matching spots "
    "clicked on each, fine-tune the fit, and fuse both into one surface.",
    [
        Param("scan", "text", "The id of the uploaded scan to merge.", maximum=64),
        Param("added_points", "points", "Spots on the added scan, in its own coordinates.", minimum=3, maximum=50),
        Param("base_points", "points", "The matching spots on this model, in the same order.", minimum=3, maximum=50),
        Param("fuse", "boolean", "Fuse both scans into one closed surface.", default=True),
    ],
)
def merge_scan(mesh, scan, added_points, base_points, fuse):
    from . import merge

    if scan not in merge.PENDING:
        raise ActionError("That scan is no longer open. Please add it again.")
    name, added = merge.PENDING[scan]
    try:
        return merge.merge(mesh, added, name, added_points, base_points, fuse)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc


@action(
    "combine_models",
    "Combine with another model",
    "Join another model to this one (a stand, a handle, a label), cut its shape "
    "out of this one, or keep only where the two overlap. Both are closed first "
    "if they have gaps.",
    [
        Param("scan", "text", "The id of the uploaded model to combine with.", maximum=64),
        Param("placement", "numbers", "Where the added model sits: a 4x4 matrix, 16 numbers in column order.", minimum=16, maximum=16),
        Param("how", "choice", "join, subtract (cut it away) or overlap (keep only the overlap).", choices=("join", "subtract", "overlap"), default="join"),
    ],
)
def combine_models(mesh, scan, placement, how):
    from . import combine, merge

    if scan not in merge.PENDING:
        raise ActionError("That model is no longer open. Please add it again.")
    name, added = merge.PENDING[scan]
    try:
        return combine.combine(mesh, added, name, combine.placement(placement), how)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc


@action(
    "make_bust",
    "Make a bust",
    "Turn an upright head or head-and-shoulders scan into a bust: close the "
    "opening at the neck, cut the ragged bottom off flat, add a base and size it.",
    [
        Param("height_mm", "number", "Height of the finished bust, base included, in mm.", minimum=10, maximum=2000, default=100),
        Param("base", "choice", "The base: round, square or none.", choices=("round", "square", "none"), default="round"),
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
    "Brush over the surface to smooth rough spots, flatten an area, or push "
    "the surface out or in. The effect fades out towards the edge of the brush.",
    [
        Param("stroke", "points", "The points the brush passed over, on the surface.", minimum=1, maximum=2000),
        Param("radius_mm", "number", "Brush radius in mm.", minimum=0.05, maximum=10000),
        Param("brush", "choice", "smooth, flatten, push_out or push_in.", choices=("smooth", "flatten", "push_out", "push_in"), default="smooth"),
        Param("strength", "choice", "light, medium or strong.", choices=("light", "medium", "strong"), default="medium"),
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
    "Use fewer, larger triangles so slicers stay quick. Keeps the shape; very fine "
    "detail below what a printer can show is smoothed.",
    [Param("triangles", "number", "About how many triangles to keep.", minimum=1000, maximum=10_000_000, default=300_000)],
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


# ---------------------------------------------------------------- cutting

PIN_PARAMS = [
    Param("pins", "boolean", "Add pin holes across the cut, and pins to print, so the parts line up.", default=True),
    Param("pin_mm", "number", "Pin thickness in mm.", minimum=1.5, maximum=20, default=4.0),
]


@action(
    "cut_in_two",
    "Cut in two",
    "Cut the model into two closed parts along a flat plane, with pin holes so they "
    "line up when glued. The model must be closed.",
    [AXIS, Param("position_mm", "number", "Where to cut along that axis, in mm (model coordinates)."), *PIN_PARAMS],
)
def cut_in_two(mesh, axis, position_mm, pins, pin_mm):
    from . import cut

    try:
        return cut.cut_in_two(mesh, axis, position_mm, pins, pin_mm)
    except cut.CutError as exc:
        raise ActionError(str(exc)) from exc


@action(
    "split_to_fit",
    "Split to fit the printer",
    "Cut a model that is too big into parts that each fit the printer, with pin "
    "holes and pins so they line up. The model must be closed.",
    [
        Param("bed_x", "number", "Printer bed width in mm.", minimum=10, maximum=5000),
        Param("bed_y", "number", "Printer bed depth in mm.", minimum=10, maximum=5000),
        Param("bed_z", "number", "Printer maximum height in mm.", minimum=10, maximum=5000),
        *PIN_PARAMS,
    ],
)
def split_to_fit(mesh, bed_x, bed_y, bed_z, pins, pin_mm):
    from . import cut

    try:
        return cut.split_to_fit(mesh, (bed_x, bed_y, bed_z), pins, pin_mm)
    except cut.CutError as exc:
        raise ActionError(str(exc)) from exc


@action(
    "add_hole",
    "Add a hole",
    "Drill a round hole into the surface at a point, for screws, magnets or cables. "
    "The model must be closed.",
    [
        Param("point", "numbers", "Where the hole starts, on the surface (model coordinates).", minimum=3, maximum=3),
        Param("direction", "numbers", "Which way the hole goes into the model.", minimum=3, maximum=3),
        Param("diameter_mm", "number", "Hole width in mm.", minimum=0.5, maximum=200, default=3.2),
        Param("depth_mm", "number", "Hole depth in mm; 0 goes all the way through.", minimum=0, maximum=5000, default=0.0),
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
    "hollow",
    "Hollow for resin",
    "Make a solid model hollow inside for resin printing, keeping walls of the "
    "chosen thickness, with drain holes so trapped resin can run out. Filament "
    "printers do not need this: the slicer's infill already fills the inside sparsely.",
    [
        Param("wall_mm", "number", "Wall thickness in mm.", minimum=0.8, maximum=20, default=2.0),
        Param("drain_holes", "boolean", "Add drain holes through the bottom.", default=True),
        Param("hole_mm", "number", "Drain hole width in mm.", minimum=1, maximum=20, default=3.0),
    ],
)
def hollow(mesh, wall_mm, drain_holes, hole_mm):
    from . import repair

    try:
        return repair.hollow(mesh, wall_mm, drain_holes, hole_mm)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc
