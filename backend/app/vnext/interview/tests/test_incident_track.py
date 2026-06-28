"""Incident-demo track: the interview OPENS with the production duplicate-charge
incident (not resume), binds the deterministic incident rubric, and flows the
concise code-review sequence. Deterministic fake-LLM path — no network.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.vnext.interview.incident import INCIDENT_LINES, incident_rubric
from app.vnext.interview.router import router

CONN_ID = "conn-incident-0001"


@pytest.fixture()
def client(monkeypatch):
    # Allow the deterministic fake-LLM path so the track is testable offline.
    monkeypatch.setenv("VNEXT_ALLOW_FAKE_LLM", "1")
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _incident_session(client) -> str:
    intake = {"resumeText": "", "jobDescription": "", "role": "Backend Engineer",
              "seniority": "senior", "languages": ["python"], "durationMinutes": 45}
    r = client.post("/vnext/interview/sessions",
                    json={"intake": intake, "mode": "llm", "fake_llm": True, "track": "incident-demo"})
    sid = r.json()["sessionId"]
    rub = client.post(f"/vnext/interview/sessions/{sid}/rubric", json={"intake": intake}).json()["rubric"]
    return sid, rub


def _hello(sid):
    return {"type": "client_hello", "session_id": sid, "client_conn_id": CONN_ID,
            "last_seq": 0, "resume": False}


def test_incident_rubric_rewards_code_and_concurrency():
    rub = incident_rubric("s1")
    ids = {c["id"] for c in rub["criteria"]}
    assert {"idempotency_fix", "concurrency_reasoning", "test_strategy"} <= ids
    assert sum(c["weight"] for c in rub["criteria"]) == 100
    # The coding criterion is implementation-shaped so the scorecard caps it
    # when no code was edited.
    fix = next(c for c in rub["criteria"] if c["id"] == "idempotency_fix")
    assert "implementation" in fix["name"].lower() or "code" in fix["description"].lower()


def test_incident_session_binds_incident_rubric(client):
    _, rub = _incident_session(client)
    assert {c["id"] for c in rub["criteria"]} >= {"idempotency_fix", "tradeoff_judgment"}


def test_incident_opens_with_duplicate_charge_not_resume(client):
    sid, _ = _incident_session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json()  # resume_ready
        ws.receive_json()  # backfill
        ws.send_json({"type": "advance.request", "signal": "session.start"})
        phase_changed = ws.receive_json()
        started = ws.receive_json()
        utter = ws.receive_json()

    assert phase_changed["to"] == "intro"
    assert started["type"] == "interviewer.turn.started"
    assert utter["type"] == "interviewer.utterance"
    text = utter["text"].lower()
    # Opens on the incident, NOT "tell me about yourself".
    assert any(k in text for k in ("duplicate", "charge", "idempotency", "retry"))
    assert "tell me about yourself" not in text


def test_incident_followups_are_concise_and_on_topic(client):
    sid, _ = _incident_session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json(); ws.receive_json()
        # Drive a few turns; each should be a short incident probe.
        lines = []
        for signal in ["session.start", "intro.done", "calibration.done", "framing.done"]:
            ws.send_json({"type": "advance.request", "signal": signal})
            ws.receive_json()  # phase.changed
            ws.receive_json()  # turn.started
            lines.append(ws.receive_json()["text"])

    # Every emitted line is one of the deterministic incident lines, concise,
    # and free of the banned templated openers.
    for ln in lines:
        assert ln in INCIDENT_LINES.values()
        assert len(ln) < 240  # short enough for TTS
        low = ln.lower()
        assert not low.startswith("can you")
        assert not low.startswith("you mentioned")
        assert not low.startswith("please provide")
    # The coding turn pushes them to write in the code box.
    assert any("code box" in ln.lower() for ln in lines)
