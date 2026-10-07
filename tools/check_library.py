#!/usr/bin/env python3
"""Run MeshRight over a library of real broken models and write a results table.

    .venv/bin/python tools/check_library.py            # all models
    .venv/bin/python tools/check_library.py busted fuze  # some of them

Reads tools/library.csv (name, URL, licence, what is wrong), downloads each
file once into tools/library-cache/ (never committed), then for each model:
analyze, the one-click cleanup with the balanced preset, analyze again. The
table goes to tools/library-results.md: score before and after, time taken,
problems left. Also includes MeshRight's own built-in sample models and the
broken bunny scan that ships with pymeshfix. Run it before every release and
commit the table, so a change that makes a repair worse shows up as a diff.
"""

from __future__ import annotations

import csv
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

import trimesh  # noqa: E402

from meshright import batch, samples  # noqa: E402
from meshright.analysis import MeshLoadError, analyze, load_mesh  # noqa: E402

CACHE = HERE / "library-cache"
LIST = HERE / "library.csv"
RESULTS = HERE / "library-results.md"


def models(wanted: set[str]) -> list[tuple[str, Path | None, str, str]]:
    """(name, path or None when it is a built-in sample, licence, note)."""
    rows = []
    with LIST.open(encoding="utf-8") as fh:
        for line in csv.reader(raw for raw in fh if raw.strip() and not raw.startswith("#")):
            name, url, licence, note = (c.strip() for c in line[:4])
            if wanted and name not in wanted:
                continue
            rows.append((name, fetch(name, url), licence, note))
    for name in samples.MAKERS:
        if not wanted or name in wanted:
            rows.append((name, None, "MeshRight sample (made in code)", "built-in sample"))
    bunny = bunny_scan()
    if bunny and (not wanted or "bunny" in wanted):
        rows.append(("bunny", bunny, "Stanford bunny scan, shipped with pymeshfix", "scan with holes in the base"))
    return rows


def fetch(name: str, url: str) -> Path:
    CACHE.mkdir(exist_ok=True)
    target = CACHE / f"{name}{Path(url).suffix.lower()}"
    if not target.exists():
        print(f"downloading {name} ...", flush=True)
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - URLs come from the committed list
            target.write_bytes(response.read())
    return target


def bunny_scan() -> Path | None:
    try:
        import pymeshfix

        path = Path(pymeshfix.__file__).parent / "examples" / "StanfordBunny.ply"
        return path if path.exists() else None
    except ImportError:
        return None


def open_model(name: str, path: Path | None) -> trimesh.Trimesh:
    if path is None:
        suffix, data = samples.make(name)
        out = CACHE / f"sample-{name}{suffix}"
        CACHE.mkdir(exist_ok=True)
        out.write_bytes(data)
        return load_mesh(out)
    return load_mesh(path)


def run(wanted: set[str]) -> list[dict]:
    results = []
    options = batch.Options(preset="balanced", put_on_bed=False, shrink_to_fit=False)
    for name, path, licence, note in models(wanted):
        row = {"name": name, "licence": licence, "note": note}
        started = time.perf_counter()
        try:
            mesh = open_model(name, path)
            before = analyze(mesh)
            fixed, steps = batch.process(mesh, options)
            after = analyze(fixed)
            row.update(
                triangles=len(mesh.faces), before=before.score, after=after.score, ready=after.printable,
                seconds=time.perf_counter() - started,
                left=", ".join(i.title for i in after.issues if i.severity != "info") or "none",
                steps=" | ".join(steps),
            )
        except (MeshLoadError, Exception) as exc:  # noqa: BLE001 - one bad model must not stop the run
            row.update(error=str(exc), seconds=time.perf_counter() - started)
        print(f"{name:18} {row.get('before', '-'):>4} -> {row.get('after', '-'):>4}  {row['seconds']:.1f}s  {row.get('left', row.get('error', ''))}", flush=True)
        results.append(row)
    return results


def write_table(results: list[dict]) -> None:
    lines = [
        "# Library results",
        "",
        f"MeshRight `tools/check_library.py`, balanced preset, {time.strftime('%Y-%m-%d')}.",
        "Scores are print-readiness out of 100, before and after the one-click cleanup.",
        "",
        "| Model | Triangles | Before | After | Ready | Seconds | Problems left |",
        "|---|---:|---:|---:|:---:|---:|---|",
    ]
    for r in results:
        if "error" in r:
            lines.append(f"| {r['name']} | - | - | - | - | {r['seconds']:.1f} | could not run: {r['error']} |")
        else:
            lines.append(f"| {r['name']} | {r['triangles']:,} | {r['before']} | {r['after']} | {'yes' if r['ready'] else 'no'} | {r['seconds']:.1f} | {r['left']} |")
    lines += ["", "## What each model is", ""]
    lines += [f"- **{r['name']}**: {r['note']} ({r['licence']})" for r in results]
    lines += ["", "## Steps taken", ""]
    lines += [f"- **{r['name']}**: {r.get('steps', r.get('error', ''))}" for r in results]
    RESULTS.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {RESULTS}")


if __name__ == "__main__":
    write_table(run(set(sys.argv[1:])))
