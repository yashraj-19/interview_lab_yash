"""vNext interview WebSocket.

Mirrors the interview_ws_v2 resume handshake (client_hello -> resume_ready +
backfill; otherwise resume_rejected), then drives the SCRIPTED, deterministic
session — NO LLM. It reproduces the TS MockInterviewAdapter's event
TYPE/ACTOR/ORDER so a later live frontend adapter reaches parity.

Inbound (JSON):
  - {"type": "candidate.text", "text": ...}
  - {"type": "candidate.code", "code": ...}
  - {"type": "candidate.run",  "code": ...}
  - {"type": "advance.request", "signal": <AdvanceSignal>}
  - {"type": "scorecard.request"}

Every emitted event is a flat ledger envelope assigned a server seq.
"""
from __future__ import annotations

import asyncio
import re

from .intent import get_classifier

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

# Imported (not mutated) from the existing resume contract module.
from app.services.interview_resume import (
    REJECT_INVALID,
    REJECT_NOT_FOUND,
    parse_client_hello,
    resume_ready_message,
    resume_rejected_message,
    select_backfill,
)

from .scenario import get_scenario
from .llm import build_scorecard_llm, build_scripted_scorecard, generate_interviewer_turn
from .phase_controller import PhaseController, TransitionContext
from .seed import (
    script_event_to_payload,
    script_events_by_signal,
)
from .store import STORE
from .session_init import SessionInitManager
from .hint_provider import get_hint_for
from .pause_policy import get_pause_for
from .reveal_guard import guard_interviewer_line

router = APIRouter()

_SCRIPT_BY_SIGNAL = script_events_by_signal()


def _scripted_interviewer_line(signal: str, track: str | None = None) -> str:
    """First scripted interviewer line for a signal's turn (llm fallback text).

    Scenario tracks (incident, problem:*) use their spec's deterministic
    per-signal lines so the fake/no-LLM path stays in the scenario narrative.
    """
    spec = get_scenario(track)
    if spec is not None and spec.lines_for_signal:
        return spec.lines_for_signal.get(signal, spec.default_line)
    for ev in _SCRIPT_BY_SIGNAL.get(signal, []):
        if ev.get("kind") == "interviewer.utterance":
            return ev["text"]
    return "Let's keep going."


def _emit(session_id: str, actor: str, type_: str, payload: dict) -> dict:
    return STORE.append_event(session_id, actor, type_, payload)


_CLASSIFIER = get_classifier()

# Onboarding readiness must be a whole word — bare substring matching marked a
# candidate ready when they said "I ALREADY tried that". A negation within a few
# words before "ready" ("I don't think I'm ready", "not at all ready") cancels
# the match. Contractions are enumerated (not \w*n't) so benign words ending in
# "nt" — environment, assignment, component — don't read as a negation.
_READY_RX = re.compile(r"\bready\b", re.IGNORECASE)
_NOT_READY_RX = re.compile(
    r"\b(?:not|never|cannot|don'?t|doesn'?t|didn'?t|won'?t|can'?t|couldn'?t|"
    r"wouldn'?t|shouldn'?t|isn'?t|aren'?t|ain'?t|wasn'?t)\b(?:\s+\S+){0,4}?\s+ready\b",
    re.IGNORECASE,
)


def _handle_candidate_text(
    session_id: str, text: str, force_immediate: bool = False, intent: str | None = None
) -> list[dict]:
    """Emit the candidate utterance plus a focused director event for recognized intents.

    Backchannels ("yeah", "okay", etc.) are logged but don't emit hints.
    Cut-in words ("wait", "stop", etc.) force immediate cancellation + hint.
    ``intent`` lets the WS loop pass its already-computed classification so the
    utterance is classified exactly once (a nondeterministic provider could
    otherwise disagree between the urgency decision and the hint decision).
    """
    seq_hint = (STORE.get_ledger(session_id).last_seq) + 1
    out: list[dict] = [
        _emit(
            session_id,
            "candidate",
            "candidate.utterance",
            {"lineId": f"cand-{seq_hint}", "text": str(text)},
        )
    ]
    if intent is None:
        intent = _CLASSIFIER.classify(text, session_id)

    # Backchannel: no hint needed, just log.
    if intent == "backchannel":
        out.append(
            _emit(
                session_id,
                "system",
                "conversation.intent.detected",
                {"intent": intent, "text": str(text), "is_backchannel": True},
            )
        )
        return out
    
    # Cut-in: urgent interrupt, force immediate.
    if intent == "cut_in":
        force_immediate = True
    
    if intent == "answer":
        return out
    out.append(
        _emit(
            session_id,
            "system",
            "conversation.intent.detected",
            {"intent": intent, "text": str(text)},
        )
    )
    # Resolve hint via the pluggable provider → session override → fallback.
    hint_payload = get_hint_for(session_id, intent)
    if hint_payload is not None:
        # One whitelisted utterance shape used by BOTH the immediate and the
        # scheduled path, so the ledger-audit contract (hint_for + hint_step +
        # attempt on every hint) holds regardless of pause policy. lineId is
        # added per-emit (the scheduler mints its own at fire time).
        hint_fields = {"text": hint_payload.get("text", "")}
        for key in ("hint_for", "hint_step", "attempt"):
            if hint_payload.get(key) is not None:
                hint_fields[key] = hint_payload[key]
        hint_fields["exhausted"] = hint_payload.get("exhausted", False)

        # If a recent candidate barge-in cancelled an interviewer, the UX expects
        # an immediate corrective hint — bypass pause policies when forced.
        if force_immediate:
            out.append(_emit(session_id, "interviewer", "interviewer.utterance",
                             {"lineId": f"dir-{seq_hint}", **hint_fields}))
            return out

        pause_ms = 0
        try:
            pause_ms = int(get_pause_for(session_id, intent) or 0)
        except Exception:
            pause_ms = 0

        if pause_ms > 0:
            # schedule interviewer utterance later; emit a system.pause.scheduled event
            out.append(_emit(session_id, "system", "system.pause.scheduled", {"intent": intent, "delay_ms": pause_ms}))
            # return a scheduled marker that the WS loop will pick up and schedule.
            # trigger_seq = the candidate utterance this hint replies to — the
            # scheduler uses it to detect that another interviewer line already
            # answered the candidate during the pause (anti-double gate).
            out.append({"scheduled_interviewer": {"payload": hint_fields, "delay_ms": pause_ms,
                                                  "trigger_seq": out[0].get("seq", 0)}})
        else:
            out.append(_emit(session_id, "interviewer", "interviewer.utterance",
                             {"lineId": f"dir-{seq_hint}", **hint_fields}))
    return out


def _scenario_code_actions(session_id: str, code: str) -> list[dict]:
    """After a candidate code edit on a scenario track: if the spec's detector
    flags the buffer, Maya selects/highlights the risky lines, explains, and —
    only when the spec allows patching (incident) — proposes a validated patch.
    Problem scenarios highlight + probe but NEVER patch: writing the solution
    for the candidate would reveal the answer. No-op off scenario tracks, when
    the code is fine, or while a proposal is still open."""
    rec = STORE.get_session(session_id)
    spec = get_scenario(rec.get("track")) if rec else None
    if spec is None or spec.code_is_unsafe is None:
        return []
    if not spec.code_is_unsafe(code):
        return []
    events = STORE.get_events(session_id, 0)
    resolved = {
        e.get("patchId")
        for e in events
        if e.get("type") in ("code.patch.applied", "code.patch.rejected")
    }
    has_open = any(
        e.get("type") == "code.patch.proposed" and e.get("patchId") not in resolved
        for e in events
    )
    if has_open:
        return []  # one open proposal at a time
    # Highlight-only scenarios: don't re-flag the same shape over and over —
    # one probe per distinct edit is Maya being sharp; three is nagging.
    if spec.build_patch is None:
        last_probe = next(
            (e for e in reversed(events)
             if e.get("type") == "interviewer.utterance" and e.get("codeProbe")),
            None,
        )
        if last_probe is not None and last_probe.get("text") == spec.action_utterance:
            return []

    out: list[dict] = []
    start, end = (spec.risky_range or (lambda c: (0, 0)))(code)
    out.append(
        _emit(session_id, "interviewer", "selection.set",
              {"selection": {"start": start, "end": end, "owner": "interviewer"}})
    )
    out.append(_emit(session_id, "interviewer", "highlight.set", {"line": start}))
    out.append(
        _emit(session_id, "interviewer", "interviewer.utterance",
              {"lineId": f"maya-{STORE.get_ledger(session_id).last_seq + 1}",
               "text": spec.action_utterance, "codeProbe": True})
    )
    if spec.build_patch is None:
        return out  # highlight + probe only (never-reveal scenarios)
    patch = spec.build_patch(code)
    if spec.patch_is_safe is not None and not spec.patch_is_safe(patch["after"], patch["before"]):
        return out  # explain + select only; never emit an unsafe patch
    patch_id = f"patch-{STORE.get_ledger(session_id).last_seq + 1}"
    out.append(
        _emit(session_id, "interviewer", "code.patch.proposed",
              {"patchId": patch_id, "summary": patch["summary"],
               "before": patch["before"], "after": patch["after"],
               "selection": patch["selection"]})
    )
    return out


def _resolve_patch(session_id: str, mtype: str, patch_id: str) -> list[dict]:
    """Candidate accepted/rejected a proposed patch. Accept → authoritative
    code.patch.applied + code.edited (server is the source of truth; the UI never
    mints code). Reject → code.patch.rejected. Both clear the selection/highlight."""
    if not patch_id:
        return []
    events = STORE.get_events(session_id, 0)
    proposed = next(
        (e for e in events if e.get("type") == "code.patch.proposed" and e.get("patchId") == patch_id),
        None,
    )
    if proposed is None:
        return []
    if any(
        e.get("type") in ("code.patch.applied", "code.patch.rejected") and e.get("patchId") == patch_id
        for e in events
    ):
        return []  # already resolved — ignore a duplicate accept/reject

    out: list[dict] = []
    if mtype == "code.patch.accept":
        out.append(
            _emit(session_id, "candidate", "code.patch.applied",
                  {"patchId": patch_id, "before": proposed["before"],
                   "after": proposed["after"], "acceptedBy": "candidate"})
        )
        seq_hint = STORE.get_ledger(session_id).last_seq + 1
        out.append(
            _emit(session_id, "candidate", "code.edited",
                  {"editId": f"edit-{seq_hint}", "after": proposed["after"], "by": "candidate"})
        )
    else:  # code.patch.reject
        out.append(_emit(session_id, "candidate", "code.patch.rejected", {"patchId": patch_id}))
    out.append(_emit(session_id, "system", "selection.set", {"selection": None}))
    out.append(_emit(session_id, "system", "highlight.set", {"line": None}))
    return out


def _context(session_id: str) -> TransitionContext:
    rec = STORE.get_session(session_id)
    ledger = STORE.get_ledger(session_id)
    return TransitionContext(
        hasRubric=bool(rec and rec.get("rubric")),
        lastSeq=ledger.last_seq if ledger else 0,
    )


def _drive_advance(ws_session_id: str, signal: str) -> list[dict]:
    """Run the controller, emit phase.changed on success, then the scripted turn."""
    rec = STORE.get_session(ws_session_id)
    controller = PhaseController(rec["phase"])
    result = controller.request(signal, _context(ws_session_id))
    if not result.ok:
        return []
    emitted: list[dict] = []
    emitted.append(
        _emit(
            ws_session_id,
            "system",
            "phase.changed",
            {"from": result.from_, "to": result.to, "signal": result.signal},
        )
    )
    rec["phase"] = controller.phase
    STORE.put_session(ws_session_id, rec)
    # Scenario tracks play THEIR deterministic per-signal line, not the generic
    # two-sum demo script — a scenario interview stays coherent with no LLM key.
    spec = get_scenario(rec.get("track"))
    if spec is not None and spec.lines_for_signal:
        emitted.append(
            _emit(
                ws_session_id,
                "interviewer",
                "interviewer.utterance",
                {"lineId": f"scn-{STORE.get_ledger(ws_session_id).last_seq + 1}",
                 "text": spec.lines_for_signal.get(signal, spec.default_line)},
            )
        )
        return emitted
    for ev in _SCRIPT_BY_SIGNAL.get(signal, []):
        actor, type_, payload = script_event_to_payload(ev)
        emitted.append(_emit(ws_session_id, actor, type_, payload))
    return emitted


def _advance_llm_prelude(ws_session_id: str, signal: str) -> tuple[list[dict], str | None, str]:
    """Live-llm: run the controller transition and emit the SYNCHRONOUS prelude
    (phase.changed + interviewer.turn.started) WITHOUT generating the line.

    Returns ``(events, turn_id, phase)``. ``turn_id`` is None when the transition
    was rejected (nothing to generate). The interviewer.utterance itself is
    produced asynchronously by ``_generate_turn`` so a candidate barge-in can
    cancel it mid-flight — the controller still owns the (already-applied) phase.
    """
    rec = STORE.get_session(ws_session_id)
    controller = PhaseController(rec["phase"])
    result = controller.request(signal, _context(ws_session_id))
    if not result.ok:
        return [], None, rec["phase"]

    pc = _emit(
        ws_session_id,
        "system",
        "phase.changed",
        {"from": result.from_, "to": result.to, "signal": result.signal},
    )
    rec["phase"] = controller.phase
    STORE.put_session(ws_session_id, rec)

    turn_id = f"turn-{pc['seq']}"
    started = _emit(
        ws_session_id,
        "system",
        "interviewer.turn.started",
        {"turnId": turn_id, "phase": controller.phase},
    )
    return [pc, started], turn_id, controller.phase


async def _generate_turn(ws_session_id: str, signal: str, phase: str, turn_id: str) -> dict | None:
    """Generate the interviewer line for ``turn_id``. Returns the utterance event
    to emit, or None if the turn was cancelled (barge-in) before completion.

    Cancellation is cooperative: ``asyncio.CancelledError`` (task.cancel during an
    in-flight LLM call) returns None so nothing is emitted; the line never reaches
    the ledger. Malformed/empty/no-key output falls back to the scripted line.
    """
    rec = STORE.get_session(ws_session_id)
    track = rec.get("track")
    try:
        turn = await generate_interviewer_turn(
            ws_session_id,
            phase=phase,
            intake=rec.get("intake") or {},
            rubric=rec.get("rubric") or {},
            transcript_events=STORE.get_events(ws_session_id, 0),
            fake_llm=bool(rec.get("fake_llm")),
            track=track,
            persona=rec.get("persona"),
        )
    except asyncio.CancelledError:
        return None  # barged-in mid-generation — emit nothing

    seq_hint = (STORE.get_ledger(ws_session_id).last_seq) + 1
    payload: dict = {"lineId": f"llm-{seq_hint}", "turnId": turn_id}
    if turn:
        # Output-side persona guard — LLM text only. The prompt already forbids
        # verdicts/praise, but nothing enforced it; one "That's exactly right!"
        # leak breaks the never-confirm rule, so offending sentences are dropped
        # (whole line -> rotating neutral probe if nothing survives). Scripted
        # fallback lines are curated and stay exempt, as does the hint ladder
        # (it is the designated pushback lane). Scenario answer terms are
        # blocked until the help ladder's final attempt (never-reveal contract).
        spec = get_scenario(track)
        reveal_terms = tuple(spec.reveal_terms) if spec is not None else ()
        attempt = 1
        if reveal_terms:
            from .hint_ladder import _count_wrong_attempts
            attempt = _count_wrong_attempts(ws_session_id, "help")
        text, guard_reasons = guard_interviewer_line(
            turn["text"], seq=seq_hint, attempt=attempt, reveal_terms=reveal_terms
        )
        if guard_reasons:
            payload["guarded"] = True
            payload["guard_reasons"] = guard_reasons
    else:
        text = _scripted_interviewer_line(signal, track)
    payload["text"] = text
    return _emit(ws_session_id, "interviewer", "interviewer.utterance", payload)


async def _build_scorecard(session_id: str) -> tuple[list[dict], dict]:
    """Build per-criterion scores + the final draft.

    In ``mode == "llm"`` the LLM proposes evidence which is validated against the
    ledger (every EvidenceRef.seq must be a real event). In ``mode == "scripted"``
    or on ANY LLM failure we use the deterministic SCORE_PLANS scorecard. Either
    way every emitted EvidenceRef.seq cites a real ledger event.
    """
    rec = STORE.get_session(session_id)
    ledger = STORE.get_ledger(session_id)
    rubric = rec.get("rubric") or {}
    if rec.get("mode") == "llm":
        return await build_scorecard_llm(
            session_id,
            rec.get("intake") or {},
            rubric,
            ledger,
            rec.get("phase", "scoring"),
            fake_llm=bool(rec.get("fake_llm")),
        )
    return build_scripted_scorecard(session_id, rubric, ledger)


@router.websocket("/vnext/interview/ws/{session_id}")
async def vnext_interview_ws(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()

    # ── handshake: first frame must be a client_hello ──
    try:
        first = await websocket.receive_json()
    except Exception:
        await websocket.send_json(resume_rejected_message(REJECT_INVALID))
        await websocket.close()
        return

    parsed = parse_client_hello(first, route_session_id=session_id)
    if not hasattr(parsed, "last_seq"):  # HandshakeError
        await websocket.send_json(resume_rejected_message(getattr(parsed, "reason", REJECT_INVALID)))
        await websocket.close()
        return

    rec = STORE.get_session(session_id)
    if rec is None:
        await websocket.send_json(resume_rejected_message(REJECT_NOT_FOUND))
        await websocket.close()
        return

    ledger = STORE.get_ledger(session_id)
    backfill = select_backfill([{"payload": e} for e in ledger.get_all()], parsed.last_seq)
    await websocket.send_json(
        resume_ready_message(resumed=parsed.resume, from_seq=parsed.last_seq, snapshot={"phase": rec["phase"]})
    )
    backfill_max = max((e.get("seq", 0) for e in backfill), default=parsed.last_seq)
    await websocket.send_json(
        {
            "type": "resume_events",
            "from_seq": parsed.last_seq,
            "last_seq": max(parsed.last_seq, backfill_max),
            "events": backfill,
        }
    )

    # Onboarding is OPT-IN per session (rec["onboarding"], set at create).
    # Sessions that didn't opt in skip the readiness gate entirely — the
    # previous unconditional gate deadlocked every interview because
    # register_greeting had no production caller, so greeting_done could
    # never become true over the wire.
    session_init = SessionInitManager(session_id)
    onboarding_enabled = bool(rec.get("onboarding"))
    # Cached once onboarding completes so the hot path stops re-reading the
    # store's is_complete() on every subsequent message (completion is monotonic).
    onboarding_done = not onboarding_enabled
    if onboarding_enabled and not (rec.get(SessionInitManager.KEY) or {}).get("greeting_done"):
        greeting_text = (
            "Hi, I'm Maya. Quick check before we begin — say something so I can "
            "confirm your audio, and tell me when you're ready."
        )
        # Registering the greeting is what lets is_complete() ever become true.
        await websocket.send_json(session_init.register_greeting(greeting_text))
        await websocket.send_json(
            _emit(
                session_id,
                "interviewer",
                "interviewer.utterance",
                {"lineId": f"onboard-greet-{STORE.get_ledger(session_id).last_seq + 1}",
                 "text": greeting_text},
            )
        )

    # ── drive the session via inbound signals ──
    # A send lock serializes the main loop's sends with the background turn-
    # generation task's send (a websocket has one writer). active_turns maps a
    # live turnId → its generation task so barge_in can cancel it; cancelled_turns
    # suppresses a turn whose LLM output lands AFTER a barge-in.
    send_lock = asyncio.Lock()
    active_turns: dict[str, asyncio.Task] = {}
    cancelled_turns: set[str] = set()
    scheduled_interviewer_tasks: list[asyncio.Task] = []

    async def send(ev: dict) -> None:
        async with send_lock:
            await websocket.send_json(ev)

    async def run_turn(signal: str, phase: str, turn_id: str) -> None:
        try:
            ev = await _generate_turn(session_id, signal, phase, turn_id)
        except asyncio.CancelledError:
            return
        finally:
            active_turns.pop(turn_id, None)
        # Emit only if it wasn't cancelled while generating / just before emit.
        if ev is not None and turn_id not in cancelled_turns:
            await send(ev)

    async def _run_scheduled_interviewer(
        session_id: str, payload: dict, delay_ms: int, trigger_seq: int = 0
    ) -> None:
        # Sleep and then emit the interviewer. Append event at send-time.
        try:
            await asyncio.sleep(delay_ms / 1000.0)
            # Anti-double gate (Voice_Assist reply_already_handled, ledger-native):
            # if ANY interviewer line already landed after the candidate utterance
            # this hint was replying to (e.g. an LLM turn raced in during the
            # pause), the candidate has been answered — emitting the hint now
            # would be a second, stale reply. Cancel instead, auditable.
            if trigger_seq and any(
                e.get("type") == "interviewer.utterance"
                for e in STORE.get_events(session_id, trigger_seq)
            ):
                await send(_emit(session_id, "system", "system.pause.cancelled",
                                 {"intent": payload.get("hint_for"), "delay_ms": delay_ms,
                                  "reason": "superseded"}))
                return
            ev = _emit(session_id, "interviewer", "interviewer.utterance", {"lineId": f"dir-{STORE.get_ledger(session_id).last_seq + 1}", **payload})
            await send(ev)
            # emit completed marker
            await send(_emit(session_id, "system", "system.pause.completed", {"intent": payload.get("hint_for"), "delay_ms": delay_ms}))
        except asyncio.CancelledError:
            return
        finally:
            # remove this task from the scheduling registry if present
            try:
                this_task = asyncio.current_task()
                if this_task in scheduled_interviewer_tasks:
                    scheduled_interviewer_tasks.remove(this_task)
            except Exception:
                pass

    try:
        while True:
            msg = await websocket.receive_json()
            if not isinstance(msg, dict):
                continue
            mtype = msg.get("type")

            if mtype == "advance.request":
                signal = str(msg.get("signal", ""))
                sess = STORE.get_session(session_id)
                # Readiness gate applies ONLY to opted-in sessions that haven't
                # finished onboarding; every other flow advances freely. Once
                # complete we cache it so this store read never recurs.
                if not onboarding_done:
                    try:
                        if session_init.is_complete():
                            onboarding_done = True
                        else:
                            # Remind candidate to finish onboarding; do not advance.
                            await send(
                                _emit(
                                    session_id,
                                    "interviewer",
                                    "interviewer.utterance",
                                    {"lineId": f"onboard-{STORE.get_ledger(session_id).last_seq + 1}",
                                     "text": "Please complete the quick audio and readiness checks before we begin."},
                                )
                            )
                            continue
                    except Exception:
                        # keep going if session_init check fails
                        onboarding_done = True
                if sess and sess.get("mode") == "llm":
                    prelude, turn_id, phase = _advance_llm_prelude(session_id, signal)
                    for ev in prelude:
                        await send(ev)
                    if turn_id is not None:
                        # Generate the line in the background so a barge_in mid-
                        # generation can cancel it before it ever reaches the ledger.
                        active_turns[turn_id] = asyncio.create_task(
                            run_turn(signal, phase, turn_id)
                        )
                else:
                    for ev in _drive_advance(session_id, signal):
                        await send(ev)

            elif mtype in ("barge_in", "interviewer.cancel"):
                # Candidate took the floor. Cancel the in-flight interviewer turn
                # (named turnId, else every active turn) so its late LLM output is
                # never emitted as the active question. Already-emitted ledger
                # events are kept; only stale FUTURE output is suppressed.
                tid = msg.get("turnId")
                targets = [tid] if tid else list(active_turns.keys())
                for t in targets:
                    if not isinstance(t, str) or not t:
                        continue
                    cancelled_turns.add(t)
                    task = active_turns.get(t)
                    if task is not None and not task.done():
                        task.cancel()
                    await send(_emit(session_id, "system", "interviewer.cancelled", {"turnId": t}))
                # Also cancel any scheduled interviewer utterances
                for task in list(scheduled_interviewer_tasks):
                    meta = getattr(task, "scheduled_meta", None) or {}
                    try:
                        task.cancel()
                        await send(_emit(session_id, "system", "system.pause.cancelled", {"intent": meta.get("payload", {}).get("hint_for"), "delay_ms": meta.get("delay_ms")}))
                    except Exception:
                        pass
                scheduled_interviewer_tasks.clear()

            elif mtype == "candidate.text":
                text_str = str(msg.get("text", ""))

                # ── opt-in onboarding: setup step, not a barge-in or an answer ──
                # While onboarding is incomplete an utterance only drives the
                # audio/readiness checks; it deliberately skips the interrupt +
                # hint machinery so an audio-problem report ("no sound") can't
                # trigger a cut-in acknowledgment or a spurious barge_in.detected.
                if not onboarding_done:
                    low = (text_str or "").lower()
                    seq_hint = STORE.get_ledger(session_id).last_seq + 1
                    await send(_emit(session_id, "candidate", "candidate.utterance",
                                     {"lineId": f"cand-{seq_hint}", "text": text_str}))
                    if "muted" in low or "no sound" in low or "can't hear" in low or "cannot hear" in low:
                        await send(session_init.mark_audio_problem(text_str))
                    else:
                        # Receiving ANY candidate speech proves the mic/STT work,
                        # so onboarding no longer soft-locks when the candidate
                        # never happens to say a "can you hear me" phrase.
                        await send(session_init.mark_audio_ok())
                        # Whole-word readiness only ("already" must not match);
                        # a negated "…not ready" never confirms.
                        if _READY_RX.search(low) and not _NOT_READY_RX.search(low):
                            await send(session_init.mark_ready())
                    if session_init.is_complete():
                        onboarding_done = True
                    continue

                # Classify ONCE; the same intent is passed into
                # _handle_candidate_text so urgency and hint decisions can
                # never diverge (a provider could otherwise disagree between
                # two calls on the same utterance).
                try:
                    intent = _CLASSIFIER.classify(text_str, session_id)
                except Exception:
                    intent = "answer"

                # Urgency is decided by the DETERMINISTIC cut-in check, NOT the
                # (possibly provider-supplied) final intent, so timing never
                # depends on a nondeterministic classifier: "wait, I'm stuck"
                # routes to the 'help' ladder but still cancels scheduled speech.
                is_urgent_interrupt = _CLASSIFIER.is_cut_in(text_str)
                cancelled_any = False

                if is_urgent_interrupt:
                    # Cut-in: aggressively cancel all scheduled interviewer utterances.
                    for task in list(scheduled_interviewer_tasks):
                        meta = getattr(task, "scheduled_meta", None) or {}
                        try:
                            task.cancel()
                            cancelled_any = True
                            await send(_emit(session_id, "system", "system.pause.cancelled",
                                           {"intent": meta.get("payload", {}).get("hint_for"),
                                            "delay_ms": meta.get("delay_ms"),
                                            "reason": "cut_in"}))
                        except Exception:
                            pass
                    scheduled_interviewer_tasks.clear()
                    # Emit barge-in latency marker (for monitoring).
                    await send(_emit(session_id, "system", "barge_in.detected",
                                   {"intent": intent, "text": text_str}))

                # Pass force_immediate flag if this was an urgent interrupt.
                for ev in _handle_candidate_text(session_id, text_str, force_immediate=cancelled_any, intent=intent):
                    # scheduled interviewer markers are dicts with key 'scheduled_interviewer'
                    if isinstance(ev, dict) and ev.get("scheduled_interviewer"):
                        si = ev["scheduled_interviewer"]
                        payload = si.get("payload") or {}
                        delay_ms = int(si.get("delay_ms", 0) or 0)
                        trigger_seq = int(si.get("trigger_seq", 0) or 0)
                        # create background task to run scheduled interviewer
                        task = asyncio.create_task(
                            _run_scheduled_interviewer(session_id, payload, delay_ms, trigger_seq)
                        )
                        # attach metadata for cancellation reporting
                        try:
                            setattr(task, "scheduled_meta", {"payload": payload, "delay_ms": delay_ms})
                        except Exception:
                            pass
                        scheduled_interviewer_tasks.append(task)
                        continue
                    await send(ev)

            elif mtype == "candidate.code":
                code = str(msg.get("code", ""))
                seq_hint = (STORE.get_ledger(session_id).last_seq) + 1
                ev = _emit(
                    session_id,
                    "candidate",
                    "code.edited",
                    {"editId": f"edit-{seq_hint}", "after": code, "by": "candidate"},
                )
                await send(ev)
                # Scenario tracks: Maya reviews the buffer and, if the spec's
                # detector flags it, selects the risky lines and (incident only)
                # proposes a validated patch.
                for action in _scenario_code_actions(session_id, code):
                    await send(action)

            elif mtype in ("code.patch.accept", "code.patch.reject"):
                for action in _resolve_patch(session_id, mtype, str(msg.get("patchId", ""))):
                    await send(action)

            elif mtype == "candidate.run":
                run_code = str(msg.get("code", ""))
                seq_hint = (STORE.get_ledger(session_id).last_seq) + 1
                run_payload: dict = {"runId": f"run-{seq_hint}", "code": run_code}
                rec_run = STORE.get_session(session_id)
                run_spec = get_scenario(rec_run.get("track")) if rec_run else None
                if run_spec is not None and run_spec.problem is not None and run_spec.problem.runnable:
                    # REAL execution against the scenario's test cases (sandboxed
                    # subprocess, hard timeout) — run in a thread so a slow run
                    # never blocks the event loop / barge-in handling.
                    from .runner import run_candidate_code
                    from .scenario import _fn_name
                    result = await asyncio.to_thread(
                        run_candidate_code, run_code, run_spec.problem,
                        _fn_name(run_spec.problem.function_signature),
                    )
                    run_payload.update(result)
                else:
                    # No runnable scenario: honest stub (never fake a pass).
                    run_payload.update({"stdout": "", "exitCode": 0})
                ev = _emit(session_id, "candidate", "code.run", run_payload)
                await send(ev)

            elif mtype == "scorecard.request":
                # _build_scorecard is bounded (see build_scorecard_llm's
                # asyncio.wait_for budget) and always returns a complete
                # scorecard, so the normal path always reaches
                # scorecard.completed. The guard below ensures the socket can
                # NEVER hang on "scoring…": if the build somehow raises, we emit
                # an additive scorecard.failed terminal event instead of nothing.
                try:
                    scores, draft = await _build_scorecard(session_id)
                except Exception as exc:  # noqa: BLE001 — must always emit a terminal event
                    ev = _emit(
                        session_id,
                        "system",
                        "scorecard.failed",
                        {"reason": type(exc).__name__},
                    )
                    await send(ev)
                else:
                    for score in scores:
                        ev = _emit(session_id, "system", "scorecard.criterion.ready", {"score": score})
                        await send(ev)
                    STORE.put_scorecard(session_id, draft)
                    ev = _emit(session_id, "system", "scorecard.completed", {"draft": draft})
                    await send(ev)

            # Unknown types are ignored (forward-compat).
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass
        return
    finally:
        # Never leak a background generation task past the connection.
        for task in list(active_turns.values()):
            if not task.done():
                task.cancel()
