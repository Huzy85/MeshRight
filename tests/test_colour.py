import numpy as np
import pytest
import trimesh
from fastapi.testclient import TestClient

from meshright import colour
from meshright.actions import run_action
from meshright.analysis import load_mesh
from meshright.server import app


def two_tone_ball(holes=False):
    """Red on top, blue below: like a colour scan."""
    ball = trimesh.creation.icosphere(subdivisions=3, radius=20)
    if holes:
        ball.update_faces(ball.triangles_center[:, 2] < 18)
        ball.remove_unreferenced_vertices()
    rgba = np.where((ball.vertices[:, 2] > 0)[:, None], [220, 30, 30, 255], [30, 30, 220, 255]).astype(np.uint8)
    ball.visual = trimesh.visual.ColorVisuals(ball, vertex_colors=rgba)
    return ball


def is_red(rgba):
    return rgba[0] > 150 and rgba[2] < 100


def is_blue(rgba):
    return rgba[2] > 150 and rgba[0] < 100


@pytest.mark.parametrize("suffix", [".ply", ".obj", ".glb"])
def test_colours_survive_opening(tmp_path, suffix):
    path = tmp_path / f"ball{suffix}"
    two_tone_ball().export(path)
    mesh = load_mesh(path)
    assert colour.has(mesh) and mesh.is_watertight
    # GLB files are Y-up, so the file's +Z ends up along -Y once opened.
    axis, sign = (1, -1) if suffix == ".glb" else (2, 1)
    along = sign * mesh.vertices[:, axis]
    assert is_red(colour.of(mesh)[np.argmax(along)]) and is_blue(colour.of(mesh)[np.argmin(along)])


def test_plain_files_have_no_colour(tmp_path):
    path = tmp_path / "ball.stl"
    trimesh.creation.icosphere().export(path)
    assert not colour.has(load_mesh(path))


def test_colours_survive_repairs():
    ball = two_tone_ball(holes=True)
    for name, params in [("cleanup", {}), ("make_solid", {}), ("reduce_detail", {"triangles": 1000}),
                         ("scale", {"factor": 2}), ("cut_in_two", {"axis": "z", "position_mm": 1})]:
        mesh = ball if name != "cut_in_two" else run_action(ball, "cleanup")[0]
        result, _ = run_action(mesh, name, params)
        assert colour.has(result), name
        top_share = np.mean([is_red(c) for c in colour.of(result)[result.vertices[:, 2] > result.bounds[1][2] - 3]])
        if name != "cut_in_two":  # the halves are laid out on their cut faces
            assert top_share > 0.9, name


def test_new_material_is_neutral():
    ball = run_action(two_tone_ball(holes=True), "cleanup")[0]
    bust, _ = run_action(ball, "make_bust", {"height_mm": 40, "base": "round"})
    base = colour.of(bust)[bust.vertices[:, 2] < 0.5]
    assert np.all(base[:, :3] == colour.NEUTRAL[:3])


def test_viewer_gets_colours(tmp_path):
    client = TestClient(app, base_url="http://127.0.0.1")
    path = tmp_path / "ball.ply"
    two_tone_ball().export(path)
    state = client.post("/api/open", files={"file": ("ball.ply", path.read_bytes())}).json()
    assert state["has_colour"] is True
    data = client.get(f"/api/doc/{state['doc_id']}/colours").content
    shown = client.get(f"/api/doc/{state['doc_id']}/mesh").headers["X-Shown-Triangles"]
    assert len(data) == int(shown) * 9
    exported = client.get(f"/api/doc/{state['doc_id']}/export?format=ply")
    assert exported.status_code == 200 and b"red" in exported.content[:500]
