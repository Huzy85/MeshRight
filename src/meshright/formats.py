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


# ---------------------------------------------------------------- scanners
#
# Scanners and scanning apps leave their name in the file: the 80-byte STL
# header, PLY "comment" lines, "#" lines at the top of an OBJ, the glTF
# "generator" field or 3MF metadata. Knowing where a scan came from lets
# MeshRight say what that kind of scan usually needs.

HANDHELD = (
    "Handheld scans are usually in millimetres, with small holes where the scanner could not see "
    "(underneath, in deep creases) and stray specks around the model. Clean up fills the holes and "
    "removes the specks; Cut a flat bottom gives it a face to print on."
)
PHONE = (
    "Phone scans often open very small (they are saved in metres) and have a ragged edge where the "
    "scan stopped. Check the size first and fix the units if it is a few millimetres across, then "
    "Clean up and Cut a flat bottom."
)
PHOTOGRAMMETRY = (
    "Photogrammetry models have no real-world size unless one was set, carry a lot of triangles and "
    "keep the ground around the object. Set the size, erase the ground with Erase an area, Clean up, "
    "then Reduce detail."
)
DEPTH_CAMERA = (
    "Depth-camera scans are coarse, usually in metres, and have holes. Check the size, then Clean up "
    "and Make Solid."
)
SURVEY = (
    "Survey scans are in metres and very dense. Check the size, cut out the part you want with "
    "Make a part from an area, then Reduce detail."
)

# (words to look for, name shown, what such scans usually need). Matched as
# whole words, ignoring case, against the file's header text.
SCANNERS: list[tuple[tuple[str, ...], str, str]] = [
    (("revopoint", "revo scan", "revoscan"), "Revopoint", HANDHELD),
    (("creality", "cr-scan", "crscan", "creality scan"), "Creality Scan", HANDHELD),
    (("shining3d", "shining 3d", "einstar", "einscan", "exscan"), "Shining 3D", HANDHELD),
    (("artec",), "Artec", HANDHELD),
    (("3dmakerpro", "3d makerpro", "jmstudio"), "3DMakerpro", HANDHELD),
    (("matter and form", "matterandform", "mfstudio"), "Matter and Form", HANDHELD),
    (("polycam",), "Polycam", PHONE),
    (("scaniverse",), "Scaniverse", PHONE),
    (("kiri engine", "kiriengine"), "KIRI Engine", PHONE),
    (("luma ai", "lumalabs", "luma labs"), "Luma AI", PHONE),
    (("3d scanner app", "3dscannerapp", "laan labs"), "3D Scanner App", PHONE),
    (("qlone",), "Qlone", PHONE),
    (("metashape", "agisoft", "photoscan"), "Agisoft Metashape", PHOTOGRAMMETRY),
    (("realitycapture", "reality capture", "capturing reality", "realityscan"), "RealityCapture", PHOTOGRAMMETRY),
    (("meshroom", "alicevision"), "Meshroom", PHOTOGRAMMETRY),
    (("colmap",), "COLMAP", PHOTOGRAMMETRY),
    (("skanect", "structure sensor", "occipital"), "Structure Sensor", DEPTH_CAMERA),
    (("kinect", "kinfu", "kinect fusion"), "Kinect", DEPTH_CAMERA),
    (("realsense",), "Intel RealSense", DEPTH_CAMERA),
    (("faro",), "FARO", SURVEY),
    (("leica", "cyclone"), "Leica", SURVEY),
    (("trimble",), "Trimble", SURVEY),
]

_HEADER_BYTES = 16 * 1024


def _header_text(path: Path, file_type: str) -> str:
    """The part of the file where a maker's name would be, as lower-case text."""
    file_type = file_type.lower()
    try:
        if file_type in ("glb", "gltf", "3mf"):
            return _asset_text(path, file_type)
        with open(path, "rb") as f:
            head = f.read(_HEADER_BYTES)
    except OSError:
        return ""
    text = head.decode("latin-1").lower()
    if file_type == "stl":
        return text[:80] if not text.startswith("solid") else text.split("\n", 1)[0]
    if file_type == "ply":
        return text.split("end_header", 1)[0]
    # OBJ, OFF, point files: the comment lines at the top.
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("#", "//", "%")) or not stripped:
            lines.append(stripped)
        else:
            break
    return "\n".join(lines)


def _asset_text(path: Path, file_type: str) -> str:
    import json

    if file_type == "3mf":
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".model")]
            text = " ".join(zf.read(n)[:_HEADER_BYTES].decode("utf-8", "replace") for n in names[:2])
            found = []
            for el in ET.fromstring(_complete_xml(text)).iter():
                if _local(el.tag) == "metadata" and el.text:
                    found.append(f"{el.get('name', '')} {el.text}")
            return " ".join(found).lower()
    if file_type == "glb":
        with open(path, "rb") as f:
            head = f.read(20)
            if len(head) < 20 or head[:4] != b"glTF":
                return ""
            length = int.from_bytes(head[12:16], "little")
            data = json.loads(f.read(min(length, 4 * _HEADER_BYTES)).decode("utf-8", "replace") if length <= 4 * _HEADER_BYTES else "{}")
    else:
        with open(path, "rb") as f:
            data = json.loads(f.read(4 * _HEADER_BYTES).decode("utf-8", "replace")) if path.stat().st_size <= 4 * _HEADER_BYTES else {}
    asset = data.get("asset", {}) if isinstance(data, dict) else {}
    return " ".join(str(asset.get(k, "")) for k in ("generator", "copyright", "extras")).lower()


def _complete_xml(text: str) -> str:
    """The metadata sits at the top of a 3MF model file; the rest (which can
    be huge) is cut off, so close whatever was opened to keep the parser happy."""
    import re

    text = text.rsplit("<", 1)[0] if not text.rstrip().endswith(">") else text
    opened: list[str] = []
    for match in re.finditer(r"<(/?)([\w:]+)[^>]*?(/?)>", text):
        closing, name, self_closing = match.groups()
        if self_closing or name.startswith(("?", "!")):
            continue
        if closing:
            if opened and opened[-1] == name:
                opened.pop()
        else:
            opened.append(name)
    return text + "".join(f"</{name}>" for name in reversed(opened))


def detect_scanner(path: str | Path, file_type: str | None = None) -> dict | None:
    """Which scanner or scanning app made the file, from its header: a dict
    with the name and what such scans usually need, or None if unknown."""
    import re

    path = Path(path)
    file_type = (file_type or path.suffix).lower().lstrip(".")
    try:
        text = _header_text(path, file_type)
    except Exception:  # noqa: BLE001 - a broken header is no reason not to open the file
        return None
    if not text:
        return None
    for words, name, advice in SCANNERS:
        for word in words:
            if re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text):
                return {"name": name, "advice": advice}
    return None
