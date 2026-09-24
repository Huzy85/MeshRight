import subprocess

import pytest
import trimesh
from fastapi.testclient import TestClient

from meshright import slicers
from meshright.server import app

client = TestClient(app, base_url="http://127.0.0.1")


def by_id(name):
    return next(s for s in slicers.KNOWN if s.id == name)


def test_found_on_windows(tmp_path):
    for version in ("5.6.0", "5.7.1"):
        exe = tmp_path / f"UltiMaker Cura {version}" / "UltiMaker-Cura.exe"
        exe.parent.mkdir()
        exe.write_text("")
    env = {"ProgramFiles": str(tmp_path)}
    command = slicers.locate(by_id("cura"), platform="win32", env=env)
    assert command == [str(tmp_path / "UltiMaker Cura 5.7.1" / "UltiMaker-Cura.exe")]
    assert slicers.locate(by_id("bambu"), platform="win32", env=env) is None


def test_found_on_mac(tmp_path):
    (tmp_path / "Applications" / "OrcaSlicer.app").mkdir(parents=True)
    command = slicers.locate(by_id("orca"), platform="darwin", env={}, home=tmp_path)
    assert command == ["open", "-a", str(tmp_path / "Applications" / "OrcaSlicer.app")]


def test_found_on_linux(tmp_path):
    exe = tmp_path / "bin" / "prusa-slicer"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    env = {"PATH": str(exe.parent)}
    assert slicers.locate(by_id("prusa"), platform="linux", env=env, home=tmp_path) == [str(exe)]
    (tmp_path / ".local/share/flatpak/app/com.bambulab.BambuStudio").mkdir(parents=True)
    assert slicers.locate(by_id("bambu"), platform="linux", env=env, home=tmp_path) == [
        "flatpak", "run", "com.bambulab.BambuStudio"]
    assert [s.id for s in slicers.installed(platform="linux", env=env, home=tmp_path)] == ["bambu", "prusa"]


def test_send_opens_the_file(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(slicers, "locate", lambda slicer, **kw: ["/opt/fake-slicer"] if slicer.id == "orca" else None)
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: started.append(args))
    path = slicers.send("orca", "cube-meshright.3mf", b"3mf data")
    assert path.read_bytes() == b"3mf data"
    assert started == [["/opt/fake-slicer", str(path)]]
    with pytest.raises(ValueError, match="not installed"):
        slicers.send("bambu", "x.3mf", b"")
    with pytest.raises(ValueError, match="not installed"):
        slicers.send("rm -rf", "x.3mf", b"")


def test_outbox_keeps_only_recent_files(monkeypatch):
    monkeypatch.setattr(slicers, "locate", lambda slicer, **kw: ["/opt/fake-slicer"])
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: None)
    for i in range(slicers.KEEP_FILES + 5):
        slicers.send("orca", f"model{i}.3mf", b"x")
    assert len(list(slicers.outbox().glob("*.3mf"))) == slicers.KEEP_FILES


def test_send_endpoint(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(slicers, "locate", lambda slicer, **kw: ["/opt/fake-slicer"] if slicer.id == "bambu" else None)
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: started.append(args))
    assert client.get("/api/slicers").json() == [{"id": "bambu", "name": "Bambu Studio"}]
    path = tmp_path / "cube.stl"
    trimesh.creation.box(extents=(10, 10, 10)).export(path)
    doc = client.post("/api/open", files={"file": ("cube.stl", path.read_bytes())}).json()["doc_id"]
    res = client.post(f"/api/doc/{doc}/send-to-slicer", json={"slicer": "bambu"})
    assert res.json() == {"message": "Opened in Bambu Studio"}
    assert started[0][-1].endswith("cube-meshright.3mf")
    assert client.post(f"/api/doc/{doc}/send-to-slicer", json={"slicer": "orca"}).status_code == 400
    assert client.post(f"/api/doc/{doc}/send-to-slicer", json={}).status_code == 400
