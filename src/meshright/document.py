"""An open model and its history, so every step can be undone."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field

import trimesh

from .actions import REGISTRY, run_action

# Marks a self-crossing result that has not been worked out yet.
_UNKNOWN = object()

# Older steps are dropped beyond this to keep memory in check on big scans.
# The original is always kept, whatever happens. Undoing past the oldest kept
# step lands on the result of the last dropped step (the "base"), not on the
# original, so undo never jumps over steps the user did not undo.
MAX_UNDO_STEPS = 20

# Every step keeps a full copy of the mesh, so twenty steps on a 100 MB scan
# would hold 2 GB. Older steps are also dropped once the kept ones weigh more
# than this, however few there are. The newest step is always kept.
MAX_UNDO_BYTES = int(os.environ.get("MESHRIGHT_MAX_UNDO_MB", "1500")) * 1024 * 1024


def mesh_bytes(mesh: trimesh.Trimesh) -> int:
    """Memory held by a mesh's vertices and faces."""
    try:
        return int(mesh.vertices.nbytes) + int(mesh.faces.nbytes)
    except Exception:
        return 0


@dataclass
class Step:
    action: str
    params: dict
    receipt: str
    mesh: trimesh.Trimesh
    crossing: object = _UNKNOWN


@dataclass
class Document:
    name: str
    original: trimesh.Trimesh
    steps: list[Step] = field(default_factory=list)
    position: int = 0  # how many of the steps are currently applied
    dropped: int = 0  # old steps forgotten to save memory
    original_crossing: object = _UNKNOWN
    # The mesh before the oldest kept step: the original until steps are dropped.
    base: trimesh.Trimesh | None = None
    base_crossing: object = _UNKNOWN
    # Guards this document's steps: one change at a time, and readers take a
    # consistent snapshot (other documents are not held up).
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    def snapshot(self) -> trimesh.Trimesh:
        """The current mesh, read safely. Meshes are never changed in place,
        so the result stays valid while later steps are added."""
        with self.lock:
            return self.mesh

    @property
    def mesh(self) -> trimesh.Trimesh:
        if self.position:
            return self.steps[self.position - 1].mesh
        return self.original if self.base is None else self.base

    @property
    def can_undo(self) -> bool:
        return self.position > 0

    @property
    def can_redo(self) -> bool:
        return self.position < len(self.steps)

    def apply(self, action: str, params: dict | None = None) -> Step:
        mesh, receipt = run_action(self.mesh, action, params)
        step = Step(action, dict(params or {}), receipt, mesh)
        del self.steps[self.position:]  # a new step replaces anything undone
        self.steps.append(step)
        self._forget_oldest()
        self.position = len(self.steps)
        return step

    def _forget_oldest(self) -> None:
        """Drop the oldest steps while there are too many, or they hold too
        much memory. Meshes shared between steps (and the original) are
        counted once."""
        while len(self.steps) > 1 and (len(self.steps) > MAX_UNDO_STEPS or self.undo_bytes() > MAX_UNDO_BYTES):
            oldest = self.steps.pop(0)
            self.base, self.base_crossing = oldest.mesh, oldest.crossing
            self.dropped += 1

    def undo_bytes(self) -> int:
        """Memory held by the kept steps, beyond the original."""
        seen = {id(self.original)}
        total = 0
        for step in self.steps:
            if id(step.mesh) not in seen:
                seen.add(id(step.mesh))
                total += mesh_bytes(step.mesh)
        return total

    def undo(self) -> None:
        if self.can_undo:
            self.position -= 1

    def redo(self) -> None:
        if self.can_redo:
            self.position += 1

    def revert(self) -> None:
        """Back to the file as it was opened. Can itself be undone."""
        if self.mesh is not self.original:
            del self.steps[self.position:]
            self.steps.append(Step("revert", {}, "Back to the original file", self.original))
            self._forget_oldest()
            self.position = len(self.steps)

    def crossing(self):
        """Self-crossing faces of the current mesh, reusing earlier results
        when only moves, turns or scaling happened since."""
        from .analysis import find_crossing_faces

        def original():
            if self.original_crossing is _UNKNOWN:
                self.original_crossing = find_crossing_faces(self.original)
            return self.original_crossing

        def at(position: int):
            if position == 0:
                if self.base is not None:
                    if self.base_crossing is _UNKNOWN:
                        self.base_crossing = find_crossing_faces(self.base)
                    return self.base_crossing
                return original()
            step = self.steps[position - 1]
            if step.crossing is _UNKNOWN:
                act = REGISTRY.get(step.action)
                if step.action == "revert":
                    step.crossing = original()
                elif act is not None and act.keeps_shape:
                    step.crossing = at(position - 1)
                else:
                    step.crossing = find_crossing_faces(step.mesh)
            return step.crossing

        return at(self.position)

    def history(self) -> dict:
        return {
            "steps": [{"action": s.action, "params": s.params, "receipt": s.receipt} for s in self.steps],
            "position": self.position,
            "can_undo": self.can_undo,
            "can_redo": self.can_redo,
            "dropped": self.dropped,
        }
