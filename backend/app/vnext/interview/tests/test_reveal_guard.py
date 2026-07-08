"""Never-confirm / never-praise / never-reveal — enforced in code, not prompts.

Unit tests for reveal_guard plus a real-WS test proving a leaking LLM line is
neutralized before it reaches the ledger. Adversarial cases mirror the leaks
observed in Voice_Assist live transcripts ("That's generally true", praise
openers) and the planted wrong answers in the senior's candidate test script.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.vnext.interview.llm.client as llm_client
from app.config import settings
from app.vnext.interview.llm.interviewer_llm import _FORBIDDEN_PRAISE
from app.vnext.interview.reveal_guard import (
    NEUTRAL_PROBES,
    confirms_or_denies,
    contains_praise,
    guard_interviewer_line,
    neutral_probe,
)
from app.vnext.interview.router import router

CONN_ID = "conn-test-guard-0001"


# ── unit: verdict detection ───────────────────────────────────────────────────

class TestConfirmDeny:
    def test_confirmations_detected(self):
        for line in [
            "That's right.",
            "That's exactly right — retries collide there.",
            "That is generally true for O(1) lookups.",
            "You're correct about the race.",
            "Exactly right.",
            "Spot on.",
            "You got it.",
            "Yes — that's the right answer.",
        ]:
            assert confirms_or_denies(line), line

    def test_denials_detected(self):
        for line in [
            "That's wrong.",
            "That's incorrect, think again.",
            "Not quite — look at the timeout path.",
            "You're wrong about the constraint.",
        ]:
            assert confirms_or_denies(line), line

    def test_neutral_probes_pass(self):
        for line in [
            "Walk the race with me: where do two retries collide?",
            "What happens when the provider times out mid-charge?",
            "Show me the transaction boundary.",
            *NEUTRAL_PROBES,
        ]:
            assert not confirms_or_denies(line), line
            assert not contains_praise(line), line


# ── unit: praise detection stays in sync with the prompt list ────────────────

class TestPraise:
    def test_every_forbidden_praise_phrase_is_caught(self):
        # The prompt-side list and the code-side guard must never drift apart.
        for phrase in _FORBIDDEN_PRAISE:
            assert contains_praise(f"{phrase}, let's continue."), phrase

    def test_common_variants_caught(self):
        for line in ["Great job on that fix.", "Excellent. Next question.", "Well done!"]:
            assert contains_praise(line), line


# ── unit: sentence-level guarding ─────────────────────────────────────────────

class TestGuardLine:
    def test_clean_line_untouched(self):
        text = "Walk me through the failure mode. What has to be atomic?"
        out, reasons = guard_interviewer_line(text, seq=5)
        assert out == text and reasons == []

    def test_offending_sentence_dropped_rest_kept(self):
        text = "That's exactly right! Now, what would you log to prove the fix?"
        out, reasons = guard_interviewer_line(text, seq=5)
        assert "exactly right" not in out.lower()
        assert "what would you log" in out.lower()
        assert reasons == ["confirm_deny"]

    def test_fully_offending_line_becomes_neutral_probe(self):
        text = "That's correct. Great job."
        out, reasons = guard_interviewer_line(text, seq=7)
        assert out == neutral_probe(7)
        assert set(reasons) == {"confirm_deny", "praise"}

    def test_probe_rotation_is_deterministic_and_varied(self):
        assert neutral_probe(1) == neutral_probe(1)
        assert {neutral_probe(i) for i in range(len(NEUTRAL_PROBES))} == set(NEUTRAL_PROBES)

    def test_reveal_terms_blocked_before_attempt_3(self):
        text = "Consider using a hash map to store seen values."
        out, reasons = guard_interviewer_line(text, seq=1, attempt=1, reveal_terms=["hash map"])
        assert "hash map" not in out.lower()
        assert reasons == ["reveal_term"]

    def test_reveal_terms_allowed_at_attempt_3(self):
        text = "Use a hash map: store each value and check for the complement."
        out, reasons = guard_interviewer_line(text, seq=1, attempt=3, reveal_terms=["hash map"])
        assert out == text and reasons == []

    def test_neutral_probes_survive_their_own_guard(self):
        # The replacement text must never itself be blockable (no loops).
        for i, probe in enumerate(NEUTRAL_PROBES):
            out, reasons = guard_interviewer_line(probe, seq=i)
            assert out == probe and reasons == []


# ── integration: a leaking LLM line is neutralized over the real WS ──────────

@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _leaky_chat():
    async def fake_chat(url, headers, body, timeout):
        text = json.dumps(body)
        if "Maya" in text:  # interviewer turn — leaks a verdict + praise
            return json.dumps({"utterance": "That's exactly right! Great job. What would you log to prove the fix holds?"})
        return json.dumps({"criteria": [
            {"id": "ps", "name": "Problem solving", "description": "d",
             "weight": 100, "signals": ["a"], "phaseHints": ["coding"]},
        ]})
    return fake_chat


def test_llm_verdict_leak_is_guarded_over_ws(client, monkeypatch):
    monkeypatch.setattr(llm_client, "_chat_completion", _leaky_chat())
    intake = {"resumeText": "Payments.", "jobDescription": "Backend.",
              "role": "Backend Engineer", "seniority": "senior",
              "languages": ["Python"], "durationMinutes": 45}
    sid = client.post("/vnext/interview/sessions", json={"intake": intake, "mode": "llm"}).json()["sessionId"]
    client.post(f"/vnext/interview/sessions/{sid}/rubric", json={"intake": intake})

    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json({"type": "client_hello", "session_id": sid,
                      "client_conn_id": CONN_ID, "last_seq": 0, "resume": False})
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "advance.request", "signal": "session.start"})
        assert ws.receive_json()["type"] == "phase.changed"
        assert ws.receive_json()["type"] == "interviewer.turn.started"
        utter = ws.receive_json()

    assert utter["type"] == "interviewer.utterance"
    low = utter["text"].lower()
    assert "exactly right" not in low
    assert "great job" not in low
    # The substantive probe survives the guard.
    assert "what would you log" in low
    assert utter.get("guarded") is True
    assert "confirm_deny" in utter.get("guard_reasons", [])

    # The guarded line — not the leak — is what reached the ledger.
    ledger = client.get(f"/vnext/interview/sessions/{sid}/ledger").json()["events"]
    stored = [e for e in ledger if e["type"] == "interviewer.utterance"]
    assert stored and all("exactly right" not in e["text"].lower() for e in stored)
