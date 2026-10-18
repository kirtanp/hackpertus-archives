"""Regression test for the per-mode evaluation pool in archai.qa_eval (ruff B023, function-uses-loop-variable).

qa_eval.main used to build the pool callable inline, as `lambda c: run_case(idx, c, m)` inside `for m in MODES:`.
A closure like that reads `m` when it runs, not when it is built, so anything that lets the queued calls outlive
their loop iteration would silently evaluate every mode as the last one. mode_runner binds the mode up front."""
from archai import qa_eval


class _StubIndex:
    """Bare stand-in for qa.Index: run_case only needs search() to return (hits, route)."""

    def search(self, q, k, mode):
        return [], {"intents": [], "places": []}


def _case():
    return {"q": "Wie viele Kinder besuchten die Schule in Hörndli?", "dataset": "consultation", "style": "para", "gold": []}


def test_mode_runner_keeps_its_mode_after_the_loop_moves_on():
    idx = _StubIndex()
    runners = {}
    for m in qa_eval.MODES:  # build a runner for every mode first, before any of them is called
        runners[m] = qa_eval.mode_runner(idx, m)
    assert m == qa_eval.MODES[-1]  # the loop variable has moved on: a late-binding closure would report this one
    assert {mode: runner(_case())["mode"] for mode, runner in runners.items()} == {mode: mode for mode in qa_eval.MODES}
