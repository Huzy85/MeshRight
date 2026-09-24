import numpy as np
import pytest
import trimesh

from meshright.actions import ActionError, run_action


def bumpy_plate():
    """A closed slab with a noisy top, like a rough scan."""
    plate = trimesh.creation.box(extents=(60, 60, 10))
    for _ in range(4):
        plate = plate.subdivide()
    rng = np.random.default_rng(0)
    top = plate.vertices[:, 2] > 4.9
    plate.vertices[top, 2] += rng.normal(0, 0.4, top.sum())
    return plate


def roughness(mesh, centre, radius):
    near = np.linalg.norm(mesh.vertices[:, :2] - centre[:2], axis=1) < radius
    top = near & (mesh.vertices[:, 2] > 3)
    return float(np.std(mesh.vertices[top, 2]))


STROKE = [[-10, 0, 5], [10, 0, 5]]


def test_smooth_calms_the_stroke_only():
    plate = bumpy_plate()
    mesh, receipt = run_action(plate, "sculpt", {"stroke": STROKE, "radius_mm": 8, "strength": "strong"})
    assert roughness(mesh, np.zeros(3), 5) < 0.5 * roughness(plate, np.zeros(3), 5)
    # Far from the stroke nothing moves.
    far = np.linalg.norm(plate.vertices[:, :2] - [25, 25], axis=1) < 4
    assert np.allclose(mesh.vertices[far], plate.vertices[far])
    assert len(mesh.faces) == len(plate.faces) and mesh.is_watertight
    assert receipt.startswith("Smoothed an area 16 mm wide")


def test_flatten():
    plate = bumpy_plate()
    mesh, receipt = run_action(plate, "sculpt", {"stroke": STROKE, "radius_mm": 8, "brush": "flatten", "strength": "strong"})
    on_line = (np.abs(plate.vertices[:, 1]) < 2.5) & (np.abs(plate.vertices[:, 0]) < 8) & (plate.vertices[:, 2] > 3)
    assert np.std(mesh.vertices[on_line, 2]) < 0.3 * np.std(plate.vertices[on_line, 2])
    assert receipt.startswith("Flattened")


@pytest.mark.parametrize(("brush", "sign"), [("push_out", 1), ("push_in", -1)])
def test_push(brush, sign):
    plate = trimesh.creation.box(extents=(60, 60, 10)).subdivide().subdivide().subdivide()
    mesh, _ = run_action(plate, "sculpt", {"stroke": [[0, 0, 5]], "radius_mm": 10, "brush": brush})
    centre = np.argmin(np.linalg.norm(plate.vertices - [0, 0, 5], axis=1))
    assert sign * (mesh.vertices[centre, 2] - 5) > 0.5
    assert mesh.is_watertight


def test_brush_must_touch():
    with pytest.raises(ActionError, match="did not touch"):
        run_action(bumpy_plate(), "sculpt", {"stroke": [[500, 500, 500]], "radius_mm": 2})
