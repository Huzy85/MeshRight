"""Fixes from the October 2026 audit: each test is one finding."""

import io
import zipfile

import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient

from meshright import cli, formats, mcp_server
from meshright.actions import REGISTRY, ActionError, run_action
from meshright.analysis import MeshLoadError, analyze, load_mesh
from meshright.document import MAX_UNDO_STEPS, Document
from meshright.server import app

client = TestClient(app, base_url="http://127.0.0.1")


def box(size=20):
    return trimesh.creation.box((size, size, size))


def test_undo_past_the_cap_stops_at_the_oldest_kept_step():
    doc = Document("box", box())
    for i in range(MAX_UNDO_STEPS + 5):
        doc.apply("move", {"x": 1})
    while doc.can_undo:
        doc.undo()
    # Five moves were forgotten: undo lands on their result, not the original.
    assert doc.mesh.bounds[0][0] == pytest.approx(box().bounds[0][0] + 5)
    doc.redo()
    assert doc.mesh.bounds[0][0] == pytest.approx(box().bounds[0][0] + 6)


def test_revert_at_the_cap_still_reaches_the_original():
    doc = Document("box", box())
    for _ in range(MAX_UNDO_STEPS):
        doc.apply("move", {"x": 1})
    doc.revert()
    assert doc.mesh is doc.original and len(doc.steps) == MAX_UNDO_STEPS
    doc.undo()
    assert doc.mesh.bounds[0][0] == pytest.approx(box().bounds[0][0] + MAX_UNDO_STEPS)


def test_an_unexpected_failure_becomes_a_plain_sentence(monkeypatch):
    def broken(mesh, **_):
        raise IndexError("index -9 is out of bounds")

    monkeypatch.setattr(REGISTRY["place_on_bed"], "run", broken)
    with pytest.raises(ActionError, match="Your model is unchanged") as caught:
        run_action(box(), "place_on_bed", {})
    assert "index" not in str(caught.value)


def test_moves_are_limited():
    with pytest.raises(ActionError, match="between"):
        run_action(box(), "move", {"x": 1e300})
    with pytest.raises(ActionError):
        run_action(box(), "move", {"x": float("nan")})


def test_zip_bombs_are_refused():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.bin", b"\0" * 5_000_000)
    with zipfile.ZipFile(data) as zf:
        with pytest.raises(formats.ReadError, match="unpacks to more than"):
            formats.read_zip_member(zf, "big.bin", limit=1_000_000)
        assert len(formats.read_zip_member(zf, "big.bin", limit=10_000_000)) == 5_000_000


def test_obj_with_zero_based_faces_is_refused(tmp_path):
    path = tmp_path / "zero.obj"
    path.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 1\nf 0 1 2\nf 0 1 3\n")
    with pytest.raises(MeshLoadError):
        load_mesh(path)


def test_tiny_models_are_not_ready_to_print():
    report = analyze(trimesh.creation.box((0.0001, 0.0001, 0.0001)))
    assert not report.printable and any(i.code == "tiny_model" and i.severity == "error" for i in report.issues)


def test_one_colour_ply_says_why_it_has_no_colours(tmp_path):
    mesh = box()
    mesh.visual.vertex_colors = np.tile([200, 30, 30, 255], (len(mesh.vertices), 1))
    path = tmp_path / "red.ply"
    mesh.export(path)
    assert "one colour all over" in load_mesh(path).metadata["notice"]


def test_cli_missing_file_and_empty_folder(tmp_path, capsys):
    assert cli.main(["check", str(tmp_path / "nope.stl")]) == 2
    assert "file not found" in capsys.readouterr().err
    assert cli.main(["check", str(tmp_path)]) == 2
    assert "no model files" in capsys.readouterr().err
    box().export(tmp_path / "a.stl")
    assert cli.main(["check", str(tmp_path)]) == 0


def test_mcp_will_not_overwrite_without_being_asked(tmp_path):
    source = tmp_path / "box.stl"
    box().export(source)
    precious = tmp_path / "precious.stl"
    precious.write_text("PRECIOUS")
    result = mcp_server.fix_model(str(source), out=str(precious), format="stl")
    assert "already exists" in result["error"] and precious.read_text() == "PRECIOUS"
    assert "error" not in mcp_server.fix_model(str(source), out=str(precious), format="stl", overwrite=True)
    result = mcp_server.fix_model(str(source), out=str(tmp_path / "no" / "such" / "deep" / "x.stl"), format="stl")
    assert "does not exist" in result["error"]


def _open(name="box.stl"):
    data = io.BytesIO()
    box().export(data, file_type="stl")
    return client.post("/api/open", files={"file": (name, data.getvalue())}).json()["doc_id"]


def test_non_latin_file_names_download():
    doc = _open("模型.stl")
    res = client.get(f"/api/doc/{doc}/export?format=stl")
    assert res.status_code == 200
    assert "filename*=UTF-8''" in res.headers["content-disposition"]


def test_pages_cannot_be_framed_by_other_sites():
    res = client.get("/api/health")
    assert res.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in res.headers["content-security-policy"]


def test_closed_model_is_reported_after_an_action():
    doc = _open()
    client.delete(f"/api/doc/{doc}")
    assert client.post(f"/api/doc/{doc}/action", json={"action": "mirror", "params": {"axis": "x"}}).status_code == 404


def test_las_scans_open(tmp_path):
    laspy = pytest.importorskip("laspy")
    rng = np.random.default_rng(1)
    points = rng.normal(size=(20000, 3))
    points = points / np.linalg.norm(points, axis=1, keepdims=True) * 0.05  # a 10 cm ball, in metres
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales = [0.0001] * 3
    las = laspy.LasData(header)
    las.x, las.y, las.z = points.T
    path = tmp_path / "ball.las"
    las.write(path)
    mesh = load_mesh(path)
    assert 90 < max(mesh.extents) < 110  # millimetres
