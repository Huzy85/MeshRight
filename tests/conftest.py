"""Test meshes are generated in code, so no real user scans live in the repo."""

import numpy as np
import pytest
import trimesh


@pytest.fixture(autouse=True)
def private_settings(tmp_path, monkeypatch):
    """Tests never read or write the real user's settings."""
    monkeypatch.setenv("MESHRIGHT_CONFIG_DIR", str(tmp_path / "config"))


def save(mesh: trimesh.Trimesh, tmp_path, name: str = "model.stl"):
    path = tmp_path / name
    mesh.export(path)
    return path


@pytest.fixture
def cube():
    return trimesh.creation.box(extents=(20, 20, 20))


@pytest.fixture
def cube_with_hole(cube):
    return trimesh.Trimesh(cube.vertices, cube.faces[2:], process=False)


@pytest.fixture
def inside_out_cube(cube):
    return trimesh.Trimesh(cube.vertices, cube.faces[:, ::-1], process=False)


@pytest.fixture
def cube_with_debris(cube):
    speck = trimesh.creation.icosphere(subdivisions=0, radius=0.5)
    speck.apply_translation((40, 0, 0))
    return trimesh.util.concatenate([trimesh.creation.icosphere(subdivisions=4, radius=10), speck])


@pytest.fixture
def non_manifold():
    # Three triangles hinged on one shared edge (0-1).
    vertices = np.array([[0, 0, 0], [10, 0, 0], [5, 8, 0], [5, -8, 0], [5, 0, 8]], dtype=float)
    faces = np.array([[0, 1, 2], [1, 0, 3], [0, 1, 4]])
    return trimesh.Trimesh(vertices, faces, process=False)
