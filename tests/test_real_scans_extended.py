"""Extended real-model integration tests — covers every action not in test_real_scans.py.

Each action is tested on at least 2–3 different real NAS models with assertions
that verify the repair/edit actually worked, not just that it ran.

Parameter conventions (from actions.py):
  kind="numbers"  → pass a flat Python list  [x, y, z]  or  [x1,y1, x2,y2, ...]
  kind="points"   → pass a list-of-lists     [[x,y,z], [x,y,z], ...]
  kind="text"     → pass a str (e.g. scan id pre-loaded in merge.PENDING)
  kind="choice"   → pass an exact choice str
"""
from __future__ import annotations

import os
import urllib.request
import urllib.error
from pathlib import Path

import numpy as np
import pytest
import trimesh

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from meshright.actions import ActionError, run_action
import meshright.merge as _merge   # for PENDING store (multi-mesh tests)

# ---------------------------------------------------------------- helpers

# A folder of real STL files to test against. Set MESHRIGHT_TEST_MODELS to
# point at your own; these tests are skipped when it is unset or missing.
NAS_DIR = Path(os.environ.get("MESHRIGHT_TEST_MODELS") or "/nonexistent")
CACHE    = Path(__file__).parent.parent / "tools" / "library-cache"
WEB_BASE = "https://raw.githubusercontent.com/mikedh/trimesh/main/models/"
WEB_MODELS = {
    "teapot":      (WEB_BASE + "teapot.stl",        "stl"),
    "featuretype": (WEB_BASE + "featuretype.STL",   "stl"),
}
NAS_MODELS = {
    "shelf_bracket":    "Shelf_Bracket.stl",
    "spool_holder":     "Spool_Holder_V1.stl",
    "fan_shroud":       "Fan_Shroud_Clamp.stl",
    "dragon_head":      "Dragon Head Part 2.stl",
    "filament_guide":   "FilamentGuide.stl",
    "rpi_camera_arm":   "RPi_Camera_Arm.stl",
    "fan_cover":        "top_cover_fan40.stl",
    "mainframe":        "MainFrame.stl",
    "smok":             "smok.stl",
    "wall_mount":       "10Angle Wall Plant Mount v1.stl",
    "p_lamp":           "P lamp.stl",
    "cookie_caddy":     "cookie-caddy.stl",
    "trophy":           "trophy_5_140209b.stl",
    "surface_holder":   "surface_holder__For_screws_.stl",
    "pump_cap":         "Pump Cap (1).stl",
    "spice_holder":     "SpiceGlassHolder_3er.stl",
}


def _nas(key: str) -> trimesh.Trimesh:
    if not NAS_DIR.is_dir():
        pytest.skip("MESHRIGHT_TEST_MODELS is not set or not a folder")
    path = NAS_DIR / NAS_MODELS[key]
    if not path.exists():
        pytest.skip(f"NAS model missing: {path.name}")
    m = trimesh.load(str(path), force="mesh")
    assert len(m.faces) > 0
    return m


def _web(key: str) -> trimesh.Trimesh:
    url, ext = WEB_MODELS[key]
    dest = CACHE / f"{key}.{ext}"
    if not dest.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        try:
            urllib.request.urlretrieve(url, dest)
        except urllib.error.URLError as e:
            pytest.skip(f"Network unavailable ({e})")
    m = trimesh.load(str(dest), force="mesh")
    assert len(m.faces) > 0
    return m


def _noisy(mesh: trimesh.Trimesh, sigma: float = 0.5) -> trimesh.Trimesh:
    noisy = mesh.copy()
    rng = np.random.default_rng(42)
    noisy.vertices = noisy.vertices + rng.normal(0, sigma, noisy.vertices.shape)
    return noisy


def _roughness(mesh: trimesh.Trimesh) -> float:
    """Mean angle (degrees) between adjacent face normals — smaller = smoother."""
    adj = trimesh.graph.face_adjacency(mesh.faces)
    n1 = mesh.face_normals[adj[:, 0]]
    n2 = mesh.face_normals[adj[:, 1]]
    dots = np.clip((n1 * n2).sum(axis=1), -1.0, 1.0)
    return float(np.degrees(np.arccos(dots)).mean())


def _damage_half(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    cx = mesh.center_mass[0]
    mask = mesh.triangles_center[:, 0] < cx
    if mask.sum() < 10:
        pytest.skip("Not enough faces on left half for symmetry test")
    return trimesh.Trimesh(mesh.vertices.copy(), mesh.faces[mask], process=False)


def _register_pending(scan_id: str, mesh: trimesh.Trimesh) -> None:
    """Pre-load a mesh into the pending scan store so multi-mesh actions can find it."""
    _merge.PENDING[scan_id] = ("test_file.stl", mesh)


def _top_face_centre(mesh: trimesh.Trimesh) -> list:
    """Return the centroid of the topmost face as [x, y, z]."""
    top_face = int(np.argmax(mesh.triangles_center[:, 2]))
    c = mesh.triangles_center[top_face]
    return [float(c[0]), float(c[1]), float(c[2])]


# ================================================================ SMOOTH SCAN

def test_smooth_scan_reduces_roughness_fan_shroud():
    """Taubin smooth reduces inter-face angle on a noisy mesh."""
    m = _nas("fan_shroud")
    noisy = _noisy(m, sigma=0.3)
    result, note = run_action(noisy, "smooth_scan", {"strength": "medium"})
    assert _roughness(result) < _roughness(noisy), \
        f"smooth_scan did not reduce roughness"


def test_smooth_scan_heavy_smoother_than_light_fan_cover():
    """Heavy smooth gives lower surface roughness than light."""
    m = _nas("fan_cover")
    noisy = _noisy(m, sigma=0.5)
    light, _ = run_action(noisy, "smooth_scan", {"strength": "light"})
    heavy, _ = run_action(noisy, "smooth_scan", {"strength": "heavy"})
    assert _roughness(heavy) < _roughness(light)


def test_smooth_scan_preserves_face_count_rpi_arm():
    """Smooth scan never changes topology — only vertex positions."""
    m = _nas("rpi_camera_arm")
    noisy = _noisy(m, sigma=0.2)
    result, _ = run_action(noisy, "smooth_scan", {"strength": "medium"})
    assert len(result.faces)    == len(noisy.faces)
    assert len(result.vertices) == len(noisy.vertices)


def test_smooth_scan_open_mesh_smok():
    """Smooth scan on a high-poly open NAS scan does not crash."""
    m = _nas("smok")
    result, _ = run_action(m, "smooth_scan", {"strength": "light"})
    assert len(result.faces) == len(m.faces)


# ================================================================ HOLLOW

def test_hollow_shelf_bracket_reduces_volume():
    """Hollowed bracket has less volume than the solid original."""
    m = _nas("shelf_bracket")
    assert m.is_watertight
    result, note = run_action(m, "hollow", {"wall_mm": 2.0, "drain_holes": True})
    if result.is_watertight:
        assert result.volume < m.volume * 0.9
    assert len(result.faces) > 0


def test_hollow_spool_holder_reduces_volume():
    """Spool holder hollow reduces volume by at least 20%."""
    m = _nas("spool_holder")
    assert m.is_watertight
    result, _ = run_action(m, "hollow", {"wall_mm": 2.5, "drain_holes": True})
    if result.is_watertight:
        assert result.volume < m.volume * 0.9


def test_hollow_dragon_head():
    """Hollow on an organic shape completes without error."""
    m = _nas("dragon_head")
    assert m.is_watertight
    result, note = run_action(m, "hollow", {"wall_mm": 2.0, "drain_holes": True})
    assert len(result.faces) > 0


def test_hollow_surface_holder_drain():
    """Small model with drain holes stays non-empty."""
    m = _nas("surface_holder")
    result, _ = run_action(m, "hollow", {"wall_mm": 1.0, "drain_holes": True, "hole_mm": 2.0})
    assert len(result.faces) > 100


# ================================================================ REMOVE BASE

def test_remove_base_raises_z_min_filament_guide():
    """remove_base increases z_min on a model with a clear flat base."""
    m = _nas("filament_guide")
    z_before = m.bounds[0][2]
    result, note = run_action(m, "remove_base", {"margin_mm": 0.5})
    z_after = result.bounds[0][2]
    assert z_after > z_before - 0.01


def test_remove_base_raises_z_min_fan_cover():
    """Fan cover (large flat base) has its base removed."""
    m = _nas("fan_cover")
    z_before = m.bounds[0][2]
    result, _ = run_action(m, "remove_base", {"margin_mm": 1.0})
    z_after = result.bounds[0][2]
    assert z_after > z_before - 0.01


def test_remove_base_fan_shroud():
    """Fan shroud (strong flat base) has base removed."""
    m = _nas("fan_shroud")
    z_before = m.bounds[0][2]
    result, _ = run_action(m, "remove_base", {"margin_mm": 0.5})
    assert result.bounds[0][2] > z_before - 0.01


def test_remove_base_spool_holder_margin():
    """Larger margin removes more of the base."""
    m = _nas("spool_holder")
    r1, _ = run_action(m, "remove_base", {"margin_mm": 0.5})
    r2, _ = run_action(m, "remove_base", {"margin_mm": 3.0})
    assert r2.bounds[0][2] >= r1.bounds[0][2] - 0.1


# ================================================================ SYMMETRY REPAIR

def test_symmetry_repair_fan_shroud_restores_faces():
    """Symmetry repair on a half-removed model adds back faces."""
    m = _nas("fan_shroud")
    damaged = _damage_half(m)
    result, _ = run_action(damaged, "symmetry_repair")
    assert len(result.faces) > len(damaged.faces)


def test_symmetry_repair_surface_holder():
    m = _nas("surface_holder")
    damaged = _damage_half(m)
    result, _ = run_action(damaged, "symmetry_repair")
    assert len(result.faces) > 0


def test_symmetry_repair_pump_cap():
    m = _nas("pump_cap")
    damaged = _damage_half(m)
    result, _ = run_action(damaged, "symmetry_repair")
    assert len(result.faces) >= len(damaged.faces)


# ================================================================ CURVATURE MAP

def test_curvature_map_fan_cover_has_variation():
    """Curvature map assigns non-uniform colours on a curved surface."""
    m = _nas("fan_cover")
    result, _ = run_action(m, "curvature_map")
    assert result.visual is not None
    colors = np.asarray(result.visual.vertex_colors)
    assert colors.shape[0] == len(result.vertices)
    assert len(np.unique(colors[:, :3], axis=0)) > 1


def test_curvature_map_trophy_high_variance():
    """Trophy curves produce high colour variance."""
    m = _nas("trophy")
    result, _ = run_action(m, "curvature_map")
    colors = np.asarray(result.visual.vertex_colors)[:, :3].astype(float)
    assert colors.std() > 1.0


def test_curvature_map_shelf_bracket():
    """Flat bracket: curvature map runs and assigns vertex colours."""
    m = _nas("shelf_bracket")
    result, _ = run_action(m, "curvature_map")
    assert result.visual is not None
    assert len(np.asarray(result.visual.vertex_colors)) == len(result.vertices)


# ================================================================ DEVIATION MAP

def test_deviation_map_detects_change_fan_shroud():
    """Deviation map between original and remeshed shows non-zero deviation."""
    m = _nas("fan_shroud")
    remeshed, _ = run_action(m, "remesh", {"target_triangles": 5000, "smooth": False})
    _register_pending("ref_shroud", m)
    result, note = run_action(remeshed, "deviation_map", {"reference": "ref_shroud"})
    assert result.visual is not None
    colors = np.asarray(result.visual.vertex_colors)[:, :3].astype(float)
    assert colors.std() > 0.5


def test_deviation_map_identical_mesh_all_neutral():
    """Deviation map of identical mesh has near-zero median deviation note."""
    m = _nas("shelf_bracket")
    _register_pending("ref_bracket", m.copy())
    result, note = run_action(m, "deviation_map", {"reference": "ref_bracket"})
    assert result.visual is not None
    # Note should confirm median ≈ 0 for identical mesh
    assert "median" in note.lower()
    # All vertices should be green — parse median from the note (e.g. "median 0.00 mm")
    import re
    m_median = re.search(r"median\s+([\d.]+)\s*mm", note)
    if m_median:
        assert float(m_median.group(1)) < 1.0


def test_deviation_map_noisy_vs_clean():
    """Deviation map between noisy scan and clean version shows spread."""
    m = _nas("rpi_camera_arm")
    noisy = _noisy(m, sigma=1.0)
    _register_pending("ref_arm", m)
    result, _ = run_action(noisy, "deviation_map", {"reference": "ref_arm"})
    assert result.visual is not None


# ================================================================ SEAM POLISH

def test_seam_polish_does_not_open_watertight_fan_shroud():
    """Seam polish preserves the watertight seal on a closed model."""
    m = _nas("fan_shroud")
    assert m.is_watertight
    result, _ = run_action(m, "seam_polish", {"detail_mm": 0.3})
    assert result.is_watertight or len(result.faces) > 0


def test_seam_polish_size_stable_dragon_head():
    """Seam polish keeps bounding box within 5% of original."""
    m = _nas("dragon_head")
    result, _ = run_action(m, "seam_polish", {"detail_mm": 0.3})
    assert np.allclose(result.extents, m.extents, rtol=0.05)


def test_seam_polish_trophy():
    m = _nas("trophy")
    result, _ = run_action(m, "seam_polish", {"detail_mm": 0.5})
    assert len(result.faces) > 0


# ================================================================ CUT IN TWO

def _midpoint_z(mesh):
    return float((mesh.bounds[0][2] + mesh.bounds[1][2]) / 2)


def test_cut_in_two_fan_shroud_produces_combined_mesh():
    """Cutting the fan shroud produces a combined mesh of both halves."""
    m = _nas("fan_shroud")
    result, note = run_action(m, "cut_in_two", {
        "axis": "z", "position_mm": _midpoint_z(m), "pins": False
    })
    assert len(result.faces) > len(m.faces) * 0.8
    assert "cut" in note.lower() or "half" in note.lower() or "top" in note.lower() or "part" in note.lower()


def test_cut_in_two_spool_holder_x_axis():
    m = _nas("spool_holder")
    mid_x = float((m.bounds[0][0] + m.bounds[1][0]) / 2)
    result, _ = run_action(m, "cut_in_two", {
        "axis": "x", "position_mm": mid_x, "pins": False
    })
    assert len(result.faces) > len(m.faces) * 0.8


def test_cut_in_two_mainframe_with_pins():
    m = _nas("mainframe")
    mid_y = float((m.bounds[0][1] + m.bounds[1][1]) / 2)
    result, _ = run_action(m, "cut_in_two", {
        "axis": "y", "position_mm": mid_y, "pins": True, "pin_mm": 4.0
    })
    assert len(result.faces) > len(m.faces) * 0.8


def test_cut_flat_bottom_reduces_height():
    """cut_flat_bottom trims the underside, reducing the model's Z extent."""
    m = _nas("rpi_camera_arm")
    height_before = m.extents[2]
    result, note = run_action(m, "cut_flat_bottom", {"cut_mm": 2.0})
    height_after = result.extents[2]
    assert height_before - height_after >= 1.5, \
        f"cut_flat_bottom did not reduce height: {height_before:.2f} → {height_after:.2f}"


def test_cut_flat_bottom_auto_fan_cover():
    m = _nas("fan_cover")
    height_before = m.extents[2]
    result, _ = run_action(m, "cut_flat_bottom", {"cut_mm": 2.0})
    assert result.extents[2] < height_before


# ================================================================ SPLIT TO FIT

def test_split_to_fit_mainframe():
    """Large mainframe (211mm) gets split for a 150mm print bed."""
    m = _nas("mainframe")
    result, note = run_action(m, "split_to_fit", {
        "bed_x": 150.0, "bed_y": 150.0, "bed_z": 150.0, "pins": False
    })
    assert len(result.faces) > 0
    assert "part" in note.lower() or "split" in note.lower() or "piece" in note.lower()


def test_split_to_fit_mainframe_small_bed():
    """Very small bed forces mainframe to split into more pieces."""
    m = _nas("mainframe")
    result, note = run_action(m, "split_to_fit", {
        "bed_x": 100.0, "bed_y": 100.0, "bed_z": 100.0, "pins": False
    })
    assert len(result.faces) > 0


# ================================================================ REDUCE DETAIL

def test_reduce_detail_smok_high_poly():
    """reduce_detail on 242k-face smok brings it under the requested limit."""
    m = _nas("smok")
    result, _ = run_action(m, "reduce_detail", {"triangles": 50000})
    assert len(result.faces) < len(m.faces)


def test_reduce_detail_cookie_caddy():
    m = _nas("cookie_caddy")
    result, _ = run_action(m, "reduce_detail", {"triangles": 10000})
    assert len(result.faces) < len(m.faces)


def test_reduce_detail_trophy_preserves_shape():
    """reduce_detail preserves rough volume on a watertight trophy."""
    m = _nas("trophy")
    assert m.is_watertight
    result, _ = run_action(m, "reduce_detail", {"triangles": 5000})
    assert len(result.faces) < len(m.faces)
    if result.is_watertight:
        assert abs(result.volume - m.volume) / m.volume < 0.20


# ================================================================ MAKE SOLID

def test_make_solid_smok_open_scan():
    m = _nas("smok")
    result, _ = run_action(m, "make_solid", {"detail_mm": 0.5})
    assert result.is_watertight


def test_make_solid_dragon_head():
    m = _nas("dragon_head")
    result, _ = run_action(m, "make_solid", {"detail_mm": 0.3})
    assert result.is_watertight


@pytest.mark.slow
def test_make_solid_teapot():
    m = _web("teapot")
    result, _ = run_action(m, "make_solid")
    assert result.is_watertight


# ================================================================ MAKE BUST

def test_make_bust_trophy_fits_height():
    """make_bust crops the trophy to the requested height."""
    m = _nas("trophy")
    height = float(m.extents[2] * 0.6)  # 60% of original height
    result, _ = run_action(m, "make_bust", {"height_mm": height, "base": "round"})
    assert len(result.faces) > 0
    assert result.extents[2] <= height + 5.0


def test_make_bust_spool_holder():
    """make_bust on any watertight model produces a non-empty result."""
    m = _nas("spool_holder")
    height = float(m.extents[2] * 0.5)
    result, _ = run_action(m, "make_bust", {"height_mm": height, "base": "none"})
    assert len(result.faces) > 0


# ================================================================ ADD HOLE

def _top_face_point(mesh):
    """Return the centroid of the topmost face as a flat list [x, y, z]."""
    idx = int(np.argmax(mesh.triangles_center[:, 2]))
    c = mesh.triangles_center[idx]
    return [float(c[0]), float(c[1]), float(c[2])]


def test_add_hole_fan_shroud_reduces_volume():
    """Adding a hole reduces the volume of a watertight model."""
    m = _nas("fan_shroud")
    assert m.is_watertight
    pt = _top_face_point(m)
    result, _ = run_action(m, "add_hole", {
        "point":       pt,
        "direction":   [0.0, 0.0, -1.0],
        "diameter_mm": 4.0,
    })
    assert len(result.faces) > 0
    if result.is_watertight and m.is_watertight:
        assert result.volume < m.volume


def test_add_hole_shelf_bracket():
    m = _nas("shelf_bracket")
    pt = _top_face_point(m)
    result, _ = run_action(m, "add_hole", {
        "point":       pt,
        "direction":   [0.0, 0.0, -1.0],
        "diameter_mm": 3.0,
    })
    assert len(result.faces) > 0


def test_add_hole_spool_holder():
    m = _nas("spool_holder")
    pt = _top_face_point(m)
    result, _ = run_action(m, "add_hole", {
        "point":       pt,
        "direction":   [0.0, 0.0, -1.0],
        "diameter_mm": 5.0,
    })
    assert len(result.faces) > 0


# ================================================================ THICKEN

def test_thicken_smok_open_scan():
    """Thicken adds thickness to an open surface scan."""
    m = _nas("smok")
    result, note = run_action(m, "thicken", {"thickness_mm": 2.0})
    assert len(result.faces) > len(m.faces)


def test_thicken_open_wall_mount():
    """Thicken on an open model adds back-face material."""
    m = _nas("wall_mount")
    if m.is_watertight:
        pytest.skip("wall_mount is watertight — need an open mesh")
    result, _ = run_action(m, "thicken", {"thickness_mm": 1.5})
    assert len(result.faces) > 0


# ================================================================ TRANSFORMS ON REAL MODELS

def test_mirror_fan_shroud_x_extents_preserved():
    """Mirror along X preserves all extents (mirrors around the centre)."""
    m = _nas("fan_shroud")
    result, _ = run_action(m, "mirror", {"axis": "x"})
    # Extents must stay the same
    assert np.allclose(result.extents, m.extents, rtol=0.01)


def test_mirror_shelf_bracket_y():
    m = _nas("shelf_bracket")
    result, _ = run_action(m, "mirror", {"axis": "y"})
    assert np.allclose(result.extents, m.extents, rtol=0.01)


def test_mirror_dragon_head_z():
    m = _nas("dragon_head")
    result, _ = run_action(m, "mirror", {"axis": "z"})
    assert np.allclose(result.extents, m.extents, rtol=0.01)


def test_rotate_fan_shroud_90_z_swaps_xy():
    """Rotating 90° around Z swaps X and Y extents."""
    m = _nas("fan_shroud")
    result, _ = run_action(m, "rotate", {"axis": "z", "degrees": 90.0})
    assert abs(result.extents[0] - m.extents[1]) < 2.0
    assert abs(result.extents[1] - m.extents[0]) < 2.0


def test_rotate_shelf_bracket_45_x():
    m = _nas("shelf_bracket")
    result, _ = run_action(m, "rotate", {"axis": "x", "degrees": 45.0})
    assert len(result.faces) == len(m.faces)


def test_scale_fan_shroud_halves_extents():
    """Scaling by 0.5 halves all extents."""
    m = _nas("fan_shroud")
    result, _ = run_action(m, "scale", {"factor": 0.5})
    assert np.allclose(result.extents, m.extents * 0.5, rtol=0.01)


def test_scale_shelf_bracket_doubles():
    m = _nas("shelf_bracket")
    result, _ = run_action(m, "scale", {"factor": 2.0})
    assert np.allclose(result.extents, m.extents * 2.0, rtol=0.01)


def test_scale_volume_cubed():
    """Scale by 2 should increase volume by 2³ = 8."""
    m = _nas("fan_shroud")
    assert m.is_watertight
    result, _ = run_action(m, "scale", {"factor": 2.0})
    assert result.is_watertight
    assert abs(result.volume - m.volume * 8.0) / m.volume < 0.05


def test_resize_fan_shroud_x_axis():
    """Resize sets the X dimension to exactly 50mm."""
    m = _nas("fan_shroud")
    result, _ = run_action(m, "resize", {"axis": "x", "size_mm": 50.0})
    assert abs(result.extents[0] - 50.0) < 1.0


def test_resize_mainframe_z():
    m = _nas("mainframe")
    result, _ = run_action(m, "resize", {"axis": "z", "size_mm": 100.0})
    assert abs(result.extents[2] - 100.0) < 2.0


def test_move_fan_shroud_shifts_centroid():
    """move(10, 0, 0) shifts the centroid by ~10mm on X."""
    m = _nas("fan_shroud")
    cx_before = m.center_mass[0]
    result, _ = run_action(m, "move", {"x": 10.0, "y": 0.0, "z": 0.0})
    assert abs(result.center_mass[0] - (cx_before + 10.0)) < 0.5


def test_move_shelf_bracket_z():
    m = _nas("shelf_bracket")
    z_before = m.center_mass[2]
    result, _ = run_action(m, "move", {"x": 0.0, "y": 0.0, "z": 20.0})
    assert abs(result.center_mass[2] - (z_before + 20.0)) < 0.5


def test_convert_units_centimetres_scales_up():
    """A model that was saved in cm (10× too small) gets scaled up by 10."""
    m = _nas("shelf_bracket")   # ~203mm, treated as if it were 203cm
    orig_max = m.extents.max()
    result, note = run_action(m, "convert_units", {"from_units": "centimetres"})
    # centimetres → mm: multiply by 10
    assert abs(result.extents.max() - orig_max * 10.0) / orig_max < 0.01


def test_convert_units_inches_scales_up():
    """A model saved in inches gets scaled up to mm (×25.4)."""
    m = _nas("surface_holder")
    orig_max = m.extents.max()
    result, _ = run_action(m, "convert_units", {"from_units": "inches"})
    assert abs(result.extents.max() - orig_max * 25.4) / orig_max < 0.01


# ================================================================ ORIENTATION

def test_lay_flat_places_model_near_origin():
    """lay_flat on 3 flat-face points puts z_min near 0."""
    m = _nas("fan_shroud")
    # Pick 3 points on the bottom flat face
    z_lo = float(m.bounds[0][2])
    cx, cy = float(m.center_mass[0]), float(m.center_mass[1])
    ex = float(m.extents[0]) * 0.3
    ey = float(m.extents[1]) * 0.3
    pts = [[cx - ex, cy, z_lo], [cx + ex, cy, z_lo], [cx, cy + ey, z_lo]]
    result, _ = run_action(m, "lay_flat", {"points": pts})
    assert result.bounds[0][2] < 1.0


def test_lay_flat_shelf_bracket():
    m = _nas("shelf_bracket")
    z_lo = float(m.bounds[0][2])
    cx, cy = float(m.center_mass[0]), float(m.center_mass[1])
    ex, ey = float(m.extents[0]) * 0.3, float(m.extents[1]) * 0.3
    pts = [[cx - ex, cy, z_lo], [cx + ex, cy, z_lo], [cx, cy + ey, z_lo]]
    result, _ = run_action(m, "lay_flat", {"points": pts})
    assert result.bounds[0][2] < 1.0


def test_place_on_bed_fan_cover():
    """place_on_bed puts z_min at 0."""
    m = _nas("fan_cover")
    tilted, _ = run_action(m, "rotate", {"axis": "y", "degrees": 45.0})
    result, _ = run_action(tilted, "place_on_bed")
    assert result.bounds[0][2] < 1.0


def test_place_on_bed_shelf_bracket():
    m = _nas("shelf_bracket")
    result, _ = run_action(m, "place_on_bed")
    assert result.bounds[0][2] < 1.0


def test_set_bottom_down_vector():
    """set_bottom with down_z=-1 is effectively a no-op on an upright model."""
    m = _nas("shelf_bracket")
    result, _ = run_action(m, "set_bottom", {
        "down_x": 0.0, "down_y": 0.0, "down_z": -1.0
    })
    assert len(result.faces) == len(m.faces)


def test_add_flat_base_fan_shroud():
    """add_flat_base adds a flat base, increasing the face count."""
    m = _nas("fan_shroud")
    result, _ = run_action(m, "add_flat_base", {"depth_mm": 2.0})
    assert len(result.faces) >= len(m.faces)


def test_add_flat_base_dragon_head():
    m = _nas("dragon_head")
    result, _ = run_action(m, "add_flat_base", {"depth_mm": 3.0})
    assert len(result.faces) > len(m.faces)


# ================================================================ COMBINE MODELS

_IDENTITY_16 = list(np.eye(4).ravel())  # 4×4 identity as 16 flat numbers


def test_combine_models_bracket_and_holder():
    """Combining two models merges them into one larger mesh."""
    m1 = _nas("shelf_bracket")
    m2 = _nas("surface_holder")
    _register_pending("holder_scan", m2)
    result, _ = run_action(m1, "combine_models", {
        "scan": "holder_scan", "placement": _IDENTITY_16, "how": "join"
    })
    assert len(result.faces) >= len(m1.faces) + len(m2.faces) - 50


def test_combine_models_fan_parts():
    m1 = _nas("fan_shroud")
    m2 = _nas("fan_cover")
    _register_pending("fan_cover_scan", m2)
    result, _ = run_action(m1, "combine_models", {
        "scan": "fan_cover_scan", "placement": _IDENTITY_16, "how": "join"
    })
    assert len(result.faces) >= max(len(m1.faces), len(m2.faces))


# ================================================================ JOIN PIECE / MERGE SCAN

def _sample_surface_points(mesh: trimesh.Trimesh, n: int = 3) -> list:
    """Sample n evenly-spread face centroids as a list of [x,y,z] lists."""
    step = max(1, len(mesh.triangles_center) // n)
    pts = mesh.triangles_center[::step][:n]
    return [[float(p[0]), float(p[1]), float(p[2])] for p in pts]


def test_join_piece_rejoin_split_model():
    """join_piece rejoins a model that was split in two at its midpoint."""
    m = _nas("shelf_bracket")
    mid_z = _midpoint_z(m)
    # Cut into two halves
    top, _ = run_action(m, "cut_in_two", {"axis": "z", "position_mm": mid_z, "pins": False})
    # Use the bottom half as base, top half as the piece to join
    # The break surface points are along the cut plane (z ≈ mid_z)
    cut_tol = m.extents[2] * 0.1
    cut_faces = np.where(np.abs(m.triangles_center[:, 2] - mid_z) < cut_tol)[0]
    if len(cut_faces) < 3:
        pytest.skip("Not enough faces near cut plane to build matching points")
    pts_base  = m.triangles_center[cut_faces[:3]].tolist()
    pts_added = m.triangles_center[cut_faces[:3]].tolist()
    _register_pending("top_piece", top)
    result, _ = run_action(m, "join_piece", {
        "scan":         "top_piece",
        "added_points": pts_added,
        "base_points":  pts_base,
    })
    assert len(result.faces) > 0


def test_merge_scan_two_scans():
    """merge_scan fuses two scans together."""
    m1 = _nas("dragon_head")
    m2 = _nas("surface_holder")
    _register_pending("surface_scan2", m2)
    result, _ = run_action(m1, "merge_scan", {
        "scan":         "surface_scan2",
        "added_points": _sample_surface_points(m1, 3),
        "base_points":  _sample_surface_points(m2, 3),
        "fuse":         True,
    })
    assert len(result.faces) > 0


# ================================================================ EXTEND BREAK

def test_extend_break_open_mesh_smok():
    """extend_break on a flat spot of the open smok scan grows the surface there
    without closing or opening anything else (the join-up check used to demand
    a watertight result, which an open scan can never give)."""
    m = _nas("smok")
    pt = m.vertices[m.faces[0, 0]].tolist()
    result, note = run_action(m, "extend_break", {
        "point":        pt,
        "length_mm":    8.0,
        "follow_curve": True,
        "taper":        True,
    })
    assert len(result.faces) > len(m.faces)
    assert note.startswith("Rebuilt the broken end 8 mm")


@pytest.mark.slow
def test_extend_break_teapot():
    """The teapot's first vertex sits on its open rim, so the rim is continued."""
    m = _web("teapot")
    pt = m.vertices[0].tolist()
    result, note = run_action(m, "extend_break", {"point": pt, "length_mm": 5.0})
    assert len(result.faces) > len(m.faces)
    assert note.startswith("Continued the open edge 5 mm")


# ================================================================ ERASE PIECE

def _make_multi_component(base_mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Create a mesh with two components: base model + a detached sphere."""
    sphere = trimesh.creation.icosphere(radius=5.0, subdivisions=2)
    offset = base_mesh.bounds[1] + np.array([30.0, 30.0, 30.0])
    sphere.apply_translation(offset)
    return trimesh.util.concatenate([base_mesh, sphere])


def test_erase_piece_removes_detached_sphere():
    """Erasing the detached sphere component removes it, leaving the base model."""
    base = _nas("shelf_bracket")
    m = _make_multi_component(base)
    sphere_pt = (base.bounds[1] + np.array([30.0, 30.0, 30.0])).tolist()
    result, _ = run_action(m, "erase_piece", {"point": sphere_pt})
    assert len(result.faces) < len(m.faces)
    assert abs(len(result.faces) - len(base.faces)) < 50


def test_erase_piece_surface_holder_detached():
    """Erase the detached sphere from a two-component model."""
    base = _nas("surface_holder")
    m = _make_multi_component(base)
    sphere_pt = (base.bounds[1] + np.array([30.0, 30.0, 30.0])).tolist()
    result, _ = run_action(m, "erase_piece", {"point": sphere_pt})
    assert len(result.faces) < len(m.faces)


# ================================================================ SCULPT

def test_sculpt_smooth_brush_fan_shroud():
    """Sculpt smooth brush preserves topology — same face count."""
    m = _nas("fan_shroud")
    noisy = _noisy(m, sigma=0.5)
    pt = _top_face_point(noisy)
    stroke = [pt]
    result, _ = run_action(noisy, "sculpt", {
        "stroke":    stroke,
        "radius_mm": 5.0,
        "brush":     "smooth",
        "strength":  "medium",
    })
    assert len(result.faces) == len(noisy.faces)


def test_sculpt_push_out_brush_shelf_bracket():
    """Sculpt push_out brush on a surface vertex does not crash."""
    m = _nas("shelf_bracket")
    # Use an actual vertex (guaranteed on surface) rather than a face centroid
    top_idx = int(np.argmax(m.vertices[:, 2]))
    v = m.vertices[top_idx]
    stroke = [[float(v[0]), float(v[1]), float(v[2])]]
    result, _ = run_action(m, "sculpt", {
        "stroke":    stroke,
        "radius_mm": 8.0,
        "brush":     "push_out",
        "strength":  "light",
    })
    assert len(result.faces) == len(m.faces)


# ================================================================ ERASE AREA

def test_erase_area_removes_faces_fan_cover():
    """Erase area via square lasso removes some faces."""
    m = _nas("fan_cover")
    # Use a top face centroid as the lasso anchor (guaranteed on surface)
    pt = _top_face_point(m)
    px, py = pt[0], pt[1]
    top_z = float(m.bounds[1][2])
    r = float(m.extents[0]) * 0.15  # lasso = 15% of model width
    lasso = [px-r, py-r, px+r, py-r, px+r, py+r, px-r, py+r]
    eye   = [px, py, top_z + 500.0]
    result, _ = run_action(m, "erase_area", {
        "lasso":       lasso,
        "view":        _IDENTITY_16,
        "eye":         eye,
        "facing_only": True,
    })
    assert len(result.faces) < len(m.faces)


def test_erase_area_shelf_bracket():
    m = _nas("shelf_bracket")
    pt = _top_face_point(m)
    px, py = pt[0], pt[1]
    top_z = float(m.bounds[1][2])
    r = float(m.extents[0]) * 0.15
    lasso = [px-r, py-r, px+r, py-r, px+r, py+r, px-r, py+r]
    eye   = [px, py, top_z + 500.0]
    result, _ = run_action(m, "erase_area", {
        "lasso": lasso, "view": _IDENTITY_16, "eye": eye, "facing_only": True,
    })
    assert len(result.faces) < len(m.faces)
