"""Print orientation advisor: which way to place a model on the bed.

Tries many ways of resting the model and measures, for each:
- overhangs: surface facing down more steeply than 45 degrees, which needs
  support (and roughly how much support material that means),
- height: taller prints take longer,
- bed contact: more contact sticks better,
- strength: printed parts are weakest between layers, so for most parts
  the strongest way is with the long side lying flat.

These are estimates to compare options; the slicer makes the real supports.
"""

from __future__ import annotations

import numpy as np
import trimesh

from .orient import rotation_to_down

OVERHANG_ANGLE = 45  # degrees from vertical that printers manage without support
BED_TOLERANCE_MM = 0.3


def _directions(mesh: trimesh.Trimesh) -> np.ndarray:
    """Down directions worth trying: flat sides of the outer shape, the six
    axis directions, and the diagonals in between."""
    try:
        hull = mesh.convex_hull
        dirs = [hull.facets_normal[np.argsort(-hull.facets_area)[:40]]] if len(hull.facets) else []
    except Exception:  # flat or tiny models: the axis directions will do
        dirs = []
    axes = [v for v in np.array(np.meshgrid([-1, 0, 1], [-1, 0, 1], [-1, 0, 1])).T.reshape(-1, 3) if np.any(v)]
    dirs.append(np.asarray(axes, dtype=np.float64))
    dirs = np.vstack(dirs)
    dirs /= np.linalg.norm(dirs, axis=1)[:, None]
    # Drop near-duplicates (within about 5 degrees).
    keep = []
    for d in dirs:
        if all(d @ k < 0.996 for k in keep):
            keep.append(d)
    return np.asarray(keep)


def evaluate(mesh: trimesh.Trimesh, down) -> dict:
    """Measure one way of placing the model (``down`` points at the bed)."""
    down = np.asarray(down, dtype=np.float64)
    down /= np.linalg.norm(down)
    heights = -(mesh.vertices @ down)
    heights -= heights.min()
    face_heights = heights[mesh.faces]
    facing_down = mesh.face_normals @ down
    areas = mesh.area_faces

    on_bed = (face_heights.max(axis=1) <= BED_TOLERANCE_MM) & (facing_down > 0.95)
    overhang = (facing_down > np.cos(np.radians(OVERHANG_ANGLE))) & ~on_bed & (face_heights.min(axis=1) > BED_TOLERANCE_MM)
    # Support: the overhang's footprint times how high it hangs.
    support_mm3 = float(np.sum(areas[overhang] * facing_down[overhang] * face_heights[overhang].mean(axis=1)))

    _, _, vt = np.linalg.svd(mesh.vertices - mesh.vertices.mean(axis=0), full_matrices=False)
    long_axis = vt[0]
    return {
        "down": [round(float(v), 6) for v in down],
        "overhang_mm2": round(float(areas[overhang].sum()), 1),
        "support_cm3": round(support_mm3 / 1000, 2),
        "height_mm": round(float(heights.max()), 1),
        "contact_mm2": round(float(areas[on_bed].sum()), 1),
        # 1 = long side flat on the bed (strongest), 0 = standing on end.
        "flatness": round(float(1 - abs(long_axis @ down)), 3),
    }


def advise(mesh: trimesh.Trimesh) -> dict:
    """The best options, each with a plain-English reason, plus how the
    model does as it is now."""
    options = [evaluate(mesh, d) for d in _directions(mesh)]
    current = evaluate(mesh, [0, 0, -1])
    stands = [o for o in options if o["contact_mm2"] > 0] or options

    def pick(key):
        return min(stands, key=key)

    fewest = pick(lambda o: (o["support_cm3"], -o["contact_mm2"]))
    strongest = pick(lambda o: (-round(o["flatness"], 1), o["support_cm3"]))
    fastest = pick(lambda o: (o["height_mm"], o["support_cm3"]))

    goals = [
        ("Fewest supports", "fewest supports", fewest, "Least surface hanging over, so less support to print and remove."),
        ("Strongest", "strongest", strongest, "The long side lies flat, so layers run along the part, which is where prints are strongest."),
        ("Fastest", "fastest", fastest, "The lowest height, so the fewest layers to print."),
    ]
    # Group goals that turned out to want the same placement.
    groups: list[tuple[dict, list]] = []
    for goal in goals:
        group = next((g for g in groups if np.dot(g[0]["down"], goal[2]["down"]) > 0.996), None)
        if group:
            group[1].append(goal)
        else:
            groups.append((goal[2], [goal]))

    options = []
    for option, members in groups:
        if len(members) == 1:
            title, _, _, why = members[0]
        else:
            names = [m[1] for m in members]
            title = "Best all round" if len(members) == len(goals) else " and ".join(m[0] for m in members)
            why = "Wins on " + ", ".join(names[:-1]) + " and " + names[-1] + "."
        options.append({**option, "title": title, "why": why})
    return {"options": options, "current": current}


def rotation_for(down) -> np.ndarray:
    return rotation_to_down(down)
