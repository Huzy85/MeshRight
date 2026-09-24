"""Wall thickness: find walls too thin for the printer to make.

From points spread evenly over the surface, a ray goes straight into the
model until it comes out the other side; that distance is the wall's
thickness there. Walls thinner than about two nozzle widths often come out
weak, full of gaps, or not at all.
"""

from __future__ import annotations

import numpy as np
import trimesh

SAMPLES = 2500


def measure(mesh: trimesh.Trimesh, limit_mm: float, samples: int = SAMPLES) -> dict:
    from .cut import CutError, _to_manifold

    try:
        solid = _to_manifold(mesh)
    except CutError:
        raise ValueError("The wall thickness can only be checked on a closed model. Run Clean up first.") from None

    points, faces = trimesh.sample.sample_surface_even(mesh, samples, seed=0)
    normals = mesh.face_normals[faces]
    reach = float(np.linalg.norm(mesh.extents)) * 1.5
    start_gap = 1e-4 * max(float(mesh.extents.max()), 1.0)

    thickness = np.full(len(points), np.inf)
    for i, (p, n) in enumerate(zip(points, normals)):
        hits = solid.ray_cast(tuple(p - n * start_gap), tuple(p - n * reach))
        if hits:
            thickness[i] = float(np.linalg.norm(np.asarray(hits[0].position) - p))
    measured = np.isfinite(thickness)
    if not measured.any():
        raise ValueError("Could not measure the walls of this model.")

    thin = measured & (thickness < limit_mm)
    thinnest = float(thickness[measured].min())
    return {
        "limit_mm": round(limit_mm, 2),
        "thinnest_mm": round(thinnest, 2),
        "thin_share": round(float(thin.sum() / measured.sum()), 4),
        "thin_spots": np.round(points[thin], 3).tolist()[:2000],
        # Scaling up by this much would bring the thinnest wall to the limit.
        "scale_to_fix": round(limit_mm / thinnest, 2) if thinnest > 0 else None,
    }
