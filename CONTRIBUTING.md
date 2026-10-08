# Contributing to MeshRight

Thanks for helping. MeshRight is meant to be a community project, so all kinds
of help count: bug reports, example meshes that break things, documentation,
design ideas and code.

## Reporting a problem

Open an issue and include:

- what you did and what you expected to happen
- the output of `meshright check yourfile.stl`
- the file, if you can share it, or a description of where it came from
  (which scanner, which app exported it)

Please only attach models you have the right to share, and never attach
anything containing personal data.

## Setting up for development

```bash
git clone https://github.com/Huzy85/MeshRight.git
cd MeshRight
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

Run the app with `meshright` and open http://127.0.0.1:8765.

To build the one-file app for your own computer:

```bash
pip install ".[e57,las,mcp]" pyinstaller
packaging/build.sh          # the app appears in dist/
```

## How the code is laid out

| Path | What it is |
| --- | --- |
| `src/meshright/actions.py` | **Every change MeshRight can make**, as named actions with checked inputs and a plain-English receipt |
| `src/meshright/analysis.py` | Loads meshes (all file types, units, colours), finds problems, computes the score |
| `src/meshright/formats.py` | Extra file readers and unit/orientation fixes per format |
| `src/meshright/document.py` | An open model and its undo history |
| `src/meshright/server.py` | Local web server and API, including protection against other websites |
| `src/meshright/cli.py` | The `meshright` command (`serve`, `check`, `fix`) |
| `src/meshright/repair.py` | Clean up, hole filling, Make Solid, thicken, hollow, reduce detail, repair presets |
| `src/meshright/intersections.py` | Finds triangles that cut through each other |
| `src/meshright/orient.py`, `orientation.py` | Which way is up, lay flat, flat bottom or base, best way to print |
| `src/meshright/cut.py` | Cut in two, split to fit, pins, numbered parts |
| `src/meshright/combine.py`, `merge.py` | Combine models, merge scans |
| `src/meshright/bust.py`, `sculpt.py`, `thickness.py` | Make a bust, sculpt brushes, wall check |
| `src/meshright/quality.py` | Rough spots and what-changed heatmaps |
| `src/meshright/colour.py` | Keeps colours of colour scans through every change |
| `src/meshright/pointcloud.py` | Point clouds to surfaces |
| `src/meshright/batch.py`, `project.py`, `settings.py` | Batch mode, .meshright projects, printer settings |
| `src/meshright/slicers.py`, `updates.py`, `samples.py` | Open in slicer, the version check, sample models |
| `src/meshright/assistant.py` | Ask AI: talks to the user's chosen AI service and checks its suggested steps |
| `src/meshright/web/` | Browser app (plain HTML, CSS and JavaScript, no build step) |
| `src/meshright/web/vendor/` | Bundled three.js, so the app works offline |
| `packaging/`, `.github/workflows/` | One-file apps, tests, releases and PyPI publishing |
| `tests/` | Tests. Test meshes are generated in code |

To add a feature: write the geometry, add an action in `actions.py`, add a
test, then a button and (if needed) a sheet in `web/index.html` and
`web/app.js`. New `POST /api/doc/{id}/...` routes in `server.py` must come
before the catch-all route for undo and redo.

## Ask AI

`src/meshright/assistant.py` connects the user's own AI service. It uses only
the Python standard library and speaks two formats: the OpenAI chat format
(which most services and Ollama and LM Studio accept) and Claude's. Rules
that must stay true:

- It sends the check report, the printer and the question, never the model.
- Every suggested step is checked against `REGISTRY` and its inputs before
  the user sees it. Anything else is dropped.
- Nothing runs until the user presses Apply, and each step is a normal,
  undoable action.
- The key is never sent back to the page, never logged, and is stored only
  in the user's settings folder with owner-only permissions. A saved key is
  reused only for the same service at the same address.

Tests use a fake AI server (`tests/test_assistant.py`); they never need a real
key or the internet.

## The MCP server

`src/meshright/mcp_server.py` offers the same checks and actions to AI apps
over MCP (stdio). It needs the optional `mcp` extra. Each tool is a plain
function whose docstring and type hints become the tool description, so a
new tool is a function added to `TOOLS`. Tools take file paths and write a
new file next to the original; they never overwrite the input. Run
`meshright mcp --show-config` to see the lines a user pastes into their
app's settings, and keep `tests/test_mcp_server.py` passing.

## Checking against the model library

Before a release, run the batch fix over the public test models:

```bash
python tools/check_library.py
```

It downloads the models in `tools/library.csv` (MIT-licensed test meshes,
cached in `tools/library-cache/`, never committed), adds the built-in
samples and the Stanford bunny, repairs each one with the Balanced preset
and writes `tools/library-results.md`: score before and after, seconds, and
what is left. Compare the table with the committed one; a model that got
worse is a bug. Add models to the CSV with a licence that allows
redistribution and a note on what the model tests.

## Keep the download small

The release workflow measures each packaged app and fails over 180 MB
(`LIMIT_MB` in `.github/workflows/release.yml`). At 0.2.0 the apps are
about 88 MB (Linux), 68 MB (Windows) and 45 MB (macOS). Before adding a
library, check what it pulls in with `pip install --dry-run --report`;
anything with a bundled Qt, Torch or OpenCV is out. Raspberry Pi and other
ARM Linux machines are untested: `pip install meshright` there depends on
aarch64 wheels for manifold3d, pymeshfix, fast-simplification and PyMCubes,
so check those exist before adding a heavy compiled dependency.

## Releasing a new version

Releases happen by themselves. Run the model library check (above) and the
tests, add a section for the new version at the top
of `CHANGELOG.md` (`## 0.1.3` and a list of changes), then raise
`__version__` in `src/meshright/__init__.py` to match. When that reaches
`main`, GitHub builds the Windows, macOS and Linux apps, publishes a release
with them and the changelog notes, and uploads the package to PyPI.

## Guidelines

- **Beginners first.** Every message a user sees should make sense to someone
  who has never heard the word "manifold". Say what is wrong and why it matters
  for printing.
- **No settings unless they are unavoidable.** Good defaults beat options.
- **Local and offline.** Nothing may send a user's files or data to a remote
  service. MeshRight makes a once-a-day check of the latest version number on
  GitHub, which can be switched off, and, only if the user sets it up, sends
  the check report to the AI service they chose. Any online feature must be
  opt-in and bring-your-own-key.
- **Never commit secrets or personal data.** No API keys, tokens, passwords,
  email addresses or personal file paths in code, tests, issues or commits.
  Keep local settings in a `.env` file; it is ignored by git.
- **Every change is an action.** New tools go in `actions.py` with a clear
  title, description and checked inputs, and return a plain-English receipt.
  The buttons, batch mode, the command line, Ask AI and the MCP server all
  use this list.
- **Add a test** for every new check or repair. Build the test mesh in code
  (see `tests/conftest.py`) rather than adding binary files.
- **Keep pull requests small** and focused on one thing.

## Licence

By contributing you agree that your work is released under the
[GNU GPL v3](LICENSE), the same licence as the rest of the project.
