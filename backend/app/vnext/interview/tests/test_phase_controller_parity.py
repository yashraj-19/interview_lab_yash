"""Drift guard: the Python phase machine must mirror state-machine.ts exactly.

The expected tables below are encoded EXPLICITLY (copied from the TS source). If
the runtime tables in phase_controller.py drift, these tests fail loudly.
"""
from app.vnext.interview.phase_controller import (
    ADVANCE_SIGNALS,
    PHASES,
    PhaseController,
    TransitionContext,
    TRANSITIONS,
)

EXPECTED_PHASES = (
    "intake", "rubric", "ready", "intro", "resume_calibration", "problem_framing",
    "coding", "debugging", "optimization", "wrap_up", "scoring", "review",
)

EXPECTED_SIGNALS = (
    "intake.submitted", "rubric.generated", "session.start", "intro.done",
    "calibration.done", "framing.done", "coding.done", "debugging.done",
    "optimization.done", "wrap.done", "scoring.done",
)

# (from, on, to, has_guard)
EXPECTED_TRANSITIONS = [
    ("intake", "intake.submitted", "rubric", False),
    ("rubric", "rubric.generated", "ready", True),
    ("ready", "session.start", "intro", True),
    ("intro", "intro.done", "resume_calibration", False),
    ("resume_calibration", "calibration.done", "problem_framing", False),
    ("problem_framing", "framing.done", "coding", False),
    ("coding", "coding.done", "debugging", False),
    ("debugging", "debugging.done", "optimization", False),
    ("optimization", "optimization.done", "wrap_up", False),
    ("wrap_up", "wrap.done", "scoring", False),
    ("scoring", "scoring.done", "review", False),
]


def test_phases_match():
    assert PHASES == EXPECTED_PHASES


def test_signals_match():
    assert ADVANCE_SIGNALS == EXPECTED_SIGNALS


def test_transitions_match():
    actual = [(t.from_, t.on, t.to, t.guard is not None) for t in TRANSITIONS]
    assert actual == EXPECTED_TRANSITIONS


def test_guard_blocks_rubric_transition_without_rubric():
    c = PhaseController("rubric")
    no_rubric = TransitionContext(hasRubric=False, lastSeq=0)
    res = c.request("rubric.generated", no_rubric)
    assert not res.ok and res.reason == "guard_failed"
    assert c.phase == "rubric"


def test_guard_allows_with_rubric():
    c = PhaseController("rubric")
    res = c.request("rubric.generated", TransitionContext(hasRubric=True, lastSeq=0))
    assert res.ok and c.phase == "ready"


def test_no_transition_for_wrong_signal():
    c = PhaseController("intake")
    res = c.request("session.start", TransitionContext(hasRubric=True, lastSeq=0))
    assert not res.ok and res.reason == "no_transition"


def test_full_linear_path():
    c = PhaseController("intake")
    ctx = TransitionContext(hasRubric=True, lastSeq=0)
    for from_, on, to, _ in EXPECTED_TRANSITIONS:
        assert c.phase == from_
        assert c.request(on, ctx).ok
        assert c.phase == to
