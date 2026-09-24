import io

import numpy as np
import pytest
from fastapi.testclient import TestClient

from meshright import project
from meshright.document import Document
from meshright.server import app

from .conftest import save

client = TestClient(app, base_url="http://127.0.0.1")


def test_save_and_reopen_keeps_current_and_original(cube_with_hole, tmp_path):
    doc = Document(name="box.stl", original=cube_with_hole)
    doc.apply("cleanup")
    doc.apply("resize", {"axis": "z", "size_mm": 50})
    path = tmp_path / "box.meshright"
    path.write_bytes(project.save(doc))

    reopened = project.load(path)
    assert reopened.name == "box.stl"
    assert np.allclose(reopened.mesh.extents, doc.mesh.extents)
    assert reopened.mesh.is_watertight
    assert "2 earlier steps" in reopened.steps[0].receipt
    reopened.revert()
    assert np.allclose(reopened.mesh.extents, cube_with_hole.extents)


def test_project_with_no_changes(cube, tmp_path):
    path = tmp_path / "plain.meshright"
    path.write_bytes(project.save(Document(name="cube.stl", original=cube)))
    reopened = project.load(path)
    assert reopened.position == 0 and not reopened.steps


def test_damaged_project(tmp_path):
    path = tmp_path / "bad.meshright"
    path.write_bytes(b"not a zip")
    with pytest.raises(project.ProjectError):
        project.load(path)


def test_project_round_trip_through_the_app(cube_with_hole, tmp_path):
    with open(save(cube_with_hole, tmp_path), "rb") as fh:
        doc_id = client.post("/api/open", files={"file": ("box.stl", fh)}).json()["doc_id"]
    client.post(f"/api/doc/{doc_id}/action", json={"action": "cleanup", "params": {}})
    saved = client.get(f"/api/doc/{doc_id}/project")
    assert saved.status_code == 200
    assert 'filename="box.meshright"' in saved.headers["content-disposition"]
    reopened = client.post("/api/open", files={"file": ("box.meshright", io.BytesIO(saved.content))}).json()
    assert reopened["printable"] and reopened["history"]["can_undo"]
    assert reopened["history"]["steps"][0]["receipt"].startswith("Reopened the saved project")
