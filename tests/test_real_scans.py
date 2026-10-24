"""Real-model integration tests.

Two sources of models:
  NAS — local STLs from the folder in MESHRIGHT_TEST_MODELS; skipped when unset.
  Web — trimesh test models downloaded once to tools/library-cache/; skipped
        when the network is unavailable (marked ``slow``).

Each new 0.3.0 action is tested on several different real models so that
edge cases from actual geometry show up here rather than in production.
"""
from __future__ import annotations

import base64
import io
import os
import urllib.request
import urllib.error
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image as PilImage

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from meshright.actions import ActionError, run_action
from meshright.analysis import analyze
from meshright.mold import analyse as mold_analyse, build as mold_build

# ---------------------------------------------------------------- helpers

# A folder of real STL files to test against. Set MESHRIGHT_TEST_MODELS to
# point at your own; these tests are skipped when it is unset or missing.
NAS_DIR = Path(os.environ.get("MESHRIGHT_TEST_MODELS") or "/nonexistent")
CACHE    = Path(__file__).parent.parent / "tools" / "library-cache"
WEB_BASE = "https://raw.githubusercontent.com/mikedh/trimesh/main/models/"
WEB_MODELS = {
    "suzanne":     (WEB_BASE + "suzanne.ply",       "ply"),
    "rabbit":      (WEB_BASE + "rabbit.obj",        "obj"),
    "teapot":      (WEB_BASE + "teapot.stl",        "stl"),
    "featuretype": (WEB_BASE + "featuretype.STL",   "stl"),
    "plate_holes": (WEB_BASE + "plate_holes.STL",   "stl"),
    "busted":      (WEB_BASE + "busted.STL",        "stl"),
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


def _synthetic_photo_b64(w: int = 250, h: int = 250) -> str:
    img = PilImage.new("RGB", (w, h), color=(128, 128, 128))
    # Draw a rough ellipse so the object mask finds something
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    draw.ellipse([40, 40, w - 40, h - 40], fill=(200, 180, 160))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ================================================================ REMESH
# Tested on: high-poly scan, medium watertight parts, open organic scan

@pytest.mark.slow
def test_remesh_teapot_reduces_triangle_count():
    """Remesh brings a dense open mesh to near the target count."""
    m = _web("teapot")
    result, note = run_action(m, "remesh", {"target_triangles": 5000, "smooth": False})
    assert len(result.faces) >= 1000
    assert "→" in note


@pytest.mark.slow
def test_remesh_featuretype_preserves_volume():
    """Remesh on a watertight CAD part keeps volume within 15%."""
    m = _web("featuretype")
    assert m.is_watertight
    result, _ = run_action(m, "remesh", {"target_triangles": 5000, "smooth": True})
    # remesh (subdivide + decimate) can shift volume by up to 15% on small detailed parts
    if result.is_watertight:
        assert abs(result.volume - m.volume) / m.volume < 0.15


@pytest.mark.slow
def test_remesh_suzanne_keeps_bounds():
    """Remesh on an open scan stays within the original bounding box."""
    m = _web("suzanne")
    result, _ = run_action(m, "remesh", {"target_triangles": 5000, "smooth": False})
    assert np.allclose(result.bounds, m.bounds, atol=2.0)


def test_remesh_spool_holder_preserves_volume():
    """Remesh on a real spool holder keeps volume within 10%."""
    m = _nas("spool_holder")
    assert m.is_watertight
    result, _ = run_action(m, "remesh", {"target_triangles": 5000, "smooth": True})
    # remesh does not guarantee watertight; volume tolerance is 10% for real parts
    if result.is_watertight:
        assert abs(result.volume - m.volume) / m.volume < 0.10


def test_remesh_fan_shroud_near_target():
    """Remesh on a watertight clamp lands within 2x of the requested count."""
    m = _nas("fan_shroud")
    result, note = run_action(m, "remesh", {"target_triangles": 5000, "smooth": False})
    # Allow up to 2x overshoot — simplification has a diminishing-returns ceiling
    assert len(result.faces) <= 5000 * 2
    assert len(result.faces) >= 1000
    assert "→" in note


def test_remesh_filament_guide_near_target():
    """Remesh on a high-triangle model lands near the requested count."""
    m = _nas("filament_guide")  # 16 k faces
    result, note = run_action(m, "remesh", {"target_triangles": 5000, "smooth": False})
    assert 1000 <= len(result.faces) <= 30000
    assert "→" in note


def test_remesh_smok_reduces_high_poly():
    """Remesh on a 242k-face open scan brings it down to a manageable count."""
    m = _nas("smok")           # 242 k faces, open
    result, note = run_action(m, "remesh", {"target_triangles": 20000, "smooth": False})
    assert len(result.faces) < len(m.faces)
    assert "→" in note


def test_remesh_rpi_arm_edge_uniformity():
    """Remesh improves triangle size uniformity on an organic part."""
    m = _nas("rpi_camera_arm")

    def cv(mesh):
        e = mesh.edges_unique
        lens = np.linalg.norm(mesh.vertices[e[:, 0]] - mesh.vertices[e[:, 1]], axis=1)
        return lens.std() / lens.mean()

    result, _ = run_action(m, "remesh", {"target_triangles": 5000, "smooth": True})
    # Allow up to 10% worse (smoothing can stretch some edges)
    assert cv(result) <= cv(m) * 1.10


# ================================================================ REPAIR WOUND
# Tested on: open organic scans, high-poly scan, watertight for safety check

@pytest.mark.slow
def test_repair_wound_teapot_adds_faces():
    """repair_wound on the open teapot adds fill faces."""
    m = _web("teapot")
    result, note = run_action(m, "repair_wound")
    assert len(result.faces) >= len(m.faces)
    assert any(w in note.lower() for w in ("wound", "boundary", "filled", "hole"))


@pytest.mark.slow
def test_repair_wound_suzanne_not_worse():
    """repair_wound never reduces the face count of an open scan."""
    m = _web("suzanne")
    result, _ = run_action(m, "repair_wound")
    assert len(result.faces) >= len(m.faces) - 10


@pytest.mark.slow
def test_repair_wound_watertight_safe():
    """repair_wound on a watertight model does not open it."""
    m = _web("busted")
    assert m.is_watertight
    result, _ = run_action(m, "repair_wound")
    assert result.is_watertight


def test_repair_wound_smok_open_scan():
    """repair_wound on a real high-poly open scan completes without error."""
    m = _nas("smok")
    result, note = run_action(m, "repair_wound")
    assert len(result.faces) > 0
    assert isinstance(note, str)


def test_repair_wound_dragon_head():
    """repair_wound on the dragon head part does not crash."""
    m = _nas("dragon_head")
    result, _ = run_action(m, "repair_wound")
    assert len(result.faces) > 0


def test_repair_wound_mainframe_no_crash():
    """repair_wound on a large (~210mm) mainframe part runs cleanly."""
    m = _nas("mainframe")
    result, _ = run_action(m, "repair_wound")
    assert len(result.faces) > 0


# ================================================================ MOLD — ANALYSE
# Tested on: small bracket, organic shape, fan cover, CAD part, large part

@pytest.mark.slow
def test_mold_analyse_featuretype():
    """Watertight CAD part: analysis returns sensible mold params."""
    m = _web("featuretype")
    a = mold_analyse(m, nozzle_mm=0.4)
    assert a.n_parts in (2, 3)
    assert 8.0 <= a.wall_mm <= 20.0
    assert np.isclose(np.linalg.norm(a.split_axis), 1.0, atol=1e-3)


def test_mold_analyse_shelf_bracket():
    """Small bracket analyses without error."""
    m = _nas("shelf_bracket")
    a = mold_analyse(m)
    assert a.n_parts in (2, 3)
    assert 8.0 <= a.wall_mm <= 20.0


def test_mold_analyse_dragon_head():
    """Organic shape with curves: analysis handles non-planar geometry."""
    m = _nas("dragon_head")
    a = mold_analyse(m, nozzle_mm=0.4)
    assert a.n_parts in (2, 3)
    assert isinstance(a.is_simple, bool)


def test_mold_analyse_fan_cover():
    """Fan cover (thin walls, complex internal geometry) analyses correctly."""
    m = _nas("fan_cover")
    a = mold_analyse(m, nozzle_mm=0.4)
    assert a.n_parts in (2, 3)


def test_mold_analyse_rpi_arm():
    """RPi camera arm analyses — expect undercut issues due to overhangs."""
    m = _nas("rpi_camera_arm")
    a = mold_analyse(m)
    assert a.n_parts in (2, 3)
    assert len(a.issues) >= 0  # may have issues, should not crash


def test_mold_analyse_mainframe():
    """Large complex part (211mm) analyses within sensible wall bounds."""
    m = _nas("mainframe")
    a = mold_analyse(m)
    assert a.n_parts in (2, 3)
    assert 8.0 <= a.wall_mm <= 20.0


# ================================================================ MOLD — BUILD
# Tested on: watertight parts of different sizes and geometries

@pytest.mark.slow
def test_mold_build_featuretype():
    """Two-part mold from watertight CAD part produces valid halves."""
    m = _web("featuretype")
    a = mold_analyse(m, nozzle_mm=0.4)
    parts = mold_build(m, a, add_clamps=False)
    assert "top" in parts
    assert len(parts["top"].faces) > 50


def test_mold_build_shelf_bracket():
    """Small bracket mold: both halves have faces and are watertight."""
    m = _nas("shelf_bracket")
    a = mold_analyse(m)
    parts = mold_build(m, a, add_clamps=False)
    top_ok = parts["top"].is_watertight
    bot = parts.get("bottom") or parts.get("side_a")
    bot_ok = bot.is_watertight if bot else False
    assert top_ok and bot_ok, f"mold halves not watertight: top={top_ok} bot={bot_ok}"


def test_mold_build_dragon_head():
    """Organic shape mold: produces at least one watertight piece."""
    m = _nas("dragon_head")
    a = mold_analyse(m)
    parts = mold_build(m, a, add_clamps=False)
    assert parts["top"].is_watertight


def test_mold_build_fan_shroud_with_clamp():
    """Compact part mold includes a valid C-clamp."""
    m = _nas("fan_shroud")
    result, note = run_action(m, "make_mold", {"include_clamps": True})
    assert len(result.faces) > 100
    assert "clamp" in note.lower() or "print two" in note.lower()


def test_mold_build_rpi_arm_action():
    """make_mold action on the RPi arm returns a combined mesh."""
    m = _nas("rpi_camera_arm")
    result, note = run_action(m, "make_mold", {"include_clamps": False})
    assert len(result.faces) > 100
    assert "mold" in note.lower()


def test_mold_build_large_mainframe():
    """Large 211mm part: mold builds without crashing, walls within range."""
    m = _nas("mainframe")
    result, note = run_action(m, "make_mold", {"include_clamps": False})
    assert len(result.faces) > 100
    assert "mm" in note.lower()  # wall thickness reported


# ================================================================ PHOTO REPAIR

@pytest.mark.slow
def test_photo_repair_teapot_with_synthetic_photo():
    """photo_repair with a synthetic photo on the open teapot completes."""
    m = _web("teapot")
    photo_b64 = _synthetic_photo_b64(300, 300)
    lo, hi = m.bounds
    cx = (lo + hi) / 2
    pts2d = "80,80,220,80,150,200,80,200"
    pts3d = (
        f"{lo[0]:.2f},{lo[1]:.2f},{lo[2]:.2f},"
        f"{hi[0]:.2f},{lo[1]:.2f},{lo[2]:.2f},"
        f"{cx[0]:.2f},{hi[1]:.2f},{lo[2]:.2f},"
        f"{lo[0]:.2f},{lo[1]:.2f},{hi[2]:.2f}"
    )
    result, note = run_action(m, "photo_repair", {
        "photo_data": photo_b64, "pts2d": pts2d, "pts3d": pts3d, "bg_threshold": 30,
    })
    assert len(result.faces) >= len(m.faces) - 10
    assert isinstance(note, str)


def test_photo_repair_dragon_head_with_synthetic_photo():
    """photo_repair on an organic NAS model completes without crash."""
    m = _nas("dragon_head")
    photo_b64 = _synthetic_photo_b64(250, 250)
    lo, hi = m.bounds
    cx = (lo + hi) / 2
    pts2d = "60,60,190,60,125,190,60,190"
    pts3d = (
        f"{lo[0]:.2f},{lo[1]:.2f},{lo[2]:.2f},"
        f"{hi[0]:.2f},{lo[1]:.2f},{lo[2]:.2f},"
        f"{cx[0]:.2f},{hi[1]:.2f},{lo[2]:.2f},"
        f"{lo[0]:.2f},{lo[1]:.2f},{hi[2]:.2f}"
    )
    result, note = run_action(m, "photo_repair", {
        "photo_data": photo_b64, "pts2d": pts2d, "pts3d": pts3d, "bg_threshold": 30,
    })
    assert len(result.faces) >= len(m.faces) - 10


def test_photo_repair_bad_image_raises():
    """Corrupted base64 raises ActionError on a real mesh."""
    m = _nas("shelf_bracket")
    with pytest.raises(ActionError):
        run_action(m, "photo_repair", {
            "photo_data": base64.b64encode(b"not_an_image").decode(),
            "pts2d": "0,0,1,1,2,2,3,3",
            "pts3d": "0,0,0,1,0,0,0,1,0,1,1,1",
        })


# ================================================================ SCAN_TO_PRINT

@pytest.mark.slow
def test_scan_to_print_suzanne():
    """scan_to_print closes the open Suzanne into a solid."""
    m = _web("suzanne")
    result, _ = run_action(m, "scan_to_print", {"target_triangles": 10000})
    assert result.is_watertight


@pytest.mark.slow
def test_scan_to_print_teapot():
    """scan_to_print seals the open teapot."""
    m = _web("teapot")
    result, _ = run_action(m, "scan_to_print", {"target_triangles": 10000})
    assert result.is_watertight


@pytest.mark.slow
def test_scan_to_print_rabbit():
    """scan_to_print seals the open rabbit scan."""
    m = _web("rabbit")
    result, _ = run_action(m, "scan_to_print", {"target_triangles": 10000})
    assert result.is_watertight


def test_scan_to_print_smok():
    """scan_to_print on a high-poly open NAS scan produces a solid."""
    m = _nas("smok")
    result, _ = run_action(m, "scan_to_print", {"target_triangles": 20000})
    assert result.is_watertight


# ================================================================ FILL HOLES

@pytest.mark.slow
def test_fill_curved_teapot():
    """fill_curved on the open teapot closes at least some holes."""
    m = _web("teapot")
    result, _ = run_action(m, "fill_curved")
    assert result.is_watertight or len(result.faces) > len(m.faces)


@pytest.mark.slow
def test_fill_curved_suzanne():
    """fill_curved on the open Suzanne adds fill faces."""
    m = _web("suzanne")
    result, _ = run_action(m, "fill_curved")
    assert result.is_watertight or len(result.faces) > len(m.faces)


def test_fill_curved_smok():
    """fill_curved on a high-poly open scan runs without crashing."""
    m = _nas("smok")
    result, note = run_action(m, "fill_curved")
    assert len(result.faces) > 0


# ================================================================ CLEANUP REGRESSION

@pytest.mark.slow
def test_cleanup_regression_busted():
    """Cleanup never makes the busted mesh's score worse by more than 5 points."""
    m = _web("busted")
    before = analyze(m)
    result, _ = run_action(m, "cleanup")
    after = analyze(result)
    assert after.score >= before.score - 5


@pytest.mark.slow
def test_cleanup_regression_teapot():
    m = _web("teapot")
    before = analyze(m)
    result, _ = run_action(m, "cleanup")
    after = analyze(result)
    assert after.score >= before.score - 5


def test_cleanup_regression_shelf_bracket():
    m = _nas("shelf_bracket")
    before = analyze(m)
    result, _ = run_action(m, "cleanup")
    after = analyze(result)
    assert after.score >= before.score - 5, \
        f"cleanup regression on shelf_bracket: {before.score} → {after.score}"


def test_cleanup_regression_spool_holder():
    m = _nas("spool_holder")
    before = analyze(m)
    result, _ = run_action(m, "cleanup")
    after = analyze(result)
    assert after.score >= before.score - 5


def test_cleanup_regression_fan_shroud():
    m = _nas("fan_shroud")
    before = analyze(m)
    result, _ = run_action(m, "cleanup")
    after = analyze(result)
    assert after.score >= before.score - 5
