"""MeshRight as an MCP server, so AI apps (Claude Code, Claude Desktop, Cursor
and others) can check and fix models on this computer.

Start it with ``meshright mcp``. It talks over stdin/stdout, opens only the
files it is asked to open, writes fixed copies next to them (never over the
original) and sends nothing anywhere: the AI app sees the report and the
receipts, not the model.

The tools are the same actions the buttons use (see ``actions.py``), so an
assistant can only do what a person could do by clicking.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import batch, settings
from .actions import REGISTRY, ActionError, run_action
from .analysis import SUPPORTED_EXTENSIONS, MeshLoadError, analyze, load_mesh

MCP_HELP = (
    "The MCP server needs the optional 'mcp' package: install it with "
    "'pip install meshright[mcp]'."
)
EXPORT_FORMATS = ("3mf", "stl", "obj", "ply")


def _printer():
    printer, chosen = settings.load()
    return printer if chosen else None


def _source(path: str) -> Path:
    source = Path(path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"No such file: {source}")
    if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type. Supported: {', '.join(SUPPORTED_EXTENSIONS)}")
    return source


def _target(source: Path, out: str | None, fmt: str) -> Path:
    fmt = str(fmt).lower().lstrip(".")
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"The format must be one of: {', '.join(EXPORT_FORMATS)}.")
    target = Path(out).expanduser() if out else source.with_name(f"{source.stem}-meshright.{fmt}")
    if target.is_dir() or not target.suffix:  # a folder, existing or not
        target = target / f"{source.stem}-meshright.{fmt}"
    if target.suffix.lower().lstrip(".") != fmt:
        target = target.with_suffix(f".{fmt}")
    if target.resolve() == source.resolve():
        raise ValueError("The output must not replace the original file.")
    return target


def _report(mesh) -> dict:
    data = analyze(mesh, printer=_printer()).to_dict()
    data.pop("highlights", None)
    return data


def _write(mesh, target: Path) -> None:
    data = mesh.export(file_type=target.suffix.lstrip("."))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)


# The tools, as plain functions (so they are easy to test and to call from
# other code). ``build_server`` wraps them for MCP.


def check_model(path: str) -> dict:
    """Check a 3D model file for printing problems. Returns a print-readiness
    score (0 to 100), a verdict, size and triangle counts, and the list of
    problems found, each with the fixes MeshRight suggests."""
    try:
        return {"file": str(_source(path)), **_report(load_mesh(_source(path)))}
    except (MeshLoadError, OSError, ValueError) as exc:
        return {"file": path, "error": str(exc)}


def list_actions() -> list[dict]:
    """Every change MeshRight can make to a model, with the inputs each one
    takes. Use the names and parameters with apply_actions."""
    return [a.describe() for a in REGISTRY.values()]


def apply_actions(path: str, actions: list[dict], out: str | None = None, format: str = "3mf") -> dict:
    """Open a model, apply a list of actions in order (each {"name": ...,
    "params": {...}} as described by list_actions) and save the result as a
    new file next to the original (or at ``out``). Returns a receipt for each
    step, the report after, and where the file was written. The original file
    is never changed."""
    try:
        source = _source(path)
        target = _target(source, out, format)
        mesh = load_mesh(source)
        receipts = []
        for step in actions or []:
            if not isinstance(step, dict) or not step.get("name"):
                raise ActionError('Each action needs a "name" (and optional "params").')
            mesh, receipt = run_action(mesh, str(step["name"]), step.get("params") or {})
            receipts.append({"action": step["name"], "receipt": receipt})
        _write(mesh, target)
        return {"file": str(source), "output": str(target), "steps": receipts, "after": _report(mesh)}
    except (MeshLoadError, ActionError, OSError, ValueError) as exc:
        return {"file": path, "error": str(exc)}


def fix_model(path: str, out: str | None = None, preset: str = "balanced", solid_if_still_broken: bool = False,
              best_bottom: bool = False, put_on_bed: bool = True, format: str = "3mf") -> dict:
    """Run MeshRight's one-click cleanup on a model file (fill holes, fix
    normals, remove debris, reduce detail per the preset: quick, balanced,
    detail or flexible) and save a fixed copy next to the original. Returns
    the score before and after, the steps taken and any problems left."""
    try:
        source = _source(path)
        target = _target(source, out, format)
        options = batch.Options.from_dict({
            "preset": preset, "solid_if_still_broken": solid_if_still_broken, "best_bottom": best_bottom,
            "put_on_bed": put_on_bed, "format": target.suffix.lstrip("."),
        })
    except (OSError, ValueError) as exc:
        return {"file": path, "error": str(exc)}
    result, data = batch.process_file(source, source.name, options, _printer())
    if not result.ok or data is None:
        return {"file": str(source), "error": result.error or "Could not fix this file."}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {
        "file": str(source), "output": str(target), "score_before": result.score_before,
        "score_after": result.score_after, "ready_to_print": result.ready, "steps": result.steps,
        "problems_left": result.problems_left,
    }


def export_colours(path: str, out: str | None = None, colours: int = 4) -> dict:
    """Write a 3MF painted for a multi-colour printer from a colour scan
    (PLY, OBJ with texture, GLB...). The scan's colours are grouped into
    2 to 8 print colours and every triangle is painted, so Bambu Studio,
    OrcaSlicer and PrusaSlicer open it ready to print. Returns the output
    file and each print colour with the share of the surface it covers."""
    from . import colour_export

    try:
        source = _source(path)
        target = _target(source, out, "3mf")
        if not out:
            target = target.with_name(f"{source.stem}-meshright-colours.3mf")
        data, summary = colour_export.export(load_mesh(source), int(colours), source.stem)
    except (MeshLoadError, OSError, ValueError) as exc:
        return {"file": path, "error": str(exc)}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"file": str(source), "output": str(target), "colours": summary}


def printer() -> dict:
    """The printer MeshRight is set up for (bed size, nozzle, material, pin
    clearance), or the defaults if none has been chosen yet."""
    current, chosen = settings.load()
    return {"chosen": chosen, **current.to_dict()}


TOOLS = (check_model, list_actions, apply_actions, fix_model, export_colours, printer)


def build_server():
    try:
        from mcp.server import MCPServer
    except ImportError as exc:
        raise RuntimeError(MCP_HELP) from exc
    server = MCPServer(
        name="MeshRight",
        instructions=(
            "MeshRight checks and fixes 3D scans and models for 3D printing. Start with check_model on a "
            "file path; it lists problems and suggested fixes. Use fix_model for the one-click cleanup, "
            "or list_actions and apply_actions for specific changes (cut to fit the bed, scale, add a "
            "base, thicken, and so on). Files are only ever read from and written to the user's computer."
        ),
    )
    for tool in TOOLS:
        server.tool()(tool)
    return server


def run() -> int:
    """Serve over stdin/stdout until the AI app closes the connection."""
    server = build_server()
    server.run()
    return 0


def register_hint() -> str:
    """How to add this server to common AI apps."""
    return json.dumps({"mcpServers": {"meshright": {"command": "meshright", "args": ["mcp"]}}}, indent=2)
