"""Never-reveal hint ladder handlers for conversational intents.

Provides attempt-based escalation (1/2/3) of hints per intent, matching the
Voice_Assist judge ladder pattern. Each emitted interviewer utterance includes
`hint_for`, `hint_step`, and `attempt` in the payload so the ledger can be
used to compute which hint to emit next. The ladder intentionally never
reveals the answer; it guides toward the next action the candidate should take.

Attempt levels:
- 1 (first wrong attempt): direct nudge, no answer, make them find it.
- 2 (still wrong): hint toward the fix, still no answer.
- 3+ (keeps getting it wrong): state the key idea directly and move on.
"""
from __future__ import annotations

from typing import List, Optional

from .store import STORE


# Attempt-based hints: [attempt1, attempt2, attempt3+]
_HINTS = {
    "help": [
        # Attempt 1: direct question to narrow down.
        "That's not right. Think it through again — what's the actual failing case?",
        # Attempt 2: hint toward the process, still make them find it.
        "Still not right. Walk through a small example step by step — what exactly happens?",
        # Attempt 3+: reveal the next concrete action.
        "You're stuck. Let's focus on the next step: write a guard to catch the edge case, then test it.",
    ],
    "repeat": [
        "Let me reframe: what does the function need to return?",
        "Focus on the loop invariants — which variables track progress?",
        "The function should iterate through the input and return the first unique item it finds.",
    ],
    "thinking": [
        "Take your time. Outline the steps in comments first if that helps.",
        "That's fine. Keep thinking through the approach.",
        "Okay, let's move forward. Start with the first step of the plan.",
    ],
    "meta_audio": [
        "I can hear you. Continue when you're ready.",
        "Audio is stable. Go ahead.",
        "Good. Let's keep going.",
    ],
}


def _count_prior_hints(session_id: str, intent: str) -> int:
    """Count how many hints have been emitted for this intent in this session."""
    events = STORE.get_events(session_id, 0)
    count = 0
    for e in events:
        if e.get("type") == "interviewer.utterance":
            # Events are flat envelopes (ledger merges payload keys at top-level).
            if e.get("hint_for") == intent:
                count += 1
    return count


def _count_wrong_attempts(session_id: str, intent: str) -> int:
    """Count consecutive wrong attempts for this intent (how many times the candidate got it wrong).

    This is an approximation based on how many times we emitted a hint for this intent.
    A more sophisticated implementation might track explicit 'wrong' events, but for now
    each hint implies a prior wrong attempt. Attempt numbering starts at 1.
    """
    return _count_prior_hints(session_id, intent) + 1


def next_hint(session_id: str, intent: str) -> Optional[dict]:
    """Return payload for the next hint for `intent`, or None if none.

    The returned dict is suitable as the `payload` for an `interviewer.utterance`
    event and contains `hint_for`, `hint_step`, `attempt`, and `exhausted` entries.

    Attempt-based escalation:
    - Attempt 1: nudge (make them think)
    - Attempt 2: hint (point them toward the fix)
    - Attempt 3+: reveal (state the key idea)
    """
    ladders: List[str] = _HINTS.get(intent, [])
    if not ladders:
        return None

    attempt = _count_wrong_attempts(session_id, intent)
    hint_idx = min(attempt - 1, len(ladders) - 1)  # clamped to the last hint for attempt 3+

    if hint_idx >= len(ladders):
        # Should not happen due to the clamping above, but be safe.
        return {
            "text": "If you're still stuck, would you like to move on or try a different approach?",
            "hint_for": intent,
            "hint_step": attempt,
            "attempt": attempt,
            "exhausted": True,
        }

    text = ladders[hint_idx]
    exhausted = (attempt >= 3)  # attempt 3+ is the final reveal

    return {
        "text": text,
        "hint_for": intent,
        "hint_step": attempt,
        "attempt": attempt,
        "exhausted": exhausted,
    }


__all__ = ["next_hint"]
