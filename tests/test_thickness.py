import pytest
import trimesh
from fastapi.testclient import TestClient

from meshright.server import app
from meshright.thickness import measure

from .conftest import save

client = TestClient(app, base_url="http://127.0.0.1")


def test_thin_plate_is_found():
    plate = trimesh.creation.box(extents=(40, 40, 0.5))
    result = measure(plate, 0.8)
    assert result["thinnest_mm"] == pytest.approx(0.5, abs=0.02)
    assert result["thin_share"] > 0.8 and result["thin_spots"]
    assert result["scale_to_fix"] == pytest.approx(1.6, abs=0.1)


def test_solid_block_is_fine():
    result = measure(trimesh.creation.box(extents=(20, 20, 20)), 0.8)
    assert result["thin_share"] == 0 and result["thinnest_mm"] > 5


def test_only_the_thin_fin_is_flagged():
    body = trimesh.creation.box(extents=(30, 30, 30))
    fin = trimesh.creation.box(extents=(0.4, 20, 20))
    fin.apply_translation((25, 0, 0))
    shape = trimesh.boolean.union([body, fin.union(trimesh.creation.box(extents=(12, 2, 2)).apply_translation((20, 0, 0)))], engine="manifold")
    result = measure(shape, 0.8)
    assert 0 < result["thin_share"] < 0.5
    assert all(p[0] > 15 for p in result["thin_spots"])


def test_needs_a_closed_model(cube_with_hole):
    with pytest.raises(ValueError, match="closed"):
        measure(cube_with_hole, 0.8)


def test_wall_thickness_api(tmp_path):
    with open(save(trimesh.creation.box(extents=(40, 40, 0.5)), tmp_path), "rb") as fh:
        doc_id = client.post("/api/open", files={"file": ("plate.stl", fh)}).json()["doc_id"]
    data = client.get(f"/api/doc/{doc_id}/wall-thickness").json()
    assert data["limit_mm"] == 0.8 and data["thin_share"] > 0.8
