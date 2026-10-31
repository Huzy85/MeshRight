"""MeshRight: check and fix 3D models and scans for printing.

Commands:
  serve   open the app in your browser (the default)
  check   check model files or folders and print a report
  fix     fix many files or whole folders at once
  ask     ask your own AI service about a model (optional)
  mcp     let AI apps such as Claude Desktop use MeshRight's tools
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__, settings
from .analysis import SUPPORTED_EXTENSIONS, MeshLoadError, analyze, load_mesh

SEVERITY_LABELS = {"error": "ERROR  ", "warning": "WARNING", "info": "INFO   "}


def _expand(paths: list[str]) -> tuple[list[Path], list[str]]:
    """Files to work on (folders are opened, their model files taken) and
    the problems found on the way."""
    files: list[Path] = []
    problems: list[str] = []
    for item in map(Path, paths):
        if item.is_dir():
            found = sorted(p for p in item.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS)
            if not found:
                problems.append(f"{item}: no model files in this folder.")
            files += found
        elif not item.exists():
            problems.append(f"{item}: file not found.")
        else:
            files.append(item)
    return files, problems


def _check(args: argparse.Namespace) -> int:
    files, problems = _expand(args.files)
    for problem in problems:
        print(problem, file=sys.stderr)
    worst = 2 if problems else 0
    for path in map(str, files):
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
    files, problems = _expand(args.paths)
    for problem in problems:
        print(problem, file=sys.stderr)
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
    kept = []
    for result, data in outputs:
        if data is None:
            continue
        target = out / result.output_name
        if target.exists() and not args.overwrite:
            kept.append(target.name)
            continue
        target.write_bytes(data)
    report = batch.summary_text([r for r, _ in outputs])
    (out / "meshright-report.txt").write_text(report, encoding="utf-8")
    print()
    print(report)
    print(f"Saved in {out}")
    if kept:
        print(f"Not replaced (already there, use --overwrite to replace): {', '.join(kept)}", file=sys.stderr)
    return 0 if all(r.ready for r, _ in outputs) else 1


def _open_browser(url: str) -> None:
    # The packaged Windows and Linux apps show a splash picture while they
    # unpack; it only exists inside those builds.
    try:
        import pyi_splash  # type: ignore[import-not-found]

        pyi_splash.close()
    except Exception:
        pass
    webbrowser.open(url)


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    import socket

    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            "Warning: MeshRight has no password. Anyone on your network who can reach this "
            "address can open and change your models. Only do this on a network you trust, "
            "and add this computer's name to MESHRIGHT_ALLOWED_HOSTS so other devices are let in.",
            file=sys.stderr,
        )
    family = socket.AF_INET6 if ":" in args.host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        if probe.connect_ex((args.host if args.host not in ("0.0.0.0", "::") else "127.0.0.1", args.port)) == 0:
            print(
                f"Port {args.port} is already in use. MeshRight may already be running: open {url} "
                "in your browser, or start it on another port with --port 8766.",
                file=sys.stderr,
            )
            return 2
    print(f"MeshRight is running at {url}  (press Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, _open_browser, args=(url,)).start()
    from .server import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def _mcp(args: argparse.Namespace) -> int:
    from . import mcp_server

    if args.show_config:
        print(mcp_server.register_hint())
        return 0
    try:
        return mcp_server.run()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 2


def _ask(args: argparse.Namespace) -> int:
    from . import assistant

    config = assistant.load()
    if args.provider or args.model or args.base_url:
        config = assistant.Config(
            provider=args.provider or (config.provider if config else "openai"),
            model=args.model or "",
            base_url=args.base_url or "",
            api_key=config.api_key if config and not args.base_url and config.provider == (args.provider or config.provider) else "",
        )
    if config is None:
        print("No AI is connected. Set one up in the app (Ask AI, then AI settings), or pass --provider and "
              "--model, with the key in the MESHRIGHT_AI_KEY environment variable.", file=sys.stderr)
        return 2
    try:
        config.validate()
        printer, chosen = settings.load()
        printer = printer if chosen else None
        report = analyze(load_mesh(args.file), printer=printer).to_dict()
        answer = assistant.ask(args.question, report, [], printer.to_dict() if printer else None, config)
    except (MeshLoadError, OSError, assistant.AssistantError) as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(answer, indent=2))
        return 0
    print(answer["reply"])
    if answer["steps"]:
        print("\nSuggested steps (open the file in the app to apply them, each can be undone):")
        for number, step in enumerate(answer["steps"], 1):
            settings_text = ", ".join(f"{k.replace('_', ' ')}: {v:g}" if isinstance(v, float) else f"{k.replace('_', ' ')}: {v}"
                                      for k, v in step["params"].items())
            print(f"  {number}. {step['title']}" + (f" ({settings_text})" if settings_text else ""))
            if step["why"]:
                print(f"     {step['why']}")
    for note in answer["dropped"]:
        print(f"  Note: {note}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meshright", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"meshright {__version__}")
    from .assistant import PROVIDERS

    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="open the MeshRight app in your browser")
    serve.add_argument("--host", default="127.0.0.1", help="address to listen on (default: this computer only)")
    serve.add_argument("--port", type=int, default=8765, help="port to use (default: 8765)")
    serve.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    serve.set_defaults(func=_serve)

    check = sub.add_parser("check", help="check mesh files and print a report")
    check.add_argument("files", nargs="+", help="model files or folders: STL, 3MF, OBJ, PLY, GLB/glTF, OFF, AMF or USD, or point clouds (XYZ, ASC, PTS, E57, LAS, LAZ)")
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
    fix.add_argument("--overwrite", action="store_true", help="replace fixed files that are already in the output folder")
    fix.set_defaults(func=_fix)

    ask = sub.add_parser("ask", help="ask your own AI service about a model (it sees the report, not the model)")
    ask.add_argument("file", help="the model file")
    ask.add_argument("question", help='your question in quotes, e.g. "will this print?"')
    ask.add_argument("--provider", help="AI service to use this time: " + ", ".join(PROVIDERS))
    ask.add_argument("--model", help="model name to use this time")
    ask.add_argument("--base-url", help="address of an OpenAI-compatible service")
    ask.add_argument("--json", action="store_true", help="print the answer as JSON")
    ask.set_defaults(func=_ask)

    mcp = sub.add_parser("mcp", help="serve MeshRight's tools to AI apps over MCP (stdin/stdout)")
    mcp.add_argument("--show-config", action="store_true", help="print the JSON to paste into an AI app's MCP settings")
    mcp.set_defaults(func=_mcp)

    args = parser.parse_args(argv)
    if not args.command:
        args = parser.parse_args(["serve"])
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # a plain sentence, not a traceback
        print(f"MeshRight stopped with an error: {exc}", file=sys.stderr)
        print("Please report it at https://github.com/Huzy85/MeshRight/issues", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
