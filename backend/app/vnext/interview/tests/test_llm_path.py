"""LLM path: provider order, rubric validation/repair, interviewer turn, and
mode wiring. NO network — the provider transport is monkeypatched with fakes.
"""
import asyncio
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.vnext.interview.llm.client as llm_client
from app.config import settings
from app.vnext.interview.llm.client import LLMUnavailable, call_llm
from app.vnext.interview.llm.rubric_llm import (
    generate_rubric_llm,
    validate_rubric_payload,
)
from app.vnext.interview.router import router

CONN_ID = "conn-test-0001-abcd"


@pytest.fixture(autouse=True)
def _isolate_provider_keys(monkeypatch):
    """Enforce this module's NO-network contract: a developer's REAL keys in
    backend/.env must never leak into these tests (each test sets exactly the
    fake keys it needs)."""
    for field in ("openrouter_api_key", "groq_api_key", "gemini_api_key", "openai_api_key"):
        monkeypatch.setattr(settings, field, "", raising=False)


# ── provider abstraction ──────────────────────────────────────────────────────

def test_no_keys_raises_unavailable():
    with pytest.raises(LLMUnavailable):
        asyncio.run(call_llm([{"role": "user", "content": "hi"}]))


def test_openrouter_tried_first(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "oai-key", raising=False)
    calls = []

    async def fake_chat(url, headers, body, timeout):
        calls.append(url)
        return "ok-from-openrouter"

    monkeypatch.setattr(llm_client, "_chat_completion", fake_chat)
    out = asyncio.run(call_llm([{"role": "user", "content": "hi"}]))
    assert out == "ok-from-openrouter"
    assert len(calls) == 1
    assert "openrouter.ai" in calls[0]


def test_falls_back_to_openai_when_openrouter_errors(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "oai-key", raising=False)
    # Isolate from real keys in a developer's .env: with groq/gemini configured
    # they would (correctly) slot in between openrouter and openai.
    monkeypatch.setattr(settings, "groq_api_key", "", raising=False)
    monkeypatch.setattr(settings, "gemini_api_key", "", raising=False)
    calls = []

    async def fake_chat(url, headers, body, timeout):
        calls.append(url)
        if "openrouter.ai" in url:
            raise RuntimeError("boom")
        return "ok-from-openai"

    monkeypatch.setattr(llm_client, "_chat_completion", fake_chat)
    out = asyncio.run(call_llm([{"role": "user", "content": "hi"}]))
    assert out == "ok-from-openai"
    assert len(calls) == 2
    assert "openrouter.ai" in calls[0] and "api.openai.com" in calls[1]


# ── rubric validation / repair ────────────────────────────────────────────────

def test_rubric_good_json():
    data = {
        "criteria": [
            {"id": "ps", "name": "Problem solving", "description": "x",
             "weight": 50, "signals": ["a"], "phaseHints": ["coding"]},
            {"id": "comm", "name": "Communication", "description": "y",
             "weight": 50, "signals": ["b"], "phaseHints": ["intro"]},
        ]
    }
    rub = validate_rubric_payload("s1", data)
    assert rub is not None
    assert rub["generatedBy"] == "llm"
    assert sum(c["weight"] for c in rub["criteria"]) == 100


def test_rubric_bad_weights_repaired():
    data = {
        "criteria": [
            {"id": "a", "name": "A", "description": "x", "weight": 3,
             "signals": ["s"], "phaseHints": ["coding"]},
            {"id": "b", "name": "B", "description": "y", "weight": 1,
             "signals": ["s"], "phaseHints": ["intro"]},
        ]
    }
    rub = validate_rubric_payload("s1", data)
    assert rub is not None
    assert sum(c["weight"] for c in rub["criteria"]) == 100


def test_rubric_missing_criteria_is_none():
    assert validate_rubric_payload("s1", {"criteria": []}) is None
    assert validate_rubric_payload("s1", {"foo": 1}) is None
    assert validate_rubric_payload("s1", "not json") is None


def test_rubric_slow_provider_times_out_under_budget(monkeypatch):
    # A provider that takes 5s must NOT make the candidate wait: the wall-clock
    # budget cancels it and the deterministic scripted rubric is returned fast.
    monkeypatch.setenv("VNEXT_RUBRIC_BUDGET_S", "0.3")
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)

    async def slow_chat(url, headers, body, timeout):
        await asyncio.sleep(5)
        return "{}"

    monkeypatch.setattr(llm_client, "_chat_completion", slow_chat)

    intake = {"role": "Backend", "seniority": "mid", "languages": [],
              "resumeText": "", "jobDescription": "", "durationMinutes": 45}
    start = time.monotonic()
    rub = asyncio.run(generate_rubric_llm("s-slow", intake))
    elapsed = time.monotonic() - start

    assert rub["generatedBy"] == "scripted"  # fell back under budget
    assert sum(c["weight"] for c in rub["criteria"]) == 100
    assert elapsed < 2.0  # well under the slow provider's 5s


def test_rubric_invalid_phase_hint_is_none():
    data = {"criteria": [
        {"id": "a", "name": "A", "description": "x", "weight": 100,
         "signals": ["s"], "phaseHints": ["not_a_phase"]},
    ]}
    assert validate_rubric_payload("s1", data) is None


# ── interviewer turn (fake provider) ──────────────────────────────────────────

def _make_llm_session(client) -> str:
    intake = {
        "resumeText": "Backend payments at scale.",
        "jobDescription": "Backend engineer.",
        "role": "Backend Engineer",
        "seniority": "senior",
        "languages": ["Python"],
        "durationMinutes": 45,
    }
    r = client.post("/vnext/interview/sessions", json={"intake": intake, "mode": "llm"})
    assert r.json()["mode"] == "llm"
    sid = r.json()["sessionId"]
    client.post(f"/vnext/interview/sessions/{sid}/rubric", json={"intake": intake})
    assert client.get(f"/vnext/interview/sessions/{sid}").json()["phase"] == "ready"
    return sid


def _hello(sid, last_seq=0, resume=False):
    return {"type": "client_hello", "session_id": sid, "client_conn_id": CONN_ID,
            "last_seq": last_seq, "resume": resume}


@pytest.fixture()
def client(monkeypatch):
    # Configure a provider so the LLM path is exercised (transport is faked).
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_llm_advance_emits_interviewer_utterance(client, monkeypatch):
    async def fake_chat(url, headers, body, timeout):
        # rubric call OR interviewer call — detect by content.
        text = json.dumps(body)
        if "Maya" not in text:
            return json.dumps({"criteria": [
                {"id": "ps", "name": "Problem solving", "description": "d",
                 "weight": 60, "signals": ["a"], "phaseHints": ["coding"]},
                {"id": "comm", "name": "Communication", "description": "d",
                 "weight": 40, "signals": ["b"], "phaseHints": ["intro"]},
            ]})
        return json.dumps({"utterance": "Welcome — tell me about a hard bug.",
                           "suggestedAdvance": "intro.done"})

    monkeypatch.setattr(llm_client, "_chat_completion", fake_chat)

    sid = _make_llm_session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json()  # resume_ready
        ws.receive_json()  # backfill
        ws.send_json({"type": "advance.request", "signal": "session.start"})
        phase_changed = ws.receive_json()
        started = ws.receive_json()  # interviewer.turn.started (barge-in protocol)
        utter = ws.receive_json()

    assert phase_changed["type"] == "phase.changed"
    assert phase_changed["to"] == "intro"  # controller owns the transition
    assert started["type"] == "interviewer.turn.started"
    assert utter["type"] == "interviewer.utterance"
    assert utter["actor"] == "interviewer"
    assert utter["text"] == "Welcome — tell me about a hard bug."
    assert utter["turnId"] == started["turnId"]
    # server-minted, monotonic seq: phase.changed → turn.started → utterance
    assert started["seq"] == phase_changed["seq"] + 1
    assert utter["seq"] == started["seq"] + 1


def test_llm_malformed_falls_back_to_scripted_line(client, monkeypatch):
    async def fake_chat(url, headers, body, timeout):
        text = json.dumps(body)
        if "Maya" not in text:
            return json.dumps({"criteria": [
                {"id": "ps", "name": "Problem solving", "description": "d",
                 "weight": 100, "signals": ["a"], "phaseHints": ["coding"]},
            ]})
        return "this is not json at all <<<"  # malformed interviewer output

    monkeypatch.setattr(llm_client, "_chat_completion", fake_chat)

    sid = _make_llm_session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "advance.request", "signal": "session.start"})
        phase_changed = ws.receive_json()
        ws.receive_json()  # interviewer.turn.started
        utter = ws.receive_json()

    assert phase_changed["type"] == "phase.changed"
    assert utter["type"] == "interviewer.utterance"
    # Scripted fallback line for session.start (the L1 greeting).
    assert "Maya" in utter["text"]
    # No malformed garbage reached the ledger.
    ledger = client.get(f"/vnext/interview/sessions/{sid}/ledger").json()["events"]
    assert all(e["type"] != "interviewer.utterance" or e["text"].strip() for e in ledger)
    assert "<<<" not in json.dumps(ledger)


def test_llm_no_key_uses_scripted_rubric_and_line(monkeypatch):
    # No providers configured at all -> rubric + interviewer fall back to scripted.
    monkeypatch.setattr(settings, "openrouter_api_key", "", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)

    intake = {"role": "Backend", "seniority": "mid", "languages": [],
              "resumeText": "", "jobDescription": "", "durationMinutes": 45}
    sid = c.post("/vnext/interview/sessions", json={"intake": intake, "mode": "llm"}).json()["sessionId"]
    rub = c.post(f"/vnext/interview/sessions/{sid}/rubric", json={"intake": intake}).json()["rubric"]
    assert rub["generatedBy"] == "scripted"  # fell back with zero keys
    assert sum(cc["weight"] for cc in rub["criteria"]) == 100

    with c.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "advance.request", "signal": "session.start"})
        ws.receive_json()  # phase.changed
        ws.receive_json()  # interviewer.turn.started
        utter = ws.receive_json()
    assert utter["type"] == "interviewer.utterance"
    assert "Maya" in utter["text"]  # scripted fallback line
