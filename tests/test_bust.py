import numpy as np
import pytest
import trimesh

from meshright.actions import ActionError, run_action
from meshright.bust import neck_level


def head_scan():
    """A head on a neck and shoulders, open at the bottom with a ragged edge,
    the way a scan of a person comes out."""
    head = trimesh.creation.icosphere(subdivisions=4, radius=40)
    head.apply_translation((0, 0, 120))
    neck = trimesh.creation.cylinder(radius=18, height=50, sections=48)
    neck.apply_translation((0, 0, 75))
    shoulders = trimesh.creation.capsule(height=60, radius=25, count=(48, 48))
    shoulders.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, (0, 1, 0)))
    shoulders.apply_translation((0, 0, 40))
    body = trimesh.boolean.union([head, neck, shoulders], engine="manifold")
    body = body.subdivide()
    centres = body.triangles_center
    ragged = 32 + 3 * np.sin(np.arctan2(centres[:, 1], centres[:, 0]) * 7)
    body.update_faces(centres[:, 2] > ragged)
    body.remove_unreferenced_vertices()
    return body


def test_neck_is_found():
    scan = head_scan()
    assert not scan.is_watertight
    level = neck_level(scan)
    assert 34 < level < 45


@pytest.mark.parametrize("base", ["round", "square"])
def test_make_bust(base):
    mesh, receipt = run_action(head_scan(), "make_bust", {"height_mm": 80, "base": base})
    assert mesh.is_watertight and mesh.body_count == 1
    assert mesh.bounds[0][2] == pytest.approx(0, abs=1e-6)
    assert mesh.extents[2] == pytest.approx(80, abs=0.05)
    assert "closed the neck" in receipt and f"{base} base" in receipt
    # The base is wider than the bust's cut face and flat on the bed.
    on_bed = mesh.vertices[mesh.vertices[:, 2] < 1e-6]
    assert len(on_bed) >= 4


def test_bust_without_base():
    mesh, receipt = run_action(head_scan(), "make_bust", {"height_mm": 60, "base": "none"})
    assert mesh.is_watertight and mesh.extents[2] == pytest.approx(60, abs=0.05)
    assert "base" not in receipt


def test_closed_model_gets_a_flat_bottom():
    ball = trimesh.creation.icosphere(subdivisions=3, radius=20)
    mesh, receipt = run_action(ball, "make_bust", {"height_mm": 40, "base": "none"})
    assert "closed" not in receipt and "cut the bottom flat" in receipt
    assert mesh.is_watertight


def test_bad_height():
    with pytest.raises(ActionError):
        run_action(head_scan(), "make_bust", {"height_mm": 5})


def test_shoulders_survive_a_saved_scan(tmp_path):
    """Regression: saved as PLY and opened again, the crossing repair once
    threw the shoulders away."""
    from meshright.analysis import load_mesh
    from meshright.cut import _to_manifold

    path = tmp_path / "head.ply"
    head_scan().export(path)
    bust, _ = run_action(load_mesh(path), "make_bust", {"height_mm": 100, "base": "none"})
    assert _to_manifold(bust).slice(3).area() > 2000  # the shoulders are there
