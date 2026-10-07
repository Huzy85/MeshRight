import json
import zipfile

import numpy as np
import pytest
import trimesh

from meshright.analysis import MeshLoadError, analyze, analyze_file, load_mesh

from .conftest import save

AMF_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<amf unit="{unit}">
  <object id="0">
    <mesh>
      <vertices>
{vertices}
      </vertices>
      <volume>
{triangles}
      </volume>
    </mesh>
  </object>
</amf>
"""


def amf_text(mesh, unit="millimeter"):
    vertices = "\n".join(
        f"<vertex><coordinates><x>{x}</x><y>{y}</y><z>{z}</z></coordinates></vertex>"
        for x, y, z in mesh.vertices
    )
    triangles = "\n".join(
        f"<triangle><v1>{a}</v1><v2>{b}</v2><v3>{c}</v3></triangle>" for a, b, c in mesh.faces
    )
    return AMF_TEMPLATE.format(unit=unit, vertices=vertices, triangles=triangles)


@pytest.fixture
def tall_box():
    # 10 wide, 20 deep, 30 tall (Z up), in millimetres
    return trimesh.creation.box(extents=(10, 20, 30))


def test_amf(tall_box, tmp_path):
    path = tmp_path / "model.amf"
    path.write_text(amf_text(tall_box))
    report = analyze_file(path)
    assert report.score == 100
    assert report.stats["size_mm"] == [10, 20, 30]


def test_zipped_amf_in_inches(tall_box, tmp_path):
    path = tmp_path / "model.amf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("model.amf", amf_text(tall_box, unit="inch"))
    mesh = load_mesh(path)
    assert np.allclose(mesh.extents, [254, 508, 762])


def test_glb_converted_to_mm_and_z_up(tall_box, tmp_path):
    # Phone scanning apps write glTF in metres with Y up.
    y_up_metres = tall_box.copy()
    y_up_metres.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    y_up_metres.apply_scale(0.001)
    assert np.allclose(y_up_metres.extents, [0.010, 0.030, 0.020])

    mesh = load_mesh(save(y_up_metres, tmp_path, "scan.glb"))
    assert np.allclose(mesh.extents, [10, 20, 30])
    assert mesh.volume > 0  # still facing outwards after the conversion


def test_3mf_multiple_objects(tmp_path):
    a = trimesh.creation.box(extents=(10, 10, 10))
    b = trimesh.creation.box(extents=(10, 10, 10))
    b.apply_translation((30, 0, 0))
    scene = trimesh.Scene([a, b])
    path = tmp_path / "plate.3mf"
    scene.export(path)
    report = analyze_file(path)
    assert report.stats["pieces"] == 2
    assert report.stats["size_mm"] == [40, 10, 10]


def test_bad_amf(tmp_path):
    path = tmp_path / "model.amf"
    path.write_text("<amf><object/></amf>")
    with pytest.raises(ValueError):
        load_mesh(path)


# ---------------------------------------------------------------- scanners


def test_scanner_from_ply_comment(cube, tmp_path):
    data = trimesh.exchange.ply.export_ply(cube, encoding="ascii")
    data = data.replace(b"format ascii 1.0\n", b"format ascii 1.0\ncomment Created by Revo Scan 5.4\n", 1)
    path = tmp_path / "scan.ply"
    path.write_bytes(data)
    mesh = load_mesh(path)
    assert mesh.metadata["scanner"]["name"] == "Revopoint"
    info = next(i for i in analyze(mesh).issues if i.code == "scan_source")
    assert info.severity == "info" and info.title == "Scanned with Revopoint" and "Clean up" in info.detail


def test_scanner_from_stl_header_obj_comment_and_gltf_generator(cube, tmp_path):
    stl = bytearray(cube.export(file_type="stl"))
    stl[0:80] = b"Exported by Creality Scan 3.2".ljust(80, b" ")
    (tmp_path / "a.stl").write_bytes(stl)
    (tmp_path / "b.obj").write_text("# Polycam 4.1\n" + cube.export(file_type="obj"), encoding="utf-8")
    files = trimesh.exchange.gltf.export_gltf(trimesh.Scene(cube))
    model = json.loads(files["model.gltf"])
    model["asset"]["generator"] = "Scaniverse 3.0"
    for name, blob in files.items():
        (tmp_path / name).write_bytes(json.dumps(model).encode() if name == "model.gltf" else blob)
    assert load_mesh(tmp_path / "a.stl").metadata["scanner"]["name"] == "Creality Scan"
    assert load_mesh(tmp_path / "b.obj").metadata["scanner"]["name"] == "Polycam"
    assert load_mesh(tmp_path / "model.gltf").metadata["scanner"]["name"] == "Scaniverse"


def test_scanner_from_3mf_metadata(cube, tmp_path):
    path = tmp_path / "scan.3mf"
    path.write_bytes(cube.export(file_type="3mf"))
    with zipfile.ZipFile(path) as zf:
        files = {n: zf.read(n) for n in zf.namelist()}
    name = next(n for n in files if n.endswith(".model"))
    files[name] = files[name].replace(b"<resources>", b'<metadata name="Application">EXScan Pro 4</metadata><resources>', 1)
    with zipfile.ZipFile(path, "w") as zf:
        for n, blob in files.items():
            zf.writestr(n, blob)
    assert load_mesh(path).metadata["scanner"]["name"] == "Shining 3D"


def test_unknown_maker_adds_no_line(cube, tmp_path):
    mesh = load_mesh(save(cube, tmp_path))
    assert "scanner" not in mesh.metadata
    assert not [i for i in analyze(mesh).issues if i.code == "scan_source"]


def test_scanner_word_must_stand_alone(cube, tmp_path):
    # "farolite" is not FARO; "artec" inside "quartech" is not Artec.
    (tmp_path / "a.obj").write_text("# farolite quartech export\n" + cube.export(file_type="obj"), encoding="utf-8")
    assert "scanner" not in load_mesh(tmp_path / "a.obj").metadata


def test_scanner_survives_changes_and_project_files(cube, tmp_path):
    from meshright import project
    from meshright.actions import run_action
    from meshright.document import Document

    (tmp_path / "a.obj").write_text("# Artec Studio 17\n" + cube.export(file_type="obj"), encoding="utf-8")
    mesh = load_mesh(tmp_path / "a.obj")
    moved, _ = run_action(mesh, "move", {"x": 5})
    assert moved.metadata["scanner"]["name"] == "Artec"
    doc = Document(name="a.obj", original=mesh)
    doc.apply("move", {"x": 5})
    saved = tmp_path / "a.meshright"
    saved.write_bytes(project.save(doc))
    again = project.load(saved)
    assert again.mesh.metadata["scanner"]["name"] == "Artec"


# ---------------------------------------------------------------- USD, E57, FBX

USDA = """#usda 1.0
(
    metersPerUnit = 0.01
    upAxis = "Y"
)

def Xform "root"
{
    float3 xformOp:scale = (2, 2, 2)
    double3 xformOp:translate = (1, 2, 3)
    uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]

    def Mesh "cube" (
        kind = "component"
    )
    {
        int[] faceVertexCounts = [4, 4, 4, 4, 4, 4]
        int[] faceVertexIndices = [0, 3, 2, 1, 4, 5, 6, 7, 0, 1, 5, 4, 1, 2, 6, 5, 2, 3, 7, 6, 3, 0, 4, 7]
        point3f[] points = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1), (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
        color3f[] primvars:displayColor = [(0.5, 0.5, 0.5)]
    }
}
"""


def test_usda_text_is_read_in_mm_z_up(tmp_path):
    # Centimetre scene, Y up: a 2-unit cube scaled by 2 and moved by (1, 2, 3)
    # is 40 mm across, with the USD Y (up) axis now Z.
    path = tmp_path / "scan.usda"
    path.write_text(USDA, encoding="utf-8")
    mesh = load_mesh(path)
    assert mesh.is_watertight and len(mesh.faces) == 12
    assert np.allclose(mesh.bounds, [[-10, -50, 0], [30, -10, 40]])


def test_usdz_with_text_layer(tmp_path):
    path = tmp_path / "scan.usdz"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("scene.usda", USDA)
        zf.writestr("0/texture.png", b"not really")
    assert np.allclose(load_mesh(path).extents, [40, 40, 40])


def test_binary_usd_explains_what_is_needed_or_reads_it(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Sdf, Usd, UsdUtils

    path = tmp_path / "scan.usda"
    path.write_text(USDA, encoding="utf-8")
    stage = Usd.Stage.Open(str(path))  # kept alive: the layer is a weak pointer into it
    stage.GetRootLayer().Export(str(tmp_path / "scan.usdc"))
    UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(str(tmp_path / "scan.usdc")), str(tmp_path / "packed.usdz"))
    assert np.allclose(load_mesh(tmp_path / "scan.usdc").extents, [40, 40, 40])
    assert np.allclose(load_mesh(tmp_path / "packed.usdz").extents, [40, 40, 40])


def test_binary_usd_message_without_the_reader(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_pxr(name, *args, **kwargs):
        if name == "pxr" or name.startswith("pxr."):
            raise ImportError("no pxr")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pxr)
    path = tmp_path / "scan.usdc"
    path.write_bytes(b"PXR-USDC" + bytes(64))
    with pytest.raises(MeshLoadError, match="meshright\\[usd\\]"):
        load_mesh(path)


def test_e57_points_become_a_surface(tmp_path):
    pye57 = pytest.importorskip("pye57")
    # A 20 mm sphere of points, stored in metres as E57 is.
    points = trimesh.creation.icosphere(4, radius=0.01).vertices
    writer = pye57.E57(str(tmp_path / "scan.e57"), mode="w")
    writer.write_scan_raw({"cartesianX": points[:, 0], "cartesianY": points[:, 1], "cartesianZ": points[:, 2]})
    writer.close()
    mesh = load_mesh(tmp_path / "scan.e57")
    assert mesh.is_watertight
    assert 15 < mesh.extents.max() < 30  # the surface sits a little outside the points
    assert "points" in mesh.metadata["notice"]


def test_fbx_says_what_to_export_instead(tmp_path):
    path = tmp_path / "scan.fbx"
    path.write_bytes(b"Kaydara FBX Binary  \x00")
    with pytest.raises(MeshLoadError, match="OBJ, GLB or STL"):
        load_mesh(path)
