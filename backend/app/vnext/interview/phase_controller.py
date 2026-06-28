"""Server PhaseController — a faithful mirror of
``src/lib/interview-v3/state-machine.ts``.

The Controller is the single authority over phase transitions. The adapter/LLM
never set the phase directly — they emit typed structural *signals* requesting an
advance, and the Controller validates each request against the transition table
(plus optional guards). On success it is what authorises the resulting
``phase.changed`` event.

This table is intentionally encoded explicitly and asserted against in
``tests/test_phase_controller_parity.py`` so any drift from the TS source fails
loudly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

# Order matters — mirrors PHASES in state-machine.ts.
PHASES: tuple[str, ...] = (
    "intake",
    "rubric",
    "ready",
    "intro",
    "resume_calibration",
    "problem_framing",
    "coding",
    "debugging",
    "optimization",
    "wrap_up",
    "scoring",
    "review",
)

ADVANCE_SIGNALS: tuple[str, ...] = (
    "intake.submitted",
    "rubric.generated",
    "session.start",
    "intro.done",
    "calibration.done",
    "framing.done",
    "coding.done",
    "debugging.done",
    "optimization.done",
    "wrap.done",
    "scoring.done",
)


@dataclass(frozen=True)
class TransitionContext:
    """Context a guard may inspect to permit/deny a transition."""

    hasRubric: bool
    lastSeq: int


TransitionGuard = Callable[[TransitionContext], bool]


@dataclass(frozen=True)
class Transition:
    from_: str
    on: str
    to: str
    guard: Optional[TransitionGuard] = None


def _has_rubric(ctx: TransitionContext) -> bool:
    return ctx.hasRubric


# Linear default path through the interview, plus guards where meaningful.
# 1:1 with TRANSITIONS in state-machine.ts.
TRANSITIONS: tuple[Transition, ...] = (
    Transition("intake", "intake.submitted", "rubric"),
    Transition("rubric", "rubric.generated", "ready", _has_rubric),
    Transition("ready", "session.start", "intro", _has_rubric),
    Transition("intro", "intro.done", "resume_calibration"),
    Transition("resume_calibration", "calibration.done", "problem_framing"),
    Transition("problem_framing", "framing.done", "coding"),
    Transition("coding", "coding.done", "debugging"),
    Transition("debugging", "debugging.done", "optimization"),
    Transition("optimization", "optimization.done", "wrap_up"),
    Transition("wrap_up", "wrap.done", "scoring"),
    Transition("scoring", "scoring.done", "review"),
)


@dataclass(frozen=True)
class TransitionResult:
    ok: bool
    from_: str
    signal: str
    to: Optional[str] = None
    reason: Optional[str] = None  # "no_transition" | "guard_failed"


class PhaseController:
    """Owns the current phase and validates every requested advance.

    Pure: it does not emit events itself — the caller turns a successful result
    into a ``phase.changed`` envelope so seq/ts/sessionId stay owned by the
    ledger writer.
    """

    def __init__(self, initial: str = "intake") -> None:
        if initial not in PHASES:
            raise ValueError(f"unknown initial phase: {initial!r}")
        self._current = initial

    @property
    def phase(self) -> str:
        return self._current

    def evaluate(self, signal: str, ctx: TransitionContext) -> TransitionResult:
        """Validate a requested advance without mutating state."""
        match = next(
            (t for t in TRANSITIONS if t.from_ == self._current and t.on == signal),
            None,
        )
        if match is None:
            return TransitionResult(False, self._current, signal, reason="no_transition")
        if match.guard is not None and not match.guard(ctx):
            return TransitionResult(False, self._current, signal, reason="guard_failed")
        return TransitionResult(True, self._current, signal, to=match.to)

    def request(self, signal: str, ctx: TransitionContext) -> TransitionResult:
        """Validate and, on success, commit the transition."""
        result = self.evaluate(signal, ctx)
        if result.ok and result.to is not None:
            self._current = result.to
        return result
