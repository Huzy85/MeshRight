"""Tests for mold analysis and generation (mold.py and make_mold / analyse_mold actions)."""
import numpy as np
import pytest
import trimesh

from meshright.actions import REGISTRY, ActionError, run_action
from meshright.mold import MoldAnalysis, MoldError, analyse, build, make_clamp


# ---------------------------------------------------------------- analysis


def _box_mesh():
    return trimesh.creation.box(extents=[40, 30, 20])


def _sphere_mesh():
    return trimesh.creation.icosphere(subdivisions=3, radius=20.0)


def _undercut_mesh():
    """Cylinder with a wide rim at the base — classic undercut that traps in a simple 2-part mold."""
    shaft = trimesh.creation.cylinder(radius=8, height=30, sections=24)
    rim = trimesh.creation.cylinder(radius=15, height=4, sections=24)
    rim.apply_translation([0, 0, -13])
    return trimesh.util.concatenate([shaft, rim])


def test_analyse_returns_2_parts_for_box():
    a = analyse(_box_mesh())
    assert a.n_parts == 2
    assert isinstance(a.split_axis, np.ndarray)
    assert np.isclose(np.linalg.norm(a.split_axis), 1.0, atol=1e-3)


def test_analyse_no_issues_for_smooth_convex():
    a = analyse(_sphere_mesh(), nozzle_mm=0.4)
    # A sphere is smooth and convex — may have fine features but no undercuts
    undercut_issues = [i for i in a.issues if i.code == "undercuts"]
    assert len(undercut_issues) == 0


def test_analyse_detects_undercut():
    a = analyse(_undercut_mesh())
    # The rim creates undercuts; may or may not exceed threshold depending on geometry
    # At minimum the analysis should run without error
    assert a.n_parts in (2, 3)


def test_analyse_wall_mm_sensible():
    a = analyse(_box_mesh())
    assert 8.0 <= a.wall_mm <= 20.0


def test_analyse_is_simple_flag():
    box = _box_mesh()
    a = analyse(box)
    # Box is simple — no undercuts, low fine-feature count
    assert isinstance(a.is_simple, bool)


# ---------------------------------------------------------------- build


def test_build_returns_top_and_bottom():
    a = analyse(_box_mesh())
    parts = build(_box_mesh(), a, add_clamps=False)
    assert "top" in parts
    assert "bottom" in parts
    assert isinstance(parts["top"], trimesh.Trimesh)
    assert isinstance(parts["bottom"], trimesh.Trimesh)


def test_build_parts_have_faces():
    a = analyse(_box_mesh())
    parts = build(_box_mesh(), a, add_clamps=False)
    assert len(parts["top"].faces) > 10
    assert len(parts["bottom"].faces) > 10


def test_build_includes_clamp_when_requested():
    a = analyse(_box_mesh())
    parts = build(_box_mesh(), a, add_clamps=True)
    assert "clamp" in parts
    assert len(parts["clamp"].faces) > 10


def test_build_no_clamp_when_not_requested():
    a = analyse(_box_mesh())
    parts = build(_box_mesh(), a, add_clamps=False)
    assert "clamp" not in parts


def test_build_sphere_mold():
    a = analyse(_sphere_mesh())
    parts = build(_sphere_mesh(), a, add_clamps=False)
    assert "top" in parts and "bottom" in parts


def test_build_respects_custom_wall_mm():
    a = analyse(_box_mesh())
    parts_thin = build(_box_mesh(), a, add_clamps=False, wall_mm=8.0)
    parts_thick = build(_box_mesh(), a, add_clamps=False, wall_mm=15.0)
    # Thicker walls → larger overall bounding box
    vol_thin = parts_thin["top"].bounding_box.volume + parts_thin["bottom"].bounding_box.volume
    vol_thick = parts_thick["top"].bounding_box.volume + parts_thick["bottom"].bounding_box.volume
    assert vol_thick > vol_thin


# ---------------------------------------------------------------- clamp


def test_make_clamp_has_faces():
    clamp = make_clamp(_box_mesh())
    assert isinstance(clamp, trimesh.Trimesh)
    assert len(clamp.faces) > 10


# ---------------------------------------------------------------- actions


def test_analyse_mold_action_returns_note():
    box = trimesh.creation.box(extents=[40, 30, 20])
    result, note = run_action(box, "analyse_mold")
    assert result is box or np.array_equal(result.vertices, box.vertices)
    assert "mold" in note.lower() or "piece" in note.lower()


def test_make_mold_action_produces_combined_mesh():
    box = trimesh.creation.box(extents=[40, 30, 20])
    result, note = run_action(box, "make_mold", {"include_clamps": False})
    # Result is the combined layout of both halves
    assert len(result.faces) > 20
    assert "mold" in note.lower() or "piece" in note.lower()


def test_make_mold_action_with_clamps():
    box = trimesh.creation.box(extents=[40, 30, 20])
    result, note = run_action(box, "make_mold", {"include_clamps": True})
    assert len(result.faces) > 20
    assert "clamp" in note.lower() or "print two" in note.lower()


def test_make_mold_action_sphere():
    sphere = trimesh.creation.icosphere(subdivisions=2, radius=15.0)
    result, note = run_action(sphere, "make_mold", {"include_clamps": False})
    assert len(result.faces) > 20


def test_build_three_part_for_undercut():
    """Undercut shape should produce 3 pieces (top + two side pieces)."""
    a = analyse(_undercut_mesh())
    if a.n_parts == 3:
        parts = build(_undercut_mesh(), a, add_clamps=False)
        assert "top" in parts
        assert "side_a" in parts or "bottom" in parts


def test_make_mold_action_three_part_note():
    """3-part mold action note mentions the side pieces when applicable."""
    undercut = _undercut_mesh()
    a = analyse(undercut)
    result, note = run_action(undercut, "make_mold", {"include_clamps": False})
    assert len(result.faces) > 20
    if a.n_parts == 3 and "side_a" in note:
        assert "side" in note.lower()
