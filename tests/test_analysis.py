import numpy as np
import pytest
import trimesh

from meshright.analysis import MeshLoadError, analyze, analyze_file, load_mesh

from .conftest import save


def codes(report):
    return {issue.code for issue in report.issues}


def test_clean_cube_is_ready(cube):
    cube.apply_translation((0, 0, 10))  # sitting on the bed
    report = analyze(cube)
    assert report.score == 100
    assert report.printable
    assert report.verdict == "Ready to print"
    assert report.issues == []
    assert report.stats["watertight"]
    assert report.stats["volume_mm3"] == pytest.approx(8000)
    assert report.stats["size_mm"] == [20, 20, 20]


def test_hole_is_found_and_highlighted(cube_with_hole):
    report = analyze(cube_with_hole)
    assert "holes" in codes(report)
    hole = next(i for i in report.issues if i.code == "holes")
    assert hole.count == 1
    assert not report.printable
    assert report.score <= 59
    assert report.stats["volume_mm3"] is None
    # A missing square is 4 open edges, each sent as 2 points of 3 coordinates.
    assert len(report.highlights["open_edges"]) == 4 * 6


def test_inside_out(inside_out_cube):
    report = analyze(inside_out_cube)
    assert "inside_out" in codes(report)
    assert not report.printable


def test_floating_debris(cube_with_debris):
    report = analyze(cube_with_debris)
    assert "floating_debris" in codes(report)
    assert report.stats["pieces"] == 2
    assert report.printable  # debris is a warning, not a blocker


def test_non_manifold(non_manifold):
    report = analyze(non_manifold)
    assert "non_manifold" in codes(report)
    assert len(report.highlights["non_manifold_edges"]) == 6


def test_duplicate_and_degenerate(cube):
    faces = np.vstack([cube.faces, cube.faces[:1]])
    vertices = np.vstack([cube.vertices, [[1, 1, 1]]])
    faces = np.vstack([faces, [[len(vertices) - 1] * 3]])
    report = analyze(trimesh.Trimesh(vertices, faces, process=False))
    assert {"duplicate_faces", "degenerate_faces"} <= codes(report)


def test_errors_sorted_first(cube_with_debris, cube_with_hole):
    combined = trimesh.util.concatenate([cube_with_hole, cube_with_debris])
    severities = [i.severity for i in analyze(combined).issues]
    assert severities == sorted(severities, key=["error", "warning", "info"].index)


def test_unit_warning():
    report = analyze(trimesh.creation.box(extents=(0.02, 0.02, 0.02)))
    assert "tiny_model" in codes(report)
    assert report.score == 100  # info only, no penalty


@pytest.mark.parametrize(
    "name", ["model.stl", "model.obj", "model.ply", "model.3mf", "model.glb", "model.off"]
)
def test_formats_round_trip(cube, tmp_path, name):
    report = analyze_file(save(cube, tmp_path, name))
    assert report.score == 100
    assert report.stats["triangles"] == 12


def test_stl_triangle_soup_is_merged(cube, tmp_path):
    # STL stores every triangle separately; vertices must be merged or
    # every edge would look open.
    mesh = load_mesh(save(cube, tmp_path))
    assert len(mesh.vertices) == 8


def test_unsupported_type(tmp_path):
    path = tmp_path / "model.txt"
    path.write_text("hello")
    with pytest.raises(MeshLoadError):
        load_mesh(path)


def test_garbage_file(tmp_path):
    path = tmp_path / "model.stl"
    path.write_bytes(b"not a mesh at all")
    with pytest.raises(MeshLoadError):
        load_mesh(path)


def test_off_bed_offers_put_on_bed(cube):
    report = analyze(cube)  # centred on the origin, so half below the bed
    issue = next(i for i in report.issues if i.code == "off_bed")
    assert "below the bed (10 mm)" in issue.title
    assert issue.fixes[0]["action"] == "place_on_bed"
    assert report.score == 100  # information only

    cube.apply_translation((0, 0, 25))
    issue = next(i for i in analyze(cube).issues if i.code == "off_bed")
    assert issue.title == "Floating 15 mm above the bed"
