import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient

from meshright.actions import ActionError, run_action
from meshright.server import app

IDENTITY = np.eye(4).ravel().tolist()
# A small loop in the middle of the screen: with the identity view, screen x, y
# are model x, y.
MIDDLE = [-0.5, -0.5, 0.5, -0.5, 0.5, 0.5, -0.5, 0.5]


def dome():
    """The top of a ball, seen from above: the part is taken from its middle."""
    ball = trimesh.creation.icosphere(subdivisions=4, radius=1.0)
    return ball


def test_part_outside_fits_over_the_surface():
    part, receipt = run_action(dome(), "part_from_area", {"lasso": MIDDLE, "view": IDENTITY, "eye": [0, 0, 10], "thickness_mm": 0.5, "gap_mm": 0.1})
    assert part.is_watertight and part.body_count == 1
    radii = np.linalg.norm(part.vertices, axis=1)
    # It sits outside the ball: from 0.1 (the gap) to 0.6 away from the surface.
    assert radii.min() == pytest.approx(1.1, abs=0.02)
    assert radii.max() == pytest.approx(1.6, abs=0.02)
    assert part.vertices[:, 2].min() > 0  # only the side facing the camera
    assert "on the outside with a 0.1 mm gap" in receipt


def test_part_inside_sits_behind_the_surface():
    part, receipt = run_action(dome(), "part_from_area", {"lasso": MIDDLE, "view": IDENTITY, "eye": [0, 0, 10], "thickness_mm": 0.4, "side": "inside"})
    radii = np.linalg.norm(part.vertices, axis=1)
    assert radii.max() == pytest.approx(1.0, abs=0.01) and radii.min() == pytest.approx(0.6, abs=0.02)
    assert part.is_watertight and "behind the surface" in receipt


def test_empty_loop():
    with pytest.raises(ActionError, match="Nothing is inside"):
        run_action(dome(), "part_from_area", {"lasso": [5, 5, 6, 5, 6, 6], "view": IDENTITY, "eye": [0, 0, 10]})


def test_part_opens_as_a_new_model(tmp_path):
    client = TestClient(app, base_url="http://127.0.0.1")
    path = tmp_path / "ball.stl"
    dome().export(path)
    doc = client.post("/api/open", files={"file": ("ball.stl", path.read_bytes())}).json()
    res = client.post(f"/api/doc/{doc['doc_id']}/new-model", json={
        "action": "part_from_area",
        "params": {"lasso": MIDDLE, "view": IDENTITY, "eye": [0, 0, 10], "thickness_mm": 0.5},
    })
    assert res.status_code == 200
    new = res.json()
    assert new["doc_id"] != doc["doc_id"] and new["name"] == "ball-part.stl"
    assert new["notice"].startswith("Made a 0.5 mm part")
    # The original is unchanged.
    assert client.get(f"/api/doc/{doc['doc_id']}/state").json()["history"]["position"] == 0
    assert client.post(f"/api/doc/{doc['doc_id']}/new-model", json={"action": "cleanup"}).status_code == 400
