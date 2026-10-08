"""Local web server: serves the browser app and the MeshRight API.

Runs on the user's own machine. Nothing is uploaded anywhere else.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import tempfile
import threading
import uuid
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import Body, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .actions import REGISTRY, ActionError, run_action
from .analysis import SUPPORTED_EXTENSIONS, MeshLoadError, analyze, load_mesh
from . import settings
from .document import Document

WEB_DIR = Path(__file__).parent / "web"
MAX_UPLOAD_MB = int(os.environ.get("MESHRIGHT_MAX_UPLOAD_MB", "1000"))

# Above this, the browser gets a lighter copy to display; the full mesh is
# kept for checking, fixing and export.
DISPLAY_MAX_TRIANGLES = int(os.environ.get("MESHRIGHT_DISPLAY_TRIANGLES", "400000"))

# Open models kept in memory. The oldest is closed when a new one is opened.
MAX_OPEN_DOCUMENTS = 4

EXPORT_FORMATS = {"stl": "model/stl", "3mf": "model/3mf", "obj": "model/obj", "ply": "application/octet-stream"}

# Host names the browser may use to reach this server. Anything else is
# refused, which stops "DNS rebinding" tricks by other websites.
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
EXTRA_HOSTS = {h.strip().lower() for h in os.environ.get("MESHRIGHT_ALLOWED_HOSTS", "").split(",") if h.strip()}

app = FastAPI(title="MeshRight", version=__version__)
log = logging.getLogger("meshright")
_documents: OrderedDict[str, Document] = OrderedDict()
_lock = threading.Lock()


# ---------------------------------------------------------------- security

def _host_name(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if ":" in host else host


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    """A plain message instead of a bare "Internal Server Error". The
    library's own text goes to the log, not the page."""
    log.exception("Unexpected error on %s", request.url.path, exc_info=exc)
    return JSONResponse(
        {"detail": "Something went wrong with that step. Your model is unchanged. "
                   "Please report this on GitHub, with the file if you can."},
        status_code=500,
    )


def _attachment(filename: str) -> str:
    """A Content-Disposition header that works for any file name: an ASCII
    copy for old browsers and the real name encoded for the rest."""
    ascii_name = filename.encode("ascii", "replace").decode("ascii").replace("?", "_").replace('"', "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _stem(name: str) -> str:
    return re.sub(r"[^\w\-. ]", "_", Path(name).stem).strip() or "model"


@app.middleware("http")
async def only_this_computer(request: Request, call_next):
    """Refuse requests that did not come from MeshRight's own page.

    Any website open in the same browser could otherwise send requests to
    this local server.
    """
    host = _host_name(request.headers.get("host", ""))
    if host not in LOCAL_HOSTS | EXTRA_HOSTS:
        return JSONResponse({"detail": "Unknown host name."}, status_code=403)

    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin is not None and urlsplit(origin).netloc.lower() != request.headers.get("host", "").lower():
            return JSONResponse({"detail": "Requests from other websites are not allowed."}, status_code=403)
        if request.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
            return JSONResponse({"detail": "Requests from other websites are not allowed."}, status_code=403)
        # Refuse oversized uploads before they are read (batches may hold many files).
        allowed = MAX_UPLOAD_MB * 1024 * 1024 * (20 if request.url.path == "/api/batch" else 1) + 1024 * 1024
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > allowed:
            return JSONResponse({"detail": f"File is larger than {MAX_UPLOAD_MB} MB."}, status_code=413)
    response = await call_next(request)
    # No other website may show MeshRight inside a frame (clickjacking).
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


# ---------------------------------------------------------------- documents

def _get(doc_id: str) -> Document:
    with _lock:
        doc = _documents.get(doc_id)
        if doc is not None:
            _documents.move_to_end(doc_id)  # the least recently used closes first
    if doc is None:
        raise HTTPException(status_code=404, detail="This model is no longer open. Please open the file again.")
    return doc


def _printer():
    """The user's printer, or None if they have not chosen one."""
    printer, chosen = settings.load()
    return printer if chosen else None


def _state(doc_id: str, doc: Document) -> dict:
    """Everything the viewer needs after opening a file or changing it.
    Runs in a worker thread; the mesh, its checks and the history are read
    together so they always match."""
    with doc.lock:
        mesh, crossing, history = doc.mesh, doc.crossing(), doc.history()
    from .colour import has as has_colour

    return {
        "doc_id": doc_id,
        "name": doc.name,
        "history": history,
        "has_colour": has_colour(mesh),
        **analyze(mesh, crossing=crossing, printer=_printer()).to_dict(),
    }


def _open(path: Path, name: str) -> dict:
    from . import project

    if name.lower().endswith(project.EXTENSION):
        doc = project.load(path)
        mesh = doc.mesh
    else:
        mesh = load_mesh(path)
        doc = Document(name=name, original=mesh)
    doc_id = uuid.uuid4().hex
    with _lock:
        _documents[doc_id] = doc
        while len(_documents) > MAX_OPEN_DOCUMENTS:
            old_id, _ = _documents.popitem(last=False)
            _display_cache.pop(old_id, None)
    state = _state(doc_id, doc)
    if mesh.metadata.get("notice"):
        state["notice"] = mesh.metadata["notice"]
    return state


async def _save_upload(file: UploadFile, suffix: str) -> Path:
    limit = MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        try:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > limit:
                    raise HTTPException(status_code=413, detail=f"File is larger than {MAX_UPLOAD_MB} MB.")
                tmp.write(chunk)
        except BaseException:
            tmp.close()
            tmp_path.unlink(missing_ok=True)
            raise
    return tmp_path


def run_with_lock(doc: Document, work):
    with doc.lock:
        return work(doc)


# ---------------------------------------------------------------- API

@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/api/updates")
async def updates() -> dict:
    """Whether a newer MeshRight is out (asks GitHub; nothing else is sent)."""
    from . import updates as checker

    try:
        return await run_in_threadpool(checker.check)
    except ValueError as exc:
        return {"error": str(exc)}  # offline is normal, not a server fault


@app.get("/api/samples")
def list_samples() -> list[dict]:
    from .samples import SAMPLES

    return [{"id": key, "name": name, "about": about} for key, (name, about) in SAMPLES.items()]


@app.get("/api/samples/{name}")
async def sample(name: str) -> Response:
    """A sample model to try, made on request."""
    from . import samples

    if name not in samples.SAMPLES:
        raise HTTPException(status_code=404, detail="There is no sample with that name.")
    filename, data = await run_in_threadpool(samples.make, name)
    return Response(content=data, media_type="model/stl", headers={"Content-Disposition": _attachment(filename)})


@app.get("/api/settings")
def get_settings() -> dict:
    printer, chosen = settings.load()
    return {
        "printer": printer.to_dict(),
        "chosen": chosen,
        "presets": [p.to_dict() for p in settings.PRESETS],
        "materials": list(settings.MATERIALS),
    }


@app.post("/api/settings")
def save_settings(payload: dict = Body(...)) -> dict:
    chosen = payload.get("printer")
    if not isinstance(chosen, dict):
        raise HTTPException(status_code=400, detail="Send the printer's details.")
    try:
        printer = settings.Printer.from_dict(chosen)
        settings.save(printer)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save the settings: {exc}") from exc
    return get_settings()


# ---------------------------------------------------------------- the AI helper

@app.get("/api/assistant")
def get_assistant() -> dict:
    """The AI connection, without the key: the page only learns whether one is saved."""
    from . import assistant

    config = assistant.load()
    return {
        "connected": config is not None,
        "config": config.public() if config else None,
        "providers": {k: {kk: v[kk] for kk in ("label", "base_url", "model", "key")} for k, v in assistant.PROVIDERS.items()},
    }


@app.post("/api/assistant")
def save_assistant(payload: dict = Body(...)) -> dict:
    from . import assistant

    try:
        assistant.update(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError:
        raise HTTPException(status_code=500, detail="Could not save the AI settings on this computer.") from None
    return get_assistant()


@app.delete("/api/assistant")
def forget_assistant() -> dict:
    from . import assistant

    assistant.forget()
    return get_assistant()


@app.post("/api/assistant/test")
async def test_assistant() -> dict:
    from . import assistant

    config = assistant.load()
    if config is None:
        raise HTTPException(status_code=400, detail="Save the AI settings first.")
    try:
        answer = await run_in_threadpool(assistant.test, config)
    except assistant.AssistantError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": f"Connected to {config.model}. It said: {answer}"}


@app.delete("/api/doc/{doc_id}")
def close_doc(doc_id: str) -> dict:
    """Close a model and free its memory."""
    with _lock:
        _documents.pop(doc_id, None)
        _display_cache.pop(doc_id, None)
    return {"closed": doc_id}


@app.get("/api/doc/{doc_id}/state")
async def doc_state(doc_id: str) -> dict:
    """Check the current model again, for example after the printer changed."""
    doc = _get(doc_id)
    return await run_in_threadpool(_state, doc_id, doc)


_batches: OrderedDict[str, bytes] = OrderedDict()


@app.post("/api/batch")
async def batch_fix(files: list[UploadFile], options: str = Form("{}")) -> dict:
    """Fix several files with the same settings. The fixed files and a report
    come back as one zip to download."""
    import json
    from dataclasses import asdict

    from . import batch

    try:
        opts = batch.Options.from_dict(json.loads(options))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not files:
        raise HTTPException(status_code=400, detail="Choose at least one file.")
    printer = _printer()

    outputs = []
    for file in files:
        name = Path(file.filename or "model").name
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            outputs.append((batch.Result(name=name, ok=False, error="Unsupported file type."), None))
            continue
        tmp_path = await _save_upload(file, suffix)
        try:
            outputs.append(await run_in_threadpool(batch.process_file, tmp_path, name, opts, printer))
        finally:
            tmp_path.unlink(missing_ok=True)

    archive = await run_in_threadpool(batch.zip_results, outputs)
    batch_id = uuid.uuid4().hex
    with _lock:
        _batches[batch_id] = archive
        while len(_batches) > 2:
            _batches.popitem(last=False)
    return {"batch_id": batch_id, "results": [asdict(r) for r, _ in outputs]}


@app.get("/api/batch/{batch_id}")
def batch_download(batch_id: str) -> Response:
    with _lock:
        archive = _batches.get(batch_id)
    if archive is None:
        raise HTTPException(status_code=404, detail="These files are no longer available. Please run the batch again.")
    return Response(
        content=archive,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="meshright-fixed.zip"'},
    )


@app.get("/api/actions")
def list_actions() -> list[dict]:
    """Every action MeshRight can perform, with its inputs."""
    return [a.describe() for a in REGISTRY.values()]


@app.post("/api/open")
async def open_file(file: UploadFile) -> dict:
    name = Path(file.filename or "model").name
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS + (".meshright",):
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type. Supported: {', '.join(SUPPORTED_EXTENSIONS)}, or a .meshright project",
        )
    tmp_path = await _save_upload(file, suffix)
    try:
        return await run_in_threadpool(_open, tmp_path, name)
    except (MeshLoadError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/api/doc/{doc_id}/action")
async def do_action(doc_id: str, payload: dict = Body(...)) -> dict:
    doc = _get(doc_id)
    name = payload.get("action")
    params = payload.get("params") or {}
    if not isinstance(name, str) or not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="Send an action name and its params.")

    def work():
        with doc.lock:
            doc.apply(name, params)
        return _state(doc_id, doc)

    try:
        state = await run_in_threadpool(work)
    except ActionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _get(doc_id)  # closed while it ran: say so instead of a state for nothing
    return state


@app.post("/api/doc/{doc_id}/ask")
async def ask_assistant(doc_id: str, payload: dict = Body(...)) -> dict:
    """Ask the connected AI about this model. Only the check report and the
    list of possible changes are sent, never the model itself. Suggested
    steps come back checked, and nothing is changed until the user applies them."""
    from . import assistant

    doc = _get(doc_id)
    question = payload.get("question") if isinstance(payload, dict) else None

    def work():
        with doc.lock:
            mesh, crossing = doc.mesh, doc.crossing()
            done = [s.receipt for s in doc.steps[: doc.position]]
        printer = _printer()
        report = analyze(mesh, crossing=crossing, printer=printer).to_dict()
        return assistant.ask(str(question or ""), report, done, printer.to_dict() if printer else None)

    try:
        return await run_in_threadpool(work)
    except assistant.AssistantError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/doc/{doc_id}/cleanup-plan")
async def cleanup_plan(doc_id: str, preset: str = "balanced") -> dict:
    """What Clean up would change, shown to the user before it runs."""
    from .repair import PRESETS, plan_cleanup

    doc = _get(doc_id)
    if preset not in PRESETS:
        raise HTTPException(status_code=400, detail=f"preset must be one of: {', '.join(PRESETS)}.")

    def work():
        with doc.lock:
            mesh, crossing = doc.mesh, doc.crossing()
        return plan_cleanup(mesh, crossing=crossing, preset=preset).to_dict()

    return await run_in_threadpool(work)


@app.post("/api/doc/{doc_id}/add-scan")
async def add_scan(doc_id: str, file: UploadFile) -> dict:
    """Upload another scan of the same object, ready to be lined up and merged."""
    from . import merge

    _get(doc_id)
    name = Path(file.filename or "scan").name
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=415, detail=f"Unsupported file type. Supported: {', '.join(SUPPORTED_EXTENSIONS)}")
    tmp_path = await _save_upload(file, suffix)
    try:
        mesh = await run_in_threadpool(load_mesh, tmp_path)
    except MeshLoadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)
    scan_id = uuid.uuid4().hex
    with _lock:
        merge.PENDING[scan_id] = (name, mesh)
        while len(merge.PENDING) > 3:
            merge.PENDING.pop(next(iter(merge.PENDING)))
    return {"scan_id": scan_id, "name": name, "triangles": len(mesh.faces)}


@app.get("/api/scan/{scan_id}/mesh")
async def added_scan_mesh(scan_id: str) -> Response:
    from . import merge
    from .repair import reduce_detail

    entry = merge.PENDING.get(scan_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="That scan is no longer open. Please add it again.")
    mesh = entry[1]
    if len(mesh.faces) > DISPLAY_MAX_TRIANGLES:
        mesh = await run_in_threadpool(reduce_detail, mesh, DISPLAY_MAX_TRIANGLES)
    return Response(content=mesh.export(file_type="stl"), media_type="model/stl")


@app.get("/api/doc/{doc_id}/wall-thickness")
async def wall_thickness(doc_id: str, flexible: bool = False) -> dict:
    """Where walls are thinner than about two nozzle widths (three for
    flexible filament such as TPU, which needs sturdier walls)."""
    from .thickness import measure

    doc = _get(doc_id)
    printer = _printer()
    nozzle = printer.nozzle_mm if printer is not None else 0.4
    flexible = flexible or (printer is not None and printer.material == "TPU")
    if printer is not None and printer.technology != "filament":
        limit = 0.6
    else:
        limit = (3 if flexible else 2) * nozzle
    try:
        return await run_in_threadpool(lambda: measure(doc.snapshot(), limit))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/doc/{doc_id}/orientations")
async def orientations(doc_id: str) -> dict:
    """Print orientation advice: fewest supports, strongest, fastest."""
    from .orientation import advise

    doc = _get(doc_id)
    return await run_in_threadpool(lambda: advise(doc.snapshot()))


@app.get("/api/doc/{doc_id}/up-candidates")
async def up_candidates(doc_id: str) -> list[dict]:
    """The most likely bottoms for "Which way is up?", best first."""
    from .orient import up_candidates as find

    doc = _get(doc_id)
    return await run_in_threadpool(lambda: find(doc.snapshot()))


@app.get("/api/slicers")
def list_slicers() -> list[dict]:
    """Slicers installed on this computer that MeshRight can open files in."""
    from . import slicers

    return [{"id": s.id, "name": s.name} for s in slicers.installed()]


@app.post("/api/doc/{doc_id}/send-to-slicer")
async def send_to_slicer(doc_id: str, body: dict = Body(...)) -> dict:
    from . import slicers

    doc = _get(doc_id)
    slicer_id = body.get("slicer") if isinstance(body, dict) else None
    if not isinstance(slicer_id, str):
        raise HTTPException(status_code=400, detail="Choose a slicer.")
    stem = _stem(doc.name)
    data = await run_in_threadpool(lambda: doc.snapshot().export(file_type="3mf"))
    try:
        await run_in_threadpool(slicers.send, slicer_id, f"{stem}-meshright.3mf", data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    name = next(s.name for s in slicers.KNOWN if s.id == slicer_id)
    return {"message": f"Opened in {name}"}


# Actions whose result opens as a new model instead of changing this one.
NEW_MODEL_ACTIONS = {"part_from_area": "part"}


@app.post("/api/doc/{doc_id}/new-model")
async def new_model(doc_id: str, payload: dict = Body(...)) -> dict:
    """Run an action on a copy and open the result as a new model; this one
    stays as it is."""
    doc = _get(doc_id)
    name = payload.get("action") if isinstance(payload, dict) else None
    if name not in NEW_MODEL_ACTIONS:
        raise HTTPException(status_code=400, detail="That action cannot make a new model.")
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="params must be a set of named values.")

    def work():
        mesh = doc.snapshot()
        try:
            result, receipt = run_action(mesh, name, params)
        except ActionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        stem = Path(doc.name).stem or "model"
        new = Document(name=f"{stem}-{NEW_MODEL_ACTIONS[name]}.stl", original=result)
        new_id = uuid.uuid4().hex
        with _lock:
            _documents[new_id] = new
            while len(_documents) > MAX_OPEN_DOCUMENTS:
                _documents.popitem(last=False)
        state = _state(new_id, new)
        state["notice"] = receipt
        return state

    return await run_in_threadpool(work)


@app.post("/api/doc/{doc_id}/{command}")
async def history_command(doc_id: str, command: str) -> dict:
    doc = _get(doc_id)
    commands = {"undo": doc.undo, "redo": doc.redo, "revert": doc.revert}
    if command not in commands:
        raise HTTPException(status_code=404, detail="Unknown command.")

    def work():
        with doc.lock:
            commands[command]()
        return _state(doc_id, doc)

    return await run_in_threadpool(work)


# doc id -> (the mesh it was made from, STL bytes, triangles shown). Holding
# the mesh itself (not its id number) means a stale copy is never served.
_display_cache: OrderedDict[str, dict] = OrderedDict()


def _display_entry(doc_id: str, doc: Document) -> dict:
    """What the viewer shows: the mesh, lightened for very big meshes, and
    its binary STL. Cached until the model changes."""
    from .repair import reduce_detail

    mesh = doc.snapshot()
    with _lock:
        cached = _display_cache.get(doc_id)
    if cached is not None and cached["source"] is mesh:
        return cached
    shown = mesh if len(mesh.faces) <= DISPLAY_MAX_TRIANGLES else reduce_detail(mesh, DISPLAY_MAX_TRIANGLES)
    if shown is not mesh:
        from .colour import carry

        shown = carry(mesh, shown)
    entry = {"source": mesh, "shown": shown, "stl": shown.export(file_type="stl")}
    with _lock:
        _display_cache[doc_id] = entry
        _display_cache.move_to_end(doc_id)
        while len(_display_cache) > 6:
            _display_cache.popitem(last=False)
    return entry


def _display_copy(doc_id: str, doc: Document) -> tuple[bytes, int, int]:
    """Binary STL for the viewer, lightened for very big meshes."""
    entry = _display_entry(doc_id, doc)
    return entry["stl"], len(entry["shown"].faces), len(entry["source"].faces)


@app.get("/api/doc/{doc_id}/colours")
async def colours(doc_id: str) -> Response:
    """The colour of each corner of each triangle the viewer shows, as RGB
    bytes in triangle order."""
    from .colour import face_vertex_rgb

    doc = _get(doc_id)

    def work():
        entry = _display_entry(doc_id, doc)
        if "colours" not in entry:
            entry["colours"] = face_vertex_rgb(entry["shown"])
        return entry["colours"]

    return Response(content=await run_in_threadpool(work), media_type="application/octet-stream")


@app.get("/api/doc/{doc_id}/changes")
async def changes(doc_id: str) -> dict:
    """How far the shown surface has moved from the original file, per
    triangle (0 to 255, base64), with the largest move and the moved share."""
    from . import quality

    doc = _get(doc_id)

    def work():
        entry = _display_entry(doc_id, doc)
        if "changes" not in entry:
            entry["changes"] = quality.change_levels(doc.original, entry["shown"])
        return entry["changes"]

    levels, largest, moved = await run_in_threadpool(work)
    return {"levels": base64.b64encode(levels.tobytes()).decode("ascii"), "largest_mm": round(largest, 3), "moved_share": round(moved, 4)}


@app.get("/api/doc/{doc_id}/roughness")
async def roughness(doc_id: str) -> dict:
    """How rough each triangle the viewer shows is, 0 to 255 (base64), and
    the share of the surface that is rough."""
    from . import quality

    doc = _get(doc_id)

    def work():
        entry = _display_entry(doc_id, doc)
        if "rough" not in entry:
            entry["rough"] = quality.face_levels(entry["shown"])
        return entry["rough"]

    levels, share = await run_in_threadpool(work)
    return {"levels": base64.b64encode(levels.tobytes()).decode("ascii"), "rough_share": round(share, 4)}


@app.get("/api/doc/{doc_id}/mesh")
async def current_mesh(doc_id: str) -> Response:
    """The current mesh as binary STL, in the same coordinates as the report.
    Very big meshes come back lightened; the headers say so."""
    doc = _get(doc_id)
    data, shown, total = await run_in_threadpool(_display_copy, doc_id, doc)
    return Response(
        content=data,
        media_type="model/stl",
        headers={"X-Shown-Triangles": str(shown), "X-Total-Triangles": str(total)},
    )


@app.get("/api/doc/{doc_id}/export")
async def export(doc_id: str, format: str = "stl") -> Response:
    fmt = format.lower()
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail=f"Export as one of: {', '.join(EXPORT_FORMATS)}.")
    doc = _get(doc_id)
    filename = f"{_stem(doc.name)}-meshright.{fmt}"

    def work():
        mesh = doc.snapshot()
        import numpy as np

        if not np.isfinite(mesh.vertices).all() or np.abs(mesh.vertices).max(initial=0) > 3e38:
            raise ValueError("The model is too far from the centre or too big to save. Use Put on bed or Set size first.")
        data = mesh.export(file_type=fmt)
        return data.encode("utf-8") if isinstance(data, str) else data

    try:
        data = await run_in_threadpool(work)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=data, media_type=EXPORT_FORMATS[fmt], headers={"Content-Disposition": _attachment(filename)})


@app.get("/api/doc/{doc_id}/export-colours")
async def export_colours(doc_id: str, colours: int = 4) -> Response:
    """A 3MF painted for multi-colour printing (Bambu Studio, PrusaSlicer,
    OrcaSlicer), from the model's own colours grouped into ``colours``
    print colours. The X-Colours header lists them with their share."""
    import json

    from . import colour_export

    doc = _get(doc_id)
    stem = _stem(doc.name)
    try:
        data, summary = await run_in_threadpool(colour_export.export, doc.snapshot(), colours, stem)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=data,
        media_type=EXPORT_FORMATS["3mf"],
        headers={
            "Content-Disposition": _attachment(f"{stem}-meshright-colours.3mf"),
            "X-Colours": json.dumps(summary),
        },
    )


@app.get("/api/doc/{doc_id}/project")
def save_project(doc_id: str) -> Response:
    """The model, its original and its steps as a .meshright file."""
    from . import project

    doc = _get(doc_id)
    return Response(
        content=run_with_lock(doc, project.save),
        media_type="application/zip",
        headers={"Content-Disposition": _attachment(f"{_stem(doc.name)}{project.EXTENSION}")},
    )


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
