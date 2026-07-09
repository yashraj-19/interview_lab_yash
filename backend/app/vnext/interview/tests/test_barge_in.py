"""Real barge-in: a candidate interruption cancels the in-flight interviewer
turn so its (slow) LLM output is NEVER emitted as the active question. The
already-applied phase.changed stays; only the future utterance is suppressed.

NO network — the provider transport is monkeypatched with a SLOW fake so the
turn is reliably still generating when the barge_in arrives.
"""
import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.vnext.interview.llm.client as llm_client
from app.config import settings
from app.vnext.interview.router import router

CONN_ID = "conn-test-barge-0001"
SLOW = 1.0  # interviewer generation latency, long enough to barge mid-flight


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _slow_interviewer_chat(captured_bodies=None):
    async def fake_chat(url, headers, body, timeout):
        text = json.dumps(body)
        if "Maya" in text:  # interviewer turn — slow so we can barge in
            if captured_bodies is not None:
                captured_bodies.append(text)
            await asyncio.sleep(SLOW)
            return json.dumps({"utterance": "Concrete: design the schema in the code box."})
        # rubric (fast)
        return json.dumps({"criteria": [
            {"id": "ps", "name": "Problem solving", "description": "d",
             "weight": 100, "signals": ["a"], "phaseHints": ["coding"]},
        ]})
    return fake_chat


def _make_llm_session(client) -> str:
    intake = {"resumeText": "Payments.", "jobDescription": "Backend.",
              "role": "Backend Engineer", "seniority": "senior",
              "languages": ["Python"], "durationMinutes": 45}
    sid = client.post("/vnext/interview/sessions", json={"intake": intake, "mode": "llm"}).json()["sessionId"]
    client.post(f"/vnext/interview/sessions/{sid}/rubric", json={"intake": intake})
    return sid


def _hello(sid):
    return {"type": "client_hello", "session_id": sid, "client_conn_id": CONN_ID,
            "last_seq": 0, "resume": False}


def test_barge_in_cancels_in_flight_turn(client, monkeypatch):
    monkeypatch.setattr(llm_client, "_chat_completion", _slow_interviewer_chat())
    sid = _make_llm_session(client)

    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json()  # resume_ready
        ws.receive_json()  # backfill

        ws.send_json({"type": "advance.request", "signal": "session.start"})
        phase_changed = ws.receive_json()
        started = ws.receive_json()
        assert phase_changed["type"] == "phase.changed"
        assert started["type"] == "interviewer.turn.started"
        turn_id = started["turnId"]

        # Candidate barges in while the interviewer line is still generating.
        ws.send_json({"type": "barge_in", "turnId": turn_id})
        cancelled = ws.receive_json()
        assert cancelled["type"] == "interviewer.cancelled"
        assert cancelled["turnId"] == turn_id

        # The candidate's answer after interruption still reaches the server.
        ws.send_json({"type": "candidate.text", "text": "Here is my real answer about idempotency."})
        cand = ws.receive_json()
        assert cand["type"] == "candidate.utterance"
        assert "idempotency" in cand["text"]

    # The cancelled interviewer turn NEVER reached the ledger as an utterance.
    ledger = client.get(f"/vnext/interview/sessions/{sid}/ledger").json()["events"]
    utterances = [e for e in ledger if e["type"] == "interviewer.utterance"]
    assert all(u.get("turnId") != turn_id for u in utterances)
    # phase.changed (already applied) is retained — only the future turn was suppressed.
    assert any(e["type"] == "phase.changed" for e in ledger)
    assert any(e["type"] == "interviewer.cancelled" and e["turnId"] == turn_id for e in ledger)


def test_next_turn_after_barge_in_sees_candidate_answer(client, monkeypatch):
    captured: list[str] = []
    monkeypatch.setattr(llm_client, "_chat_completion", _slow_interviewer_chat(captured))
    sid = _make_llm_session(client)

    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json(); ws.receive_json()

        ws.send_json({"type": "advance.request", "signal": "session.start"})
        ws.receive_json()  # phase.changed (ready→intro)
        started = ws.receive_json()
        first_turn = started["turnId"]
        ws.send_json({"type": "barge_in", "turnId": first_turn})
        assert ws.receive_json()["type"] == "interviewer.cancelled"

        # The candidate's answer now drives a REACTIVE conversation turn directly
        # (no forced advance) — and that turn's prompt is built from a transcript
        # that includes the answer.
        ws.send_json({"type": "candidate.text", "text": "I used a unique idempotency key per charge."})
        assert ws.receive_json()["type"] == "candidate.utterance"
        ws.receive_json()  # interviewer.turn.started
        ws.receive_json()  # conversation.intent.detected
        utter = ws.receive_json()  # the reactive interviewer reply
        assert utter["type"] == "interviewer.utterance"

    # The reactive turn's prompt was built from a transcript that included the answer.
    assert any("idempotency key per charge" in body for body in captured)


def test_barge_in_with_no_active_turn_is_safe(client, monkeypatch):
    monkeypatch.setattr(llm_client, "_chat_completion", _slow_interviewer_chat())
    sid = _make_llm_session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json(); ws.receive_json()
        # No turn in flight: a targeted barge_in just acks; nothing crashes.
        ws.send_json({"type": "barge_in", "turnId": "turn-999"})
        ack = ws.receive_json()
        assert ack["type"] == "interviewer.cancelled"
        assert ack["turnId"] == "turn-999"
        # Session still works afterward.
        ws.send_json({"type": "candidate.text", "text": "still alive"})
        assert ws.receive_json()["type"] == "candidate.utterance"
