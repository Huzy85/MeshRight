"""Batch mode: the same fixes for many files at once.

Used by the browser ("Fix several files") and the command line
(``meshright fix``). Each file goes through the normal actions, so the
results match what a person would get by clicking.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import trimesh

from .actions import ActionError, run_action
from .analysis import MeshLoadError, analyze, load_mesh

FORMATS = ("3mf", "stl", "obj")


@dataclass
class Options:
    cleanup: bool = True
    solid_if_still_broken: bool = False
    best_bottom: bool = False
    put_on_bed: bool = True
    shrink_to_fit: bool = True
    format: str = "3mf"
    preset: str = "balanced"  # repair preset for Clean up: quick, balanced or detail

    @classmethod
    def from_dict(cls, data: dict) -> "Options":
        if not isinstance(data, dict):
            raise ValueError("Batch options must be a set of named choices.")
        options = cls(**{k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__})
        options.format = str(options.format).lower()
        if options.format not in FORMATS:
            raise ValueError(f"Export format must be one of: {', '.join(FORMATS)}.")
        options.preset = str(options.preset).lower()
        if options.preset not in ("quick", "balanced", "detail", "flexible"):
            raise ValueError("The repair preset must be quick, balanced, detail or flexible.")
        for name in ("cleanup", "solid_if_still_broken", "best_bottom", "put_on_bed", "shrink_to_fit"):
            value = getattr(options, name)
            setattr(options, name, value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on"))
        return options


@dataclass
class Result:
    name: str
    ok: bool
    score_before: int | None = None
    score_after: int | None = None
    ready: bool = False
    steps: list[str] = field(default_factory=list)
    problems_left: list[str] = field(default_factory=list)
    error: str = ""
    output_name: str = ""


def process(mesh: trimesh.Trimesh, options: Options, printer=None) -> tuple[trimesh.Trimesh, list[str]]:
    """Apply the chosen fixes in a sensible order. Returns the mesh and receipts."""
    steps = []

    def run(name, params=None):
        nonlocal mesh
        try:
            mesh, receipt = run_action(mesh, name, params or {})
        except ActionError as exc:
            receipt = f"Skipped a step: {exc}"
        steps.append(receipt)

    if options.cleanup:
        run("cleanup", {"preset": options.preset})
    if options.solid_if_still_broken and not analyze(mesh, crossing=None).stats["watertight"]:
        run("make_solid")
    if options.best_bottom:
        from .orient import up_candidates

        down = up_candidates(mesh, limit=1)[0]["down"]
        run("set_bottom", dict(zip(("down_x", "down_y", "down_z"), down)))
    if options.put_on_bed:
        run("place_on_bed")
    if options.shrink_to_fit and printer is not None:
        report = analyze(mesh, crossing=None, printer=printer)
        too_big = next((i for i in report.issues if i.code == "too_big"), None)
        if too_big:
            fix = too_big.fixes[0]
            run(fix["action"], fix["params"])
    return mesh, steps


def process_file(path: Path, name: str, options: Options, printer=None) -> tuple[Result, bytes | None]:
    result = Result(name=name, ok=False)
    try:
        mesh = load_mesh(path)
    except MeshLoadError as exc:
        result.error = str(exc)
        return result, None
    try:
        result.score_before = analyze(mesh, printer=printer).score
        mesh, result.steps = process(mesh, options, printer)
    except Exception as exc:  # one troublesome file must not stop the batch
        result.error = f"Could not fix this file: {exc}"
        return result, None
    after = analyze(mesh, printer=printer)
    result.score_after = after.score
    result.ready = after.printable
    result.problems_left = [i.title for i in after.issues if i.severity != "info"]
    result.ok = True
    result.output_name = f"{Path(name).stem}-meshright.{options.format}"
    data = mesh.export(file_type=options.format)
    return result, data.encode("utf-8") if isinstance(data, str) else data


def summary_text(results: list[Result]) -> str:
    lines = ["MeshRight batch report", ""]
    for r in results:
        if not r.ok:
            lines.append(f"{r.name}: could not be fixed. {r.error}")
            continue
        state = "ready to print" if r.ready else "still needs attention"
        lines.append(f"{r.name} -> {r.output_name}: score {r.score_before} -> {r.score_after}, {state}")
        for step in r.steps:
            lines.append(f"    - {step}")
        for problem in r.problems_left:
            lines.append(f"    ! {problem}")
    ready = sum(r.ready for r in results)
    lines += ["", f"{ready} of {len(results)} files ready to print."]
    return "\n".join(lines) + "\n"


def unique_names(results: list[Result]) -> None:
    """Give every output its own name (two inputs can share a name)."""
    used: set[str] = set()
    for r in results:
        if not r.output_name:
            continue
        base = Path(r.output_name)
        name, n = r.output_name, 2
        while name.lower() in used:
            name = f"{base.stem}-{n}{base.suffix}"
            n += 1
        used.add(name.lower())
        r.output_name = name


def zip_results(outputs: list[tuple[Result, bytes | None]]) -> bytes:
    results = [r for r, _ in outputs]
    unique_names(results)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for result, data in outputs:
            if data is not None:
                zf.writestr(result.output_name, data)
        zf.writestr("meshright-report.txt", summary_text(results))
    return buffer.getvalue()
