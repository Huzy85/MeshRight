"""An open model and its history, so every step can be undone."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import trimesh

from .actions import REGISTRY, run_action

# Marks a self-crossing result that has not been worked out yet.
_UNKNOWN = object()

# Older steps are dropped beyond this to keep memory in check on big scans.
# The original is always kept, whatever happens.
MAX_UNDO_STEPS = 20


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
        return self.steps[self.position - 1].mesh if self.position else self.original

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
        if len(self.steps) > MAX_UNDO_STEPS:
            self.steps.pop(0)
            self.dropped += 1
        self.position = len(self.steps)
        return step

    def undo(self) -> None:
        if self.can_undo:
            self.position -= 1

    def redo(self) -> None:
        if self.can_redo:
            self.position += 1

    def revert(self) -> None:
        """Back to the file as it was opened. Can itself be undone."""
        if self.position:
            del self.steps[self.position:]
            self.steps.append(Step("revert", {}, "Back to the original file", self.original))
            self.position = len(self.steps)

    def crossing(self):
        """Self-crossing faces of the current mesh, reusing earlier results
        when only moves, turns or scaling happened since."""
        from .analysis import find_crossing_faces

        def at(position: int):
            if position == 0:
                if self.original_crossing is _UNKNOWN:
                    self.original_crossing = find_crossing_faces(self.original)
                return self.original_crossing
            step = self.steps[position - 1]
            if step.crossing is _UNKNOWN:
                act = REGISTRY.get(step.action)
                if step.action == "revert":
                    step.crossing = at(0)
                elif act is not None and act.keeps_shape and position > 1:
                    step.crossing = at(position - 1)
                elif act is not None and act.keeps_shape and self.dropped == 0:
                    step.crossing = at(0)
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
