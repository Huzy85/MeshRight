import trimesh
from fastapi.testclient import TestClient

from meshright.actions import run_action
from meshright.orientation import advise, evaluate
from meshright.server import app

from .conftest import save

client = TestClient(app, base_url="http://127.0.0.1")


def upside_down_t():
    """A T standing on its stem: the bar hangs out and needs support."""
    stem = trimesh.creation.box(extents=(10, 10, 40))
    stem.apply_translation((0, 0, 20))
    bar = trimesh.creation.box(extents=(50, 10, 8))
    bar.apply_translation((0, 0, 44))
    return trimesh.boolean.union([stem, bar], engine="manifold")


def test_overhang_is_measured():
    t = upside_down_t()
    now = evaluate(t, [0, 0, -1])
    assert now["overhang_mm2"] == 400  # the bar's underside, less where the stem joins and now["support_cm3"] > 10
    flipped = evaluate(t, [0, 0, 1])  # standing on the bar: nothing hangs out
    assert flipped["support_cm3"] == 0


def test_advice_finds_a_placement_without_supports():
    advice = advise(upside_down_t())
    fewest = advice["options"][0]
    assert fewest["title"] in ("Fewest supports", "Best all round") or "Fewest supports" in fewest["title"]
    assert fewest["support_cm3"] == 0
    assert advice["current"]["support_cm3"] > 10
    mesh, _ = run_action(upside_down_t(), "set_bottom", dict(zip(("down_x", "down_y", "down_z"), fewest["down"])))
    assert evaluate(mesh, [0, 0, -1])["support_cm3"] == 0


def test_strongest_lays_a_long_part_flat():
    rod = trimesh.creation.box(extents=(8, 8, 80))  # standing on end
    strongest = next(o for o in advise(rod)["options"] if "Strongest" in o["title"] or "strongest" in o["why"])
    assert strongest["flatness"] > 0.99 and strongest["height_mm"] == 8


def test_orientation_api(tmp_path):
    with open(save(upside_down_t(), tmp_path), "rb") as fh:
        doc_id = client.post("/api/open", files={"file": ("t.stl", fh)}).json()["doc_id"]
    data = client.get(f"/api/doc/{doc_id}/orientations").json()
    assert data["options"] and "current" in data
    assert all({"title", "why", "down", "support_cm3", "height_mm"} <= set(o) for o in data["options"])
