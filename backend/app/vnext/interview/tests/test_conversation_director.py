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


def test_scheduled_hint_superseded_by_interviewer_reply(client):
    """Anti-double gate: a pause-scheduled hint must NOT fire if another
    interviewer line already answered the candidate during the pause —
    it is cancelled with reason=superseded instead (one reply per turn)."""
    sid = _make_llm_session(client)
    # Schedule help hints 400ms out so an advance can race in during the pause.
    client.patch(f"/vnext/interview/sessions/{sid}/pause_policies", json={"help": 400})

    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json(); ws.receive_json()

        ws.send_json({"type": "candidate.text", "text": "I'm stuck, can you guide me?"})
        assert ws.receive_json()["type"] == "candidate.utterance"
        assert ws.receive_json()["type"] == "conversation.intent.detected"
        assert ws.receive_json()["type"] == "system.pause.scheduled"

        # An interviewer line lands during the pause (scripted advance).
        ws.send_json({"type": "advance.request", "signal": "session.start"})
        got_interviewer_line = False
        outcome = None  # "superseded" | "hint_fired"
        for _ in range(20):  # drain until the scheduler resolves
            ev = ws.receive_json()
            if ev["type"] == "interviewer.utterance":
                if ev.get("hint_for") == "help":
                    outcome = "hint_fired"
                    break
                got_interviewer_line = True
            elif ev["type"] == "system.pause.cancelled" and ev.get("reason") == "superseded":
                outcome = "superseded"
                break
            elif ev["type"] == "system.pause.completed":
                outcome = "hint_fired"
                break
        assert got_interviewer_line, "the advance should have produced an interviewer line"
        assert outcome == "superseded", f"scheduled hint should be superseded, got {outcome}"
