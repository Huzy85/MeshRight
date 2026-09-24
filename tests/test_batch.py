import io
import zipfile

import trimesh
from fastapi.testclient import TestClient

from meshright import settings
from meshright.batch import Options, process_file
from meshright.cli import main
from meshright.server import app

from .conftest import save

client = TestClient(app, base_url="http://127.0.0.1")


def test_process_file_fixes_and_reports(cube_with_hole, tmp_path):
    result, data = process_file(save(cube_with_hole, tmp_path), "box.stl", Options())
    assert result.ok and result.ready
    assert result.score_before < result.score_after
    assert result.output_name == "box-meshright.3mf"
    assert any("filled 1 hole" in s for s in result.steps)
    mesh = trimesh.load(io.BytesIO(data), file_type="3mf", force="mesh")
    assert mesh.is_watertight and abs(mesh.bounds[0][2]) < 1e-6


def test_shrinks_to_fit_the_printer(tmp_path):
    big = trimesh.creation.box(extents=(300, 100, 100))
    printer = settings.Printer("Mini", 180, 180, 180)
    result, _ = process_file(save(big, tmp_path), "big.stl", Options(), printer)
    assert result.ready and any("Scaled" in s for s in result.steps)


def test_unreadable_file_is_reported(tmp_path):
    path = tmp_path / "junk.stl"
    path.write_bytes(b"junk")
    result, data = process_file(path, "junk.stl", Options())
    assert not result.ok and data is None and result.error


def test_batch_api_returns_a_zip(cube_with_hole, cube, tmp_path):
    a = save(cube_with_hole, tmp_path, "a.stl")
    b = save(cube, tmp_path, "b.obj")
    with open(a, "rb") as fa, open(b, "rb") as fb:
        res = client.post(
            "/api/batch",
            files=[("files", ("a.stl", fa)), ("files", ("b.obj", fb)), ("files", ("notes.txt", b"hi"))],
            data={"options": '{"format": "stl"}'},
        )
    assert res.status_code == 200
    data = res.json()
    assert [r["ok"] for r in data["results"]] == [True, True, False]
    archive = client.get(f"/api/batch/{data['batch_id']}")
    names = zipfile.ZipFile(io.BytesIO(archive.content)).namelist()
    assert sorted(names) == ["a-meshright.stl", "b-meshright.stl", "meshright-report.txt"]


def test_batch_api_refuses_bad_options(cube, tmp_path):
    with open(save(cube, tmp_path), "rb") as fh:
        res = client.post("/api/batch", files=[("files", ("a.stl", fh))], data={"options": '{"format": "exe"}'})
    assert res.status_code == 400


def test_command_line_fixes_a_folder(cube_with_hole, cube, tmp_path, capsys):
    folder = tmp_path / "scans"
    folder.mkdir()
    save(cube_with_hole, folder, "one.stl")
    save(cube, folder, "two.stl")
    code = main(["fix", str(folder), "--format", "stl"])
    out = folder / "meshright-fixed"
    assert code == 0
    assert sorted(p.name for p in out.iterdir()) == ["meshright-report.txt", "one-meshright.stl", "two-meshright.stl"]
    assert "2 of 2 files ready to print" in capsys.readouterr().out


def test_batch_preset_is_checked():
    import pytest

    from meshright.batch import Options

    assert Options.from_dict({"preset": "Quick"}).preset == "quick"
    with pytest.raises(ValueError, match="preset"):
        Options.from_dict({"preset": "turbo"})
