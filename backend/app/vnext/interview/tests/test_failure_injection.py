"""Failure-injection coverage (E7 item 4).

Several E7 failure cases are ALREADY covered elsewhere; this file adds only the
gaps and documents the existing coverage so the suite is auditable:

  * LLM interviewer malformed -> scripted line; garbage never reaches ledger
        -> test_llm_path.py::test_llm_malformed_falls_back_to_scripted_line
  * LLM scorecard malformed -> rubric-shaped scripted fallback (E5 fix)
        -> test_scorecard_llm.py::test_llm_malformed_scorecard_falls_back_to_scripted
           + the build_scripted_scorecard rubric-shape tests
  * WS reconnect from last_seq returns ONLY newer events
        -> test_ws_and_parity.py::test_reconnect_backfill_only_newer
  * unknown session WS handshake -> resume_rejected (no crash)
        -> test_ws_and_parity.py::test_handshake_rejects_unknown_session

ADDED HERE:
  * LLM rubric malformed (keys present) -> deterministic scripted rubric fallback.
  * duplicate/stale seq does NOT corrupt the ledger (backfill idempotency).
  * missing/nonexistent session over REST -> clean 404 (no crash).

No real network: the LLM provider transport is monkeypatched.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.vnext.interview.llm.client as llm_client
from app.config import settings
from app.vnext.interview.ledger import SessionLedger
from app.vnext.interview.router import router


@pytest.fixture()
def client(monkeypatch):
    # Provider configured so the LLM path runs; transport faked per-test.
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── LLM rubric malformed -> deterministic scripted rubric ─────────────────────

def test_llm_rubric_malformed_falls_back_to_scripted(client, monkeypatch):
    async def fake_chat(url, headers, body, timeout):
        # Every LLM call returns garbage; the rubric call must fall back.
        return "totally not json <<<"

    monkeypatch.setattr(llm_client, "_chat_completion", fake_chat)

    intake = {"role": "Backend Engineer", "seniority": "senior", "languages": ["Python"],
              "resumeText": "payments", "jobDescription": "backend", "durationMinutes": 45}
    sid = client.post("/vnext/interview/sessions", json={"intake": intake, "mode": "llm"}).json()["sessionId"]
    rub = client.post(f"/vnext/interview/sessions/{sid}/rubric", json={"intake": intake}).json()["rubric"]

    # Fell back to the deterministic, rubric-shaped scripted rubric.
    assert rub["generatedBy"] == "scripted"
    assert rub["criteria"], "scripted fallback must be rubric-shaped with criteria"
    assert sum(c["weight"] for c in rub["criteria"]) == 100
    # No malformed garbage leaked anywhere.
    assert "<<<" not in json.dumps(rub)
    # Session advanced to ready on the fallback rubric.
    assert client.get(f"/vnext/interview/sessions/{sid}").json()["phase"] == "ready"


# ── duplicate / stale seq does NOT corrupt the ledger ─────────────────────────

def test_duplicate_stale_seq_backfill_idempotent():
    led = SessionLedger("s1", clock=lambda: 1000)
    led.append("interviewer", "interviewer.utterance", {"lineId": "l1", "text": "a"})
    led.append("candidate", "candidate.utterance", {"lineId": "c1", "text": "b"})
    led.append("interviewer", "interviewer.utterance", {"lineId": "l2", "text": "c"})
    assert led.last_seq == 3

    # Backfill at the high-water mark is empty (re-applying seq <= lastSeq = no-op).
    assert led.backfill(3) == []
    # Backfill at an OLD/stale seq returns ONLY strictly-newer events, in order.
    assert [e["seq"] for e in led.backfill(1)] == [2, 3]
    # Re-requesting the same stale seq yields the identical set (no mutation).
    assert [e["seq"] for e in led.backfill(1)] == [2, 3]

    # A late/duplicate-looking append still mints the NEXT seq — never reuses one.
    nxt = led.append("candidate", "candidate.utterance", {"lineId": "c2", "text": "d"})
    assert nxt["seq"] == 4
    seqs = [e["seq"] for e in led.get_all()]
    assert seqs == [1, 2, 3, 4]
    assert len(seqs) == len(set(seqs))  # no collisions / corruption


# ── missing / nonexistent session over REST -> clean 404 ──────────────────────

def test_rest_missing_session_returns_404(client):
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/vnext/interview/sessions/{missing}").status_code == 404
    assert client.get(f"/vnext/interview/sessions/{missing}/ledger").status_code == 404
    assert client.get(f"/vnext/interview/sessions/{missing}/review").status_code == 404
    r = client.post(f"/vnext/interview/sessions/{missing}/rubric", json={})
    assert r.status_code == 404
    assert r.json()["detail"] == "session_not_found"
