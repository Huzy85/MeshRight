import numpy as np
import pytest
import trimesh

from meshright.actions import ActionError, run_action
from meshright.analysis import analyze
from meshright.document import Document
from meshright.intersections import crossing_pairs
from meshright.repair import plan_cleanup, self_intersecting_faces


def folded_sphere():
    """A sphere with its top pushed down through its own bottom."""
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=10)
    vertices = sphere.vertices.copy()
    top = vertices[:, 2] > 7
    vertices[top, 2] -= 16
    return trimesh.Trimesh(vertices, sphere.faces)


def scan_with_problems():
    """An egg-shaped scan with two holes and a speck of noise."""
    egg = trimesh.creation.icosphere(subdivisions=4, radius=1)
    egg.apply_scale((30, 24, 36))
    centres = egg.triangles_center
    keep = (np.linalg.norm(centres - [30, 0, 0], axis=1) > 8) & (np.linalg.norm(centres - [0, 0, 36], axis=1) > 6)
    egg = trimesh.Trimesh(egg.vertices, egg.faces[keep])
    speck = trimesh.creation.icosphere(subdivisions=0, radius=1.5)
    speck.apply_translation((45, 10, -20))
    return trimesh.util.concatenate([egg, speck])


# ---------------------------------------------------------------- self-crossing detector

def test_crossing_detector_simple_case():
    vertices = np.array([[0, 0, 0], [2, 0, 0], [0, 2, 0], [0.5, 0.5, -1], [0.5, 0.5, 1], [1.5, 0.5, 0]], float)
    faces = np.array([[0, 1, 2], [3, 4, 5]])
    assert crossing_pairs(vertices, faces).tolist() == [[0, 1]]


@pytest.mark.parametrize(
    "mesh",
    [
        trimesh.creation.box(),
        trimesh.creation.icosphere(4),
        trimesh.creation.torus(10, 3),
        trimesh.creation.cylinder(5, 10),
        trimesh.creation.capsule(),
    ],
)
def test_clean_shapes_do_not_cross(mesh):
    assert len(crossing_pairs(mesh.vertices, mesh.faces)) == 0


def test_folded_surface_is_found():
    assert len(self_intersecting_faces(folded_sphere())) > 0


def test_overlapping_separate_parts_are_not_faults():
    a = trimesh.creation.box(extents=(10, 10, 10))
    b = a.copy()
    b.apply_translation((5.3, 4.7, 5.1))
    both = trimesh.util.concatenate([a, b])
    assert len(crossing_pairs(both.vertices, both.faces)) > 0  # they do overlap
    assert len(self_intersecting_faces(both)) == 0  # but that is fine to print


def test_analysis_reports_crossing():
    report = analyze(folded_sphere())
    issue = next(i for i in report.issues if i.code == "self_intersections")
    assert issue.severity == "error"
    assert issue.fixes[0]["action"] == "cleanup"
    assert report.highlights["crossing_edges"]


# ---------------------------------------------------------------- cleanup

def test_plan_lists_everything_before_changing_it():
    plan = plan_cleanup(scan_with_problems())
    keys = [s["key"] for s in plan.steps()]
    assert keys == ["remove_debris", "fill_holes"]
    assert plan.debris_pieces == 1
    assert len(plan.holes_to_fill) == 2


def test_cleanup_fixes_a_broken_scan():
    mesh, receipt = run_action(scan_with_problems(), "cleanup")
    report = analyze(mesh)
    # Only the advice to give the pointy egg a flat base remains.
    assert report.printable
    assert {i.code for i in report.issues} <= {"small_contact", "off_bed"}
    assert report.stats["watertight"] and report.stats["pieces"] == 1
    assert "deleted 1 loose piece" in receipt and "filled 2 holes" in receipt


def test_cleanup_can_keep_loose_pieces():
    mesh, receipt = run_action(scan_with_problems(), "cleanup", {"remove_loose_pieces": False})
    assert analyze(mesh).stats["pieces"] == 2
    assert "loose" not in receipt


def test_cleanup_repairs_self_crossing():
    mesh, receipt = run_action(folded_sphere(), "cleanup")
    assert len(self_intersecting_faces(mesh)) == 0
    assert "cut through" in receipt


def test_cleanup_turns_inside_out_model_the_right_way():
    box = trimesh.creation.box(extents=(10, 10, 10))
    inside_out = trimesh.Trimesh(box.vertices, box.faces[:, ::-1])
    mesh, receipt = run_action(inside_out, "cleanup")
    assert mesh.volume > 0
    assert "outwards" in receipt


def test_cleanup_leaves_open_outline_of_a_relief():
    # An open dish: the rim is the model's edge, not a hole to plug.
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=10)
    dish = trimesh.Trimesh(sphere.vertices, sphere.faces[sphere.triangles_center[:, 2] < 2])
    plan = plan_cleanup(dish)
    assert len(plan.holes) == 1 and not plan.holes[0].fill
    step = next(s for s in plan.steps() if s["key"] == "close_open_edge")
    assert step["checked"] is False
    kept, _ = run_action(dish, "cleanup")
    assert not kept.is_watertight
    closed, _ = run_action(dish, "cleanup", {"close_open_edge": True})
    assert closed.is_watertight


def test_cleanup_closes_the_open_bottom_of_a_scanned_vase():
    capsule = trimesh.creation.capsule(height=40, radius=15, count=[32, 32])
    capsule.apply_translation((0, 0, 35))
    vase = trimesh.Trimesh(capsule.vertices, capsule.faces[capsule.triangles_center[:, 2] > 12])
    plan = plan_cleanup(vase)
    assert plan.holes[0].fill
    mesh, _ = run_action(vase, "cleanup")
    assert mesh.is_watertight


def test_cleanup_on_clean_model_changes_nothing():
    mesh, receipt = run_action(trimesh.creation.box(), "cleanup")
    assert receipt == "Cleanup found nothing to change"


def test_cleanup_reduces_heavy_scans(monkeypatch):
    from meshright import repair

    monkeypatch.setitem(repair.PRESETS, "balanced", (1000, 800))
    mesh, receipt = run_action(trimesh.creation.icosphere(subdivisions=4), "cleanup")
    assert len(mesh.faces) <= 900
    assert analyze(mesh).stats["watertight"]
    assert "reduced detail" in receipt


# ---------------------------------------------------------------- make solid

def test_make_solid_closes_a_broken_scan():
    mesh, receipt = run_action(scan_with_problems(), "make_solid")
    report = analyze(mesh)
    assert report.stats["watertight"]
    assert not any(i.code in ("holes", "non_manifold") for i in report.issues)
    assert np.allclose(report.stats["size_mm"], [76.5, 48, 72], atol=2.5)
    assert "closed solid" in receipt


def test_make_solid_merges_overlapping_blobs():
    blobs = trimesh.util.concatenate(
        [trimesh.creation.icosphere(3, radius=10).apply_translation((i * 8, 0, 0)) for i in range(3)]
    )
    mesh, _ = run_action(blobs, "make_solid")
    report = analyze(mesh)
    assert report.stats["pieces"] == 1 and report.printable


# ---------------------------------------------------------------- remembering results

def test_turning_reuses_the_self_crossing_result(monkeypatch):
    from meshright import analysis

    calls = []
    real = analysis.find_crossing_faces
    monkeypatch.setattr(analysis, "find_crossing_faces", lambda m: calls.append(1) or real(m))
    doc = Document(name="folded.stl", original=folded_sphere())
    first = doc.crossing()
    doc.apply("rotate", {"axis": "x", "degrees": 90})
    doc.apply("resize", {"axis": "z", "size_mm": 50})
    assert np.array_equal(doc.crossing(), first)
    assert len(calls) == 1
    doc.apply("cleanup")
    assert len(doc.crossing()) == 0
    assert len(calls) == 2


def test_reducing_detail_keeps_a_closed_scan_closed():
    from meshright.repair import reduce_detail

    rng = np.random.default_rng(0)
    scan = trimesh.creation.icosphere(subdivisions=6, radius=30)
    scan.vertices += rng.normal(0, 0.05, scan.vertices.shape)
    reduced = reduce_detail(scan, 5000)
    assert len(reduced.faces) <= 5200
    report = analyze(reduced, crossing=None)
    assert report.stats["watertight"]
    assert not [i for i in report.issues if i.severity != "info"]


# ---------------------------------------------------------------- thickness

def open_cap():
    sphere = trimesh.creation.icosphere(subdivisions=4, radius=40)
    return trimesh.Trimesh(sphere.vertices, sphere.faces[sphere.triangles_center[:, 2] > 10])


def test_thicken_makes_a_closed_shell_and_keeps_the_front():
    cap = open_cap()
    mesh, receipt = run_action(cap, "thicken", {"thickness_mm": 2})
    report = analyze(mesh)
    assert report.printable and report.stats["watertight"]
    assert report.stats["volume_mm3"] == pytest.approx(cap.area * 2, rel=0.1)
    assert np.allclose(mesh.bounds[1], cap.bounds[1])
    assert "2 mm" in receipt


def test_open_surface_is_offered_thickness():
    issue = next(i for i in analyze(open_cap()).issues if i.code == "holes")
    assert issue.fixes[0]["action"] == "thicken"


def test_thicken_refuses_closed_models():
    with pytest.raises(ActionError):
        run_action(trimesh.creation.box(), "thicken", {"thickness_mm": 2})


def test_heavy_meshes_offer_to_reduce(monkeypatch):
    from meshright import analysis

    monkeypatch.setattr(analysis, "HEAVY_MESH_TRIANGLES", 1000)
    sphere = trimesh.creation.icosphere(subdivisions=4)  # 5,120 triangles
    issue = next(i for i in analyze(sphere).issues if i.code == "heavy_mesh")
    assert issue.severity == "warning" and issue.fixes[0]["action"] == "reduce_detail"
    mesh, receipt = run_action(sphere, "reduce_detail", {"triangles": 1000})
    assert len(mesh.faces) <= 1100 and mesh.is_watertight
    assert receipt.startswith("Reduced detail from 5,120")


def test_hollow_for_resin():
    egg = trimesh.creation.icosphere(4, radius=1)
    egg.apply_scale((30, 24, 36))
    mesh, receipt = run_action(egg, "hollow", {"wall_mm": 2})
    report = analyze(mesh, crossing=None)
    assert report.stats["watertight"] and report.stats["pieces"] == 1  # drain holes join inside and outside
    assert mesh.volume < 0.4 * egg.volume
    assert "drain holes" in receipt


def test_hollow_refuses_thin_models():
    with pytest.raises(ActionError, match="too thin"):
        run_action(trimesh.creation.box(extents=(20, 20, 3)), "hollow", {"wall_mm": 2})


def test_repair_presets_choose_how_much_detail_to_keep():
    heavy = trimesh.creation.icosphere(subdivisions=7)  # about 330,000 triangles
    assert plan_cleanup(heavy, crossing=None, preset="balanced").reduce_to == 0
    assert plan_cleanup(heavy, crossing=None, preset="quick").reduce_to == 150_000
    assert plan_cleanup(heavy, crossing=None, preset="detail").reduce_to == 0
    mesh, receipt = run_action(heavy, "cleanup", {"preset": "quick"})
    assert len(mesh.faces) <= 160_000 and "reduced detail" in receipt


def test_crossing_repair_never_throws_away_the_shape(monkeypatch):
    from meshright import repair

    ball = trimesh.creation.icosphere(subdivisions=3, radius=10)
    # Pretend MeshFix's repair and the local repair both wreck the piece.
    monkeypatch.setattr(repair, "_from_meshfix", lambda tin: trimesh.creation.icosphere(radius=2))
    monkeypatch.setattr(repair, "_local_fix", lambda piece, bad: None)
    kept = repair._fix_piece(ball, np.array([0, 1]))
    assert kept is ball
