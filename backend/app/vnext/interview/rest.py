"""vNext interview REST surface.

Deterministic, no LLM, no Supabase. Phase transitions always flow through the
PhaseController; the SessionLedger is the single seq writer.
"""
from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .incident import INCIDENT_TRACK, incident_rubric
from .llm import generate_jd, generate_rubric_llm
from .models import Intake
from .phase_controller import PhaseController, TransitionContext
from .seed import generate_rubric
from .store import STORE, active_store_mode

router = APIRouter(prefix="/vnext/interview", tags=["vnext-interview"])

# "scripted" = deterministic seed (default). "llm" = OpenRouter-first adaptive
# path with scripted fallback. Persisted on the session; ws/rest branch on it.
SessionMode = Literal["scripted", "llm"]


def fake_llm_allowed() -> bool:
    """TEST-ONLY gate. A per-request ``fake_llm`` flag is honored ONLY when this
    env opt-in is set, so production can never be forced onto the canned path."""
    return os.getenv("VNEXT_ALLOW_FAKE_LLM") == "1"


class CreateSessionBody(BaseModel):
    intake: Intake
    mode: SessionMode = "scripted"
    # TEST-ONLY: when true AND the backend allows it (VNEXT_ALLOW_FAKE_LLM=1) the
    # llm-mode session skips OpenRouter and uses deterministic scripted content
    # over the SAME real WS/store/controller path. Ignored otherwise.
    fake_llm: bool = False
    # Optional lab-only interview track (e.g. "incident-demo"). None = default flow.
    track: str | None = None


class RubricBody(BaseModel):
    intake: Intake | None = None


class JdBody(BaseModel):
    role: str
    seniority: str = "mid"
    languages: list[str] = []
    # TEST-ONLY: honored only when VNEXT_ALLOW_FAKE_LLM=1 (deterministic template).
    fake_llm: bool = False


@router.get("/warmup")
async def warmup() -> dict:
    """Trivial liveness ping. No DB/Redis, no auth (mirrors interviews warmup).

    Reports the active store mode ("memory" | "supabase") for debugging.
    """
    return {"ok": True, "store": active_store_mode()}


@router.post("/jd")
async def generate_jd_endpoint(body: JdBody) -> dict:
    """Auto-generate a job description from {role, seniority?, languages?}.

    OpenRouter-first with a deterministic template fallback. The ``fake_llm`` flag
    forces the template, but only when VNEXT_ALLOW_FAKE_LLM=1 (otherwise ignored).
    """
    fake = bool(body.fake_llm) and fake_llm_allowed()
    jd = await generate_jd(body.role, body.seniority, body.languages, fake_llm=fake)
    return {"jobDescription": jd}


@router.post("/sessions")
async def create_session(body: CreateSessionBody) -> dict:
    """Create a session from intake and advance intake -> rubric."""
    intake = body.intake.model_dump()
    session_id = STORE.create_session(intake)
    rec = STORE.get_session(session_id)
    rec["mode"] = body.mode
    rec["fake_llm"] = bool(body.fake_llm) and fake_llm_allowed()
    rec["track"] = body.track
    STORE.put_session(session_id, rec)
    ledger = STORE.get_ledger(session_id)

    controller = PhaseController(rec["phase"])
    ctx = TransitionContext(hasRubric=rec["rubric"] is not None, lastSeq=ledger.last_seq)
    result = controller.request("intake.submitted", ctx)
    if result.ok:
        STORE.append_event(
            session_id,
            "system",
            "phase.changed",
            {"from": result.from_, "to": result.to, "signal": result.signal},
        )
        rec["phase"] = controller.phase
        STORE.put_session(session_id, rec)

    return {"sessionId": session_id, "phase": rec["phase"], "mode": rec.get("mode", "scripted")}


@router.post("/sessions/{session_id}/rubric")
async def bind_rubric(session_id: str, body: RubricBody) -> dict:
    """Deterministically generate the rubric, bind it, advance rubric -> ready."""
    rec = STORE.get_session(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="session_not_found")

    intake = body.intake.model_dump() if body.intake is not None else rec["intake"]
    if rec.get("track") == INCIDENT_TRACK:
        # Deterministic, incident-shaped rubric that rewards code/concurrency/test/
        # ops/tradeoff evidence — same whether the LLM is live or faked.
        rubric = incident_rubric(session_id)
    elif rec.get("mode") == "llm":
        # LLM rubric with deterministic scripted fallback baked in.
        rubric = await generate_rubric_llm(session_id, intake, fake_llm=bool(rec.get("fake_llm")))
    else:
        rubric = generate_rubric(session_id, intake)

    rec["rubric"] = rubric
    rec["intake"] = intake
    STORE.put_session(session_id, rec)

    # Bind first so the controller's hasRubric guard sees it.
    STORE.append_event(session_id, "system", "rubric.bound", {"rubric": rubric})

    ledger = STORE.get_ledger(session_id)
    controller = PhaseController(rec["phase"])
    ctx = TransitionContext(hasRubric=True, lastSeq=ledger.last_seq)
    result = controller.request("rubric.generated", ctx)
    if result.ok:
        STORE.append_event(
            session_id,
            "system",
            "phase.changed",
            {"from": result.from_, "to": result.to, "signal": result.signal},
        )
        rec["phase"] = controller.phase
        STORE.put_session(session_id, rec)

    return {"rubric": rubric}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    rec = STORE.get_session(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    ledger = STORE.get_ledger(session_id)
    return {
        "sessionId": session_id,
        "intake": rec["intake"],
        "rubric": rec["rubric"],
        "phase": rec["phase"],
        "lastSeq": ledger.last_seq if ledger else 0,
    }


@router.get("/sessions/{session_id}/ledger")
async def get_ledger(session_id: str, since: int = 0) -> dict:
    rec = STORE.get_session(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    return {"events": STORE.get_events(session_id, since)}


@router.get("/sessions/{session_id}/review")
async def get_review(session_id: str) -> dict:
    rec = STORE.get_session(session_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    return {
        "ledger": STORE.get_events(session_id, 0),
        "scorecard": rec["scorecard"],
        "rubric": rec["rubric"],
    }
