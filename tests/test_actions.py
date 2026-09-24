import numpy as np
import pytest
import trimesh

from meshright.actions import REGISTRY, ActionError, run_action
from meshright.analysis import analyze


@pytest.fixture
def box():
    # 10 wide (x), 20 deep (y), 30 tall (z)
    return trimesh.creation.box(extents=(10, 20, 30))


def test_input_mesh_is_never_changed(box):
    before = box.vertices.copy()
    run_action(box, "scale", {"factor": 2})
    assert np.array_equal(box.vertices, before)


def test_move(box):
    mesh, receipt = run_action(box, "move", {"x": 5, "z": -1})
    assert np.allclose(mesh.bounds.mean(axis=0), [5, 0, -1])
    assert "Moved" in receipt


def test_rotate_keeps_centre_and_turns(box):
    mesh, _ = run_action(box, "rotate", {"axis": "x", "degrees": 90})
    assert np.allclose(mesh.extents, [10, 30, 20])
    assert np.allclose(mesh.bounds.mean(axis=0), box.bounds.mean(axis=0))


def test_scale_and_resize(box):
    mesh, _ = run_action(box, "scale", {"factor": 0.5})
    assert np.allclose(mesh.extents, [5, 10, 15])
    mesh, receipt = run_action(box, "resize", {"axis": "z", "size_mm": 120})
    assert np.allclose(mesh.extents, [40, 80, 120])
    assert "×4" in receipt


def test_convert_units(box):
    mesh, _ = run_action(box, "convert_units", {"from_units": "inches"})
    assert np.allclose(mesh.extents, [254, 508, 762])


def test_mirror_keeps_model_facing_outwards(box):
    mesh, _ = run_action(box, "mirror", {"axis": "x"})
    assert mesh.volume > 0
    assert analyze(mesh).score == 100


def test_place_on_bed(box):
    moved, _ = run_action(box, "move", {"x": 50, "y": -20, "z": 33})
    mesh, _ = run_action(moved, "place_on_bed")
    assert np.allclose(mesh.bounds[0][2], 0)
    assert np.allclose(mesh.bounds.mean(axis=0)[:2], [0, 0])


@pytest.mark.parametrize(
    "name, params",
    [
        ("scale", {"factor": -1}),
        ("scale", {"factor": "big"}),
        ("scale", {"factor": float("nan")}),
        ("rotate", {"axis": "w", "degrees": 90}),
        ("rotate", {"axis": "x"}),
        ("move", {"x": 1, "sideways": 2}),
        ("launch_rocket", {}),
    ],
)
def test_bad_inputs_are_refused(box, name, params):
    with pytest.raises(ActionError):
        run_action(box, name, params)


def test_every_action_describes_itself():
    for act in REGISTRY.values():
        info = act.describe()
        assert info["title"] and info["description"]
        for p in info["params"]:
            assert p["description"]


def test_tiny_model_offers_unit_fixes():
    tiny = trimesh.creation.box(extents=(2, 1, 1))
    issue = next(i for i in analyze(tiny).issues if i.code == "tiny_model")
    labels = [f["label"] for f in issue.fixes]
    assert "Saved in inches: make it 50.8 mm" in labels
    fix = issue.fixes[0]
    mesh, _ = run_action(tiny, fix["action"], fix["params"])
    assert not any(i.code == "tiny_model" for i in analyze(mesh).issues)


def test_erase_area_inside_a_loop():
    box = trimesh.creation.box(extents=(1.6, 1.6, 1.6))
    identity = np.eye(4).ravel().tolist()  # screen x, y = model x, y
    right_half = [0, -1, 1, -1, 1, 1, 0, 1]
    mesh, receipt = run_action(box, "erase_area", {"lasso": right_half, "view": identity, "eye": [0, 0, 10], "facing_only": False})
    assert len(mesh.faces) == 6 and receipt == "Erased 6 triangles"
    mesh, _ = run_action(box, "erase_area", {"lasso": right_half, "view": identity, "eye": [0, 0, 10]})
    assert len(mesh.faces) == 11  # only the triangle facing the camera


def test_erase_area_refuses_empty_or_everything():
    box = trimesh.creation.box(extents=(1.6, 1.6, 1.6))
    identity = np.eye(4).ravel().tolist()
    with pytest.raises(ActionError):
        run_action(box, "erase_area", {"lasso": [5, 5, 6, 5, 6, 6], "view": identity, "eye": [0, 0, 10]})
    with pytest.raises(ActionError):
        run_action(box, "erase_area", {"lasso": [-2, -2, 2, -2, 2, 2, -2, 2], "view": identity, "eye": [0, 0, 10], "facing_only": False})


def test_erase_piece_but_never_the_main_model():
    model = trimesh.creation.icosphere(3, radius=10)
    table = trimesh.creation.box(extents=(60, 60, 2))
    table.apply_translation((0, 0, -12))
    both = trimesh.util.concatenate([model, table])
    mesh, receipt = run_action(both, "erase_piece", {"point": [20, 20, -11]})
    assert len(mesh.faces) == len(model.faces) and "separate piece" in receipt
    with pytest.raises(ActionError):
        run_action(both, "erase_piece", {"point": [0, 0, 10]})
