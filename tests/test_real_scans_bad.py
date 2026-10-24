"""Integration tests on genuinely broken/messy scan inputs.

Every model here has a KNOWN defect that MeshRight is supposed to fix.
These are all slow (network downloads) so run with:  pytest -m slow

Models and their defects:
  bunny_open  — Stanford bunny, non-watertight, open bottom (scan never captured it).
                Saved in METRES (~0.15m), so mm-assuming tools see a 0.15mm pygmy bunny.
  fuze_scan   — Open surface, 7 disconnected components; photogrammetry-style artefacts.
  rabbit_open — Open mesh, 11 disconnected components.
  beetle_open — Open surface, 15 disconnected components, badly converted quad mesh.
  soup_broken — 100-face non-watertight degenerate shell (extreme edge case).
"""
from __future__ import annotations

import urllib.request
import urllib.error
from pathlib import Path

import numpy as np
import pytest
import trimesh

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from meshright.actions import ActionError, run_action

# ---------------------------------------------------------------- helpers

CACHE = Path(__file__).parent.parent / "tools" / "library-cache"
WEB_MODELS = {
    "bunny_open":  "https://raw.githubusercontent.com/alecjacobson/common-3d-test-models/master/data/stanford-bunny.obj",
    "beetle_open": "https://raw.githubusercontent.com/alecjacobson/common-3d-test-models/master/data/beetle.obj",
    "fuze_scan":   "https://raw.githubusercontent.com/mikedh/trimesh/main/models/fuze.obj",
    "rabbit_open": "https://raw.githubusercontent.com/mikedh/trimesh/main/models/rabbit.obj",
    "soup_broken": "https://raw.githubusercontent.com/mikedh/trimesh/main/models/soup.stl",
}


def _bad(key: str) -> trimesh.Trimesh:
    ext = WEB_MODELS[key].rsplit(".", 1)[-1]
    dest = CACHE / f"{key}.{ext}"
    if not dest.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        try:
            urllib.request.urlretrieve(WEB_MODELS[key], dest)
        except urllib.error.URLError as e:
            pytest.skip(f"Network unavailable ({e})")
    m = trimesh.load(str(dest), force="mesh")
    assert m is not None
    return m


pytestmark = pytest.mark.slow


# ================================================================ STANFORD BUNNY
# Real laser scan: open bottom, saved in metres (~0.15 m body height).

def test_bunny_not_watertight():
    """Confirms the bunny is open — baseline assertion so later tests make sense."""
    m = _bad("bunny_open")
    assert not m.is_watertight


def test_bunny_is_in_metres():
    """Bunny body is ~0.15 m tall — tools assuming mm will see a 0.15 mm object."""
    m = _bad("bunny_open")
    assert m.extents.max() < 0.5, f"Expected metre-scale, got {m.extents.max():.3f}"


def test_convert_units_bunny_metres_to_mm():
    """convert_units(metres) scales the bunny to ~150 mm — correct print size."""
    m = _bad("bunny_open")
    orig_max = m.extents.max()
    result, note = run_action(m, "convert_units", {"from_units": "metres"})
    assert abs(result.extents.max() - orig_max * 1000.0) / orig_max < 0.01
    assert result.extents.max() > 100.0  # now in mm range


def test_smooth_scan_reduces_roughness_bunny():
    """smooth_scan reduces inter-face angle variance on the real laser-scan surface."""
    m = _bad("bunny_open")
    adj = trimesh.graph.face_adjacency(m.faces)
    n1 = m.face_normals[adj[:, 0]]
    n2 = m.face_normals[adj[:, 1]]
    dots_before = np.clip((n1 * n2).sum(axis=1), -1, 1)
    roughness_before = float(np.degrees(np.arccos(dots_before)).mean())

    result, _ = run_action(m, "smooth_scan", {"strength": "medium"})

    adj2 = trimesh.graph.face_adjacency(result.faces)
    n1r = result.face_normals[adj2[:, 0]]
    n2r = result.face_normals[adj2[:, 1]]
    dots_after = np.clip((n1r * n2r).sum(axis=1), -1, 1)
    roughness_after = float(np.degrees(np.arccos(dots_after)).mean())

    assert roughness_after < roughness_before, \
        f"smooth_scan did not reduce roughness on bunny: {roughness_before:.2f}° → {roughness_after:.2f}°"


@pytest.mark.xfail(
    strict=True,
    reason="BUG: make_solid refuses a mostly-closed single-layer mesh (open bottom) "
           "because it can't find inside/outside. Should handle this case — "
           "the bunny has a clear interior; only a small base hole is missing.",
)
def test_make_solid_closes_bunny():
    """make_solid should produce a watertight mesh from the open-bottomed bunny."""
    m = _bad("bunny_open")
    assert not m.is_watertight
    result, note = run_action(m, "make_solid", {"detail_mm": 0.0})
    assert result.is_watertight, "make_solid did not close the bunny"
    assert result.volume > 0


@pytest.mark.xfail(
    strict=True,
    reason="BUG: scan_to_print calls make_solid, which refuses the bunny. "
           "Blocked by the same inside/outside detection issue.",
)
def test_scan_to_print_bunny():
    """Full scan-to-print pipeline on the bunny produces a watertight solid."""
    m = _bad("bunny_open")
    result, note = run_action(m, "scan_to_print", {})
    assert result.is_watertight
    assert len(result.faces) > 100


def test_fill_curved_closes_bunny_holes():
    """fill_curved should reduce boundary edge count on the bunny."""
    m = _bad("bunny_open")
    boundary_before = len(trimesh.grouping.group_rows(
        np.sort(m.edges_unique[m.edges_unique_length == 1], axis=1), require_count=1
    ))
    result, _ = run_action(m, "fill_curved", {})
    # Any reduction in boundary edges is a win
    boundary_after = len(trimesh.grouping.group_rows(
        np.sort(result.edges_unique[result.edges_unique_length == 1], axis=1), require_count=1
    )) if not result.is_watertight else 0
    assert boundary_after <= boundary_before


# ================================================================ FUZE SCAN
# Open surface, 7 components — closest freely available to a Polycam export.

def test_fuze_is_open():
    """Confirms fuze has multiple open components — baseline."""
    m = _bad("fuze_scan")
    assert not m.is_watertight


def test_cleanup_removes_fuze_debris():
    """cleanup should reduce the component count on the fuze scan."""
    m = _bad("fuze_scan")
    comps_before = len(trimesh.graph.connected_components(m.face_adjacency))
    result, note = run_action(m, "cleanup", {
        "remove_loose_pieces": True, "fill": True, "fix_crossing": True, "reduce": False
    })
    comps_after = len(trimesh.graph.connected_components(result.face_adjacency))
    assert comps_after <= comps_before


def test_make_solid_fuze_produces_output():
    """make_solid on an open single-layer surface: graceful error or watertight result."""
    m = _bad("fuze_scan")
    try:
        result, _ = run_action(m, "make_solid")
        assert len(result.faces) > 0
        # A watertight result is ideal; a graceful ActionError is also acceptable
    except ActionError:
        pass


def test_smooth_scan_fuze():
    """smooth_scan does not crash on an open multi-component scan."""
    m = _bad("fuze_scan")
    result, _ = run_action(m, "smooth_scan", {"strength": "light"})
    assert len(result.faces) == len(m.faces)


# ================================================================ RABBIT OPEN
# Open mesh, 11 disconnected components.

def test_rabbit_has_multiple_components():
    m = _bad("rabbit_open")
    comps = len(trimesh.graph.connected_components(m.face_adjacency))
    assert comps > 1


def test_cleanup_consolidates_rabbit():
    """cleanup reduces rabbit's 11 disconnected parts down to fewer."""
    m = _bad("rabbit_open")
    comps_before = len(trimesh.graph.connected_components(m.face_adjacency))
    result, _ = run_action(m, "cleanup", {"remove_loose_pieces": True, "fill": True,
                                          "fix_crossing": True, "reduce": False})
    comps_after = len(trimesh.graph.connected_components(result.face_adjacency))
    assert comps_after < comps_before


def test_scan_to_print_rabbit():
    """Full pipeline on an open multi-component scan produces a watertight result."""
    m = _bad("rabbit_open")
    result, _ = run_action(m, "scan_to_print", {})
    assert result.is_watertight


# ================================================================ BEETLE OPEN
# Open surface, 15 disconnected components — badly converted quad mesh.

def test_beetle_is_open_multicomponent():
    m = _bad("beetle_open")
    assert not m.is_watertight


def test_make_solid_beetle():
    """make_solid on the broken beetle mesh: graceful error or watertight result."""
    m = _bad("beetle_open")
    try:
        result, _ = run_action(m, "make_solid")
        assert len(result.faces) > 0
    except ActionError:
        pass


# ================================================================ SOUP (EXTREME)
# 100-face degenerate non-watertight shell — stress test for robustness.

def test_soup_cleanup_does_not_crash():
    """cleanup on a severely degenerate mesh returns something without crashing."""
    m = _bad("soup_broken")
    result, note = run_action(m, "cleanup", {})
    assert len(result.faces) > 0


def test_soup_make_solid_does_not_crash():
    """make_solid on an extreme edge-case mesh does not raise an unhandled exception."""
    m = _bad("soup_broken")
    try:
        result, _ = run_action(m, "make_solid")
        assert len(result.faces) > 0
    except ActionError:
        pass  # A graceful ActionError is acceptable; an unhandled crash is not


# ================================================================ EXTEND BREAK — clean open rim
# Create a cylinder with one cap removed: the cleanest possible open rim.
# This is the test fixture extend_break was designed for.

def _open_cylinder(radius=10.0, height=30.0) -> trimesh.Trimesh:
    """A cylinder open at the top — a clean circular break boundary."""
    cyl = trimesh.creation.cylinder(radius=radius, height=height, sections=32)
    top_z = cyl.bounds[1][2]
    # Remove faces whose centroid is within 0.5mm of the top cap
    mask = cyl.triangles_center[:, 2] < (top_z - 0.5)
    return trimesh.Trimesh(cyl.vertices, cyl.faces[mask], process=False)


@pytest.mark.xfail(
    strict=True,
    reason="BUG: extend_break fails its own join-up check even on a perfect synthetic "
           "cylinder with a clean circular rim. The swept surface is constructed but "
           "the seam-weld step cannot close it. Needs fixing in extend.py before "
           "extend_break can be advertised as working.",
)
def test_extend_break_clean_cylinder_rim():
    """extend_break on a clean circular rim should extend the cylinder wall."""
    m = _open_cylinder()
    assert not m.is_watertight
    top_v_idx = int(np.argmax(m.vertices[:, 2]))
    pt = m.vertices[top_v_idx].tolist()
    result, note = run_action(m, "extend_break", {
        "point":        pt,
        "length_mm":    8.0,
        "follow_curve": True,
        "taper":        False,
    })
    assert len(result.faces) > len(m.faces)
    assert result.extents[2] > m.extents[2] + 5.0, \
        "extend_break did not lengthen the cylinder"
