from meshright import analysis, samples
from meshright.actions import run_action


def test_leaning_model_is_never_offered_a_cut_that_cannot_steady_it():
    """The face sample leans forward past its foot. Cutting 0.2 mm off again and
    again made the foot bigger but never steady, so the check asked forever."""
    mesh, _ = run_action(samples.face_relief(), "thicken", {"thickness_mm": 2})
    for _ in range(4):
        report = analysis.analyze(mesh)
        fixes = [f for i in report.issues if i.code in ("may_tip_over", "small_contact") for f in i.fixes]
        if not fixes:
            break
        mesh, _ = run_action(mesh, fixes[0]["action"], fixes[0]["params"])
    else:
        raise AssertionError("still asking after 4 fixes")
    assert not [i for i in analysis.analyze(mesh).issues if i.code == "may_tip_over"]
