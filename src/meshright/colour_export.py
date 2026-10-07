"""Print in colours: a 3MF whose triangles are painted for a multi-colour
printer.

A colour scan has a colour on every point, far more than a printer can lay
down. This groups those colours into a handful of print colours (one per
filament), gives every triangle one of them, and writes a 3MF that slicers
open with the painting already done:

* Bambu Studio and OrcaSlicer read the ``paint_color`` attribute;
* PrusaSlicer reads ``slic3rpe:mmu_segmentation``;
* every other 3MF viewer sees the standard material colours.

Both painting attributes hold the same code, so one file suits all three.
The code is the slicer's own bit stream for a whole, unsplit triangle
painted with filament N (see ``paint_code``). The slicer still decides
which filament goes with which colour; the user only has to match them up.
"""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

import numpy as np
import trimesh
from scipy.cluster.vq import kmeans2, vq

from . import __version__, colour

MIN_COLOURS = 2
MAX_COLOURS = 8
DEFAULT_COLOURS = 4
# Points used to find the colour groups. Every point is then assigned.
SAMPLE = 200_000
SMOOTH_PASSES = 3

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PRUSA_NS = "http://schemas.slic3r.org/3mf/2017/06"


def group(mesh: trimesh.Trimesh, colours: int = DEFAULT_COLOURS, smooth: int = SMOOTH_PASSES) -> tuple[np.ndarray, np.ndarray]:
    """Give every triangle one of ``colours`` print colours.

    Returns the group of each triangle (0 = the biggest group) and the
    palette: one sRGB colour (0-255) per group, biggest group first. The
    grouping happens in Lab, where distances match how different two
    colours look. Each triangle takes the group most of its corners have,
    then lone triangles that disagree with their neighbours join them, so
    the slicer gets clean areas rather than speckle."""
    if not MIN_COLOURS <= colours <= MAX_COLOURS:
        raise ValueError(f"Choose between {MIN_COLOURS} and {MAX_COLOURS} colours.")
    if len(mesh.faces) == 0:
        raise ValueError("The model has no triangles.")
    rgb = colour.of(mesh)[:, :3]
    lab = _lab(rgb)
    rng = np.random.default_rng(0)
    sample = lab if len(lab) <= SAMPLE else lab[rng.choice(len(lab), SAMPLE, replace=False)]
    distinct = np.unique(sample.round(2), axis=0)
    k = min(colours, len(distinct))
    if k == 1:
        centres = distinct
    else:
        centres, _ = kmeans2(sample, k, minit="++", seed=1, iter=25)
    point_group, _ = vq(lab, centres)
    face_group = _majority(point_group[mesh.faces])
    for _ in range(smooth):
        face_group = _smooth(mesh, face_group)
    # Biggest group first, and drop any group that ended up empty.
    present, counts = np.unique(face_group, return_counts=True)
    order = present[np.argsort(-counts, kind="stable")]
    remap = np.full(len(centres), -1, dtype=np.int64)
    remap[order] = np.arange(len(order))
    face_group = remap[face_group]
    palette = np.array(
        [np.rint(rgb[mesh.faces[face_group == g]].reshape(-1, 3).mean(axis=0)) for g in range(len(order))],
        dtype=np.uint8,
    ).reshape(-1, 3)
    return face_group, palette


def paint_code(filament: int) -> str:
    """The painting code slicers store on a whole triangle painted with
    filament 1, 2, 3...: PrusaSlicer's TriangleSelector bit stream, written
    as upper-case hex digits in the slicers' own order (last nibble first).

    1 -> "4", 2 -> "8", 3 -> "0C", 4 -> "1C" ... 17 -> "EC", 18 -> "0FC"."""
    if filament < 1:
        raise ValueError("Filaments count from 1.")
    if filament <= 2:
        return "48"[filament - 1]
    rest = filament - 3
    nibbles = [0xC]
    while rest >= 15:
        nibbles.append(0xF)
        rest -= 15
    nibbles.append(rest)
    return "".join(f"{n:X}" for n in reversed(nibbles))


def write_3mf(mesh: trimesh.Trimesh, face_group: np.ndarray, palette: np.ndarray, name: str = "model") -> bytes:
    """A 3MF with every triangle painted with its group's filament, plus
    standard material colours so plain 3MF viewers show the colours too."""
    faces = np.asarray(mesh.faces, dtype=np.int64)
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    if len(face_group) != len(faces):
        raise ValueError("One group per triangle, please.")
    codes = [paint_code(i + 1) for i in range(len(palette))]
    out = io.StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    out.write(f'<model unit="millimeter" xml:lang="en-US" xmlns="{CORE_NS}" xmlns:slic3rpe="{PRUSA_NS}">\n')
    out.write(f" <metadata name=\"Application\">MeshRight {__version__}</metadata>\n")
    out.write(f" <metadata name=\"Title\">{escape(name)}</metadata>\n")
    out.write(" <resources>\n")
    out.write('  <basematerials id="1">\n')
    for i, (r, g, b) in enumerate(palette):
        out.write(f'   <base name="Colour {i + 1}" displaycolor="#{r:02X}{g:02X}{b:02X}FF"/>\n')
    out.write("  </basematerials>\n")
    out.write(f'  <object id="2" name="{escape(name)}" type="model" pid="1" pindex="0">\n')
    out.write("   <mesh>\n    <vertices>\n")
    out.writelines(f'     <vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>\n' for x, y, z in vertices)
    out.write("    </vertices>\n    <triangles>\n")
    out.writelines(
        f'     <triangle v1="{a}" v2="{b}" v3="{c}" p1="{g}" paint_color="{codes[g]}" slic3rpe:mmu_segmentation="{codes[g]}"/>\n'
        for (a, b, c), g in zip(faces.tolist(), face_group.tolist())
    )
    out.write("    </triangles>\n   </mesh>\n  </object>\n </resources>\n")
    out.write(' <build>\n  <item objectid="2"/>\n </build>\n</model>\n')
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
        ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
        "</Types>\n"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        ' <Relationship Target="/3D/3dmodel.model" Id="rel0"'
        ' Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>\n'
        "</Relationships>\n"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("3D/3dmodel.model", out.getvalue())
    return buffer.getvalue()


def export(mesh: trimesh.Trimesh, colours: int = DEFAULT_COLOURS, name: str = "model") -> tuple[bytes, list[dict]]:
    """The painted 3MF and, for each print colour, its hex value and the
    share of triangles it covers (biggest first)."""
    if not colour.has(mesh):
        raise ValueError("This model has no colours to print. Open a colour scan (PLY, OBJ with texture, GLB) first.")
    face_group, palette = group(mesh, colours)
    counts = np.bincount(face_group, minlength=len(palette))
    summary = [
        {"colour": f"#{r:02x}{g:02x}{b:02x}", "share": round(float(n) / len(face_group), 3)}
        for (r, g, b), n in zip(palette.tolist(), counts.tolist())
    ]
    return write_3mf(mesh, face_group, palette, name), summary


def _lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB (0-255) to CIE Lab (D65), where equal distances look equal."""
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    linear = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    matrix = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])
    xyz = linear @ matrix.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0)
    return np.c_[116.0 * f[:, 1] - 16.0, 500.0 * (f[:, 0] - f[:, 1]), 200.0 * (f[:, 1] - f[:, 2])]


def _majority(corners: np.ndarray) -> np.ndarray:
    """The group most corners of each triangle have (the first corner's
    when all three differ)."""
    a, b, c = corners[:, 0], corners[:, 1], corners[:, 2]
    return np.where((b == c) & (a != b), b, a)


def _smooth(mesh: trimesh.Trimesh, face_group: np.ndarray) -> np.ndarray:
    """A triangle whose neighbours mostly agree on another group joins them."""
    adjacency = mesh.face_adjacency
    if len(adjacency) == 0:
        return face_group
    n = len(face_group)
    groups = int(face_group.max()) + 1
    votes = np.zeros((n, groups), dtype=np.int32)
    np.add.at(votes, (adjacency[:, 0], face_group[adjacency[:, 1]]), 1)
    np.add.at(votes, (adjacency[:, 1], face_group[adjacency[:, 0]]), 1)
    best = votes.argmax(axis=1)
    strong = votes[np.arange(n), best] >= 2
    return np.where(strong, best, face_group)
