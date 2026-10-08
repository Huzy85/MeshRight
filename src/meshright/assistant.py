"""The assistant: plain-English help from any AI model the user connects.

The user picks a service (OpenAI, Claude, Gemini, OpenRouter, DeepSeek,
Mistral, Groq, a local Ollama or LM Studio, or any other service that speaks
the OpenAI chat format) and pastes a key if it needs one. MeshRight then
sends the AI the model's check report, the list of changes MeshRight can
make, and the user's question. The model file itself is never sent.

The AI answers in plain words and may suggest steps. Every suggested step is
checked against MeshRight's own actions before the user sees it, and nothing
changes until the user presses Apply. Each applied step can be undone.

Uses only the Python standard library to talk to the AI, so no extra
packages are needed.
"""

from __future__ import annotations

import json
import os
import re
import stat
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, fields
from urllib.parse import urlsplit

from . import settings
from .actions import REGISTRY, ActionError, _clean_params

# Services the user can pick from. "openai" means the OpenAI chat format,
# which nearly every service and local runner also speaks.
PROVIDERS = {
    "openai": {"label": "OpenAI", "style": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini", "key": True},
    "anthropic": {"label": "Claude (Anthropic)", "style": "anthropic", "base_url": "https://api.anthropic.com/v1", "model": "claude-haiku-5-5", "key": True},
    "gemini": {"label": "Google Gemini", "style": "openai", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.5-flash", "key": True},
    "openrouter": {"label": "OpenRouter (hundreds of models)", "style": "openai", "base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4.1-mini", "key": True},
    "deepseek": {"label": "DeepSeek", "style": "openai", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "key": True},
    "mistral": {"label": "Mistral", "style": "openai", "base_url": "https://api.mistral.ai/v1", "model": "mistral-small-latest", "key": True},
    "groq": {"label": "Groq", "style": "openai", "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile", "key": True},
    "ollama": {"label": "Ollama (on this computer)", "style": "openai", "base_url": "http://localhost:11434/v1", "model": "qwen3:8b", "key": False},
    "lmstudio": {"label": "LM Studio (on this computer)", "style": "openai", "base_url": "http://localhost:1234/v1", "model": "local-model", "key": False},
    "custom": {"label": "Other (OpenAI-compatible)", "style": "openai", "base_url": "http://localhost:8080/v1", "model": "", "key": False},
}

# Changes the AI may suggest. Steps that need the user to click on the model
# (erase an area, sculpt) or to pick another file (merge, combine) are left
# out: the AI cannot see the model, so it cannot choose those spots.
_INTERACTIVE_KINDS = {"points", "numbers"}
_NEEDS_A_FILE = {"merge_scan", "combine_models"}

MAX_STEPS = 8
MAX_QUESTION_CHARS = 2000
TIMEOUT_S = 120


class AssistantError(ValueError):
    """Something went wrong talking to the AI. The message is shown to the user."""


@dataclass
class Config:
    provider: str = "openai"
    base_url: str = ""
    model: str = ""
    api_key: str = ""

    def style(self) -> str:
        return PROVIDERS.get(self.provider, PROVIDERS["custom"])["style"]

    def validate(self) -> None:
        if self.provider not in PROVIDERS:
            raise AssistantError(f"Choose one of: {', '.join(PROVIDERS)}.")
        self.base_url = (self.base_url or PROVIDERS[self.provider]["base_url"]).strip().rstrip("/")
        parts = urlsplit(self.base_url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise AssistantError("The address must start with http:// or https://.")
        self.model = (self.model or PROVIDERS[self.provider]["model"]).strip()[:200]
        if not self.model:
            raise AssistantError("Type the name of the model to use.")
        self.api_key = str(self.api_key or "").strip()[:500]

    def public(self) -> dict:
        """Safe to show in the page: the key is never sent back, only whether one is saved."""
        data = asdict(self)
        key = data.pop("api_key")
        data["has_key"] = bool(key or os.environ.get("MESHRIGHT_AI_KEY"))
        data["key_hint"] = f"…{key[-4:]}" if len(key) >= 8 else ""
        return data


def _path():
    return settings.config_dir() / "assistant.json"


def load() -> Config | None:
    """The saved AI connection, or None if the user has not set one up."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        known = {f.name for f in fields(Config)}
        config = Config(**{k: v for k, v in data.items() if k in known})
        config.validate()
        return config
    except (OSError, ValueError, TypeError):
        return None


def save(config: Config) -> None:
    """Save the connection. The file is readable by this user only."""
    config.validate()
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(asdict(config), handle, indent=2)
    os.replace(tmp, path)


def forget() -> None:
    try:
        _path().unlink()
    except FileNotFoundError:
        pass


def update(payload: dict) -> Config:
    """Save new settings from the page. An empty key keeps the saved one."""
    if not isinstance(payload, dict):
        raise AssistantError("Send the AI settings as named fields.")
    old = load()
    config = Config(
        provider=str(payload.get("provider") or "openai"),
        base_url=str(payload.get("base_url") or ""),
        model=str(payload.get("model") or ""),
        api_key=str(payload.get("api_key") or ""),
    )
    config.validate()
    # The saved key is kept only for the same service at the same address, so
    # a changed address can never be used to send the key somewhere else.
    if not config.api_key and old is not None and (old.provider, old.base_url) == (config.provider, config.base_url):
        config.api_key = old.api_key
    save(config)
    return config


# ---------------------------------------------------------------- what the AI is told

def allowed_actions() -> list[dict]:
    out = []
    for act in REGISTRY.values():
        if act.name in _NEEDS_A_FILE or any(p.kind in _INTERACTIVE_KINDS for p in act.params):
            continue
        out.append(act.describe())
    return out


SYSTEM_PROMPT = """You are the helper inside MeshRight, a free app that checks and fixes 3D models and 3D scans for 3D printing.
The person using it is often a beginner. Use plain British English, short sentences, no jargon. If you must use a term
such as "slicer" or "triangle", explain it in a few words. Never invent problems: only talk about what the report shows.
You cannot see the model, only the report below.

You can suggest changes, but only from the ACTIONS list, with parameters exactly as described there. Distances are in
millimetres. Suggest the fewest steps that solve the problem, in order. If no change is needed, suggest none. If the
person asks for something MeshRight cannot do, say so and suggest the nearest thing it can do.

Reply with JSON only, no other text, in this shape:
{"reply": "your answer in plain words", "steps": [{"action": "name", "params": {...}, "why": "one short reason"}]}"""


def build_messages(question: str, report: dict, history: list[str], printer: dict | None) -> list[dict]:
    context = {
        "report": report,
        "steps_already_done": history[-20:],
        "printer": printer or "not set up yet",
        "ACTIONS": allowed_actions(),
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "MODEL AND APP STATE (JSON):\n" + json.dumps(context, default=str)
            + "\n\nQUESTION:\n" + question},
    ]


def trim_report(report: dict) -> dict:
    """The parts of the report the AI needs: no highlight coordinates, no
    file paths; issue text and fixes kept."""
    keep = {k: report[k] for k in ("score", "verdict", "printable", "stats") if k in report}
    keep["issues"] = [
        {k: i.get(k) for k in ("code", "severity", "title", "detail", "count", "fixes")}
        for i in report.get("issues", [])[:30]
    ]
    return keep


# ---------------------------------------------------------------- talking to the AI

def _post(url: str, headers: dict, body: dict) -> dict:
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "MeshRight", **headers})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return json.loads(response.read(5 * 1024 * 1024).decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            data = json.loads(exc.read(20000).decode("utf-8", "replace"))
            err = data.get("error", data)
            detail = err.get("message", "") if isinstance(err, dict) else str(err)
        except (ValueError, AttributeError, OSError):
            detail = ""  # the error body is optional
        if exc.code in (401, 403):
            raise AssistantError("The AI service refused the key. Check it in AI settings.") from None
        if exc.code == 404:
            raise AssistantError("The AI service does not know that model or address. Check both in AI settings.") from None
        if exc.code == 429:
            raise AssistantError("The AI service says you are sending too much, or your credit has run out. Try again later.") from None
        raise AssistantError(f"The AI service returned an error ({exc.code}). {detail[:300]}".strip()) from None
    except urllib.error.URLError as exc:
        raise AssistantError(
            f"Could not reach the AI service at {urlsplit(url).netloc}. Check the address, your internet, "
            "or that the local AI program is running."
        ) from exc
    except TimeoutError as exc:
        raise AssistantError("The AI service took too long to answer. Try again, or pick a faster model.") from exc
    except ValueError as exc:
        raise AssistantError("The AI service sent back something that is not JSON.") from exc


def complete(config: Config, messages: list[dict]) -> str:
    """Send the messages to the chosen AI and return its text answer."""
    key = config.api_key or os.environ.get("MESHRIGHT_AI_KEY", "")
    if config.style() == "anthropic":
        if not key:
            raise AssistantError("Claude needs an API key. Add it in AI settings.")
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        rest = [m for m in messages if m["role"] != "system"]
        data = _post(f"{config.base_url}/messages",
                     {"x-api-key": key, "anthropic-version": "2023-06-01"},
                     {"model": config.model, "max_tokens": 2000, "system": system, "messages": rest})
        parts = data.get("content") or []
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    if PROVIDERS.get(config.provider, {}).get("key") and not key:
        raise AssistantError(f"{PROVIDERS[config.provider]['label']} needs an API key. Add it in AI settings.")
    data = _post(f"{config.base_url}/chat/completions", headers,
                 {"model": config.model, "messages": messages, "temperature": 0.2, "max_tokens": 2000})
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise AssistantError("The AI service answered in a way MeshRight does not understand.") from None


def _parse(text: str) -> dict:
    """The JSON object in the AI's answer. Models sometimes wrap it in a
    code block, add thinking first, or answer in plain text."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    candidate = fenced.group(1) if fenced else text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""
    try:
        data = json.loads(candidate) if candidate else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return {"reply": text[:4000] or "The AI gave no answer.", "steps": []}
    return data


def check_steps(raw_steps) -> tuple[list[dict], list[str]]:
    """Keep only steps MeshRight can really do, with valid settings. Returns
    the good steps and a note for each one dropped."""
    allowed = {a["name"] for a in allowed_actions()}
    good, dropped = [], []
    for step in (raw_steps if isinstance(raw_steps, list) else [])[:MAX_STEPS]:
        if not isinstance(step, dict):
            continue
        name = str(step.get("action", ""))
        params = step.get("params") if isinstance(step.get("params"), dict) else {}
        if name not in allowed:
            dropped.append(f"Left out a step MeshRight does not have: '{name[:40]}'.")
            continue
        act = REGISTRY[name]
        try:
            clean = _clean_params(act, params)
        except ActionError as exc:
            dropped.append(f"Left out '{act.title}': {exc}")
            continue
        good.append({
            "action": name,
            "title": act.title,
            "params": {k: v for k, v in clean.items() if k in params},
            "why": str(step.get("why", ""))[:300],
        })
    return good, dropped


def ask(question: str, report: dict, history: list[str], printer: dict | None, config: Config | None = None) -> dict:
    """Ask the AI about the open model. Returns its reply and checked steps."""
    config = config or load()
    if config is None:
        raise AssistantError("Connect an AI first: open AI settings and choose a service.")
    question = str(question or "").strip()
    if not question:
        raise AssistantError("Type a question first.")
    if len(question) > MAX_QUESTION_CHARS:
        raise AssistantError(f"Please keep the question under {MAX_QUESTION_CHARS} characters.")
    text = complete(config, build_messages(question, trim_report(report), history, printer))
    data = _parse(text)
    steps, dropped = check_steps(data.get("steps"))
    return {"reply": str(data.get("reply", ""))[:4000], "steps": steps, "dropped": dropped,
            "model": config.model, "provider": PROVIDERS[config.provider]["label"]}


def test(config: Config) -> str:
    """Check the connection with a tiny request."""
    text = complete(config, [{"role": "user", "content": "Reply with the single word: ready"}])
    return text.strip()[:200] or "(empty answer)"
