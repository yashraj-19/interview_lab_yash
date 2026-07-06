"""Never-reveal hint ladder handlers for conversational intents.

Provides a small, auditable ladder of hints per intent. Each emitted interviewer
utterance produced by this module includes `hint_for` and `hint_step` in the
payload so the ledger can be used to compute which hint to emit next. The
ladder intentionally never reveals the answer; it guides toward the next
action the candidate should take.
"""
from __future__ import annotations

from typing import List, Optional

from .store import STORE


_HINTS = {
    "help": [
        "Let's narrow that down: what's the failing condition or error message?",
        "Try writing a small guard that checks the input shape before the main logic.",
        "What's the smallest failing example you can reproduce locally? Start by adding an assertion or a minimal test case.",
    ],
    "repeat": [
        "I'll reframe the goal: implement the function so it returns the first unique item in the list.",
        "Focus on the loop invariants: which variables track seen state and when do you return?",
    ],
    "thinking": [
        "Take a breath — try outlining the steps in comments first, then implement the first one.",
    ],
    "meta_audio": [
        "I can hear you. If audio is stable, say 'I'm ready' to continue.",
    ],
}


def _count_prior_hints(session_id: str, intent: str) -> int:
    events = STORE.get_events(session_id, 0)
    count = 0
    for e in events:
        if e.get("type") == "interviewer.utterance":
            # Events are flat envelopes (ledger merges payload keys at top-level).
            if e.get("hint_for") == intent:
                count += 1
    return count


def next_hint(session_id: str, intent: str) -> Optional[dict]:
    """Return payload for the next hint for `intent`, or None if none.

    The returned dict is suitable as the `payload` for an `interviewer.utterance`
    event and contains `hint_for` and `hint_step` entries.
    """
    ladders: List[str] = _HINTS.get(intent, [])
    if not ladders:
        return None
    idx = _count_prior_hints(session_id, intent)
    if idx >= len(ladders):
        # already exhausted ladder — give a generic nudge without revealing
        return {
            "text": "If you're still stuck, would you like a higher-level hint or to move on?",
            "hint_for": intent,
            "hint_step": idx + 1,
            "exhausted": True,
        }
    return {
        "text": "Hint: " + ladders[idx],
        "hint_for": intent,
        "hint_step": idx + 1,
        "exhausted": False,
    }


__all__ = ["next_hint"]
