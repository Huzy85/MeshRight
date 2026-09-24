import numpy as np
import pytest
import trimesh

from meshright.actions import ActionError, run_action
from meshright.analysis import analyze
from meshright.orient import fit_plane, up_candidates


def tilted_box():
    box = trimesh.creation.box(extents=(10, 20, 30))
    box.apply_transform(trimesh.transformations.rotation_matrix(0.7, [1, 0.3, 0]))
    return box


def egg():
    mesh = trimesh.creation.icosphere(subdivisions=4, radius=1)
    mesh.apply_scale((30, 24, 36))
    return mesh


def down_params(down):
    return dict(zip(("down_x", "down_y", "down_z"), down))


def test_fit_plane_through_noisy_points():
    rng = np.random.default_rng(1)
    points = np.c_[rng.uniform(-10, 10, (50, 2)), rng.normal(0, 0.05, 50)]
    centre, normal = fit_plane(points)
    assert abs(abs(normal[2]) - 1) < 1e-3


def test_best_guess_for_a_box_is_its_biggest_side():
    best = up_candidates(tilted_box())[0]
    mesh, receipt = run_action(tilted_box(), "set_bottom", down_params(best["down"]))
    # Lies on its 20 x 30 side, squared up with the grid, on the bed.
    assert np.allclose(mesh.extents, [30, 20, 10], atol=1e-6)
    assert np.isclose(mesh.bounds[0][2], 0)
    assert "bottom" in receipt


def test_turntable_opening_is_the_best_guess():
    cylinder = trimesh.creation.cylinder(radius=10, height=30, sections=64)
    scan = trimesh.Trimesh(cylinder.vertices, cylinder.faces[cylinder.face_normals[:, 2] > -0.9])
    turn = trimesh.transformations.rotation_matrix(1.3, [1, 0, 0])
    scan.apply_transform(turn)
    best = up_candidates(scan)[0]
    assert "turntable" in best["reason"]
    expected = turn[:3, :3] @ [0, 0, -1]
    assert np.dot(best["down"], expected) > 0.99


def test_lay_flat_on_picked_points():
    flat, _ = run_action(tilted_box(), "set_bottom", down_params(up_candidates(tilted_box())[0]["down"]))
    bottom = flat.vertices[flat.vertices[:, 2] < 0.01]
    tilt = trimesh.transformations.rotation_matrix(0.5, [0.2, 1, 0])
    tilted = flat.copy()
    tilted.apply_transform(tilt)
    points = trimesh.transform_points(bottom, tilt) + np.random.default_rng(0).normal(0, 0.05, bottom.shape)
    mesh, receipt = run_action(tilted, "lay_flat", {"points": points.tolist()})
    assert np.allclose(sorted(mesh.extents), [10, 20, 30], atol=0.3)
    assert "4 points" in receipt


@pytest.mark.parametrize(
    "points",
    [
        [[0, 0, 0], [1, 1, 1]],  # too few
        [[0, 0, 0], [1, 0, 0], [2, 0, 0]],  # in a line
        [[0, 0], [1, 0], [0, 1]],  # not 3D
        "bottom",
    ],
)
def test_lay_flat_refuses_bad_points(points):
    with pytest.raises(ActionError):
        run_action(tilted_box(), "lay_flat", {"points": points})


def test_egg_on_a_tiny_spot_gets_a_flat_cut():
    report = analyze(egg())
    issue = next(i for i in report.issues if i.code == "small_contact")
    fix = issue.fixes[0]
    assert fix["action"] == "cut_flat_bottom" and fix["params"]["cut_mm"] > 0
    mesh, receipt = run_action(egg(), fix["action"], fix["params"])
    after = analyze(mesh)
    assert after.score == 100 and after.stats["watertight"]
    assert np.isclose(mesh.bounds[0][2], 0)
    assert np.isclose(mesh.extents[2], 72 - fix["params"]["cut_mm"], atol=0.01)


def test_leaning_model_may_tip_over():
    # A tall slab leaning far over its own base.
    slab = trimesh.creation.box(extents=(4, 20, 60))
    shear = np.eye(4)
    shear[0, 2] = 0.8
    slab.apply_transform(shear)
    slab.apply_translation((0, 0, 30))
    codes = [i.code for i in analyze(slab).issues]
    assert "may_tip_over" in codes


def test_cut_needs_a_closed_model():
    box = trimesh.creation.box(extents=(10, 10, 10))
    open_box = trimesh.Trimesh(box.vertices, box.faces[2:])
    with pytest.raises(ActionError):
        run_action(open_box, "cut_flat_bottom", {"cut_mm": 1})


def test_cut_cannot_remove_everything():
    with pytest.raises(ActionError):
        run_action(trimesh.creation.box(extents=(10, 10, 10)), "cut_flat_bottom", {"cut_mm": 10})


def test_a_hole_in_the_side_is_not_taken_for_the_turntable_edge():
    cylinder = trimesh.creation.cylinder(radius=10, height=40, sections=64)
    cylinder = cylinder.subdivide_to_size(2.0)
    centres = cylinder.triangles_center
    keep = (cylinder.face_normals[:, 2] > -0.9) & (np.linalg.norm(centres - [10, 0, 0], axis=1) > 6)
    scan = trimesh.Trimesh(cylinder.vertices, cylinder.faces[keep])
    best = up_candidates(scan)[0]
    assert "turntable" in best["reason"]
    assert np.dot(best["down"], [0, 0, -1]) > 0.99


def test_flat_base_keeps_the_whole_model():
    ball = egg()
    issue = next(i for i in analyze(ball).issues if i.code == "small_contact")
    fix = next(f for f in issue.fixes if f["action"] == "add_flat_base")
    mesh, receipt = run_action(ball, fix["action"], fix["params"])
    assert mesh.is_watertight and mesh.volume > ball.volume
    assert mesh.extents[2] == pytest.approx(ball.extents[2], abs=0.01)  # nothing cut off
    assert not any(i.code in ("may_tip_over", "small_contact") for i in analyze(mesh).issues)
    assert "nothing was cut off" in receipt
    assert np.isclose(mesh.bounds[0][2], 0)
