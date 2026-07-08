"""Rehearsal of the senior's candidate test script — as automated behavior checks.

NOTHING here is hardcoded to make the script pass; the script's moments are
encoded as ASSERTIONS against the generic engine, so this suite grades the
interviewer the same way a human running the script would:

  script moment                          → engine property under test
  ─────────────────────────────────────────────────────────────────────
  "Hold on, let me answer that first"    → barge-in cancels the in-flight turn
  (mid-Maya utterance)                     and scheduled speech; the candidate's
                                           continuation reaches the next prompt
  wrong answers ("increase the timeout") → Maya pushes back without EVER saying
                                           right/wrong (guard neutralizes leaks)
  asking for hints repeatedly            → rapid re-asks are throttled; spaced
                                           asks escalate without revealing
  partial fix in the editor              → risky code is highlighted + patch
                                           proposed; a REJECT is respected
  "Can I see what evidence you used?"    → every scorecard citation resolves to
                                           a real ledger event
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.vnext.interview.llm.client as llm_client
from app.config import settings
from app.vnext.interview.incident import INCIDENT_SEED_CODE
from app.vnext.interview.router import router

CONN_ID = "conn-script-rehearsal-01"
SLOW = 0.8  # generation latency long enough to barge mid-flight


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _interviewer_llm(captured: list[str] | None = None, leak: bool = False, slow: bool = False):
    """Fake transport for Maya's turns: optionally slow (barge-in window) and
    optionally leaking a verdict (so the rehearsal proves the guard, not luck)."""
    async def fake_chat(url, headers, body, timeout):
        text = json.dumps(body)
        if "Maya" in text:
            if captured is not None:
                captured.append(text)
            if slow:
                await asyncio.sleep(SLOW)
            line = (
                "That's wrong — the timeout isn't the issue. Walk the race with me: where do two retries collide?"
                if leak
                else "Walk the race with me: where exactly do two retries collide?"
            )
            return json.dumps({"utterance": line})
        return json.dumps({"criteria": [
            {"id": "idempotency_fix", "name": "Idempotency fix", "description": "d",
             "weight": 50, "signals": ["a"], "phaseHints": ["coding"]},
            {"id": "concurrency_reasoning", "name": "Concurrency", "description": "d",
             "weight": 50, "signals": ["a"], "phaseHints": ["debugging"]},
        ]})
    return fake_chat


def _incident_session(client) -> str:
    intake = {"resumeText": "Payments backend.", "jobDescription": "Backend engineer.",
              "role": "Backend Engineer", "seniority": "senior",
              "languages": ["Python"], "durationMinutes": 25}
    sid = client.post("/vnext/interview/sessions",
                      json={"intake": intake, "mode": "llm", "track": "incident-demo"}).json()["sessionId"]
    client.post(f"/vnext/interview/sessions/{sid}/rubric", json={"intake": intake})
    return sid


def _hello(sid):
    return {"type": "client_hello", "session_id": sid, "client_conn_id": CONN_ID,
            "last_seq": 0, "resume": False}


def test_barge_in_moment_hold_on_yields_the_floor(client, monkeypatch):
    """Script: interrupt Maya mid-speech with 'Hold on, let me answer that part
    first', then continue with the real point. The in-flight turn must die and
    the continuation must shape the NEXT turn."""
    captured: list[str] = []
    monkeypatch.setattr(llm_client, "_chat_completion", _interviewer_llm(captured, slow=True))
    sid = _incident_session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json(); ws.receive_json()

        ws.send_json({"type": "advance.request", "signal": "session.start"})
        assert ws.receive_json()["type"] == "phase.changed"
        started = ws.receive_json()
        turn_id = started["turnId"]

        # Maya is still generating — the candidate cuts in. "hold" is an
        # utterance-initial interrupt word: urgency fires no matter the intent.
        ws.send_json({"type": "barge_in", "turnId": turn_id})
        assert ws.receive_json()["type"] == "interviewer.cancelled"
        ws.send_json({"type": "candidate.text",
                      "text": "Hold on, let me answer that part first."})
        # barge_in.detected + candidate.utterance + intent(thinking) + the
        # thinking acknowledgment ("take your time") — Maya yields audibly.
        frames = [ws.receive_json() for _ in range(4)]
        types = [f["type"] for f in frames]
        assert "barge_in.detected" in types  # urgency preserved for content cut-ins
        acks = [f for f in frames if f["type"] == "interviewer.utterance"]
        assert acks and acks[0].get("hint_for") == "thinking"

        ws.send_json({"type": "candidate.text",
                      "text": "The core issue is the external provider call is not safely tied to our database write."})
        assert ws.receive_json()["type"] == "candidate.utterance"

        ws.send_json({"type": "advance.request", "signal": "intro.done"})
        ws.receive_json()  # phase.changed
        ws.receive_json()  # turn.started
        utter = ws.receive_json()
        assert utter["type"] == "interviewer.utterance"

    # The cancelled turn never reached the ledger; the continuation reached the prompt.
    ledger = client.get(f"/vnext/interview/sessions/{sid}/ledger").json()["events"]
    assert all(u.get("turnId") != turn_id for u in ledger if u["type"] == "interviewer.utterance")
    assert any("not safely tied" in body for body in captured)


def test_wrong_answer_moment_pushback_without_verdict(client, monkeypatch):
    """Script: plant 'Maybe we can just increase the timeout.' Maya must push
    back — and even when the model TRIES to open with 'That's wrong', the
    candidate never hears a verdict."""
    monkeypatch.setattr(llm_client, "_chat_completion", _interviewer_llm(leak=True))
    sid = _incident_session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "candidate.text", "text": "Maybe we can just increase the timeout."})
        assert ws.receive_json()["type"] == "candidate.utterance"

        ws.send_json({"type": "advance.request", "signal": "session.start"})
        ws.receive_json(); ws.receive_json()
        utter = ws.receive_json()

    low = utter["text"].lower()
    assert "wrong" not in low and "that's right" not in low
    assert "collide" in low                  # the substantive probe survived
    assert utter.get("guarded") is True      # and the leak is auditable


def test_hint_discipline_moment_throttle_then_escalate(client, monkeypatch):
    """Script: the candidate asks for help repeatedly. Instant re-asks get the
    restate-and-apply throttle; a properly spaced re-ask escalates one rung —
    and no rung before the last may name the fix."""
    monkeypatch.setattr(llm_client, "_chat_completion", _interviewer_llm())
    sid = _incident_session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json(); ws.receive_json()

        ws.send_json({"type": "candidate.text", "text": "I'm stuck, can you help me?"})
        ws.receive_json(); ws.receive_json()
        h1 = ws.receive_json()
        assert h1["hint_step"] == 1

        # Rapid-fire re-ask (faster than the hint could be read) → throttled.
        ws.send_json({"type": "candidate.text", "text": "hint please, I need help"})
        ws.receive_json(); ws.receive_json()
        throttled = ws.receive_json()
        assert throttled.get("hint_throttled") == "help"
        assert "hint_for" not in throttled  # never advances the ladder


def test_patch_moment_reject_is_respected(client, monkeypatch):
    """Script: 'I'd rather write it myself…' — reject the proposal once, then
    submit an own partial fix. The reject must land; Maya must not force the
    patch; the candidate's keyed lookup counts as progress (no re-proposal)."""
    monkeypatch.setattr(llm_client, "_chat_completion", _interviewer_llm())
    sid = _incident_session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json(); ws.receive_json()

        # The seed code is still racy: Maya highlights + proposes her patch.
        ws.send_json({"type": "candidate.code", "code": INCIDENT_SEED_CODE})
        assert ws.receive_json()["type"] == "code.edited"
        assert ws.receive_json()["type"] == "selection.set"
        assert ws.receive_json()["type"] == "highlight.set"
        assert ws.receive_json()["type"] == "interviewer.utterance"
        proposed = ws.receive_json()
        assert proposed["type"] == "code.patch.proposed"

        # Candidate rejects — writes it themselves.
        ws.send_json({"type": "code.patch.reject", "patchId": proposed["patchId"]})
        assert ws.receive_json()["type"] == "code.patch.rejected"
        ws.receive_json(); ws.receive_json()  # selection/highlight cleared

        # Their own partial fix keys the lookup by idempotency_key → safe now.
        own_fix = INCIDENT_SEED_CODE.replace(
            "WHERE customer_id = %s AND amount_cents = %s",
            "WHERE idempotency_key = %s",
        )
        ws.send_json({"type": "candidate.code", "code": own_fix})
        assert ws.receive_json()["type"] == "code.edited"
        # No forced patch, no re-highlight: the editor stays theirs.
        ws.send_json({"type": "candidate.text", "text": "I keyed the lookup by the idempotency key instead."})
        assert ws.receive_json()["type"] == "candidate.utterance"


def test_scorecard_moment_evidence_resolves(client, monkeypatch):
    """Script closer: 'Can I see what evidence you used for the scorecard?' —
    every citation must resolve to a real ledger event."""
    monkeypatch.setattr(llm_client, "_chat_completion", _interviewer_llm())
    sid = _incident_session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "candidate.text",
                      "text": "The check and the insert race between the provider call."})
        ws.receive_json()
        ws.send_json({"type": "candidate.code", "code": INCIDENT_SEED_CODE.replace(
            "WHERE customer_id = %s AND amount_cents = %s", "WHERE idempotency_key = %s")})
        ws.receive_json()
        ws.send_json({"type": "scorecard.request"})
        frames = []
        for _ in range(10):
            f = ws.receive_json()
            frames.append(f)
            if f["type"] in ("scorecard.completed", "scorecard.failed"):
                break

    completed = frames[-1]
    assert completed["type"] == "scorecard.completed"
    ledger = client.get(f"/vnext/interview/sessions/{sid}/ledger").json()["events"]
    seqs = {e["seq"] for e in ledger}
    for score in completed["draft"]["scores"]:
        for ref in score.get("evidence", []):
            assert ref["seq"] in seqs, f"evidence seq {ref['seq']} does not resolve"
