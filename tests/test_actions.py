import numpy as np
import pytest
import trimesh

from meshright.actions import REGISTRY, ActionError, run_action
from meshright.analysis import analyze


@pytest.fixture
def box():
    # 10 wide (x), 20 deep (y), 30 tall (z)
    return trimesh.creation.box(extents=(10, 20, 30))


def test_input_mesh_is_never_changed(box):
    before = box.vertices.copy()
    run_action(box, "scale", {"factor": 2})
    assert np.array_equal(box.vertices, before)


def test_move(box):
    mesh, receipt = run_action(box, "move", {"x": 5, "z": -1})
    assert np.allclose(mesh.bounds.mean(axis=0), [5, 0, -1])
    assert "Moved" in receipt


def test_rotate_keeps_centre_and_turns(box):
    mesh, _ = run_action(box, "rotate", {"axis": "x", "degrees": 90})
    assert np.allclose(mesh.extents, [10, 30, 20])
    assert np.allclose(mesh.bounds.mean(axis=0), box.bounds.mean(axis=0))


def test_scale_and_resize(box):
    mesh, _ = run_action(box, "scale", {"factor": 0.5})
    assert np.allclose(mesh.extents, [5, 10, 15])
    mesh, receipt = run_action(box, "resize", {"axis": "z", "size_mm": 120})
    assert np.allclose(mesh.extents, [40, 80, 120])
    assert "×4" in receipt


def test_convert_units(box):
    mesh, _ = run_action(box, "convert_units", {"from_units": "inches"})
    assert np.allclose(mesh.extents, [254, 508, 762])


def test_mirror_keeps_model_facing_outwards(box):
    mesh, _ = run_action(box, "mirror", {"axis": "x"})
    assert mesh.volume > 0
    assert analyze(mesh).score == 100


def test_place_on_bed(box):
    moved, _ = run_action(box, "move", {"x": 50, "y": -20, "z": 33})
    mesh, _ = run_action(moved, "place_on_bed")
    assert np.allclose(mesh.bounds[0][2], 0)
    assert np.allclose(mesh.bounds.mean(axis=0)[:2], [0, 0])


@pytest.mark.parametrize(
    "name, params",
    [
        ("scale", {"factor": -1}),
        ("scale", {"factor": "big"}),
        ("scale", {"factor": float("nan")}),
        ("rotate", {"axis": "w", "degrees": 90}),
        ("rotate", {"axis": "x"}),
        ("move", {"x": 1, "sideways": 2}),
        ("launch_rocket", {}),
    ],
)
def test_bad_inputs_are_refused(box, name, params):
    with pytest.raises(ActionError):
        run_action(box, name, params)


def test_every_action_describes_itself():
    for act in REGISTRY.values():
        info = act.describe()
        assert info["title"] and info["description"]
        for p in info["params"]:
            assert p["description"]


def test_tiny_model_offers_unit_fixes():
    tiny = trimesh.creation.box(extents=(2, 1, 1))
    issue = next(i for i in analyze(tiny).issues if i.code == "tiny_model")
    labels = [f["label"] for f in issue.fixes]
    assert "Saved in inches: make it 50.8 mm" in labels
    fix = issue.fixes[0]
    mesh, _ = run_action(tiny, fix["action"], fix["params"])
    assert not any(i.code == "tiny_model" for i in analyze(mesh).issues)


def test_erase_area_inside_a_loop():
    box = trimesh.creation.box(extents=(1.6, 1.6, 1.6))
    identity = np.eye(4).ravel().tolist()  # screen x, y = model x, y
    right_half = [0, -1, 1, -1, 1, 1, 0, 1]
    mesh, receipt = run_action(box, "erase_area", {"lasso": right_half, "view": identity, "eye": [0, 0, 10], "facing_only": False})
    assert len(mesh.faces) == 6 and receipt == "Erased 6 triangles"
    mesh, _ = run_action(box, "erase_area", {"lasso": right_half, "view": identity, "eye": [0, 0, 10]})
    assert len(mesh.faces) == 11  # only the triangle facing the camera


def test_erase_area_refuses_empty_or_everything():
    box = trimesh.creation.box(extents=(1.6, 1.6, 1.6))
    identity = np.eye(4).ravel().tolist()
    with pytest.raises(ActionError):
        run_action(box, "erase_area", {"lasso": [5, 5, 6, 5, 6, 6], "view": identity, "eye": [0, 0, 10]})
    with pytest.raises(ActionError):
        run_action(box, "erase_area", {"lasso": [-2, -2, 2, -2, 2, 2, -2, 2], "view": identity, "eye": [0, 0, 10], "facing_only": False})


def test_erase_piece_but_never_the_main_model():
    model = trimesh.creation.icosphere(3, radius=10)
    table = trimesh.creation.box(extents=(60, 60, 2))
    table.apply_translation((0, 0, -12))
    both = trimesh.util.concatenate([model, table])
    mesh, receipt = run_action(both, "erase_piece", {"point": [20, 20, -11]})
    assert len(mesh.faces) == len(model.faces) and "separate piece" in receipt
    with pytest.raises(ActionError):
        run_action(both, "erase_piece", {"point": [0, 0, 10]})


def test_extend_break_grows_a_cut_off_tab_back_closed():
    import trimesh

    from meshright.actions import run_action

    tab = trimesh.creation.box([20, 6, 40])
    tab.apply_translation([0, 0, 20])
    cut = trimesh.creation.box([40, 20, 20])
    cut.apply_translation([0, 0, 40])
    from meshright.cut import _to_manifold, _to_mesh

    broken = _to_mesh(_to_manifold(tab) - _to_manifold(cut))
    assert abs(broken.bounds[1][2] - 30) < 1e-6
    grown, receipt = run_action(broken, "extend_break", {"point": [0, 0, 30], "length_mm": 10})
    assert grown.is_watertight
    assert abs(grown.bounds[1][2] - 40) < 0.5
    assert abs(grown.volume - tab.volume) / tab.volume < 0.02
    assert "Rebuilt" in receipt


# ---------------------------------------------------------------- new v0.3 actions


def _scan_with_base():
    """Cylinder scan with a flat base sitting on Z=0."""
    return trimesh.creation.cylinder(radius=20, height=60, sections=32)


def _scan_with_hole():
    """Icosphere with a polar hole (boundary ring), ready for fill_curved."""
    s = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    s.update_faces(s.vertices[s.faces].max(axis=1)[:, 2] <= 18.0)
    s.remove_unreferenced_vertices()
    return s


def test_smooth_scan_keeps_watertight():
    mesh, receipt = run_action(_scan_with_base(), "smooth_scan", {"strength": "medium"})
    assert mesh.is_watertight
    assert "smoothing" in receipt.lower()


def test_smooth_scan_all_strengths():
    base = _scan_with_base()
    for strength in ("light", "medium", "heavy"):
        mesh, _ = run_action(base, "smooth_scan", {"strength": strength})
        assert mesh.is_watertight


def test_remove_base_removes_faces():
    base = _scan_with_base()
    n_before = len(base.faces)
    mesh, receipt = run_action(base, "remove_base", {"margin_mm": 1.5})
    assert len(mesh.faces) < n_before
    assert "base" in receipt.lower() or "removed" in receipt.lower()


def test_seam_polish_produces_watertight():
    broken = trimesh.creation.icosphere(subdivisions=3, radius=10.0)
    half = trimesh.creation.box([30, 30, 10])
    half.apply_translation([0, 0, 5])
    from meshright.cut import _to_manifold, _to_mesh
    piece = _to_mesh(_to_manifold(broken) - _to_manifold(half))
    mesh, receipt = run_action(piece, "seam_polish")
    assert mesh.is_watertight
    assert receipt


def test_fill_curved_closes_holes():
    mesh, receipt = run_action(_scan_with_hole(), "fill_curved", {"max_hole_edges": 300})
    assert mesh.is_watertight
    assert "Filled" in receipt


def test_scan_to_print_produces_watertight_solid():
    base = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    mesh, receipt = run_action(base, "scan_to_print", {})
    assert mesh.is_watertight
    assert receipt


def test_curvature_map_colours_convex_orange_concave_blue():
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    mesh, receipt = run_action(sphere, "curvature_map")
    assert mesh.visual.vertex_colors is not None
    colors = mesh.visual.vertex_colors
    # Convex sphere should be mostly orange (R > B)
    assert colors[:, 0].mean() > colors[:, 2].mean()
    assert "convex" in receipt.lower() or "concave" in receipt.lower()


def test_curvature_map_detects_concave_dent():
    from meshright.repair import mean_curvature
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    dented = sphere.copy()
    top = dented.vertices[:, 2] > 16
    dented.vertices[top, 2] -= 3  # push top in = concave dent
    kappa = mean_curvature(dented)
    assert kappa[top].mean() < 0  # dented region is concave


# ---------------------------------------------------------------- symmetry repair


def test_symmetry_repair_symmetric_box():
    """A fully symmetric box should come back watertight with preserved volume."""
    box = trimesh.creation.box([40, 20, 15])
    result, note = run_action(box, "symmetry_repair")
    assert result.is_watertight
    assert abs(result.volume - box.volume) / box.volume < 0.01
    assert "symmetry" in note.lower()


def test_symmetry_repair_detects_damaged_half():
    """Auto mode should keep the intact half and restore ~original volume."""
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    centres = sphere.triangles_center
    wound = np.linalg.norm(centres - [15, 0, 0], axis=1) < 7
    damaged = trimesh.Trimesh(sphere.vertices, sphere.faces[~wound], process=False)
    damaged.remove_unreferenced_vertices()

    result, note = run_action(damaged, "symmetry_repair")
    assert result.is_watertight
    assert result.volume / sphere.volume > 0.90  # within 10% of original
    assert "negative" in note.lower()  # intact side is negative (x < 0)


def test_symmetry_repair_keep_param():
    """Explicit keep='positive' / 'negative' should be honoured and produce watertight results."""
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)

    r_neg, note_neg = run_action(sphere, "symmetry_repair", {"keep": "negative"})
    r_pos, note_pos = run_action(sphere, "symmetry_repair", {"keep": "positive"})
    assert "negative" in note_neg.lower()
    assert "positive" in note_pos.lower()
    assert r_neg.is_watertight
    assert r_pos.is_watertight


# ---------------------------------------------------------------- repair_wound + photo_repair


def test_repair_wound_closes_missing_piece():
    """Wound repair fills the hole left by a missing piece and gives a watertight model."""
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    centres = sphere.triangles_center
    wound = np.linalg.norm(centres - [15, 0, 0], axis=1) < 7
    broken = trimesh.Trimesh(sphere.vertices, sphere.faces[~wound], process=False)
    broken.remove_unreferenced_vertices()

    result, note = run_action(broken, "repair_wound")
    assert result.is_watertight
    assert result.volume / sphere.volume > 0.90
    assert "wound" in note.lower() or "boundary" in note.lower()


def test_repair_wound_already_closed_is_safe():
    """repair_wound on a watertight model should not break it."""
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    result, _ = run_action(sphere, "repair_wound")
    assert result.is_watertight


def test_photo_repair_bad_base64_raises():
    """photo_repair with invalid base64 should raise ActionError."""
    from meshright.actions import ActionError
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    with pytest.raises(ActionError, match="base64"):
        run_action(sphere, "photo_repair", {
            "photo_data": "not-valid-base64!!!",
            "pts2d": "0,0,1,1,2,2,3,3",
            "pts3d": "0,0,0,1,0,0,0,1,0,1,1,1",
        })


def test_photo_repair_bad_correspondences_raises():
    """photo_repair with too few point pairs should raise ActionError."""
    import base64, io
    from PIL import Image as PilImage
    from meshright.actions import ActionError

    # Create a minimal valid PNG to pass the image check
    img = PilImage.new("RGB", (100, 100), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    photo_b64 = base64.b64encode(buf.getvalue()).decode()

    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    with pytest.raises(ActionError):
        run_action(sphere, "photo_repair", {
            "photo_data": photo_b64,
            "pts2d": "0,0,1,1,2,2",   # only 3 points, need 4+
            "pts3d": "0,0,0,1,0,0,0,1,0",
        })


# ---------------------------------------------------------------- remesh


def test_remesh_changes_triangle_count():
    """remesh produces a mesh with approximately the requested triangle count."""
    sphere = trimesh.creation.icosphere(subdivisions=5, radius=20.0)  # 20480 faces
    result, note = run_action(sphere, "remesh", {"target_triangles": 5000, "smooth": False})
    assert 1000 <= len(result.faces) <= 30000
    assert "→" in note


def test_remesh_preserves_volume():
    """remesh keeps the volume within 10% of the original."""
    sphere = trimesh.creation.icosphere(subdivisions=4, radius=20.0)  # 5120 faces
    result, _ = run_action(sphere, "remesh", {"target_triangles": 5000, "smooth": False})
    assert abs(result.volume - sphere.volume) / sphere.volume < 0.10


def test_remesh_with_smooth():
    """remesh with smooth=True completes without error."""
    sphere = trimesh.creation.icosphere(subdivisions=4, radius=20.0)
    result, note = run_action(sphere, "remesh", {"target_triangles": 5000, "smooth": True})
    assert len(result.faces) > 10
    assert "smoothing" in note.lower()


def test_remesh_is_registered():
    """remesh action is in the REGISTRY."""
    assert "remesh" in REGISTRY


def split_corners(mesh):
    """Every triangle with its own three corners, as some raw PLY and STL files store it."""
    return trimesh.Trimesh(mesh.vertices[mesh.faces].reshape(-1, 3), np.arange(len(mesh.faces) * 3).reshape(-1, 3), process=False)


@pytest.mark.parametrize("name, params", [
    ("smooth_scan", {"strength": "medium"}),
    ("scan_to_print", {"target_triangles": 10000}),
    ("repair_wound", {"smooth_passes": 3, "max_hole_edges": 600}),
])
@pytest.mark.parametrize("shape", ["sphere", "torus"])
def test_smoothing_survives_split_corners(name, params, shape):
    mesh = trimesh.creation.icosphere(3, radius=20) if shape == "sphere" else trimesh.creation.torus(20, 6)
    raw = split_corners(mesh)
    before = raw.vertices.copy()
    result, _ = run_action(raw, name, params)
    assert result.body_count == 1 and result.is_watertight
    assert np.array_equal(raw.vertices, before)  # the original is left alone
