"""Tests for the hardening pass before 0.3.0: memory caps, autosave, atomic
settings, safe XML, the batch size cap, the CLI overwrite guard and the mold
receipt saying what was left out."""

import io
import os

import pytest
import trimesh
from fastapi.testclient import TestClient

from meshright import document, formats, mold, project, settings
from meshright.actions import run_action
from meshright.cli import main
from meshright.document import Document
from meshright.server import app

from .conftest import save

client = TestClient(app, base_url="http://127.0.0.1")


def box(size=20):
    return trimesh.creation.box((size, size, size))


def _open(name="box.stl", mesh=None):
    data = io.BytesIO()
    (mesh or box()).export(data, file_type="stl")
    return client.post("/api/open", files={"file": (name, data.getvalue())}).json()["doc_id"]


# ------------------------------------------------------------- undo memory cap


def test_undo_history_is_capped_by_memory_not_only_by_count(monkeypatch):
    doc = Document("box", box())
    one_step = document.mesh_bytes(box())
    monkeypatch.setattr(document, "MAX_UNDO_BYTES", one_step * 3)
    for _ in range(10):
        doc.apply("move", {"x": 1})
    # Three meshes' worth fit; the rest were forgotten, the newest kept.
    assert len(doc.steps) == 3 and doc.dropped == 7
    assert doc.undo_bytes() <= one_step * 3
    while doc.can_undo:
        doc.undo()
    assert doc.mesh.bounds[0][0] == pytest.approx(box().bounds[0][0] + 7)


def test_the_newest_step_is_always_kept_however_big(monkeypatch):
    monkeypatch.setattr(document, "MAX_UNDO_BYTES", 1)
    doc = Document("box", box())
    doc.apply("move", {"x": 1})
    doc.apply("move", {"x": 1})
    assert len(doc.steps) == 1 and doc.can_undo
    doc.undo()
    assert doc.mesh.bounds[0][0] == pytest.approx(box().bounds[0][0] + 1)


def test_shared_meshes_count_once():
    doc = Document("box", box())
    doc.apply("move", {"x": 1})
    doc.revert()  # the revert step holds the original, which is not counted
    assert doc.undo_bytes() == document.mesh_bytes(doc.steps[0].mesh)


# ------------------------------------------------------------------- autosave


def test_autosave_writes_a_project_that_reopens(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHRIGHT_AUTOSAVE", "sync")
    doc = _open("scan.stl")
    client.post(f"/api/doc/{doc}/action", json={"action": "move", "params": {"x": 5}})
    files = client.get("/api/recover").json()["files"]
    assert len(files) == 1 and files[0]["file"].startswith("scan-") and files[0]["size_mb"] >= 0
    folder = project.autosave_dir()
    assert not list(folder.glob(".*.tmp"))
    restored = project.load(folder / files[0]["file"])
    assert restored.mesh.bounds[0][0] == pytest.approx(box().bounds[0][0] + 5)
    # Undo is autosaved too: the file now holds the state before the move.
    assert client.post(f"/api/doc/{doc}/undo").status_code == 200
    restored = project.load(folder / files[0]["file"])
    assert restored.mesh.bounds[0][0] == pytest.approx(box().bounds[0][0])


def test_autosave_can_be_recovered_as_a_new_model(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHRIGHT_AUTOSAVE", "sync")
    doc = _open("scan.stl")
    client.post(f"/api/doc/{doc}/action", json={"action": "move", "params": {"x": 5}})
    name = client.get("/api/recover").json()["files"][0]["file"]
    res = client.post(f"/api/recover/{name}")
    assert res.status_code == 200 and res.json()["doc_id"] != doc
    assert client.post("/api/recover/nothing.meshright").status_code == 404
    assert client.post("/api/recover/..%2Fsettings.json").status_code in (404, 405, 422)


def test_autosave_keeps_only_the_newest_files(monkeypatch):
    monkeypatch.setattr(project, "AUTOSAVE_KEEP", 2)
    for key in "abc":
        project.autosave(Document("box", box()), key)
    assert len(project.autosaves()) == 2
    assert project.autosave_path("box-a.meshright") is None


def test_autosave_failure_does_not_fail_the_step(monkeypatch):
    monkeypatch.setenv("MESHRIGHT_AUTOSAVE", "sync")

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(project, "autosave", broken)
    doc = _open()
    assert client.post(f"/api/doc/{doc}/action", json={"action": "move", "params": {"x": 1}}).status_code == 200


def test_autosave_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("MESHRIGHT_AUTOSAVE", "0")
    doc = _open()
    client.post(f"/api/doc/{doc}/action", json={"action": "move", "params": {"x": 1}})
    assert client.get("/api/recover").json()["files"] == []


# ------------------------------------------------------------ atomic settings


def test_settings_are_saved_atomically():
    printer = settings.Printer("Bambu Lab A1 mini", 180, 180, 180)
    settings.save(printer)
    folder = settings.config_dir()
    assert not list(folder.glob(".*.tmp"))
    assert settings.load() == (printer, True)


def test_write_atomic_leaves_the_old_file_when_the_write_fails(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_bytes(b"old")

    def broken(fd):
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", broken)
    with pytest.raises(OSError):
        settings.write_atomic(path, b"new")
    assert path.read_bytes() == b"old" and not list(tmp_path.glob(".*.tmp"))


# ---------------------------------------------------------------- safe XML


def test_amf_with_an_entity_bomb_is_refused(tmp_path):
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE amf [<!ENTITY a "aaaaaaaaaa">'
        '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        '<amf unit="millimeter"><object id="1"><mesh><vertices>'
        "<vertex><coordinates><x>&b;</x><y>0</y><z>0</z></coordinates></vertex>"
        "</vertices></mesh></object></amf>"
    )
    path = tmp_path / "bomb.amf"
    path.write_text(bomb)
    with pytest.raises(Exception) as err:
        formats.read_amf(path)
    assert not isinstance(err.value, MemoryError)
    assert type(err.value).__name__ in ("EntitiesForbidden", "ReadError", "ParseError")


# ------------------------------------------------------------- batch size cap


def test_a_batch_that_grows_too_large_skips_the_rest(cube, monkeypatch):
    from meshright import server

    monkeypatch.setattr(server, "MAX_BATCH_MB", 0)
    data = io.BytesIO()
    cube.export(data, file_type="stl")
    files = [("files", (f"part{i}.stl", data.getvalue())) for i in range(3)]
    res = client.post("/api/batch", files=files, data={"format": "stl"})
    assert res.status_code == 200
    results = res.json()["results"]
    assert results[0]["ok"]
    assert all("Skipped: the batch already holds" in (r["error"] or "") for r in results[1:])
    # The zip still has the one fixed file and the report.
    import zipfile

    with zipfile.ZipFile(io.BytesIO(client.get(f"/api/batch/{res.json()['batch_id']}").content)) as zf:
        assert "meshright-report.txt" in zf.namelist() and len(zf.namelist()) == 2


# ------------------------------------------------------------ CLI overwrite


def test_cli_does_not_replace_fixed_files_unless_asked(cube_with_hole, tmp_path, capsys):
    folder = tmp_path / "scans"
    folder.mkdir()
    save(cube_with_hole, folder, "one.stl")
    assert main(["fix", str(folder), "--format", "stl"]) == 0
    fixed = folder / "meshright-fixed" / "one-meshright.stl"
    fixed.write_bytes(b"hand edited")
    capsys.readouterr()
    assert main(["fix", str(folder), "--format", "stl"]) == 0
    assert fixed.read_bytes() == b"hand edited"
    assert "Not replaced" in capsys.readouterr().err
    assert main(["fix", str(folder), "--format", "stl", "--overwrite"]) == 0
    assert fixed.read_bytes() != b"hand edited"


# ------------------------------------------------------- mold notes surfaced


POUR_NOTE = "The pour hole could not be cut; drill one through the top half into the cavity before casting."


def test_mold_receipt_says_what_was_left_out(monkeypatch):
    def no_spout(top, *args, notes=None, **kwargs):
        mold._note(notes, POUR_NOTE)
        return top

    monkeypatch.setattr(mold, "_add_pour_spout", no_spout)
    mesh = trimesh.creation.icosphere(subdivisions=2, radius=15)
    _, note = run_action(mesh, "make_mold", {"include_clamps": False})
    assert "Left out: " + POUR_NOTE in note
    assert "alignment pins, pour hole and air vents" not in note


def test_mold_receipt_is_clean_when_every_feature_was_cut():
    mesh = trimesh.creation.icosphere(subdivisions=2, radius=15)
    _, note = run_action(mesh, "make_mold", {"include_clamps": False})
    assert "alignment pins, pour hole and air vents" in note and "Left out" not in note


def test_alignment_pins_really_are_added():
    """The pin union used manifold3d's | operator, which does not exist, so
    every mold shipped without pins and said nothing. Pins add material to
    the top half, so its volume must grow when they go in."""
    import numpy as np

    mesh = trimesh.creation.icosphere(subdivisions=2, radius=15)
    a = mold.analyse(mesh)
    notes: list[str] = []
    with_pins = mold.build(mesh, a, add_clamps=False, notes=notes)
    assert notes == []
    real = mold._add_alignment_pins
    mold._add_alignment_pins = lambda pos, neg, *args, **kwargs: (pos, neg)
    try:
        without_pins = mold.build(mesh, a, add_clamps=False)
    finally:
        mold._add_alignment_pins = real
    grew = with_pins["top"].volume - without_pins["top"].volume
    assert 130 < grew < 180  # two 4 mm pins, 6 mm tall: about 150 mm3, none drilled away
    shrank = without_pins["bottom"].volume - with_pins["bottom"].volume
    assert 180 < shrank < 240  # two matching 4.3 mm holes, 7 mm deep: about 200 mm3
    assert with_pins["top"].is_watertight and with_pins["bottom"].is_watertight


def test_each_mold_feature_reports_when_its_boolean_fails(monkeypatch):
    import numpy as np

    def broken(mesh):
        raise RuntimeError("boolean failed")

    monkeypatch.setattr(mold, "_to_manifold", broken)
    half = trimesh.creation.box((40, 40, 40))  # top face at z=20 is the parting face
    axis, offset = np.array([0.0, 0.0, 1.0]), 20.0
    notes: list[str] = []
    pos, neg = mold._add_alignment_pins(half, half.copy(), axis, offset, notes=notes)
    top = mold._add_pour_spout(half, axis, offset, notes=notes)
    vents = mold._add_vent_channels(half, axis, offset, notes=notes)
    bolts = mold._add_bolt_holes(half, axis, offset, notes=notes, which="top")
    clamp = mold.make_clamp(half, notes=notes)
    # Every feature was left out, nothing crashed, the halves came back as they were.
    assert pos is half and top is half and vents is half and bolts is half
    assert len(clamp.faces) == 12  # the plain outer block
    assert [n.split(";")[0] for n in notes] == [
        "Alignment pins could not be added",
        "The pour hole could not be cut",
        "Air vents could not be cut",
        "Bolt holes could not be cut in the top half",
        "The clamp's slot could not be cut, so the clamp is a solid block",
    ]
