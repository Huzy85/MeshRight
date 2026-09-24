"""Command line entry point: ``meshright serve`` and ``meshright check``."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__
from . import settings
from .analysis import SUPPORTED_EXTENSIONS, MeshLoadError, analyze, load_mesh

SEVERITY_LABELS = {"error": "ERROR  ", "warning": "WARNING", "info": "INFO   "}


def _check(args: argparse.Namespace) -> int:
    worst = 0
    for path in args.files:
        try:
            printer, chosen = settings.load()
            report = analyze(load_mesh(path), printer=printer if chosen else None)
        except (MeshLoadError, OSError) as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            worst = 2
            continue

        if args.json:
            data = report.to_dict()
            data.pop("highlights")
            print(json.dumps({"file": path, **data}, indent=2))
        else:
            s = report.stats
            print(f"{path}")
            print(f"  Score:     {report.score}/100  ({report.verdict})")
            print(f"  Triangles: {s['triangles']:,}   Pieces: {s['pieces']}")
            print(f"  Size (mm): {' x '.join(f'{v:g}' for v in s['size_mm'])}")
            if not report.issues:
                print("  No problems found.")
            for issue in report.issues:
                print(f"  {SEVERITY_LABELS[issue.severity]} {issue.title}")
            print()

        if not report.printable:
            worst = max(worst, 1)
    return worst


def _fix(args: argparse.Namespace) -> int:
    from . import batch

    printer, chosen = settings.load()
    options = batch.Options(
        cleanup=not args.no_cleanup,
        preset=args.preset,
        solid_if_still_broken=args.solid,
        best_bottom=args.best_bottom,
        put_on_bed=not args.no_bed,
        shrink_to_fit=not args.no_fit,
        format=args.format,
    )
    files: list[Path] = []
    for item in map(Path, args.paths):
        if item.is_dir():
            files += sorted(p for p in item.iterdir() if p.suffix.lower() in SUPPORTED_EXTENSIONS)
        else:
            files.append(item)
    if not files:
        print("No model files found.", file=sys.stderr)
        return 2

    out = Path(args.out) if args.out else (files[0].parent / "meshright-fixed")
    out.mkdir(parents=True, exist_ok=True)
    outputs = []
    for path in files:
        print(f"Fixing {path.name} ...", flush=True)
        outputs.append(batch.process_file(path, path.name, options, printer if chosen else None))
    batch.unique_names([r for r, _ in outputs])
    for result, data in outputs:
        if data is not None:
            (out / result.output_name).write_bytes(data)
    report = batch.summary_text([r for r, _ in outputs])
    (out / "meshright-report.txt").write_text(report, encoding="utf-8")
    print()
    print(report)
    print(f"Saved in {out}")
    return 0 if all(r.ready for r, _ in outputs) else 1


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    url = f"http://{args.host}:{args.port}"
    print(f"MeshRight is running at {url}  (press Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    from .server import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meshright", description=__doc__)
    parser.add_argument("--version", action="version", version=f"meshright {__version__}")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="open the MeshRight app in your browser")
    serve.add_argument("--host", default="127.0.0.1", help="address to listen on (default: this computer only)")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    serve.set_defaults(func=_serve)

    check = sub.add_parser("check", help="check mesh files and print a report")
    check.add_argument("files", nargs="+", help="STL, 3MF, OBJ, PLY, GLB/glTF, OFF or AMF files")
    check.add_argument("--json", action="store_true", help="print the report as JSON")
    check.set_defaults(func=_check)

    fix = sub.add_parser("fix", help="fix many files or a whole folder at once")
    fix.add_argument("paths", nargs="+", help="model files and/or folders")
    fix.add_argument("--out", help="folder for the fixed files (default: meshright-fixed next to the first file)")
    fix.add_argument("--format", choices=("3mf", "stl", "obj"), default="3mf")
    fix.add_argument("--no-cleanup", action="store_true", help="skip the one-click cleanup")
    fix.add_argument("--preset", choices=("quick", "balanced", "detail", "flexible"), default="balanced",
                     help="repair preset: quick (fewest triangles), balanced, detail (keep every triangle) or flexible (TPU)")
    fix.add_argument("--solid", action="store_true", help="use Make Solid on files cleanup cannot close")
    fix.add_argument("--best-bottom", action="store_true", help="turn each model to MeshRight's best guess of its bottom")
    fix.add_argument("--no-bed", action="store_true", help="do not centre the models on the bed")
    fix.add_argument("--no-fit", action="store_true", help="do not shrink models that are too big for your printer")
    fix.set_defaults(func=_fix)

    args = parser.parse_args(argv)
    if not args.command:
        args = parser.parse_args(["serve"])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
