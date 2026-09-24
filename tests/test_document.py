import numpy as np
import trimesh

from meshright import document
from meshright.document import Document


def make_doc():
    return Document(name="box.stl", original=trimesh.creation.box(extents=(10, 10, 10)))


def test_undo_redo():
    doc = make_doc()
    doc.apply("scale", {"factor": 2})
    doc.apply("scale", {"factor": 3})
    assert np.allclose(doc.mesh.extents, 60)
    doc.undo()
    assert np.allclose(doc.mesh.extents, 20)
    doc.undo()
    assert np.allclose(doc.mesh.extents, 10)
    assert not doc.can_undo
    doc.redo()
    assert np.allclose(doc.mesh.extents, 20)


def test_new_step_replaces_undone_steps():
    doc = make_doc()
    doc.apply("scale", {"factor": 2})
    doc.undo()
    doc.apply("scale", {"factor": 5})
    assert not doc.can_redo
    assert [s.action for s in doc.steps] == ["scale"]
    assert np.allclose(doc.mesh.extents, 50)


def test_revert_to_original_can_be_undone():
    doc = make_doc()
    doc.apply("scale", {"factor": 2})
    doc.revert()
    assert np.allclose(doc.mesh.extents, 10)
    doc.undo()
    assert np.allclose(doc.mesh.extents, 20)


def test_original_survives_long_histories(monkeypatch):
    monkeypatch.setattr(document, "MAX_UNDO_STEPS", 3)
    doc = make_doc()
    for _ in range(5):
        doc.apply("move", {"x": 1})
    assert len(doc.steps) == 3 and doc.dropped == 2
    doc.revert()
    assert np.allclose(doc.mesh.bounds.mean(axis=0), 0)
