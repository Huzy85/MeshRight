"""Point clouds: files of scanned dots rather than a surface.

The points are cleaned of strays, then a closed surface is built around them
with the same voxel method as Make Solid. Simple and dependable; fine detail
below the point spacing is smoothed.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

POINT_EXTENSIONS = (".xyz", ".asc", ".pts")

# A point is a stray when its neighbours are this many standard deviations
# further away than usual.
OUTLIER_STD = 2.5
OUTLIER_NEIGHBOURS = 12


def read_text_points(path: str | Path) -> np.ndarray:
    """XYZ, ASC and PTS files: one point per line, x y z first (extra
    columns such as colour are ignored, header lines are skipped)."""
    rows = []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            parts = line.replace(",", " ").split()
            if len(parts) < 3:
                continue
            try:
                rows.append((float(parts[0]), float(parts[1]), float(parts[2])))
            except ValueError:
                continue
    points = np.asarray(rows, dtype=np.float64).reshape(-1, 3)
    return points[np.all(np.isfinite(points), axis=1)]


def remove_strays(points: np.ndarray) -> tuple[np.ndarray, int]:
    from scipy.spatial import cKDTree

    if len(points) < OUTLIER_NEIGHBOURS * 2:
        return points, 0
    distances, _ = cKDTree(points).query(points, k=OUTLIER_NEIGHBOURS + 1)
    mean = distances[:, 1:].mean(axis=1)
    keep = mean <= mean.mean() + OUTLIER_STD * mean.std()
    return points[keep], int((~keep).sum())


def points_to_mesh(points: np.ndarray) -> tuple[trimesh.Trimesh, str]:
    """Build a closed, printable surface around a cloud of points."""
    from scipy import ndimage
    from scipy.spatial import cKDTree

    from .repair import grid_to_mesh

    if len(points) < 100:
        raise ValueError("There are too few points to build a surface from.")
    points, strays = remove_strays(points)
    sample = points[np.random.default_rng(0).choice(len(points), min(len(points), 20000), replace=False)]
    spacing = float(np.median(cKDTree(points).query(sample, k=2)[0][:, 1]))
    span = float(np.ptp(points, axis=0).max())
    pitch = max(spacing * 1.5, span / 300, 1e-6)

    pad = 4
    origin = points.min(axis=0) - pad * pitch
    index = np.floor((points - origin) / pitch).astype(np.int64)
    grid = np.zeros(index.max(axis=0) + pad + 1, dtype=bool)
    grid[tuple(index.T)] = True
    # Close the gaps between neighbouring points a little more each try until
    # the inside can be filled (the scan goes all the way round), then shrink
    # back by the same amount so the size stays true. A one-sided scan never
    # fills and is kept as a thin shell.
    surface = grid
    for grow in range(2, 7):
        grown = ndimage.binary_dilation(surface, iterations=grow)
        filled = ndimage.binary_fill_holes(grown)
        if filled.sum() - grown.sum() > 2 * surface.sum():
            grid = ndimage.binary_erosion(filled, iterations=grow)
            break
    else:
        grid = ndimage.binary_dilation(surface, iterations=2)

    # Stray points that survived the filter become tiny separate lumps: drop
    # anything much smaller than the main shape.
    labels, count = ndimage.label(grid)
    if count > 1:
        sizes = np.bincount(labels.ravel())
        sizes[0] = 0
        grid = sizes[labels] >= 0.01 * sizes.max()

    mesh = grid_to_mesh(grid, origin, pitch)
    note = f"Built a surface from {len(points):,} points"
    if strays:
        note += f" (removed {strays:,} stray points)"
    return mesh, note
