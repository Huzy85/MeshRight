import numpy as np
import pytest
import trimesh

from meshright import settings
from meshright.actions import ActionError, run_action
from meshright.analysis import analyze


def parts_of(mesh):
    labels = trimesh.graph.connected_component_labels(mesh.face_adjacency, node_count=len(mesh.faces))
    return [mesh.submesh([np.flatnonzero(labels == k)], append=True) for k in range(labels.max() + 1)]


def block():
    box = trimesh.creation.box(extents=(100, 60, 40))
    box.apply_translation((0, 0, 20))
    return box


def test_cut_in_two_with_pins():
    mesh, receipt = run_action(block(), "cut_in_two", {"axis": "x", "position_mm": 10})
    parts = parts_of(mesh)
    assert len(parts) == 4  # two halves and two pins
    assert all(p.is_watertight for p in parts)
    assert "2 pin holes and 2 pins" in receipt
    # Each half rests on its cut face, so the cut direction is now the height.
    heights = sorted(round(p.extents[2], 3) for p in parts if p.volume > 1000)
    assert heights == [40, 60]
    # Pin holes: the halves lose a little volume, the pins make it back.
    halves = sum(p.volume for p in parts if p.volume > 1000)
    assert halves < block().volume


def test_cut_without_pins():
    mesh, receipt = run_action(block(), "cut_in_two", {"axis": "z", "position_mm": 15, "pins": False})
    parts = parts_of(mesh)
    assert len(parts) == 2 and sum(p.volume for p in parts) == pytest.approx(block().volume)
    assert "pin" not in receipt
    assert np.isclose(mesh.bounds[0][2], 0)


def test_cut_must_cross_the_model():
    with pytest.raises(ActionError, match="inside the model"):
        run_action(block(), "cut_in_two", {"axis": "z", "position_mm": 80})


def test_cut_needs_a_closed_model(cube_with_hole):
    with pytest.raises(ActionError, match="closed"):
        run_action(cube_with_hole, "cut_in_two", {"axis": "x", "position_mm": 0})


def test_split_to_fit_the_printer():
    big = trimesh.creation.icosphere(4, radius=1)
    big.apply_scale((200, 120, 150))
    big.apply_translation((0, 0, 150))
    printer = settings.Printer("Bambu Lab A1", 256, 256, 256)
    issue = next(i for i in analyze(big, printer=printer).issues if i.code == "too_big")
    split = next(f for f in issue.fixes if f["action"] == "split_to_fit")
    mesh, receipt = run_action(big, split["action"], split["params"])
    report = analyze(mesh, printer=printer)
    assert report.printable
    assert "parts_fit_separately" in {i.code for i in report.issues}
    assert not {"too_big", "huge_model"} & {i.code for i in report.issues}
    assert "Split into" in receipt
    for part in parts_of(mesh):
        assert part.is_watertight
        assert all(np.sort(part.extents) <= 256)


def test_split_when_it_already_fits():
    with pytest.raises(ActionError, match="already fits"):
        run_action(block(), "split_to_fit", {"bed_x": 256, "bed_y": 256, "bed_z": 256})


def test_add_a_hole_through_and_blind():
    box = trimesh.creation.box(extents=(40, 40, 20))
    top = [0, 0, 10]
    through, receipt = run_action(box, "add_hole", {"point": top, "direction": [0, 0, -1], "diameter_mm": 5})
    removed = box.volume - through.volume
    assert removed == pytest.approx(np.pi * 2.5**2 * 20, rel=0.02)
    assert through.is_watertight and "all the way through" in receipt
    blind, receipt = run_action(box, "add_hole", {"point": top, "direction": [0, 0, -1], "diameter_mm": 5, "depth_mm": 8})
    assert box.volume - blind.volume == pytest.approx(np.pi * 2.5**2 * 8, rel=0.03)
    assert "8 mm deep" in receipt


def test_a_hole_that_misses_is_refused():
    with pytest.raises(ActionError, match="missed"):
        run_action(block(), "add_hole", {"point": [500, 500, 500], "direction": [0, 0, -1], "diameter_mm": 5})
