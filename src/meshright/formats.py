"""Readers for mesh formats that trimesh does not handle on its own, and
per-format fixes so every model arrives in millimetres with Z pointing up."""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
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



class ReadError(ValueError):
    """A file could not be read, with a message written for the user."""


# A zipped model may not unpack to more than this. A tiny crafted zip can
# otherwise expand to many gigabytes and exhaust the computer's memory.
MAX_UNZIPPED_BYTES = 2 * 1024 * 1024 * 1024


def read_zip_member(zf: zipfile.ZipFile, name: str, limit: int = MAX_UNZIPPED_BYTES) -> bytes:
    """One file from a zip, refusing anything that unpacks too big. The size
    written in the zip can lie, so the bytes are counted as they come."""
    info = zf.getinfo(name)
    if info.file_size > limit:
        raise ReadError("This file unpacks to more than 2 GB, so it was not opened.")
    chunks, total = [], 0
    with zf.open(info) as handle:
        while chunk := handle.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                raise ReadError("This file unpacks to more than 2 GB, so it was not opened.")
            chunks.append(chunk)
    return b"".join(chunks)


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
                raise ReadError("The AMF archive is empty.")
            data = read_zip_member(zf, names[0])

    root = ET.fromstring(data)
    if _local(root.tag) != "amf":
        raise ReadError("Not an AMF file.")
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
        raise ReadError("The AMF file does not contain any triangles.")
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


def _head(zf: zipfile.ZipFile, name: str) -> bytes:
    with zf.open(name) as handle:
        return handle.read(_HEADER_BYTES)


def _asset_text(path: Path, file_type: str) -> str:
    import json

    if file_type == "3mf":
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".model")]
            text = " ".join(_head(zf, n).decode("utf-8", "replace") for n in names[:2])
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


# ---------------------------------------------------------------- USD
#
# USD (Pixar's Universal Scene Description) comes as text (.usda), binary
# (.usdc) or a zip of either with textures (.usdz, what iPhones make with
# Object Capture). Text files are read here with no extra library. Binary
# files need Pixar's own reader, ``pip install usd-core`` (optional extra
# ``meshright[usd]``); without it the user is told how to get a text export.

USD_EXTENSIONS = (".usd", ".usda", ".usdc", ".usdz")
USD_BINARY_HELP = (
    "This USD file is binary (usdc), which needs Pixar's large reader. It is not in the downloaded "
    "app, to keep it small. Export the model as OBJ, GLB or USDA instead (Reality Composer, Blender "
    "and most scanner apps can), or, if you installed MeshRight with pip, run "
    "'pip install meshright[usd]'."
)


def read_usd(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """Read a USD file (usda, usdc, usd or usdz) into vertices (mm, Z up) and faces."""
    path = Path(path)
    data = path.read_bytes()
    if data[:2] == b"PK":
        return _read_usdz(path, data)
    if data[:5] == b"#usda":
        return _read_usda(data.decode("utf-8", "replace"))
    if data[:8] == b"PXR-USDC":
        return _read_with_pixar(path)
    raise ReadError("Not a USD file.")


def _read_usdz(path: Path, data: bytes) -> tuple[np.ndarray, np.ndarray]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        layers = [n for n in zf.namelist() if n.lower().endswith((".usda", ".usdc", ".usd"))]
        if not layers:
            raise ReadError("The USDZ archive holds no model.")
        first = read_zip_member(zf, layers[0])  # the first layer in the archive is the scene
    if first[:5] == b"#usda":
        return _read_usda(first.decode("utf-8", "replace"))
    return _read_with_pixar(path)


def _read_with_pixar(path: Path) -> tuple[np.ndarray, np.ndarray]:
    try:
        from pxr import Usd, UsdGeom
    except ImportError as exc:
        raise ReadError(USD_BINARY_HELP) from exc
    stage = Usd.Stage.Open(str(path))
    if stage is None:
        raise ReadError("Could not open the USD file.")
    scale = float(UsdGeom.GetStageMetersPerUnit(stage) or 0.01) * 1000.0
    y_up = UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.y
    pieces = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(prim)
        points = mesh.GetPointsAttr().Get()
        counts = mesh.GetFaceVertexCountsAttr().Get()
        indices = mesh.GetFaceVertexIndicesAttr().Get()
        if not points or not counts or not indices:
            continue
        matrix = np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default()), dtype=np.float64)
        vertices = np.asarray(points, dtype=np.float64)
        vertices = vertices @ matrix[:3, :3] + matrix[3, :3]  # USD matrices act on row vectors
        pieces.append((vertices, _fan_triangles(np.asarray(counts, dtype=np.int64), np.asarray(indices, dtype=np.int64))))
    return _finish_usd(pieces, scale, y_up)


def _fan_triangles(counts: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """Polygons (vertex counts plus a flat index list) as triangles."""
    faces = []
    at = 0
    for count in counts:
        poly = indices[at:at + count]
        at += count
        for k in range(1, count - 1):
            faces.append((poly[0], poly[k], poly[k + 1]))
    return np.asarray(faces, dtype=np.int64).reshape(-1, 3)


def _finish_usd(pieces, scale: float, y_up: bool) -> tuple[np.ndarray, np.ndarray]:
    pieces = [(v, f) for v, f in pieces if len(f)]
    if not pieces:
        raise ReadError("The USD file does not contain any triangles.")
    offset = 0
    all_vertices, all_faces = [], []
    for vertices, faces in pieces:
        all_vertices.append(vertices)
        all_faces.append(faces + offset)
        offset += len(vertices)
    vertices = np.vstack(all_vertices) * scale
    if y_up:
        vertices = vertices[:, [0, 2, 1]] * np.array([1.0, -1.0, 1.0])
    return vertices, np.vstack(all_faces)


# --- the text reader


def _read_usda(text: str) -> tuple[np.ndarray, np.ndarray]:
    meta = _usda_stage_metadata(text.split("\n", 1)[1] if "\n" in text else "")
    try:
        scale = float(meta.get("metersPerUnit", 0.01)) * 1000.0
    except ValueError:
        scale = 10.0
    y_up = str(meta.get("upAxis", "Y")).strip('"').upper() != "Z"
    pieces = []
    for attrs, transform in _usda_meshes(text):
        points = _usda_tuples(attrs.get("points", ""))
        counts = _usda_ints(attrs.get("faceVertexCounts", ""))
        indices = _usda_ints(attrs.get("faceVertexIndices", ""))
        if len(points) == 0 or len(counts) == 0 or len(indices) == 0:
            continue
        vertices = points @ transform[:3, :3] + transform[3, :3]
        pieces.append((vertices, _fan_triangles(counts, indices)))
    return _finish_usd(pieces, scale, y_up)


def _usda_stage_metadata(text: str) -> dict:
    """The ( ... ) block at the top of a usda file, as name -> raw value."""
    stripped = text.lstrip()
    if not stripped.startswith("("):
        return {}
    depth, end = 0, None
    for i, ch in enumerate(stripped):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    block = stripped[1:end] if end else ""
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"^\s*(\w+)\s*=\s*(.+?)\s*$", block, re.MULTILINE)}


# A prim: def Mesh "name" ( metadata ) {
_PRIM_START = re.compile(r'\b(?:def|over|class)\s+(?:(\w+)\s+)?"[^"]*"\s*(?:\((?:[^()]|\([^()]*\))*\))?\s*\{', re.DOTALL)
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _matching_brace(text: str, open_at: int) -> int:
    """Index of the } that closes the { at ``open_at`` (or the end of the text)."""
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return len(text)


def _usda_meshes(text: str):
    """Every Mesh prim as (attributes name -> raw value, its transform to the
    stage as a 4x4 acting on row vectors). Walks the prims so a mesh picks up
    the transforms of every prim that holds it."""
    yield from _usda_walk(text, 0, len(text), np.eye(4))


def _usda_walk(text: str, start: int, end: int, parent: np.ndarray):
    pattern = _PRIM_START
    pos = start
    while True:
        match = pattern.search(text, pos, end)
        if match is None:
            return
        body_start = match.end()
        body_end = _matching_brace(text, body_start - 1)
        prim_type = match.group(1)
        # This prim's own text: its body with the child prims cut out.
        own, children = [], []
        at = body_start
        while True:
            child = pattern.search(text, at, body_end)
            if child is None:
                own.append(text[at:body_end])
                break
            own.append(text[at:child.start()])
            child_end = _matching_brace(text, child.end() - 1)
            children.append((child.start(), child_end + 1))
            at = child_end + 1
        attrs = _usda_attributes("".join(own))
        transform = _usda_transform(attrs) @ parent
        if prim_type == "Mesh":
            yield attrs, transform
        for child_start, child_end in children:
            yield from _usda_walk(text, child_start, child_end, transform)
        pos = body_end + 1


def _usda_attributes(body: str) -> dict:
    attrs = {}
    pattern = re.compile(r'^\s*(?:uniform\s+|custom\s+)?[\w\[\]]+\s+([\w:.]+)\s*(?:\([^)]*\))?\s*=\s*', re.MULTILINE)
    for match in pattern.finditer(body):
        name = match.group(1)
        value, _ = _usda_value(body, match.end())
        attrs[name] = value
    return attrs


def _usda_value(text: str, start: int) -> tuple[str, int]:
    """The value starting at ``start``: a bracketed block, a quoted string or
    the rest of the line."""
    pairs = {"[": "]", "(": ")"}
    ch = text[start:start + 1]
    if ch in pairs:
        depth, i = 0, start
        while i < len(text):
            if text[i] in pairs:
                depth += 1
            elif text[i] in ")]":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1], i + 1
            i += 1
        return text[start:], len(text)
    if ch == '"':
        end = text.find('"', start + 1)
        return text[start:end + 1], end + 1
    end = text.find("\n", start)
    end = len(text) if end == -1 else end
    return text[start:end].strip(), end


def _usda_ints(raw: str) -> np.ndarray:
    return np.asarray([int(v) for v in re.findall(r"-?\d+", raw)], dtype=np.int64)


def _usda_tuples(raw: str) -> np.ndarray:
    values = [float(v) for v in _NUMBER.findall(raw)]
    return np.asarray(values, dtype=np.float64).reshape(-1, 3) if len(values) >= 3 else np.zeros((0, 3))


def _usda_transform(attrs: dict) -> np.ndarray:
    """This prim's own transform (4x4, row-vector convention) from its xformOps."""
    order_raw = attrs.get("xformOpOrder", "")
    order = [s.strip().strip('"') for s in order_raw.strip("[]").split(",") if s.strip()] if order_raw else []
    if not order:
        order = [k for k in attrs if k.startswith("xformOp:")]
    matrix = np.eye(4)
    for op in order:
        raw = attrs.get(op)
        if raw is None:
            continue
        values = _usda_tuples(raw).ravel() if not op.startswith("xformOp:transform") else None
        step = np.eye(4)
        if op.startswith("xformOp:transform"):
            nums = [float(v) for v in _NUMBER.findall(raw)]
            if len(nums) == 16:
                step = np.asarray(nums).reshape(4, 4)
        elif op.startswith("xformOp:translate") and len(values) == 3:
            step[3, :3] = values
        elif op.startswith("xformOp:scale") and len(values) == 3:
            step[:3, :3] = np.diag(values)
        elif op.startswith("xformOp:rotateXYZ") and len(values) == 3:
            rx, ry, rz = np.radians(values)
            cx, sx, cy, sy, cz, sz = np.cos(rx), np.sin(rx), np.cos(ry), np.sin(ry), np.cos(rz), np.sin(rz)
            mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
            my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
            mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
            step[:3, :3] = (mz @ my @ mx).T
        elif op.startswith("!invert!"):
            continue
        # USD applies ops right to left: the first op in the list is applied last.
        matrix = step @ matrix
    return matrix


# ---------------------------------------------------------------- E57 and FBX

E57_HELP = (
    "E57 scans need the optional reader. In the downloaded app it is built in; "
    "with pip, install it with 'pip install meshright[e57]'. "
    "Or export the scan as PLY or XYZ points from your scanning software."
)
FBX_HELP = (
    "FBX is a closed format MeshRight cannot read. Export the model as OBJ, GLB or STL instead: "
    "every scanner app and Blender (File > Export) can do that."
)
MAX_E57_POINTS = 3_000_000


def read_e57(path: str | Path) -> np.ndarray:
    """All the points of an E57 scan, in millimetres."""
    try:
        import pye57
    except ImportError as exc:
        raise ReadError(E57_HELP) from exc
    clouds = []
    e57 = pye57.E57(str(path))
    try:
        for index in range(e57.scan_count):
            data = e57.read_scan(index, ignore_missing_fields=True)
            if not all(k in data for k in ("cartesianX", "cartesianY", "cartesianZ")):
                continue
            clouds.append(np.column_stack([data["cartesianX"], data["cartesianY"], data["cartesianZ"]]).astype(np.float64))
    finally:
        e57.close()
    if not clouds:
        raise ReadError("The E57 file holds no points with x, y, z positions.")
    points = np.vstack(clouds) * 1000.0  # E57 is in metres
    points = points[np.all(np.isfinite(points), axis=1)]
    if len(points) > MAX_E57_POINTS:
        keep = np.random.default_rng(0).choice(len(points), MAX_E57_POINTS, replace=False)
        points = points[np.sort(keep)]
    return points


LAS_HELP = (
    "LAS and LAZ laser scans need the optional reader. In the downloaded app it is built in; "
    "with pip, install it with 'pip install meshright[las]'. "
    "Or export the scan as PLY or XYZ points from your scanning software."
)
MAX_LAS_POINTS = 3_000_000


def read_las(path: str | Path) -> np.ndarray:
    """The points of a LAS or LAZ laser scan, in millimetres. Very big scans
    are thinned evenly to keep the surface build quick."""
    try:
        import laspy
    except ImportError as exc:
        raise ReadError(LAS_HELP) from exc
    try:
        with laspy.open(str(path)) as reader:
            header = reader.header
            data = reader.read()
    except Exception as exc:
        if "lazrs" in str(exc).lower() or "laszip" in str(exc).lower():
            raise ReadError(LAS_HELP) from exc
        raise ReadError("Could not read this LAS file. It may be damaged.") from exc
    points = np.column_stack([np.asarray(data.x), np.asarray(data.y), np.asarray(data.z)]).astype(np.float64)
    points = points[np.all(np.isfinite(points), axis=1)]
    if not len(points):
        raise ReadError("The LAS file holds no points.")
    unit = _las_unit_mm(header)
    points = (points - points.min(axis=0)) * unit  # survey coordinates are huge: start at zero
    if len(points) > MAX_LAS_POINTS:
        keep = np.random.default_rng(0).choice(len(points), MAX_LAS_POINTS, replace=False)
        points = points[np.sort(keep)]
    return points


def _las_unit_mm(header) -> float:
    """Millimetres per LAS unit: metres unless the file says feet."""
    try:
        wkt = header.parse_crs().to_wkt().lower() if header.parse_crs() is not None else ""
    except Exception:
        wkt = ""
    if "us survey foot" in wkt or "foot_us" in wkt:
        return 304.8006096
    if "foot" in wkt or "feet" in wkt:
        return 304.8
    return 1000.0
