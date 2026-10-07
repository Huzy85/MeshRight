"""Print in colours: grouping scan colours and writing a painted 3MF."""

import io
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import pytest
import trimesh

from meshright import colour_export
from meshright.colour_export import CORE_NS, PRUSA_NS

from .test_colour import two_tone_ball
from .test_server import client, open_doc


def speckled_ball():
    """Red on top, blue below, with a sprinkling of single off-colour points."""
    ball = two_tone_ball()
    rgba = np.asarray(ball.visual.vertex_colors).copy()
    rng = np.random.default_rng(3)
    noisy = rng.choice(len(rgba), 40, replace=False)
    rgba[noisy] = [30, 200, 30, 255]
    ball.visual = trimesh.visual.ColorVisuals(ball, vertex_colors=rgba)
    return ball


def test_two_tone_ball_gives_two_groups():
    ball = two_tone_ball()
    face_group, palette = colour_export.group(ball, colours=2)
    assert len(palette) == 2 and set(np.unique(face_group)) == {0, 1}
    top = ball.triangles_center[:, 2] > 3
    bottom = ball.triangles_center[:, 2] < -3
    # Which group is red depends on the start; both halves must be uniform.
    assert len(np.unique(face_group[top])) == 1 and len(np.unique(face_group[bottom])) == 1
    assert face_group[top][0] != face_group[bottom][0]
    red = palette[face_group[top][0]]
    assert red[0] > 150 and red[2] < 100


def test_asking_for_more_colours_than_there_are_is_fine():
    face_group, palette = colour_export.group(two_tone_ball(), colours=6)
    assert len(palette) == 2
    assert face_group.max() == 1


def test_lone_speckles_join_their_neighbours():
    ball = speckled_ball()
    face_group, palette = colour_export.group(ball, colours=3)
    counts = np.bincount(face_group, minlength=len(palette))
    # Green points sat alone, so the smoothing folds them into the halves.
    assert counts.min() == 0 or counts.min() < 0.01 * len(face_group)
    assert np.argsort(-counts)[0] == 0  # biggest group first


def test_paint_codes_match_the_slicers():
    codes = {1: "4", 2: "8", 3: "0C", 4: "1C", 17: "EC", 18: "0FC", 33: "0FFC"}
    for filament, code in codes.items():
        assert colour_export.paint_code(filament) == code
    with pytest.raises(ValueError):
        colour_export.paint_code(0)


def test_colour_limits():
    with pytest.raises(ValueError, match="between 2 and 8"):
        colour_export.group(two_tone_ball(), colours=1)
    with pytest.raises(ValueError, match="no colours"):
        colour_export.export(trimesh.creation.box(), 4)


def test_3mf_is_painted_for_every_slicer():
    ball = two_tone_ball()
    data, summary = colour_export.export(ball, 4, "ball")
    assert len(summary) == 2 and abs(sum(c["share"] for c in summary) - 1) < 0.01
    assert all(c["colour"].startswith("#") and len(c["colour"]) == 7 for c in summary)

    # Any 3MF reader opens it as the same shape.
    again = trimesh.load(io.BytesIO(data), file_type="3mf", force="mesh")
    assert len(again.faces) == len(ball.faces) and again.is_watertight
    assert np.allclose(again.bounds, ball.bounds, atol=0.01)

    xml = zipfile.ZipFile(io.BytesIO(data)).read("3D/3dmodel.model").decode()
    root = ET.fromstring(xml)
    core = f"{{{CORE_NS}}}"
    materials = root.findall(f"{core}resources/{core}basematerials/{core}base")
    assert [m.get("displaycolor") for m in materials] == [c["colour"].upper() + "FF" for c in summary]
    obj = root.find(f"{core}resources/{core}object")
    assert obj.get("pid") == "1" and obj.get("pindex") == "0"
    triangles = root.findall(f"{core}resources/{core}object/{core}mesh/{core}triangles/{core}triangle")
    assert len(triangles) == len(ball.faces)
    codes = {t.get("paint_color") for t in triangles}
    assert codes == {"4", "8"}
    for t in triangles:
        assert t.get(f"{{{PRUSA_NS}}}mmu_segmentation") == t.get("paint_color")
        assert t.get("p1") == {"4": "0", "8": "1"}[t.get("paint_color")]
    assert "MeshRight" in xml


def test_server_exports_colours(tmp_path):
    path = tmp_path / "ball.ply"
    two_tone_ball().export(path)
    with open(path, "rb") as fh:
        res = client.post("/api/open", files={"file": ("ball.ply", fh)})
    assert res.status_code == 200 and res.json()["has_colour"]
    doc_id = res.json()["doc_id"]
    res = client.get(f"/api/doc/{doc_id}/export-colours", params={"colours": 3})
    assert res.status_code == 200
    assert 'filename="ball-meshright-colours.3mf"' in res.headers["content-disposition"]
    assert len(res.headers["x-colours"]) and "#" in res.headers["x-colours"]
    assert zipfile.ZipFile(io.BytesIO(res.content)).namelist() == ["[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"]
    assert client.get(f"/api/doc/{doc_id}/export-colours", params={"colours": 1}).status_code == 400


def test_server_refuses_a_plain_model(cube, tmp_path):
    doc_id = open_doc(cube, tmp_path)["doc_id"]
    res = client.get(f"/api/doc/{doc_id}/export-colours")
    assert res.status_code == 400 and "no colours" in res.json()["detail"]
