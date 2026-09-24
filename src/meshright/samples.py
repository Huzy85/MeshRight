"""Sample models to try MeshRight on, made in code so nothing large ships.

Each one shows a common kind of scan problem and the tool that fixes it.
They are made the same way every time (fixed random seed).
"""

from __future__ import annotations

import numpy as np
import trimesh

SAMPLES = {
    "broken-scan": ("broken_scan.stl", "A scan with holes, a loose crumb and rough spots. Try Clean up."),
    "face-relief": ("face_relief.stl", "An open surface like a face scan. Try Give it thickness."),
    "scan-on-table": ("scan_on_table.stl", "A figure scanned with part of the table. Try Erase an area."),
}


def _roughen(mesh: trimesh.Trimesh, amount: float, rng) -> None:
    mesh.vertices += mesh.vertex_normals * rng.normal(0, amount, (len(mesh.vertices), 1))


def _cut_patch(mesh: trimesh.Trimesh, centre, radius: float) -> None:
    keep = np.linalg.norm(mesh.triangles_center - np.asarray(centre), axis=1) > radius
    mesh.update_faces(keep)
    mesh.remove_unreferenced_vertices()


def broken_scan() -> trimesh.Trimesh:
    rng = np.random.default_rng(1)
    egg = trimesh.creation.icosphere(subdivisions=5)
    egg.vertices *= (32, 30, 42)
    egg.vertices[:, 2] += np.where(egg.vertices[:, 2] > 0, egg.vertices[:, 2] * 0.15, 0)
    _roughen(egg, 0.35, rng)
    _cut_patch(egg, (0, 0, 48), 9)      # a hole the scanner could not see
    _cut_patch(egg, (31, 0, 0), 6)      # and one on the side
    crumb = trimesh.creation.icosphere(subdivisions=1, radius=2.0)
    crumb.apply_translation((45, 12, -20))
    mesh = trimesh.util.concatenate([egg, crumb])
    mesh.apply_translation((0, 0, 6))   # partly below the bed, as scans often are
    return mesh


def face_relief() -> trimesh.Trimesh:
    rng = np.random.default_rng(2)
    n = 90
    x, y = np.meshgrid(np.linspace(-1, 1, n), np.linspace(-1.2, 1.2, n))

    def bump(cx, cy, sx, sy, h):
        return h * np.exp(-(((x - cx) / sx) ** 2 + ((y - cy) / sy) ** 2))

    z = (bump(0, 0, 0.8, 1.0, 22)            # the face
         + bump(0, 0.05, 0.12, 0.35, 12)      # nose
         - bump(-0.33, 0.35, 0.16, 0.1, 5)    # eyes
         - bump(0.33, 0.35, 0.16, 0.1, 5)
         + bump(0, -0.5, 0.3, 0.08, 3)        # lips
         + rng.normal(0, 0.15, x.shape))
    vertices = np.column_stack([x.ravel() * 60, y.ravel() * 60, z.ravel()])
    faces = []
    for i in range(n - 1):
        for j in range(n - 1):
            a = i * n + j
            faces += [[a, a + 1, a + n], [a + 1, a + n + 1, a + n]]
    mesh = trimesh.Trimesh(vertices, np.array(faces))
    # Keep an oval, the way a face scan is trimmed.
    keep = (mesh.triangles_center[:, 0] / 55) ** 2 + (mesh.triangles_center[:, 1] / 70) ** 2 < 1
    mesh.update_faces(keep)
    mesh.remove_unreferenced_vertices()
    # Stand it up, facing forwards.
    mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, (1, 0, 0)))
    mesh.apply_translation((0, 0, -mesh.bounds[0][2]))
    return mesh


def scan_on_table() -> trimesh.Trimesh:
    rng = np.random.default_rng(3)
    body = trimesh.creation.capsule(height=50, radius=14, count=(32, 32))
    body.apply_translation((0, 0, 39))
    head = trimesh.creation.icosphere(subdivisions=3, radius=12)
    head.apply_translation((0, 0, 88))
    figure = trimesh.boolean.union([body, head], engine="manifold")
    _roughen(figure, 0.25, rng)
    # The turntable: a thin, open, one-sided disc under the figure.
    angles = np.linspace(0, 2 * np.pi, 64, endpoint=False)
    rim = np.column_stack([np.cos(angles) * 70, np.sin(angles) * 70, np.zeros(64)])
    vertices = np.vstack([[0, 0, 0], rim])
    faces = np.array([[0, k + 1, (k + 1) % 64 + 1] for k in range(64)])
    table = trimesh.Trimesh(vertices, faces).subdivide().subdivide()
    table.vertices[:, 2] += rng.normal(0, 0.2, len(table.vertices))
    return trimesh.util.concatenate([figure, table])


MAKERS = {"broken-scan": broken_scan, "face-relief": face_relief, "scan-on-table": scan_on_table}


def make(name: str) -> tuple[str, bytes]:
    """The sample's file name and STL bytes."""
    filename, _ = SAMPLES[name]
    return filename, MAKERS[name]().export(file_type="stl")
