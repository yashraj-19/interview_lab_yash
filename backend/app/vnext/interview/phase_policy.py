"""Interview State Manager — WHEN a phase is allowed to complete.

The old flow advanced a phase every time the candidate stopped speaking. This
module replaces that with completion CONDITIONS: the LLM may propose that the
current topic is done, but the transition only happens if the conversation has
genuinely earned it. Advancement depends on evidence and engagement, never on
speech completion.

Pure functions over the ledger-derived ConversationMemory + evidence status —
no I/O, fully unit-testable.
"""
from __future__ import annotations

from typing import Optional

from .conversation_memory import ConversationMemory

# Backend mirror of the frontend NEXT_SIGNAL_BY_PHASE (state-machine.ts). The
# PhaseController still validates every transition; this only names the signal.
NEXT_SIGNAL_BY_PHASE: dict[str, str] = {
    "intro": "intro.done",
    "resume_calibration": "calibration.done",
    "problem_framing": "framing.done",
    "coding": "coding.done",
    "debugging": "debugging.done",
    "optimization": "optimization.done",
    "wrap_up": "wrap.done",
}

# Minimum substantive candidate turns before a phase may complete — the
# anti-lockstep floor. Coding/debugging demand more back-and-forth than a
# greeting. intro is light (it's a warm-up), the technical phases are not.
_MIN_TURNS: dict[str, int] = {
    "intro": 1,
    "resume_calibration": 2,
    "problem_framing": 2,
    "coding": 2,
    "debugging": 2,
    "optimization": 2,
    "wrap_up": 1,
}

# Phases that must show hard code evidence before they can complete — you can't
# talk your way past the coding round without writing anything.
_REQUIRES_CODE = frozenset({"coding"})


def next_signal(phase: str) -> Optional[str]:
    return NEXT_SIGNAL_BY_PHASE.get(phase)


def can_advance(
    phase: str,
    memory: ConversationMemory,
    evidence: dict,
    *,
    llm_says_complete: bool,
) -> bool:
    """True only when it is genuinely time to leave ``phase``.

    Gates (all must hold):
    - the LLM judged the candidate has satisfied this topic, AND
    - there is a next phase on the linear path, AND
    - the candidate has had at least the minimum substantive exchange here, AND
    - coding phases have real code evidence, AND
    - we never advance toward wrap-up/scoring without code OR design evidence.
    """
    if not llm_says_complete:
        return False
    if next_signal(phase) is None:
        return False
    if memory.phase_turn_count < _MIN_TURNS.get(phase, 2):
        return False
    if phase in _REQUIRES_CODE and not memory.has_code:
        return False
    # Guard the run toward the end: don't let the interview coast into wrap-up
    # without any hard technical proof (mirrors the existing suggestedAdvance
    # guard in interviewer_llm).
    if phase in ("optimization", "wrap_up") and not (evidence.get("code") or evidence.get("design")):
        return False
    return True


__all__ = ["NEXT_SIGNAL_BY_PHASE", "next_signal", "can_advance"]
