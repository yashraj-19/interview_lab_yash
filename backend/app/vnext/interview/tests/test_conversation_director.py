import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.vnext.interview.router import router

CONN_ID = "conn-test-director-0001"


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _make_llm_session(client) -> str:
    intake = {
        "resumeText": "Payments backend experience.",
        "jobDescription": "Backend engineer",
        "role": "Backend Engineer",
        "seniority": "senior",
        "languages": ["Python"],
        "durationMinutes": 45,
    }
    sid = client.post("/vnext/interview/sessions", json={"intake": intake, "mode": "scripted"}).json()["sessionId"]
    client.post(f"/vnext/interview/sessions/{sid}/rubric", json={"intake": intake})
    return sid


def _hello(sid: str) -> dict:
    return {
        "type": "client_hello",
        "session_id": sid,
        "client_conn_id": CONN_ID,
        "last_seq": 0,
        "resume": False,
    }


def test_repeat_intent_emits_director_reply(client):
    sid = _make_llm_session(client)

    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json()  # resume_ready
        ws.receive_json()  # backfill

        ws.send_json({"type": "candidate.text", "text": "Can you repeat that?"})
        candidate = ws.receive_json()
        assert candidate["type"] == "candidate.utterance"

        intent = ws.receive_json()
        assert intent["type"] == "conversation.intent.detected"
        assert intent["intent"] == "repeat"

        reply = ws.receive_json()
        assert reply["type"] == "interviewer.utterance"
        assert "repeat" in reply["text"].lower() or "reframe" in reply["text"].lower()


def test_help_intent_emits_hint_without_revealing_answer(client):
    sid = _make_llm_session(client)

    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json()
        ws.receive_json()

        ws.send_json({"type": "candidate.text", "text": "I’m stuck, can you guide me?"})
        ws.receive_json()  # candidate utterance
        intent = ws.receive_json()
        assert intent["type"] == "conversation.intent.detected"
        assert intent["intent"] == "help"

        reply = ws.receive_json()
        assert reply["type"] == "interviewer.utterance"
        text = reply["text"].lower()
        assert "hint" in text or "next step" in text or "focus" in text
        assert "unique constraint" not in text
        assert "idempotency key" not in text
