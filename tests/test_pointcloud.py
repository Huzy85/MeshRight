import numpy as np
import pytest
import trimesh

from meshright.analysis import MeshLoadError, analyze, load_mesh
from meshright.pointcloud import read_text_points, remove_strays


def capsule_points(tmp_path, name="scan.xyz", strays=200):
    rng = np.random.default_rng(1)
    capsule = trimesh.creation.capsule(height=40, radius=15)
    points, _ = trimesh.sample.sample_surface(capsule, 40000, seed=1)
    points += rng.normal(0, 0.05, points.shape)
    points = np.vstack([points, rng.uniform(-60, 60, (strays, 3))])
    path = tmp_path / name
    if name.endswith(".ply"):
        trimesh.PointCloud(points).export(path)
    else:
        np.savetxt(path, points, fmt="%.4f")
    return path, capsule


def test_strays_are_removed():
    rng = np.random.default_rng(0)
    cloud = np.vstack([rng.normal(0, 1, (5000, 3)), rng.uniform(40, 60, (20, 3))])
    kept, removed = remove_strays(cloud)
    assert removed >= 20 and len(kept) >= 4900


def test_text_points_skip_headers_and_extra_columns(tmp_path):
    path = tmp_path / "scan.pts"
    path.write_text("3\n0 0 0 255 0 0\n1,2,3,10,10,10\nnot a point\n4 5 6\n")
    assert read_text_points(path).tolist() == [[0, 0, 0], [1, 2, 3], [4, 5, 6]]


@pytest.mark.parametrize("name", ["scan.xyz", "scan.ply"])
def test_point_cloud_becomes_a_closed_solid(tmp_path, name):
    path, capsule = capsule_points(tmp_path, name)
    mesh = load_mesh(path)
    assert "stray points" in mesh.metadata["notice"]
    report = analyze(mesh)
    assert report.printable and report.stats["watertight"]
    assert np.allclose(report.stats["size_mm"], capsule.extents, atol=0.5)
    assert report.stats["volume_mm3"] == pytest.approx(capsule.volume, rel=0.12)


def test_one_sided_scan_becomes_a_printable_shell(tmp_path):
    sphere = trimesh.creation.icosphere(4, radius=20)
    points, _ = trimesh.sample.sample_surface(sphere, 20000, seed=2)
    path = tmp_path / "front.xyz"
    np.savetxt(path, points[points[:, 1] < 0], fmt="%.4f")
    report = analyze(load_mesh(path))
    assert report.stats["watertight"] and report.printable


def test_too_few_points(tmp_path):
    path = tmp_path / "tiny.xyz"
    path.write_text("0 0 0\n1 1 1\n")
    with pytest.raises(MeshLoadError):
        load_mesh(path)
