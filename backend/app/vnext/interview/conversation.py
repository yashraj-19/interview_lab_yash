"""Conversation Manager — the anti-lockstep brain of the live interview.

Replaces "one candidate utterance = advance one phase" with real interview
behavior: classify what the candidate did, respond appropriately, and only
advance the phase when the topic is genuinely complete.

Routing (llm mode):
    help / stuck        -> deterministic never-reveal hint ladder     STAY
    repeat              -> restate the current question in fresh words STAY
    thinking            -> a brief "take your time"                    STAY
    backchannel         -> logged, no spoken response
    substantive answer  -> ONE reactive LLM turn (react + follow-up /
                           challenge / help / clarify), which MAY advance
                           the phase if phase_policy allows it.

The manager only produces events; ws.py owns the socket, the cancellable-task
registry (barge-in), the send lock, and the output guard invocation. The
substantive path is async so a barge-in can cancel it mid-generation exactly
like a normal interviewer turn.
"""
from __future__ import annotations

from .conversation_memory import build_memory
from .intent import get_classifier
from .hint_provider import get_hint_for
from .llm.interviewer_llm import compute_evidence_status, generate_conversation_turn
from .phase_controller import PhaseController, TransitionContext
from .phase_policy import can_advance, next_signal
from .reveal_guard import guard_interviewer_line
from .store import STORE

_CLASSIFIER = get_classifier()

# Deterministic "take your time" lines (thinking intent) — rotated by seq so
# they never read as a stuck record.
_THINKING_LINES = (
    "Take your time — think it through out loud when you're ready.",
    "No rush. Talk me through your thinking as it comes.",
    "That's fine, take a moment. I'm listening.",
)

# Deterministic acknowledgment used only when the conversation LLM is
# unavailable (no key / fake_llm) — keeps the offline path alive and in-phase
# instead of going silent, and never advances on its own.
_OFFLINE_ACKS = (
    "Go on — walk me through your reasoning.",
    "Okay. What's your thinking behind that?",
    "Keep going — how would you handle the tricky case?",
)


def route(text: str, session_id: str) -> str:
    """Classify a candidate utterance into a routing category.

    Conversational categories (help/repeat/thinking/backchannel/meta_audio/
    cut_in) are handled deterministically; everything else is 'substantive'
    and gets a reactive LLM turn.
    """
    intent = _CLASSIFIER.classify(text, session_id)
    if intent in ("help", "repeat", "thinking", "backchannel", "meta_audio", "cut_in"):
        return intent
    return "substantive"


def _intent_event(session_id: str, intent: str, text: str, **extra) -> dict:
    return STORE.append_event(
        session_id, "system", "conversation.intent.detected",
        {"intent": intent, "text": str(text), **extra},
    )


def deterministic_turn(session_id: str, category: str, text: str) -> list[dict]:
    """Instant, no-LLM handling for conversational intents. Never advances the
    phase — these are all "stay and support" moves."""
    out: list[dict] = []
    if category == "backchannel":
        out.append(_intent_event(session_id, "backchannel", text, is_backchannel=True))
        return out
    out.append(_intent_event(session_id, category, text))
    seq = STORE.get_ledger(session_id).last_seq + 1

    if category in ("help", "cut_in"):
        # help -> contingent never-reveal hint ladder; cut_in -> the floor-
        # yield acknowledgment ladder. Both are per-scenario aware.
        hint = get_hint_for(session_id, "help" if category == "help" else "cut_in")
        if hint:
            payload = {"lineId": f"dir-{seq}", "text": hint.get("text", "")}
            for k in ("hint_for", "hint_step", "attempt", "hint_throttled"):
                if hint.get(k) is not None:
                    payload[k] = hint[k]
            payload["exhausted"] = hint.get("exhausted", False)
            out.append(STORE.append_event(session_id, "interviewer", "interviewer.utterance", payload))
        return out

    if category == "repeat":
        mem = build_memory(STORE.get_events(session_id, 0), _phase(session_id))
        last_q = mem.last_question or "Let's focus on the current task — talk me through your approach."
        out.append(STORE.append_event(
            session_id, "interviewer", "interviewer.utterance",
            {"lineId": f"dir-{seq}", "text": f"Sure — {last_q}"},
        ))
        return out

    if category == "thinking":
        out.append(STORE.append_event(
            session_id, "interviewer", "interviewer.utterance",
            {"lineId": f"dir-{seq}", "text": _THINKING_LINES[seq % len(_THINKING_LINES)]},
        ))
        return out

    if category == "meta_audio":
        out.append(STORE.append_event(
            session_id, "interviewer", "interviewer.utterance",
            {"lineId": f"dir-{seq}", "text": "I can hear you. Go ahead whenever you're ready."},
        ))
        return out
    return out


def _phase(session_id: str) -> str:
    rec = STORE.get_session(session_id)
    return (rec or {}).get("phase", "intro")


async def substantive_turn(session_id: str, candidate_text: str, turn_id: str | None = None) -> list[dict]:
    """Reactive turn for a real answer: understand it, respond in-phase, and
    advance ONLY if the phase is genuinely complete. Returns the ordered events
    to emit (the LLM call happens first so a barge-in can cancel with nothing
    emitted). Falls back to a deterministic in-phase acknowledgment — never a
    silent drop — when the LLM is unavailable."""
    rec = STORE.get_session(session_id)
    phase = (rec or {}).get("phase", "intro")
    events = STORE.get_events(session_id, 0)
    memory = build_memory(events, phase)

    result = await generate_conversation_turn(
        session_id,
        phase=phase,
        intake=(rec or {}).get("intake") or {},
        rubric=(rec or {}).get("rubric") or {},
        memory=memory,
        transcript_events=events,
        candidate_text=candidate_text,
        track=(rec or {}).get("track"),
        persona=(rec or {}).get("persona"),
    )

    out: list[dict] = [_intent_event(session_id, result["intent"] if result else "answered", candidate_text)]

    if result is None:
        # Offline/no-key: stay in phase with a deterministic nudge (never advance).
        seq = STORE.get_ledger(session_id).last_seq + 1
        out.append(STORE.append_event(
            session_id, "interviewer", "interviewer.utterance",
            {"lineId": f"llm-{seq}", "text": _OFFLINE_ACKS[seq % len(_OFFLINE_ACKS)]},
        ))
        return out

    # Guard the reply (never confirm/deny/praise/reveal) before it can land.
    # attempt=1 so scenario reveal terms are ALWAYS blocked here — only the
    # dedicated hint ladder's final rung is ever allowed to reveal.
    guard_seq = STORE.get_ledger(session_id).last_seq + 1
    spec_terms = _reveal_terms(rec)
    reply, guard_reasons = guard_interviewer_line(
        result["reply"], seq=guard_seq, attempt=1, reveal_terms=spec_terms,
    )

    # Decide advancement from evidence + engagement, not speech.
    evidence = compute_evidence_status(events)
    advance = can_advance(phase, memory, evidence, llm_says_complete=result["advance"])

    if advance:
        sig = next_signal(phase)
        controller = PhaseController(phase)
        ctx = TransitionContext(hasRubric=bool((rec or {}).get("rubric")),
                                lastSeq=STORE.get_ledger(session_id).last_seq)
        res = controller.request(sig, ctx)
        if res.ok:
            out.append(STORE.append_event(
                session_id, "system", "phase.changed",
                {"from": res.from_, "to": res.to, "signal": res.signal},
            ))
            rec["phase"] = controller.phase
            STORE.put_session(session_id, rec)
        else:
            advance = False  # controller rejected — stay put

    seq = STORE.get_ledger(session_id).last_seq + 1
    payload = {
        "lineId": f"llm-{seq}", "text": reply,
        "covered": result["covered"],
        # A within-phase probe is a follow-up (remembered so it's never
        # repeated); a phase-opening line is not.
        "isFollowUp": not advance,
    }
    if turn_id:
        payload["turnId"] = turn_id
    if result["note"]:
        payload["note"] = result["note"]
    if guard_reasons:
        payload["guarded"] = True
        payload["guard_reasons"] = guard_reasons
    out.append(STORE.append_event(session_id, "interviewer", "interviewer.utterance", payload))
    return out


def _reveal_terms(rec) -> tuple[str, ...]:
    from .scenario import get_scenario
    spec = get_scenario((rec or {}).get("track"))
    return tuple(spec.reveal_terms) if spec is not None else ()


__all__ = ["route", "deterministic_turn", "substantive_turn"]
