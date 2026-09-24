"""Readers for mesh formats that trimesh does not handle on its own, and
per-format fixes so every model arrives in millimetres with Z pointing up."""

from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

# AMF "unit" attribute -> millimetres.
AMF_UNITS_TO_MM = {
    "millimeter": 1.0,
    "inch": 25.4,
    "feet": 304.8,
    "meter": 1000.0,
    "micron": 0.001,
}

# glTF is Y-up and measured in metres; 3D printing is Z-up in millimetres.
GLTF_TO_PRINT = np.array(
    [
        [1000.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, -1000.0, 0.0],
        [0.0, 1000.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
)


def _local(tag: str) -> str:
    """Tag name without any XML namespace."""
    return tag.rsplit("}", 1)[-1]


def read_amf(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """Read an AMF file (plain XML or zipped) into vertices (mm) and faces."""
    data = Path(path).read_bytes()
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            if not names:
                raise ValueError("The AMF archive is empty.")
            data = zf.read(names[0])

    root = ET.fromstring(data)
    if _local(root.tag) != "amf":
        raise ValueError("Not an AMF file.")
    scale = AMF_UNITS_TO_MM.get((root.get("unit") or "millimeter").lower(), 1.0)

    all_vertices: list[np.ndarray] = []
    all_faces: list[np.ndarray] = []
    offset = 0
    for mesh in (el for el in root.iter() if _local(el.tag) == "mesh"):
        coords = []
        for vertex in (el for el in mesh.iter() if _local(el.tag) == "coordinates"):
            values = {_local(c.tag): float(c.text) for c in vertex}
            coords.append((values["x"], values["y"], values["z"]))
        triangles = []
        for tri in (el for el in mesh.iter() if _local(el.tag) == "triangle"):
            values = {_local(c.tag): int(c.text) for c in tri}
            triangles.append((values["v1"], values["v2"], values["v3"]))
        if not coords or not triangles:
            continue
        all_vertices.append(np.asarray(coords, dtype=np.float64))
        all_faces.append(np.asarray(triangles, dtype=np.int64) + offset)
        offset += len(coords)

    if not all_faces:
        raise ValueError("The AMF file does not contain any triangles.")
    return np.vstack(all_vertices) * scale, np.vstack(all_faces)
