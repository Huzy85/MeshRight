import numpy as np
import pytest
import trimesh

from meshright import quality


@pytest.mark.parametrize("shape", [
    trimesh.creation.box(extents=(20, 20, 20)),
    trimesh.creation.box(extents=(20, 20, 20)).subdivide().subdivide(),
    trimesh.creation.icosphere(subdivisions=1),
    trimesh.creation.cylinder(radius=5, height=20, sections=64),
    trimesh.creation.torus(major_radius=20, minor_radius=5),
    trimesh.boolean.union([trimesh.creation.box(extents=(20, 20, 20)), trimesh.creation.box(extents=(8, 8, 30))], engine="manifold"),
])
def test_clean_shapes_are_not_rough(shape):
    levels, share = quality.face_levels(shape)
    assert share == 0 and levels.max() < 0.1 * 255


def test_noisy_area_is_found():
    ball = trimesh.creation.icosphere(subdivisions=4, radius=30)
    rng = np.random.default_rng(0)
    top = ball.vertices[:, 2] > 15
    ball.vertices[top] += ball.vertex_normals[top] * rng.normal(0, 0.4, (top.sum(), 1))
    levels, share = quality.face_levels(ball)
    centres = ball.triangles_center[:, 2]
    assert levels[centres > 18].mean() > 5 * max(levels[centres < 10].mean(), 1)
    assert 0.1 < share < 0.3


def test_empty():
    levels, share = quality.face_levels(trimesh.Trimesh())
    assert len(levels) == 0 and share == 0


def test_changes_show_where_the_surface_moved():
    ball = trimesh.creation.icosphere(subdivisions=4, radius=20)
    dented = ball.copy()
    top = dented.vertices[:, 2] > 17
    dented.vertices[top] -= [0, 0, 1.5]
    levels, largest, moved = quality.change_levels(ball, dented)
    centres = dented.triangles_center[:, 2]
    assert levels[centres > 17].mean() > 100 and levels[centres < 0].max() < 20
    assert 1.0 < largest < 1.6 and 0.01 < moved < 0.2
    same, largest, moved = quality.change_levels(ball, ball.copy())
    assert same.max() < 20 and moved == 0
