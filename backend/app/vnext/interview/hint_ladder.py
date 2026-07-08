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

import time
from typing import List, Optional

from .store import STORE


# Attempt-based hints: [attempt1, attempt2, attempt3+]
# Wording rules: never accuse (a help request is not a wrong answer), never
# name a specific problem's solution (scenario-neutral), never confirm/deny.
_HINTS = {
    "help": [
        # Attempt 1: neutral narrowing question — asking for help is not being wrong.
        "Where exactly are you stuck? Focus on the specific case that breaks your current approach.",
        # Attempt 2: process hint, still make them find it.
        "Walk through one small example out loud, step by step — say what each value is as it changes.",
        # Attempt 3+: the next concrete action, still no answer.
        "Here's the next step: write down the exact condition that fails, add a guard for it in the code box, then re-run.",
    ],
    "repeat": [
        "Sure — let me reframe: what does the function need to return, given what it receives?",
        "Once more: restate the goal in your own words. What's the input, and what exactly must come back?",
        "One more time, slowly: re-read the task statement on the panel line by line, and tell me the first requirement it lists.",
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
    # Cut-in acknowledgments: the candidate took the floor — yield it audibly.
    # Without this a bare "wait" cancelled scheduled speech and then got silence.
    "cut_in": [
        "Go ahead — I'm listening.",
        "Sure, you have the floor. What's on your mind?",
        "Alright, I've stopped. Tell me what you need.",
    ],
}

# Intents whose ladder is a genuine escalation toward a stuck candidate: once
# exhausted it's fair to offer to move on. Every OTHER intent is an
# acknowledgment/reassurance (cut_in, thinking, meta_audio) — a 4th occurrence
# is not "still stuck", so those clamp to their final rung instead of switching
# to a move-on prompt (which would wrongly accuse the candidate).
_ESCALATING_INTENTS = frozenset({"help", "repeat"})


# Progress events: evidence the candidate actually TRIED after a hint —
# editing code, running it, or giving a substantive answer.
_PROGRESS_TYPES = {"code.edited", "code.run"}


def _is_progress_event(e: dict) -> bool:
    t = e.get("type")
    if t in _PROGRESS_TYPES:
        return True
    return t == "conversation.intent.detected" and e.get("intent") == "answer"


def _count_prior_hints(session_id: str, intent: str, store=None) -> int:
    """Count how many hints have been emitted for this intent in this session."""
    if store is None:
        store = STORE
    events = store.get_events(session_id, 0)
    count = 0
    for e in events:
        if e.get("type") == "interviewer.utterance":
            # Events are flat envelopes (ledger merges payload keys at top-level).
            if e.get("hint_for") == intent:
                count += 1
    return count


def _progress_credits(events: list[dict], intent: str) -> int:
    """Wood's contingency signal: how many hints for `intent` were followed by
    real progress (a code edit, a run, a substantive answer) before the next
    hint request. Each earns a credit that steps the help level BACK — the
    contingent-tutoring rule: succeed after help → less help next time; fail
    after help → more."""
    credits = 0
    hint_open = False  # a hint was given; watching for progress before the next one
    for e in events:
        if e.get("type") == "interviewer.utterance" and e.get("hint_for") == intent:
            hint_open = True
        elif hint_open and _is_progress_event(e):
            credits += 1
            hint_open = False
    return credits


def _count_wrong_attempts(session_id: str, intent: str, store=None) -> int:
    """Effective help level for `intent`, recomputed from the ledger.

    Base: each prior hint implies a wrong attempt (numbering starts at 1).
    For the escalating HELP ladder, Wood's contingent-tutoring rule adjusts it:
    every hint that was followed by real progress earns a credit that steps
    the level back — a candidate who applies hint 1 and comes back stays
    around level 1 instead of being marched to the reveal.
    """
    if store is None:
        store = STORE
    prior = _count_prior_hints(session_id, intent, store)
    if intent == "help" and prior:
        prior -= min(_progress_credits(store.get_events(session_id, 0), intent), prior)
    return prior + 1


# ── hint-gaming throttle (Baker/MATHia): refuse rapid-fire escalation ────────

# A hint can't have been read+tried faster than this. ~15 chars/sec reading
# plus a floor for actually attempting something.
_MIN_READ_TRY_MS = 8_000
_READ_MS_PER_CHAR = 66  # ≈15 chars/second


def _throttle_check(session_id: str, intent: str, store, now_ms: Optional[int]) -> Optional[dict]:
    """If the previous help hint was requested faster than it could be read and
    tried, refuse escalation: ask for a restatement instead. The throttle line
    carries NO hint_for, so it never advances the attempt counter."""
    if intent != "help":
        return None
    events = store.get_events(session_id, 0)
    last_hint = next(
        (e for e in reversed(events)
         if e.get("type") == "interviewer.utterance" and e.get("hint_for") == intent),
        None,
    )
    if last_hint is None or not isinstance(last_hint.get("ts"), (int, float)):
        return None
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    needed = max(_MIN_READ_TRY_MS, len(str(last_hint.get("text", ""))) * _READ_MS_PER_CHAR)
    if now - last_hint["ts"] >= needed:
        return None
    return {
        "text": ("Before I take you further — apply the last hint first. "
                 "Restate it in your own words and try it in the code box."),
        "hint_throttled": intent,
        "exhausted": False,
    }


def next_hint(
    session_id: str,
    intent: str,
    store=None,
    ladder: Optional[List[str]] = None,
    now_ms: Optional[int] = None,
) -> Optional[dict]:
    """Return payload for the next hint for `intent`, or None if none.

    The returned dict is suitable as the `payload` for an `interviewer.utterance`
    event and contains `hint_for`, `hint_step`, `attempt`, and `exhausted` entries.

    ``ladder`` overrides the built-in ladder for `intent` (used by
    ``hint_provider`` for per-session overrides) so escalation/exhaustion logic
    lives in ONE place instead of being re-implemented per caller. ``now_ms``
    is injectable for deterministic throttle tests.

    Attempt-based escalation (help level is CONTINGENT — see
    ``_count_wrong_attempts``; rapid-fire requests are throttled — see
    ``_throttle_check``):
    - Attempt 1: nudge (make them think)
    - Attempt 2: hint (point them toward the fix)
    - Attempt 3+: reveal (state the key idea)
    """
    if store is None:
        store = STORE

    ladders: List[str] = ladder if ladder is not None else _HINTS.get(intent, [])
    if not ladders:
        return None

    throttled = _throttle_check(session_id, intent, store, now_ms)
    if throttled is not None:
        return throttled

    attempt = _count_wrong_attempts(session_id, intent, store)

    if attempt > len(ladders):
        # Ladder exhausted on a previous attempt.
        if intent in _ESCALATING_INTENTS:
            # help/repeat: offer to move on rather than repeat the final rung.
            return {
                "text": "If you're still stuck, would you like to move on or try a different approach?",
                "hint_for": intent,
                "hint_step": len(ladders),  # stays at the max rung
                "attempt": attempt,
                "exhausted": True,
            }
        # Acknowledgment intents (cut_in/thinking/meta_audio): clamp to the last
        # rung and keep reassuring — a repeat is not a failure to escalate.
        hint_idx = len(ladders) - 1
    else:
        hint_idx = attempt - 1

    text = ladders[hint_idx]
    hint_step = hint_idx + 1  # 1-based rung actually served
    exhausted = attempt >= len(ladders)  # final rung reached (or passed)

    return {
        "text": text,
        "hint_for": intent,
        "hint_step": hint_step,
        "attempt": attempt,
        "exhausted": exhausted,
    }


__all__ = ["next_hint"]
