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

from .incident import (
    INCIDENT_PATCH_UTTERANCE,
    INCIDENT_TRACK,
    incident_code_is_unsafe,
    incident_line,
    incident_patch,
    incident_patch_is_safe,
    incident_risky_range,
)
from .llm import build_scorecard_llm, build_scripted_scorecard, generate_interviewer_turn
from .phase_controller import PhaseController, TransitionContext
from .seed import (
    script_event_to_payload,
    script_events_by_signal,
)
from .store import STORE
from .session_init import SessionInitManager
from .hint_ladder import next_hint
from .hint_provider import get_hint_for
from .pause_policy import get_pause_for

router = APIRouter()

_SCRIPT_BY_SIGNAL = script_events_by_signal()


def _scripted_interviewer_line(signal: str, track: str | None = None) -> str:
    """First scripted interviewer line for a signal's turn (llm fallback text).

    For the incident track the fallback is the deterministic incident line so the
    fake/no-LLM path stays in the incident narrative.
    """
    if track == INCIDENT_TRACK:
        return incident_line(signal)
    for ev in _SCRIPT_BY_SIGNAL.get(signal, []):
        if ev.get("kind") == "interviewer.utterance":
            return ev["text"]
    return "Let's keep going."


def _emit(session_id: str, actor: str, type_: str, payload: dict) -> dict:
    return STORE.append_event(session_id, actor, type_, payload)


_CLASSIFIER = get_classifier()


def _intent_reply_text(intent: str) -> str | None:
    replies = {
        "repeat": "Let me repeat the goal more clearly so you can focus on the next concrete step.",
        "help": "Let's narrow this to the next step: identify the failing condition and write the first guard or check in the code box.",
        "thinking": "Take your time. I’ll wait while you work through it.",
        "meta_audio": "I can hear you. Please continue and I’ll stay with you.",
    }
    return replies.get(intent)


def _handle_candidate_text(session_id: str, text: str, force_immediate: bool = False) -> list[dict]:
    """Emit the candidate utterance plus a focused director event for recognized intents.
    
    Backchannels ("yeah", "okay", etc.) are logged but don't emit hints.
    Cut-in words ("wait", "stop", etc.) force immediate cancellation + hint.
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
        # If a recent candidate barge-in cancelled an interviewer, the UX expects
        # an immediate corrective hint — bypass pause policies when forced.
        if force_immediate:
            payload = {"lineId": f"dir-{seq_hint}", "text": hint_payload.get("text", ""),
                       "hint_for": hint_payload.get("hint_for"), "hint_step": hint_payload.get("hint_step"),
                       "exhausted": hint_payload.get("exhausted", False)}
            out.append(_emit(session_id, "interviewer", "interviewer.utterance", payload))
            return out

        pause_ms = 0
        try:
            pause_ms = int(get_pause_for(session_id, intent) or 0)
        except Exception:
            pause_ms = 0

        if pause_ms > 0:
            # schedule interviewer utterance later; emit a system.pause.scheduled event
            out.append(_emit(session_id, "system", "system.pause.scheduled", {"intent": intent, "delay_ms": pause_ms}))
            # return a scheduled marker that the WS loop will pick up and schedule
            out.append({"scheduled_interviewer": {"payload": hint_payload, "delay_ms": pause_ms}})
        else:
            payload = {"lineId": f"dir-{seq_hint}", "text": hint_payload.get("text", ""),
                       "hint_for": hint_payload.get("hint_for"), "hint_step": hint_payload.get("hint_step"),
                       "exhausted": hint_payload.get("exhausted", False)}
            out.append(_emit(session_id, "interviewer", "interviewer.utterance", payload))
    return out


def _incident_code_actions(session_id: str, code: str) -> list[dict]:
    """After a candidate code edit on the incident track: if the buffer is still
    racy, Maya selects/highlights the read-before-write path, explains it, and
    proposes an idempotent patch. No-op off the incident track or when safe, and
    never proposes a second patch while one is still open."""
    rec = STORE.get_session(session_id)
    if not rec or rec.get("track") != INCIDENT_TRACK:
        return []
    if not incident_code_is_unsafe(code):
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

    out: list[dict] = []
    start, end = incident_risky_range(code)
    out.append(
        _emit(session_id, "interviewer", "selection.set",
              {"selection": {"start": start, "end": end, "owner": "interviewer"}})
    )
    out.append(_emit(session_id, "interviewer", "highlight.set", {"line": start}))
    out.append(
        _emit(session_id, "interviewer", "interviewer.utterance",
              {"lineId": f"maya-{STORE.get_ledger(session_id).last_seq + 1}",
               "text": INCIDENT_PATCH_UTTERANCE})
    )
    patch = incident_patch(code)
    if not incident_patch_is_safe(patch["after"], patch["before"]):
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
        )
    except asyncio.CancelledError:
        return None  # barged-in mid-generation — emit nothing

    text = turn["text"] if turn else _scripted_interviewer_line(signal, track)
    seq_hint = (STORE.get_ledger(ws_session_id).last_seq) + 1
    return _emit(
        ws_session_id,
        "interviewer",
        "interviewer.utterance",
        {"lineId": f"llm-{seq_hint}", "text": text, "turnId": turn_id},
    )


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

    # Initialize session-init manager (do not auto-emit greeting here to
    # preserve existing scripted event ordering; callers may register greeting
    # later when appropriate).
    session_init = SessionInitManager(session_id)

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

    async def _run_scheduled_interviewer(session_id: str, payload: dict, delay_ms: int) -> None:
        # Sleep and then emit the interviewer. Append event at send-time.
        try:
            await asyncio.sleep(delay_ms / 1000.0)
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
                # Block advances until onboarding complete.
                try:
                    if not session_init.is_complete():
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
                    pass
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
                # Inspect intent first to determine if urgent interrupt.
                try:
                    intent = _CLASSIFIER.classify(text_str, session_id)
                except Exception:
                    intent = "answer"

                # Determine if this is an urgent interrupt (cut-in word like "wait", "stop").
                # Only cancel scheduled interviewer tasks on urgent interrupts, not on every text.
                is_urgent_interrupt = intent == "cut_in"
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

                # quick audio/problem heuristics
                low = (text_str or "").lower()
                if "muted" in low or "no sound" in low or "can't hear" in low or "cannot hear" in low:
                    ev = session_init.mark_audio_problem(text_str)
                    await send(ev)
                elif intent == "meta_audio":
                    # candidate checking connectivity → mark audio ok
                    ev = session_init.mark_audio_ok()
                    await send(ev)

                # explicit readiness phrases
                ready_phrases = ("ready", "i'm ready", "im ready", "i am ready")
                if any(p in low for p in ready_phrases):
                    ev = session_init.mark_ready()
                    await send(ev)

                # Pass force_immediate flag if this was an urgent interrupt.
                for ev in _handle_candidate_text(session_id, text_str, force_immediate=cancelled_any):
                    # scheduled interviewer markers are dicts with key 'scheduled_interviewer'
                    if isinstance(ev, dict) and ev.get("scheduled_interviewer"):
                        si = ev["scheduled_interviewer"]
                        payload = si.get("payload") or {}
                        delay_ms = int(si.get("delay_ms", 0) or 0)
                        # create background task to run scheduled interviewer
                        task = asyncio.create_task(_run_scheduled_interviewer(session_id, payload, delay_ms))
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
                # Incident track: Maya reviews the buffer and, if it's still racy,
                # selects the risky lines and proposes an idempotent patch.
                for action in _incident_code_actions(session_id, code):
                    await send(action)

            elif mtype in ("code.patch.accept", "code.patch.reject"):
                for action in _resolve_patch(session_id, mtype, str(msg.get("patchId", ""))):
                    await send(action)

            elif mtype == "candidate.run":
                seq_hint = (STORE.get_ledger(session_id).last_seq) + 1
                ev = _emit(
                    session_id,
                    "candidate",
                    "code.run",
                    {"runId": f"run-{seq_hint}", "code": str(msg.get("code", "")), "stdout": "", "exitCode": 0},
                )
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
