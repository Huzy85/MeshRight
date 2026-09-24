import io
import pytest

import trimesh
from fastapi.testclient import TestClient

from meshright.server import app

from .conftest import save

client = TestClient(app, base_url="http://127.0.0.1")


def upload(path, name=None):
    with open(path, "rb") as fh:
        return client.post("/api/open", files={"file": (name or path.name, fh)})


def open_doc(mesh, tmp_path):
    res = upload(save(mesh, tmp_path))
    assert res.status_code == 200
    return res.json()


def test_app_page_is_served():
    res = client.get("/")
    assert res.status_code == 200
    assert "MeshRight" in res.text
    assert client.get("/app.js").status_code == 200
    assert client.get("/vendor/three/three.module.min.js").status_code == 200


def test_health_and_actions():
    assert client.get("/api/health").json()["status"] == "ok"
    names = {a["name"] for a in client.get("/api/actions").json()}
    assert {"move", "rotate", "resize", "mirror", "place_on_bed"} <= names


def test_open_reports_problems(cube_with_hole, tmp_path):
    data = open_doc(cube_with_hole, tmp_path)
    assert data["printable"] is False
    assert data["issues"][0]["code"] == "holes"
    assert data["highlights"]["open_edges"]
    assert data["history"]["can_undo"] is False


def test_viewer_gets_the_current_mesh(cube_with_hole, tmp_path):
    data = open_doc(cube_with_hole, tmp_path)
    res = client.get(f"/api/doc/{data['doc_id']}/mesh")
    assert res.status_code == 200
    # binary STL: 80-byte header, 4-byte count, 50 bytes per triangle
    assert len(res.content) == 84 + 50 * 10


def test_action_undo_redo_revert(cube, tmp_path):
    doc_id = open_doc(cube, tmp_path)["doc_id"]
    url = f"/api/doc/{doc_id}"

    data = client.post(f"{url}/action", json={"action": "resize", "params": {"axis": "z", "size_mm": 100}}).json()
    assert data["stats"]["size_mm"] == [100, 100, 100]
    assert data["history"]["steps"][0]["receipt"].startswith("Set Z size to 100 mm")

    data = client.post(f"{url}/undo").json()
    assert data["stats"]["size_mm"] == [20, 20, 20]
    assert data["history"]["can_redo"] is True

    data = client.post(f"{url}/redo").json()
    assert data["stats"]["size_mm"] == [100, 100, 100]

    data = client.post(f"{url}/revert").json()
    assert data["stats"]["size_mm"] == [20, 20, 20]


def test_bad_action_is_explained(cube, tmp_path):
    doc_id = open_doc(cube, tmp_path)["doc_id"]
    res = client.post(f"/api/doc/{doc_id}/action", json={"action": "scale", "params": {"factor": 0}})
    assert res.status_code == 400
    assert "at least" in res.json()["detail"]


def test_export_formats(cube, tmp_path):
    doc_id = open_doc(cube, tmp_path)["doc_id"]
    for fmt in ("stl", "3mf", "obj"):
        res = client.get(f"/api/doc/{doc_id}/export", params={"format": fmt})
        assert res.status_code == 200
        assert f'filename="model-meshright.{fmt}"' in res.headers["content-disposition"]
        mesh = trimesh.load(io.BytesIO(res.content), file_type=fmt, force="mesh")
        assert len(mesh.faces) == 12
    assert client.get(f"/api/doc/{doc_id}/export", params={"format": "exe"}).status_code == 400


def test_unknown_document():
    assert client.get("/api/doc/nope/mesh").status_code == 404
    assert client.post("/api/doc/nope/undo").status_code == 404


def test_rejects_wrong_extension(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    assert upload(path).status_code == 415


def test_rejects_unreadable_mesh(tmp_path):
    path = tmp_path / "broken.stl"
    path.write_bytes(b"not a mesh")
    res = upload(path)
    assert res.status_code == 422
    assert "detail" in res.json()


def test_other_websites_are_blocked(cube, tmp_path):
    doc_id = open_doc(cube, tmp_path)["doc_id"]
    url = f"/api/doc/{doc_id}/undo"
    assert client.post(url, headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post(url, headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 403
    assert client.post(url, headers={"origin": "http://127.0.0.1"}).status_code == 200


def test_big_meshes_are_lightened_for_display(monkeypatch, tmp_path):
    from meshright import server

    monkeypatch.setattr(server, "DISPLAY_MAX_TRIANGLES", 500)
    sphere = trimesh.creation.icosphere(subdivisions=4)  # 5,120 triangles
    doc_id = open_doc(sphere, tmp_path)["doc_id"]
    res = client.get(f"/api/doc/{doc_id}/mesh")
    shown = int(res.headers["x-shown-triangles"])
    assert shown <= 600 and res.headers["x-total-triangles"] == "5120"
    assert len(res.content) == 84 + 50 * shown
    # Export keeps full detail.
    exported = client.get(f"/api/doc/{doc_id}/export", params={"format": "stl"})
    assert len(exported.content) == 84 + 50 * 5120


def test_samples_show_their_problem():
    listed = client.get("/api/samples").json()
    assert {s["id"] for s in listed} == {"broken-scan", "face-relief", "scan-on-table"}
    expected = {"broken-scan": "holes", "face-relief": "holes", "scan-on-table": "self_intersections"}
    for item in listed:
        res = client.get(f"/api/samples/{item['id']}")
        assert res.status_code == 200 and item["name"] in res.headers["content-disposition"]
        opened = client.post("/api/open", files={"file": (item["name"], res.content)}).json()
        assert expected[item["id"]] in [i["code"] for i in opened["issues"]]
    assert client.get("/api/samples/nope").status_code == 404


def test_roughness_matches_the_shown_triangles(tmp_path):
    import base64

    import numpy as np

    rough = trimesh.creation.icosphere(subdivisions=4, radius=30)
    rng = np.random.default_rng(0)
    top = rough.vertices[:, 2] > 15
    rough.vertices[top] += rough.vertex_normals[top] * rng.normal(0, 0.4, (top.sum(), 1))
    doc = open_doc(rough, tmp_path)["doc_id"]
    data = client.get(f"/api/doc/{doc}/roughness").json()
    levels = np.frombuffer(base64.b64decode(data["levels"]), dtype=np.uint8)
    shown = client.get(f"/api/doc/{doc}/mesh")
    assert len(levels) == int(shown.headers["X-Shown-Triangles"])
    assert 0.1 < data["rough_share"] < 0.3


def test_several_models_open_and_close(tmp_path):
    from meshright import server

    ids = [open_doc(trimesh.creation.box(extents=(10 + i, 10, 10)), tmp_path)["doc_id"] for i in range(server.MAX_OPEN_DOCUMENTS)]
    # Using the first one keeps it open when one more is opened.
    assert client.get(f"/api/doc/{ids[0]}/state").status_code == 200
    extra = open_doc(trimesh.creation.box(), tmp_path)["doc_id"]
    assert client.get(f"/api/doc/{ids[0]}/state").status_code == 200
    assert client.get(f"/api/doc/{ids[1]}/state").status_code == 404
    assert client.delete(f"/api/doc/{extra}").status_code == 200
    assert client.get(f"/api/doc/{extra}/state").status_code == 404


def test_flexible_filament_asks_for_thicker_walls(tmp_path):
    doc = open_doc(trimesh.creation.box(extents=(30, 30, 1.0)), tmp_path)["doc_id"]
    normal = client.get(f"/api/doc/{doc}/wall-thickness").json()
    flexible = client.get(f"/api/doc/{doc}/wall-thickness?flexible=true").json()
    assert normal["limit_mm"] == 0.8 and flexible["limit_mm"] == pytest.approx(1.2)
    assert normal["thin_share"] < 0.5 < flexible["thin_share"]
