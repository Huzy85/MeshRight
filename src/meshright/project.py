"""Project files (.meshright): save your work and carry on later.

A project is a zip holding the original model, the current version and the
list of steps taken. Reopening it restores the current version; "Back to the
original file" still works, and the earlier steps are listed for reference.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import numpy as np
import trimesh

from . import __version__
from .document import Document, Step

EXTENSION = ".meshright"
FORMAT_VERSION = 1


class ProjectError(ValueError):
    """The project file could not be read."""


def _mesh_bytes(mesh: trimesh.Trimesh) -> bytes:
    return mesh.export(file_type="ply")  # binary PLY: compact and exact


def _read_mesh(data: bytes) -> trimesh.Trimesh:
    loaded = trimesh.load(io.BytesIO(data), file_type="ply", process=False)
    return trimesh.Trimesh(np.asarray(loaded.vertices), np.asarray(loaded.faces), process=False)


def save(doc: Document) -> bytes:
    applied = doc.steps[: doc.position]
    info = {
        "format": FORMAT_VERSION,
        "app": f"MeshRight {__version__}",
        "name": doc.name,
        "steps": [{"action": s.action, "params": _plain(s.params), "receipt": s.receipt} for s in applied],
        "earlier_steps": doc.dropped,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.json", json.dumps(info, indent=2))
        zf.writestr("original.ply", _mesh_bytes(doc.original))
        if applied:
            zf.writestr("current.ply", _mesh_bytes(doc.mesh))
    return buffer.getvalue()


def _plain(value):
    """Step parameters as plain JSON (numpy arrays become lists)."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


def load(path: str | Path) -> Document:
    try:
        with zipfile.ZipFile(path) as zf:
            info = json.loads(zf.read("project.json"))
            if not isinstance(info, dict) or not isinstance(info.get("format", 1), int):
                raise ProjectError("This project file is damaged.")
            if info.get("format", 0) > FORMAT_VERSION:
                raise ProjectError("This project was saved by a newer MeshRight. Please update MeshRight to open it.")
            original = _read_mesh(zf.read("original.ply"))
            current = _read_mesh(zf.read("current.ply")) if "current.ply" in zf.namelist() else None
    except ProjectError:
        raise
    except Exception as exc:
        raise ProjectError(f"This is not a MeshRight project, or it is damaged ({exc}).") from exc
    if len(original.faces) == 0:
        raise ProjectError("This project file is damaged: the model is empty.")

    doc = Document(name=str(info.get("name", "model")), original=original)
    steps = [s for s in info.get("steps", []) if isinstance(s, dict)] if isinstance(info.get("steps"), list) else []
    if current is not None and len(current.faces) and steps:
        earlier = "; ".join(str(s.get("receipt", "")) for s in steps)
        doc.steps.append(Step(
            "restore",
            {"earlier_steps": steps},
            f"Reopened the saved project ({len(steps)} earlier step{'s' if len(steps) != 1 else ''}: {earlier})",
            current,
        ))
        doc.position = 1
    return doc
