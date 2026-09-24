"""Regression tests for problems found in the code review."""

import io
import json
import threading
import zipfile

import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient
from scipy.spatial import Delaunay

from meshright import repair, server, settings
from meshright.actions import ActionError, run_action
from meshright.analysis import analyze
from meshright.batch import Options, process_file
from meshright.document import Document

from .conftest import save

client = TestClient(server.app, base_url="http://127.0.0.1")


def open_doc(mesh, tmp_path, name="model.stl"):
    with open(save(mesh, tmp_path, name), "rb") as fh:
        return client.post("/api/open", files={"file": (name, fh)}).json()["doc_id"]


def test_cleanup_leaves_a_small_open_edge_open():
    # A relief whose open outline has only 4 edges, with a 64-edge hole inside.
    t = np.linspace(0, 2 * np.pi, 64, endpoint=False)
    points = np.vstack([
        [[0, 0], [100, 0], [100, 100], [0, 100]],
        np.c_[50 + 10 * np.cos(t), 50 + 10 * np.sin(t)],
        np.c_[50 + 30 * np.cos(t + 0.05), 50 + 30 * np.sin(t + 0.05)],
    ])
    triangles = Delaunay(points).simplices
    centres = points[triangles].mean(axis=1)
    triangles = triangles[np.hypot(centres[:, 0] - 50, centres[:, 1] - 50) > 10]
    relief = trimesh.Trimesh(np.c_[points, 0.05 * np.hypot(points[:, 0] - 50, points[:, 1] - 50)], triangles)
    relief.fix_normals()
    out, receipt = repair.cleanup(relief)
    assert "filled 1 hole" in receipt
    remaining = repair._hole_info(out)
    assert len(remaining) == 1 and not remaining[0].fill


def test_an_action_that_would_empty_the_model_is_refused():
    flat = trimesh.Trimesh([[0, 0, 0], [1, 0, 0], [2, 0, 0]], [[0, 1, 2], [0, 2, 1]], process=False)
    with pytest.raises(ActionError, match="nothing"):
        run_action(flat, "cleanup")


def test_empty_mesh_report_does_not_crash():
    report = analyze(trimesh.Trimesh(np.zeros((0, 3)), np.zeros((0, 3), dtype=int)))
    assert report.verdict == "The model is empty" and not report.printable


def test_self_crossing_check_works_at_any_scale():
    vertices = np.array([[0, 0, 0], [2, 0, 0], [0, 2, 0], [0.5, 0.5, -1], [0.5, 0.5, 1], [1.5, 0.5, 0]], float)
    faces = np.array([[0, 1, 2], [3, 4, 5]])
    from meshright.intersections import crossing_pairs

    for scale in (1e-4, 1e-2, 1, 1e3):
        assert crossing_pairs(vertices * scale, faces).tolist() == [[0, 1]], scale


def test_no_printer_chosen_means_no_fit_check():
    assert settings.load()[1] is False
    bar = trimesh.creation.box(extents=(300, 10, 10))
    bar.apply_translation((0, 0, 5))
    doc = Document("bar.stl", bar)
    state = server._state("x", doc)
    assert not any(i["code"] == "too_big" for i in state["issues"])


def test_shrink_to_fit_never_suggests_zero():
    huge = trimesh.creation.box(extents=(250_000, 10_000, 10_000))  # saved in micrometres
    printer = settings.Printer("Mini", 180, 180, 180)
    fix = next(i for i in analyze(huge, printer=printer).issues if i.code == "too_big").fixes[0]
    assert fix["params"]["factor"] > 0
    run_action(huge, fix["action"], fix["params"])


@pytest.mark.parametrize("params", [{"x": 10**400}, {"x": "1e999"}])
def test_huge_numbers_are_refused_politely(cube, tmp_path, params):
    doc_id = open_doc(cube, tmp_path)
    res = client.post(f"/api/doc/{doc_id}/action", json={"action": "move", "params": params})
    assert res.status_code == 400


def test_bad_settings_and_batch_options(cube, tmp_path):
    assert client.post("/api/settings", json={"printer": [1, 2]}).status_code == 400
    with open(save(cube, tmp_path), "rb") as fh:
        res = client.post("/api/batch", files=[("files", ("a.stl", fh))], data={"options": "[1]"})
    assert res.status_code == 400


def test_damaged_files_get_a_clear_message(tmp_path):
    path = tmp_path / "bad.ply"
    path.write_text("ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\n"
                    "element face 1\nproperty list uchar int vertex_indices\nend_header\n0 0 0\n1 0 0\n0 1 0\n3 0 1 9\n")
    with open(path, "rb") as fh:
        res = client.post("/api/open", files={"file": ("bad.ply", fh)})
    assert res.status_code == 422 and "damaged" in res.json()["detail"]


def test_damaged_project_gets_a_clear_message():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("project.json", json.dumps([1, 2]))
    res = client.post("/api/open", files={"file": ("x.meshright", buffer.getvalue())})
    assert res.status_code == 422


def test_one_bad_file_does_not_sink_a_batch(tmp_path):
    single = trimesh.Trimesh([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]], process=False)
    result, _ = process_file(save(single, tmp_path), "one.stl", Options(best_bottom=True))
    assert result.ok or result.error


def test_oversized_upload_is_refused_before_reading(monkeypatch):
    monkeypatch.setattr(server, "MAX_UPLOAD_MB", 1)
    res = client.post("/api/open", files={"file": ("big.stl", b"x" * (3 * 1024 * 1024))})
    assert res.status_code == 413


def test_state_is_consistent_while_undoing_and_redoing():
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=10)
    doc = Document("x", sphere)
    doc.apply("erase_area", {"lasso": [-100, -100, 0, -100, 0, 100, -100, 100], "view": list(np.eye(4).ravel()),
                             "eye": [0, 0, 100], "facing_only": False})
    stop = False

    def toggle():
        while not stop:
            with doc.lock:
                doc.undo()
            with doc.lock:
                doc.redo()

    worker = threading.Thread(target=toggle)
    worker.start()
    try:
        for _ in range(200):
            server._state("x", doc)
    finally:
        stop = True
        worker.join()


def test_display_copy_always_matches_the_current_model():
    doc = Document("b", trimesh.creation.box((20, 20, 20)))
    for k in range(40):
        doc.apply("move", {"x": k})
        if k % 3 == 0:
            doc.undo()
        data, _, _ = server._display_copy("doc", doc)
        shown = trimesh.load(io.BytesIO(data), file_type="stl")
        assert np.allclose(shown.bounds, doc.mesh.bounds)
