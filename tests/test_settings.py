import pytest
import trimesh
from fastapi.testclient import TestClient

from meshright import settings
from meshright.actions import run_action
from meshright.analysis import analyze
from meshright.server import app

client = TestClient(app, base_url="http://127.0.0.1")
MINI = settings.Printer("Bambu Lab A1 mini", 180, 180, 180)


def test_nothing_chosen_at_first():
    printer, chosen = settings.load()
    assert not chosen and printer.name == "Not set"


def test_save_and_load():
    settings.save(MINI)
    printer, chosen = settings.load()
    assert chosen and printer == MINI


def test_every_preset_is_valid():
    for preset in settings.PRESETS:
        preset.validate()


@pytest.mark.parametrize(
    "change",
    [{"bed_x": 0}, {"bed_z": 99999}, {"nozzle_mm": 5}, {"technology": "laser"}, {"material": "Wood"}],
)
def test_bad_settings_are_refused(change):
    with pytest.raises(ValueError):
        settings.Printer.from_dict({**MINI.to_dict(), **change})


def test_too_big_offers_shrink_to_fit():
    big = trimesh.creation.box(extents=(250, 100, 100))
    report = analyze(big, printer=MINI)
    issue = next(i for i in report.issues if i.code == "too_big")
    assert not report.printable
    fix = issue.fixes[0]
    mesh, _ = run_action(big, fix["action"], fix["params"])
    assert not any(i.code == "too_big" for i in analyze(mesh, printer=MINI).issues)


def test_turning_on_the_bed_counts_as_fitting():
    # 170 deep, 100 wide: fits a 180 x 180 bed either way round.
    assert not any(i.code == "too_big" for i in analyze(trimesh.creation.box(extents=(100, 170, 50)), printer=MINI).issues)


def test_suggests_laying_it_another_way():
    mk4 = settings.Printer("Prusa MK4", 250, 210, 220)
    tall = trimesh.creation.box(extents=(50, 50, 240))  # fits lying down
    issue = next(i for i in analyze(tall, printer=mk4).issues if i.code == "too_big")
    assert "Which way is up" in issue.detail


def test_weight_uses_the_material():
    box = trimesh.creation.box(extents=(10, 10, 10))  # 1 cm³
    petg = settings.Printer(**{**MINI.to_dict(), "material": "PETG"})
    stats = analyze(box, printer=petg).stats
    assert stats["filament_g"] == 1.3 and stats["material"] == "PETG"


def test_settings_api():
    data = client.get("/api/settings").json()
    assert data["chosen"] is False and len(data["presets"]) >= 10
    res = client.post("/api/settings", json={"printer": MINI.to_dict()})
    assert res.status_code == 200 and res.json()["chosen"] is True
    assert client.post("/api/settings", json={"printer": {**MINI.to_dict(), "bed_x": -1}}).status_code == 400
