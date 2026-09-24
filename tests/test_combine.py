import numpy as np
import pytest
import trimesh

from meshright import merge
from meshright.actions import ActionError, run_action


def block():
    box = trimesh.creation.box(extents=(40, 40, 20))
    box.apply_translation((0, 0, 10))
    return box


def placed(offset=(0, 0, 0), scale=1.0):
    """16 numbers in column order, as the 3D view sends them."""
    matrix = np.eye(4)
    matrix[:3, :3] *= scale
    matrix[:3, 3] = offset
    return matrix.T.ravel().tolist()


@pytest.fixture
def cylinder():
    peg = trimesh.creation.cylinder(radius=5, height=30, sections=48)
    merge.PENDING["peg"] = ("peg.stl", peg)
    yield peg
    merge.PENDING.pop("peg", None)


def test_join(cylinder):
    mesh, receipt = run_action(block(), "combine_models", {"scan": "peg", "placement": placed((0, 0, 25))})
    assert mesh.is_watertight and mesh.body_count == 1
    assert mesh.bounds[1][2] == pytest.approx(40)
    assert receipt == "Joined peg.stl to the model"


def test_subtract_makes_a_hole(cylinder):
    mesh, receipt = run_action(block(), "combine_models", {"scan": "peg", "placement": placed((0, 0, 10)), "how": "subtract"})
    assert mesh.is_watertight
    assert mesh.volume == pytest.approx(block().volume - np.pi * 25 * 20, rel=0.01)
    assert "Cut the shape of peg.stl" in receipt


def test_overlap(cylinder):
    mesh, _ = run_action(block(), "combine_models", {"scan": "peg", "placement": placed((0, 0, 10), 0.5), "how": "overlap"})
    # Half size: radius 2.5, height 15, all inside the block.
    assert mesh.volume == pytest.approx(np.pi * 2.5**2 * 15, rel=0.01)


def test_apart_models(cylinder):
    mesh, receipt = run_action(block(), "combine_models", {"scan": "peg", "placement": placed((100, 0, 15))})
    assert mesh.body_count == 2 and "separate pieces" in receipt
    with pytest.raises(ActionError, match="does not touch"):
        run_action(block(), "combine_models", {"scan": "peg", "placement": placed((100, 0, 15)), "how": "subtract"})


def test_open_models_are_closed_first():
    cup = trimesh.creation.cylinder(radius=5, height=30, sections=48)
    cup.update_faces(cup.face_normals[:, 2] < 0.9)  # take the lid off
    cup.remove_unreferenced_vertices()
    assert not cup.is_watertight
    merge.PENDING["cup"] = ("cup.stl", cup)
    try:
        mesh, receipt = run_action(block(), "combine_models", {"scan": "cup", "placement": placed((0, 0, 25))})
    finally:
        merge.PENDING.pop("cup")
    assert mesh.is_watertight
    assert "first closed the gaps in cup.stl" in receipt


def test_bad_placement(cylinder):
    mirrored = np.diag([-1.0, 1, 1, 1]).T.ravel().tolist()
    with pytest.raises(ActionError, match="mirrored"):
        run_action(block(), "combine_models", {"scan": "peg", "placement": mirrored})
    with pytest.raises(ActionError, match="no longer open"):
        run_action(block(), "combine_models", {"scan": "gone", "placement": placed()})
