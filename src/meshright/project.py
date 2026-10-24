"""Project files (.meshright): save your work and carry on later.

A project is a zip holding the original model, the current version and the
list of steps taken. Reopening it restores the current version; "Back to the
original file" still works, and the earlier steps are listed for reference.
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np
import trimesh

from . import __version__
from .document import Document, Step
from .formats import ReadError, read_zip_member

EXTENSION = ".meshright"
FORMAT_VERSION = 1

# How many autosaved projects are kept; the oldest go first.
AUTOSAVE_KEEP = 10


class ProjectError(ValueError):
    """The project file could not be read."""


def _mesh_bytes(mesh: trimesh.Trimesh) -> bytes:
    return mesh.export(file_type="ply")  # binary PLY: compact and exact


def _read_mesh(data: bytes) -> trimesh.Trimesh:
    loaded = trimesh.load(io.BytesIO(data), file_type="ply", process=False)
    return trimesh.Trimesh(np.asarray(loaded.vertices), np.asarray(loaded.faces), process=False)


def save(doc: Document) -> bytes:
    buffer = io.BytesIO()
    write(doc, buffer)
    return buffer.getvalue()


def write(doc: Document, target) -> None:
    """Write the project zip to an open binary file or buffer. The document
    is read under its lock, then the meshes are exported outside it (meshes
    are never changed in place), so a long export does not hold up steps."""
    with doc.lock:
        applied = list(doc.steps[: doc.position])
        original, current, dropped = doc.original, doc.mesh, doc.dropped
    info = {
        "format": FORMAT_VERSION,
        "app": f"MeshRight {__version__}",
        "name": doc.name,
        "steps": [{"action": s.action, "params": _plain(s.params), "receipt": s.receipt} for s in applied],
        "earlier_steps": dropped,
        "scanner": original.metadata.get("scanner"),
    }
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.json", json.dumps(info, indent=2))
        zf.writestr("original.ply", _mesh_bytes(original))
        if applied:
            zf.writestr("current.ply", _mesh_bytes(current))


# ---------------------------------------------------------------- autosave
#
# After every step the open model is saved as a project in the settings
# folder, so a crash or a closed laptop lid loses at most the last step.
# Autosaves are ordinary .meshright files: the Recover list in the app
# opens them, or open the file by hand.


def autosave_dir() -> Path:
    from .settings import config_dir

    return config_dir() / "autosave"


def autosave(doc: Document, key: str) -> Path:
    """Save the document to the autosave folder. The zip is written to a
    temporary file first and renamed into place, so a crash mid-write never
    leaves a broken autosave behind."""
    folder = autosave_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w\-]", "_", Path(doc.name).stem)[:60] or "model"
    path = folder / f"{stem}-{key}{EXTENSION}"
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        with open(tmp, "wb") as handle:
            write(doc, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    for old in _autosave_files(folder)[AUTOSAVE_KEEP:]:
        old.unlink(missing_ok=True)
    return path


def _autosave_files(folder: Path) -> list[Path]:
    """Autosaves, newest first."""
    files = [p for p in folder.glob(f"*{EXTENSION}") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def autosaves() -> list[dict]:
    """What can be recovered: file name, when it was saved and its size."""
    folder = autosave_dir()
    if not folder.is_dir():
        return []
    found = []
    for path in _autosave_files(folder):
        stat = path.stat()
        found.append({
            "file": path.name,
            "saved": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "size_mb": round(stat.st_size / 1e6, 1),
        })
    return found


def autosave_path(file: str) -> Path | None:
    """The autosave with this file name, or None if there is no such file.
    Only a bare file name is accepted, so nothing outside the folder can be
    reached."""
    if file != Path(file).name or not file.endswith(EXTENSION) or file.startswith("."):
        return None
    path = autosave_dir() / file
    return path if path.is_file() else None


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
            info = json.loads(read_zip_member(zf, "project.json", 10 * 1024 * 1024))
            if not isinstance(info, dict) or not isinstance(info.get("format", 1), int):
                raise ProjectError("This project file is damaged.")
            if info.get("format", 0) > FORMAT_VERSION:
                raise ProjectError("This project was saved by a newer MeshRight. Please update MeshRight to open it.")
            original = _read_mesh(read_zip_member(zf, "original.ply"))
            current = _read_mesh(read_zip_member(zf, "current.ply")) if "current.ply" in zf.namelist() else None
    except ProjectError:
        raise
    except Exception as exc:
        if isinstance(exc, ReadError):
            raise ProjectError(str(exc)) from exc
        raise ProjectError("This is not a MeshRight project, or it is damaged.") from exc
    if len(original.faces) == 0:
        raise ProjectError("This project file is damaged: the model is empty.")

    doc = Document(name=str(info.get("name", "model")), original=original)
    scanner = info.get("scanner")
    if isinstance(scanner, dict) and isinstance(scanner.get("name"), str):
        original.metadata["scanner"] = {"name": scanner["name"][:80], "advice": str(scanner.get("advice", ""))[:1000]}
        if current is not None:
            current.metadata["scanner"] = original.metadata["scanner"]
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
