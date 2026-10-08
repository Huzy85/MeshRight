"""The AI helper, tested against a fake AI service on this computer."""

import json
import os
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import trimesh
from fastapi.testclient import TestClient

from meshright import assistant, cli
from meshright.analysis import analyze


class FakeAI(BaseHTTPRequestHandler):
    answer = "{}"
    status = 200
    seen: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeAI.seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        if FakeAI.status != 200:
            self.send_response(FakeAI.status)
            self.end_headers()
            self.wfile.write(b'{"error": {"message": "nope"}}')
            return
        if self.path.endswith("/messages"):
            data = {"content": [{"type": "text", "text": FakeAI.answer}]}
        else:
            data = {"choices": [{"message": {"content": FakeAI.answer}}]}
        out = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_ai(tmp_path, monkeypatch):
    monkeypatch.setenv("MESHRIGHT_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("MESHRIGHT_AI_KEY", raising=False)
    server = HTTPServer(("127.0.0.1", 0), FakeAI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    FakeAI.seen, FakeAI.status = [], 200
    FakeAI.answer = json.dumps({
        "reply": "It is 1 mm too tall for nothing; make it 50 mm.",
        "steps": [
            {"action": "resize", "params": {"axis": "z", "size_mm": 50}, "why": "Fits the bed."},
            {"action": "delete_everything", "params": {}},
            {"action": "move", "params": {"x": 1e12}},
            {"action": "erase_area", "params": {}},
        ],
    })
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


def report():
    return analyze(trimesh.creation.box((20, 20, 20))).to_dict()


def test_only_real_and_safe_steps_come_back(fake_ai):
    config = assistant.Config(provider="custom", base_url=fake_ai, model="m")
    answer = assistant.ask("make it 50 mm tall", report(), [], None, config)
    assert [s["action"] for s in answer["steps"]] == ["resize"]
    assert answer["steps"][0]["params"] == {"axis": "z", "size_mm": 50}
    assert len(answer["dropped"]) == 3  # made-up action, huge move, needs clicks on the model
    sent = FakeAI.seen[0]["body"]
    text = json.dumps(sent)
    assert "highlights" not in text and len(text) < 60000  # never the model or its fault positions
    assert "erase_area" not in text  # clicking tools are not offered


def test_key_is_sent_but_never_shown_back(fake_ai, monkeypatch):
    assistant.update({"provider": "custom", "base_url": fake_ai, "model": "m", "api_key": "secret-key-1234"})
    path = assistant._path()
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assistant.ask("hi", report(), [], None)
    assert FakeAI.seen[-1]["headers"]["authorization"] == "Bearer secret-key-1234"

    from meshright.server import app
    client = TestClient(app, base_url="http://127.0.0.1")
    shown = client.get("/api/assistant").json()
    assert "secret-key-1234" not in json.dumps(shown) and shown["config"]["has_key"]
    # Saving again without a key keeps the old one.
    client.post("/api/assistant", json={"provider": "custom", "base_url": fake_ai, "model": "other"})
    assert assistant.load().api_key == "secret-key-1234"
    # A new address never inherits the saved key.
    client.post("/api/assistant", json={"provider": "custom", "base_url": "http://127.0.0.1:9/v1", "model": "other"})
    assert assistant.load().api_key == ""
    client.delete("/api/assistant")
    assert assistant.load() is None


def test_claude_format(fake_ai):
    config = assistant.Config(provider="anthropic", base_url=fake_ai, model="claude", api_key="k-123456789")
    answer = assistant.ask("help", report(), [], None, config)
    call = FakeAI.seen[-1]
    assert call["path"].endswith("/messages") and call["headers"]["x-api-key"] == "k-123456789"
    assert call["body"]["system"] and all(m["role"] != "system" for m in call["body"]["messages"])
    assert answer["steps"][0]["action"] == "resize"


def test_messy_answers_are_understood(fake_ai):
    config = assistant.Config(provider="custom", base_url=fake_ai, model="m")
    FakeAI.answer = '<think>hmm</think>Sure!\n```json\n{"reply": "Looks fine.", "steps": []}\n```'
    assert assistant.ask("ok?", report(), [], None, config)["reply"] == "Looks fine."
    FakeAI.answer = "Just plain words, no JSON."
    answer = assistant.ask("ok?", report(), [], None, config)
    assert answer["reply"] == "Just plain words, no JSON." and answer["steps"] == []


def test_errors_are_plain(fake_ai):
    config = assistant.Config(provider="custom", base_url=fake_ai, model="m")
    FakeAI.status = 401
    with pytest.raises(assistant.AssistantError, match="refused the key"):
        assistant.ask("hi", report(), [], None, config)
    with pytest.raises(assistant.AssistantError, match="Could not reach"):
        assistant.ask("hi", report(), [], None, assistant.Config(provider="custom", base_url="http://127.0.0.1:9/v1", model="m"))
    with pytest.raises(assistant.AssistantError, match="needs an API key"):
        assistant.ask("hi", report(), [], None, assistant.Config(provider="openai", base_url=fake_ai, model="m"))
    with pytest.raises(assistant.AssistantError, match="http"):
        assistant.Config(provider="custom", base_url="file:///etc/passwd", model="m").validate()


def test_ask_through_the_app(fake_ai, tmp_path):
    from meshright.server import app

    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.post("/api/assistant", json={"provider": "custom", "base_url": fake_ai, "model": "m"}).status_code == 200
    path = tmp_path / "box.stl"
    trimesh.creation.box((20, 20, 20)).export(path)
    with open(path, "rb") as handle:
        doc = client.post("/api/open", files={"file": ("box.stl", handle)}).json()["doc_id"]
    answer = client.post(f"/api/doc/{doc}/ask", json={"question": "make it 50 mm"}).json()
    assert answer["steps"][0]["action"] == "resize"
    state = client.post(f"/api/doc/{doc}/action", json=answer["steps"][0]).json()
    assert abs(state["stats"]["size_mm"][2] - 50) < 0.01
    assert client.post(f"/api/doc/{doc}/ask", json={"question": ""}).status_code == 400


def test_cli_ask(fake_ai, tmp_path, capsys):
    path = tmp_path / "box.stl"
    trimesh.creation.box((20, 20, 20)).export(path)
    assert cli.main(["ask", str(path), "make it 50 mm", "--provider", "custom", "--base-url", fake_ai, "--model", "m"]) == 0
    out = capsys.readouterr().out
    assert "1. Set size (axis: z, size mm: 50)" in out
    assert cli.main(["ask", str(path), "hi"]) == 2  # nothing saved, nothing passed
