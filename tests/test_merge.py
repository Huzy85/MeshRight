import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient

from meshright import merge
from meshright.actions import ActionError, run_action
from meshright.analysis import analyze
from meshright.server import app

from .conftest import save

client = TestClient(app, base_url="http://127.0.0.1")


def halves():
    """Two overlapping partial scans of a flattened capsule; the second one
    moved and turned as a scanner would leave it."""
    full = trimesh.creation.capsule(height=40, radius=15, count=[48, 48])
    full.apply_scale((1, 0.7, 1))
    centres = full.triangles_center
    front = trimesh.Trimesh(full.vertices, full.faces[centres[:, 0] < 5])
    back = trimesh.Trimesh(full.vertices, full.faces[centres[:, 0] > -5])
    for part in (front, back):
        part.remove_unreferenced_vertices()
    move = trimesh.transformations.random_rotation_matrix(np.random.default_rng(3).random(3))
    move[:3, 3] = [40, -25, 10]
    back.apply_transform(move)
    spots = np.array([[0, 10.4, 20], [0, -10.4, -20], [0, 0, 35], [2, 10, 0]])
    spots = np.array([full.vertices[np.argmin(np.linalg.norm(full.vertices - p, axis=1))] for p in spots])
    return full, front, back, spots, trimesh.transform_points(spots, move)


@pytest.mark.parametrize("click_error", [0.0, 1.5])
def test_merge_lines_up_and_fuses(click_error):
    full, front, back, base_spots, added_spots = halves()
    added_spots = added_spots + np.random.default_rng(0).normal(0, click_error, added_spots.shape)
    merge.PENDING["t"] = ("back.stl", back)
    mesh, receipt = run_action(front, "merge_scan", {"scan": "t", "added_points": added_spots.tolist(), "base_points": base_spots.tolist()})
    report = analyze(mesh)
    assert report.printable and report.stats["watertight"] and report.stats["pieces"] == 1
    assert np.allclose(report.stats["size_mm"], full.extents, atol=1.0)
    assert report.stats["volume_mm3"] == pytest.approx(full.volume, rel=0.05)
    assert "Lined up back.stl" in receipt


def test_mismatched_spots_are_refused():
    _, front, back, base_spots, added_spots = halves()
    merge.PENDING["t"] = ("back.stl", back)
    wrong = added_spots[[1, 0, 3, 2]] + 30
    with pytest.raises(ActionError, match="barely overlap"):
        run_action(front, "merge_scan", {"scan": "t", "added_points": wrong.tolist(), "base_points": base_spots.tolist()})


def test_unknown_scan_is_refused():
    box = trimesh.creation.box()
    with pytest.raises(ActionError):
        run_action(box, "merge_scan", {"scan": "gone", "added_points": [[0, 0, 0]] * 3, "base_points": [[0, 0, 0]] * 3})


def test_add_scan_api(tmp_path):
    _, front, back, _, _ = halves()
    with open(save(front, tmp_path, "front.stl"), "rb") as fh:
        doc_id = client.post("/api/open", files={"file": ("front.stl", fh)}).json()["doc_id"]
    with open(save(back, tmp_path, "back.stl"), "rb") as fh:
        res = client.post(f"/api/doc/{doc_id}/add-scan", files={"file": ("back.stl", fh)})
    assert res.status_code == 200
    scan = res.json()
    assert scan["name"] == "back.stl"
    shown = client.get(f"/api/scan/{scan['scan_id']}/mesh")
    assert shown.status_code == 200 and len(shown.content) > 84
