#!/usr/bin/env bash
# Build the MeshRight app (one file, no Python needed) for this computer.
# Usage: packaging/build.sh   -> dist/MeshRight (or dist/MeshRight.exe)
# On Windows, run it from Git Bash (GitHub's Windows runners do this).
set -euo pipefail
cd "$(dirname "$0")/.."
SEP=":"
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) SEP=";" ;; esac
pyinstaller --noconfirm --clean --onefile --name MeshRight \
  --add-data "src/meshright/web${SEP}meshright/web" \
  --collect-submodules uvicorn \
  --collect-data trimesh \
  --collect-all pymeshfix \
  --collect-all fast_simplification \
  --collect-all mcubes \
  --collect-all manifold3d \
  --hidden-import meshright.repair \
  --hidden-import meshright.orient \
  --hidden-import meshright.batch \
  --hidden-import meshright.intersections \
  packaging/launcher.py
