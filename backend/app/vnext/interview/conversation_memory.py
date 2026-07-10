"""Conversation Memory — what Maya remembers across a live interview.

A real interviewer never asks the same follow-up twice, remembers what the
candidate got right and wrong, and knows how long they've been on the current
topic. This module reconstructs that state from the event ledger (the single
source of truth), so it survives reconnects for free and never drifts from
what actually happened.

The memory is DERIVED, not stored: `build_memory(events)` replays the ledger.
Turn-scoped annotations Maya produces (covered tags, a one-line note about the
candidate's answer) are persisted as fields on the interviewer.utterance event
that carried them, so they come back on replay like everything else.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Phases where the interview is actually running (memory/turn-counts are
# meaningful here; setup phases are ignored).
_ACTIVE_PHASES = frozenset({
    "intro", "resume_calibration", "problem_framing", "coding",
    "debugging", "optimization", "wrap_up",
})


@dataclass
class ConversationMemory:
    """Everything Maya should recall when composing her next turn."""

    # How many substantive candidate answers have landed in the CURRENT phase —
    # the anti-lockstep floor (never advance on the first utterance).
    phase_turn_count: int = 0
    # Expectation/topic tags the candidate has already touched (avoid re-asking).
    covered: list[str] = field(default_factory=list)
    # Openers Maya has already used, so she can vary her phrasing.
    prior_openers: list[str] = field(default_factory=list)
    # Full text of follow-ups already asked — never repeat one verbatim.
    follow_ups_asked: list[str] = field(default_factory=list)
    # Short notes Maya recorded about the candidate's answers (mistakes, gaps,
    # strengths) — the running assessment she reasons from.
    notes: list[str] = field(default_factory=list)
    # The most recent interviewer question, for a natural "repeat".
    last_question: str = ""
    # Whether the candidate has produced code / run it this phase (evidence gate).
    has_code: bool = False
    has_run: bool = False

    def as_prompt_context(self) -> str:
        """Compact, model-friendly rendering for the conversation prompt."""
        parts: list[str] = [f"Turns on current topic: {self.phase_turn_count}"]
        if self.covered:
            parts.append("Already covered (don't re-ask): " + ", ".join(self.covered[-12:]))
        if self.follow_ups_asked:
            recent = "; ".join(q[:80] for q in self.follow_ups_asked[-4:])
            parts.append("Follow-ups you ALREADY asked (never repeat these): " + recent)
        if self.notes:
            parts.append("Your running notes on the candidate: " + " | ".join(self.notes[-6:]))
        return "\n".join(parts)


def _opener(text: str, words: int = 6) -> str:
    return " ".join((text or "").strip().split()[:words])


def build_memory(events: list[dict], current_phase: str) -> ConversationMemory:
    """Replay the ledger into a ConversationMemory for ``current_phase``.

    phase_turn_count / has_code / has_run are scoped to the current phase (reset
    at each ``phase.changed``); covered/openers/follow-ups/notes accumulate for
    the whole session (Maya remembers the whole conversation).
    """
    mem = ConversationMemory()
    phase = None
    for e in events:
        t = e.get("type")
        if t == "phase.changed":
            phase = e.get("to")
            # New topic: reset the per-phase counters.
            mem.phase_turn_count = 0
            mem.has_code = False
            mem.has_run = False
            continue
        in_active = (phase in _ACTIVE_PHASES)
        if t == "candidate.utterance":
            # Only substantive answers count toward the turn floor; the manager
            # tags conversational fillers so they don't inflate it.
            if in_active and not e.get("nonSubstantive"):
                mem.phase_turn_count += 1
        elif t == "code.edited":
            if in_active:
                mem.has_code = True
        elif t == "code.run":
            if in_active:
                mem.has_run = True
        elif t == "interviewer.utterance":
            text = str(e.get("text", "")).strip()
            # last_question: what "can you repeat?" restates — never a hint,
            # nudge, previous restatement, or short acknowledgment ("I can hear
            # you. Go ahead." became the "question" in a live run). Prefer lines
            # that actually ask/instruct something substantial.
            if (
                text
                and not e.get("hint_for")
                and not e.get("nudgeLevel")
                and not e.get("restated")
                and ("?" in text or len(text) > 60)
            ):
                mem.last_question = text
            if text and not e.get("hint_for") and not e.get("nudgeLevel"):
                op = _opener(text)
                if op:
                    mem.prior_openers.append(op)
            # Turn annotations Maya attached to her own line.
            if e.get("isFollowUp") and text:
                mem.follow_ups_asked.append(text)
            for c in (e.get("covered") or []):
                if c not in mem.covered:
                    mem.covered.append(c)
            note = e.get("note")
            if note:
                mem.notes.append(str(note))
    return mem


__all__ = ["ConversationMemory", "build_memory"]
