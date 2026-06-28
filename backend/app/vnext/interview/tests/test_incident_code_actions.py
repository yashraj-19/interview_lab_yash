"""Live AI code actions (incident track): Maya selects/highlights the risky
lines and proposes an idempotent patch when the candidate's code is still racy;
the candidate accepts (server applies it) or rejects. Deterministic — no network.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.vnext.interview.incident import (
    INCIDENT_FIXED_CODE,
    INCIDENT_SEED_CODE,
    incident_code_is_unsafe,
    incident_patch_is_safe,
)
from app.vnext.interview.router import router

CONN_ID = "conn-codeact-0001"
SAFE_FIX = (
    "def charge_customer(db, provider, customer_id, amount_cents, idempotency_key):\n"
    "    rows = db.query('SELECT id FROM charges WHERE idempotency_key = %s', idempotency_key)\n"
    "    if rows:\n        return rows[0]\n"
    "    db.execute('INSERT INTO charges (...) VALUES (...) ON CONFLICT DO NOTHING')\n"
    "    return rows\n"
)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("VNEXT_ALLOW_FAKE_LLM", "1")
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _incident_session(client, track="incident-demo") -> str:
    intake = {"resumeText": "", "jobDescription": "", "role": "Backend Engineer",
              "seniority": "senior", "languages": ["python"], "durationMinutes": 45}
    body = {"intake": intake, "mode": "llm", "fake_llm": True}
    if track:
        body["track"] = track
    sid = client.post("/vnext/interview/sessions", json=body).json()["sessionId"]
    client.post(f"/vnext/interview/sessions/{sid}/rubric", json={"intake": intake})
    return sid


def _hello(sid):
    return {"type": "client_hello", "session_id": sid, "client_conn_id": CONN_ID,
            "last_seq": 0, "resume": False}


def test_unit_detector_and_validator():
    assert incident_code_is_unsafe(INCIDENT_SEED_CODE) is True
    assert incident_code_is_unsafe(SAFE_FIX) is False
    assert incident_code_is_unsafe("print('unrelated')") is False  # not the incident code
    assert incident_patch_is_safe(INCIDENT_FIXED_CODE, INCIDENT_SEED_CODE) is True
    assert incident_patch_is_safe("", INCIDENT_SEED_CODE) is False
    assert incident_patch_is_safe("def charge_customer(): pass", INCIDENT_SEED_CODE) is False


def test_unsafe_code_triggers_select_highlight_and_patch(client):
    sid = _incident_session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid)); ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "candidate.code", "code": INCIDENT_SEED_CODE})
        edited = ws.receive_json()
        selection = ws.receive_json()
        highlight = ws.receive_json()
        utter = ws.receive_json()
        proposed = ws.receive_json()

    assert edited["type"] == "code.edited" and edited["by"] == "candidate"
    assert selection["type"] == "selection.set"
    assert selection["selection"]["owner"] == "interviewer"
    assert highlight["type"] == "highlight.set" and isinstance(highlight["line"], int)
    assert utter["type"] == "interviewer.utterance" and "select" in utter["text"].lower()
    assert proposed["type"] == "code.patch.proposed"
    assert proposed["patchId"]
    assert "idempotency_key" in proposed["after"]
    assert "charge_customer" in proposed["after"]


def test_accept_patch_applies_via_server_ledger(client):
    sid = _incident_session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid)); ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "candidate.code", "code": INCIDENT_SEED_CODE})
        ws.receive_json()  # code.edited
        ws.receive_json()  # selection.set
        ws.receive_json()  # highlight.set
        ws.receive_json()  # utterance
        proposed = ws.receive_json()
        pid = proposed["patchId"]

        ws.send_json({"type": "code.patch.accept", "patchId": pid})
        applied = ws.receive_json()
        edited = ws.receive_json()
        sel_clear = ws.receive_json()
        hi_clear = ws.receive_json()

    assert applied["type"] == "code.patch.applied" and applied["patchId"] == pid
    assert applied["acceptedBy"] == "candidate"
    assert edited["type"] == "code.edited" and "idempotency_key" in edited["after"]
    assert edited["after"] == INCIDENT_FIXED_CODE
    assert sel_clear["type"] == "selection.set" and sel_clear["selection"] is None
    assert hi_clear["type"] == "highlight.set" and hi_clear["line"] is None
    # The applied code is on the authoritative ledger.
    ledger = client.get(f"/vnext/interview/sessions/{sid}/ledger").json()["events"]
    assert any(e["type"] == "code.patch.applied" and e["patchId"] == pid for e in ledger)


def test_reject_patch_keeps_code_unchanged(client):
    sid = _incident_session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid)); ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "candidate.code", "code": INCIDENT_SEED_CODE})
        for _ in range(4):
            ws.receive_json()
        proposed = ws.receive_json()
        pid = proposed["patchId"]
        ws.send_json({"type": "code.patch.reject", "patchId": pid})
        rejected = ws.receive_json()
        ws.receive_json()  # selection clear
        ws.receive_json()  # highlight clear

    assert rejected["type"] == "code.patch.rejected" and rejected["patchId"] == pid
    ledger = client.get(f"/vnext/interview/sessions/{sid}/ledger").json()["events"]
    # No code.edited beyond the candidate's original (no patch applied).
    edits = [e for e in ledger if e["type"] == "code.edited"]
    assert len(edits) == 1
    assert all(e["type"] != "code.patch.applied" for e in ledger)


def test_safe_code_emits_no_actions(client):
    sid = _incident_session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid)); ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "candidate.code", "code": SAFE_FIX})
        edited = ws.receive_json()
        # Next, a candidate.text to prove nothing else was queued before it.
        ws.send_json({"type": "candidate.text", "text": "done"})
        nxt = ws.receive_json()
    assert edited["type"] == "code.edited"
    assert nxt["type"] == "candidate.utterance"  # no selection/patch in between


def test_default_track_emits_no_code_actions(client):
    sid = _incident_session(client, track=None)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid)); ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "candidate.code", "code": INCIDENT_SEED_CODE})
        edited = ws.receive_json()
        ws.send_json({"type": "candidate.text", "text": "done"})
        nxt = ws.receive_json()
    assert edited["type"] == "code.edited"
    assert nxt["type"] == "candidate.utterance"
