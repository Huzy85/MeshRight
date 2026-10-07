"""The MCP tools: the same actions as the buttons, driven by an AI app."""

import pytest
import trimesh

from meshright import mcp_server


def _broken_ball(path):
    ball = trimesh.creation.icosphere(3, radius=10)
    ball = trimesh.Trimesh(ball.vertices, ball.faces[10:], process=False)  # a hole
    ball.export(path)
    return path


def test_check_model_reports_problems(tmp_path):
    report = mcp_server.check_model(str(_broken_ball(tmp_path / "ball.stl")))
    assert report["score"] < 100 and "highlights" not in report
    assert any(issue["code"] == "holes" for issue in report["issues"])


def test_check_model_errors_are_messages_not_exceptions(tmp_path):
    assert "No such file" in mcp_server.check_model(str(tmp_path / "missing.stl"))["error"]
    (tmp_path / "notes.txt").write_text("hi")
    assert "Unsupported" in mcp_server.check_model(str(tmp_path / "notes.txt"))["error"]


def test_list_actions_matches_the_registry():
    names = {a["name"] for a in mcp_server.list_actions()}
    assert {"cleanup", "scale", "cut_in_two"} <= names


def test_apply_actions_writes_next_to_the_original(tmp_path):
    source = _broken_ball(tmp_path / "ball.stl")
    result = mcp_server.apply_actions(
        str(source), [{"name": "cleanup"}, {"name": "scale", "params": {"factor": 2}}])
    assert "error" not in result, result
    assert result["output"] == str(tmp_path / "ball-meshright.3mf")
    assert [s["action"] for s in result["steps"]] == ["cleanup", "scale"]
    fixed = trimesh.load(result["output"], force="mesh")
    assert fixed.is_watertight and 39 < fixed.extents.max() < 41
    assert source.read_bytes() == _broken_ball(tmp_path / "again.stl").read_bytes()  # original untouched


def test_apply_actions_refuses_to_overwrite_the_original(tmp_path):
    source = _broken_ball(tmp_path / "ball.stl")
    result = mcp_server.apply_actions(str(source), [], out=str(source), format="stl")
    assert "must not replace" in result["error"]


def test_apply_actions_bad_action_is_a_message(tmp_path):
    source = _broken_ball(tmp_path / "ball.stl")
    assert "Unknown action" in mcp_server.apply_actions(str(source), [{"name": "teleport"}])["error"]
    assert "name" in mcp_server.apply_actions(str(source), ["cleanup"])["error"]


def test_fix_model_runs_the_cleanup(tmp_path):
    source = _broken_ball(tmp_path / "ball.stl")
    result = mcp_server.fix_model(str(source), out=str(tmp_path / "out"), format="stl")
    assert "error" not in result, result
    assert result["output"] == str(tmp_path / "out" / "ball-meshright.stl")
    assert result["ready_to_print"] and result["score_after"] > result["score_before"]
    assert trimesh.load(result["output"], force="mesh").is_watertight


def test_export_colours_writes_a_painted_3mf(tmp_path):
    from .test_colour import two_tone_ball

    path = tmp_path / "ball.ply"
    two_tone_ball().export(path)
    result = mcp_server.export_colours(str(path), colours=3)
    assert "error" not in result, result
    assert result["output"].endswith("ball-meshright-colours.3mf") and (tmp_path / "ball-meshright-colours.3mf").exists()
    assert len(result["colours"]) == 2 and all("colour" in c and "share" in c for c in result["colours"])
    plain = tmp_path / "box.stl"
    trimesh.creation.box().export(plain)
    assert "no colours" in mcp_server.export_colours(str(plain))["error"]
    assert "between 2 and 8" in mcp_server.export_colours(str(path), colours=9)["error"]


def test_printer_has_the_clearance(monkeypatch, tmp_path):
    monkeypatch.setenv("MESHRIGHT_CONFIG_DIR", str(tmp_path))
    info = mcp_server.printer()
    assert info["chosen"] is False and info["clearance_mm"] == 0.3


def test_server_lists_every_tool():
    pytest.importorskip("mcp")
    import asyncio

    server = mcp_server.build_server()
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == {t.__name__ for t in mcp_server.TOOLS}
    check = next(t for t in tools if t.name == "check_model")
    assert "score" in check.description and "path" in check.input_schema["properties"]


def test_cli_prints_the_config(capsys):
    from meshright.cli import main

    assert main(["mcp", "--show-config"]) == 0
    assert '"meshright"' in capsys.readouterr().out
