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
pip install pyinstaller
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
| `src/meshright/web/` | Browser app (plain HTML, CSS and JavaScript, no build step) |
| `src/meshright/web/vendor/` | Bundled three.js, so the app works offline |
| `packaging/`, `.github/workflows/` | One-file apps, tests, releases and PyPI publishing |
| `tests/` | Tests. Test meshes are generated in code |

To add a feature: write the geometry, add an action in `actions.py`, add a
test, then a button and (if needed) a sheet in `web/index.html` and
`web/app.js`. New `POST /api/doc/{id}/...` routes in `server.py` must come
before the catch-all route for undo and redo.

## Guidelines

- **Beginners first.** Every message a user sees should make sense to someone
  who has never heard the word "manifold". Say what is wrong and why it matters
  for printing.
- **No settings unless they are unavoidable.** Good defaults beat options.
- **Local and offline.** Nothing may send a user's files or data to a remote
  service. The only request MeshRight makes is a once-a-day check of the latest
  version number on GitHub, which can be switched off. Any future online
  feature (such as an AI assistant) must be opt-in and bring-your-own-key.
- **Never commit secrets or personal data.** No API keys, tokens, passwords,
  email addresses or personal file paths in code, tests, issues or commits.
  Keep local settings in a `.env` file; it is ignored by git.
- **Every change is an action.** New tools go in `actions.py` with a clear
  title, description and checked inputs, and return a plain-English receipt.
  The buttons, batch mode and the command line all use this list, and a
  future optional AI assistant would too.
- **Add a test** for every new check or repair. Build the test mesh in code
  (see `tests/conftest.py`) rather than adding binary files.
- **Keep pull requests small** and focused on one thing.

## Licence

By contributing you agree that your work is released under the
[GNU GPL v3](LICENSE), the same licence as the rest of the project.
