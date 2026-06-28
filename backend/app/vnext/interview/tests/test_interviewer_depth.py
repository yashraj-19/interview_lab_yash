"""Deeper technical interviewer: evidence-aware probes, no generic praise, and
strict scorecard caps + JD generation. NO network — prompt-builder is tested
directly; the provider transport is monkeypatched where the LLM is exercised.
"""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.vnext.interview.llm.client as llm_client
from app.config import settings
from app.vnext.interview.llm.interviewer_llm import (
    _FORBIDDEN_PRAISE,
    _FORBIDDEN_OPENERS,
    _VARIED_OPENERS,
    _build_messages,
    compute_evidence_status,
    missing_evidence_summary,
)
from app.vnext.interview.llm.jd_llm import _fallback_jd, generate_jd
from app.vnext.interview.llm.scorecard_llm import validate_scorecard_scores
from app.vnext.interview.router import router

# A payments/backend transcript like the failure case: polished SELF-REPORT
# answers, NO code/code_run events anywhere.
_SOFT_TRANSCRIPT = [
    {"seq": 1, "type": "interviewer.utterance", "actor": "interviewer", "text": "Tell me about your background."},
    {"seq": 2, "type": "candidate.utterance", "actor": "candidate",
     "text": "I worked on payments at scale and collaborated closely with my team."},
    {"seq": 3, "type": "interviewer.utterance", "actor": "interviewer", "text": "What was a challenge?"},
    {"seq": 4, "type": "candidate.utterance", "actor": "candidate",
     "text": "We improved reliability and I cared a lot about code quality."},
]

_TECH_TERMS = ("schema", "index", "transaction", "concurrency", "idempot",
               "complexity", "code", "api", "tradeoff")


def _joined(messages) -> str:
    return "\n".join(m["content"] for m in messages).lower()


# ── deeper technical probe (prompt-builder) ───────────────────────────────────

def test_soft_transcript_yields_technical_probe_prompt():
    status = compute_evidence_status(_SOFT_TRANSCRIPT)
    assert status["code"] is False  # no code events
    summary = missing_evidence_summary(status)
    msgs = _build_messages(
        phase="resume_calibration",
        intake={"role": "Backend Engineer", "seniority": "senior",
                "languages": ["Python"], "jobDescription": "payments backend"},
        rubric={"criteria": [{"name": "Technical Competence"}]},
        transcript="(soft answers)",
        evidence_summary=summary,
        last_answer="I cared a lot about code quality.",
    )
    blob = _joined(msgs)
    assert any(term in blob for term in _TECH_TERMS)
    # code is missing -> prompt explicitly demands it / the code box.
    assert "code box" in blob


def test_coding_phase_asks_for_code():
    msgs = _build_messages(
        phase="coding", intake={"role": "Backend Engineer"}, rubric={"criteria": []},
        transcript="(t)", evidence_summary=missing_evidence_summary(compute_evidence_status([])),
    )
    blob = _joined(msgs)
    assert "code box" in blob and "code" in blob


def test_no_generic_praise_in_prompt():
    msgs = _build_messages(
        phase="resume_calibration", intake={"role": "Backend"}, rubric={"criteria": []},
        transcript="(t)", evidence_summary="x",
    )
    system = msgs[0]["content"]
    # The system prompt forbids these and must not itself model them affirmatively.
    for phrase in ("Great to hear", "That's a solid approach"):
        # appears only inside the explicit prohibition list, never as guidance.
        assert system.count(phrase) <= 1
    assert "NEVER use generic praise" in system
    # The constant the WS/tests rely on stays populated.
    assert "solid approach" in _FORBIDDEN_PRAISE


def test_prompt_forbids_repetitive_openers_and_encourages_variety():
    msgs = _build_messages(
        phase="coding", intake={"role": "Backend"}, rubric={"criteria": []},
        transcript="(t)", evidence_summary="x",
    )
    system = msgs[0]["content"]
    # The varied-phrasing rule is present and names the forbidden openers.
    assert "VARY YOUR PHRASING" in system
    for opener in _FORBIDDEN_OPENERS:
        assert opener in system
    # Encourages natural alternatives and bans back-to-back repetition.
    assert any(alt in system for alt in _VARIED_OPENERS)
    assert "consecutive" in system.lower()


def test_prompt_warns_against_repeating_previous_opening():
    msgs = _build_messages(
        phase="coding", intake={"role": "Backend"}, rubric={"criteria": []},
        transcript="(t)", evidence_summary="x",
        prev_opening="Can you walk me through",
    )
    user = msgs[1]["content"]
    assert "previous turn opened with" in user
    assert "Can you walk me through" in user


# ── strict scorecard caps ─────────────────────────────────────────────────────

def _ledger_no_code():
    return [
        {"seq": 2, "type": "candidate.utterance", "actor": "candidate",
         "text": "I built a payments service and cared about quality and the team."},
    ]


def _coding_rubric():
    return {"id": "r", "criteria": [
        {"id": "tech", "name": "Technical Competence", "description": "coding and implementation", "weight": 100},
    ]}


def test_coding_criterion_capped_without_code():
    parsed = {"scores": [
        {"criterionId": "tech", "score": 85, "verdict": "strong",
         "evidence": [{"seq": 2, "kind": "utterance", "excerpt": "I built a payments service"}], "gaps": []},
    ]}
    scores = validate_scorecard_scores(_coding_rubric(), _ledger_no_code(), parsed)
    s = scores[0]
    assert s["score"] <= 70
    assert s["verdict"] in {"mixed", "weak", "insufficient_evidence"}


def test_collaboration_capped_without_conflict_evidence():
    rubric = {"id": "r", "criteria": [
        {"id": "collab", "name": "Collaboration", "description": "teamwork and communication", "weight": 100},
    ]}
    parsed = {"scores": [
        {"criterionId": "collab", "score": 90, "verdict": "strong",
         "evidence": [{"seq": 2, "kind": "utterance", "excerpt": "cared about quality and the team"}], "gaps": []},
    ]}
    scores = validate_scorecard_scores(rubric, _ledger_no_code(), parsed)
    s = scores[0]
    assert s["score"] <= 80
    assert s["verdict"] in {"mixed", "weak", "insufficient_evidence"}


def test_caps_do_not_apply_with_code_and_conflict_evidence():
    ledger = [
        {"seq": 2, "type": "candidate.utterance", "actor": "candidate",
         "text": "I disagreed with the team and negotiated the transaction boundary; I owned the design."},
        {"seq": 3, "type": "code.edited", "actor": "candidate", "after": "def charge(): ..."},
        {"seq": 4, "type": "code.run", "actor": "candidate", "code": "charge()", "stdout": "ok", "exitCode": 0},
    ]
    rubric = {"id": "r", "criteria": [
        {"id": "tech", "name": "Technical Competence", "description": "coding", "weight": 50},
        {"id": "collab", "name": "Collaboration", "description": "teamwork", "weight": 50},
    ]}
    parsed = {"scores": [
        {"criterionId": "tech", "score": 88, "verdict": "strong",
         "evidence": [{"seq": 3, "kind": "code", "excerpt": "def charge"}], "gaps": []},
        {"criterionId": "collab", "score": 90, "verdict": "strong",
         "evidence": [{"seq": 2, "kind": "utterance", "excerpt": "I disagreed with the team"}], "gaps": []},
    ]}
    scores = validate_scorecard_scores(rubric, ledger, parsed)
    by_id = {s["criterionId"]: s for s in scores}
    assert by_id["tech"]["score"] == 88 and by_id["tech"]["verdict"] == "strong"
    assert by_id["collab"]["score"] == 90 and by_id["collab"]["verdict"] == "strong"


def test_thin_ledger_is_conservative():
    # Self-report only, no evidence cited -> insufficient.
    parsed = {"scores": [
        {"criterionId": "tech", "score": 80, "verdict": "strong", "evidence": [], "gaps": []},
    ]}
    scores = validate_scorecard_scores(_coding_rubric(), _ledger_no_code(), parsed)
    assert scores[0]["verdict"] == "insufficient_evidence"


# ── JD generation ─────────────────────────────────────────────────────────────

def test_jd_fallback_template_role_relevant():
    jd = _fallback_jd("Backend Engineer", "senior", ["Python", "Go"])
    assert "Backend Engineer" in jd and "Python" in jd
    assert len(jd) > 80


def test_generate_jd_fake_returns_template():
    jd = asyncio.run(generate_jd("Backend Engineer", "senior", ["Python"], fake_llm=True))
    assert "Backend Engineer" in jd and jd.strip()


def test_generate_jd_no_key_returns_template(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    jd = asyncio.run(generate_jd("Data Engineer", "mid", ["SQL"]))
    assert "Data Engineer" in jd


def test_jd_endpoint_returns_job_description(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    r = c.post("/vnext/interview/jd", json={"role": "Backend Engineer", "seniority": "senior",
                                            "languages": ["Python"]})
    assert r.status_code == 200
    jd = r.json()["jobDescription"]
    assert "Backend Engineer" in jd and len(jd) > 80


def test_jd_endpoint_fake_flag_gated(monkeypatch):
    monkeypatch.setenv("VNEXT_ALLOW_FAKE_LLM", "1")
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key", raising=False)

    async def fake_chat(url, headers, body, timeout):
        return "SHOULD NOT BE USED"

    monkeypatch.setattr(llm_client, "_chat_completion", fake_chat)
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    r = c.post("/vnext/interview/jd", json={"role": "Backend Engineer", "fake_llm": True})
    jd = r.json()["jobDescription"]
    assert "Backend Engineer" in jd and "SHOULD NOT BE USED" not in jd
