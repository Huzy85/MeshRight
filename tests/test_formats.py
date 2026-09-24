import zipfile

import numpy as np
import pytest
import trimesh

from meshright.analysis import analyze_file, load_mesh

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
