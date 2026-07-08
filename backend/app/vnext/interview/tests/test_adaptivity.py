"""Stage 5: contingent tutoring, hint-gaming throttle, silence ladder, stall recovery.

Research grounding: Wood's contingency rule ("never succeed too easily nor
fail too often"), Baker/MATHia gaming detection (forced read time between
hint rungs), Ericsson & Simon think-aloud (neutral keep-talking nudges only).
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.vnext.interview.llm.client as llm_client
import app.vnext.interview.ws as ws_mod
from app.config import settings
from app.vnext.interview.hint_ladder import _HINTS, next_hint
from app.vnext.interview.router import router
from app.vnext.interview.store import InMemoryInterviewStore

CONN_ID = "conn-test-adaptive-0001"
NOW = int(time.time() * 1000)
LATER = NOW + 120_000  # far past any read-time requirement


@pytest.fixture
def store():
    return InMemoryInterviewStore()


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── hint-gaming throttle ──────────────────────────────────────────────────────

class TestGamingThrottle:
    def test_rapid_second_request_is_throttled_and_does_not_escalate(self, store):
        sid = store.create_session({"role": "SDE"})
        h1 = next_hint(sid, "help", store, now_ms=LATER)
        assert h1["hint_step"] == 1
        store.append_event(sid, "interviewer", "interviewer.utterance",
                           {"hint_for": "help", "text": h1["text"]})
        # Asked again "instantly" (event ts is real; now is ~the same moment).
        throttled = next_hint(sid, "help", store)
        assert throttled is not None
        assert throttled.get("hint_throttled") == "help"
        assert "hint_for" not in throttled  # never advances the attempt counter
        assert "restate" in throttled["text"].lower() or "apply the last hint" in throttled["text"].lower()
        # After genuine reading/trying time, escalation proceeds to rung 2.
        h2 = next_hint(sid, "help", store, now_ms=LATER)
        assert h2["hint_for"] == "help" and h2["hint_step"] == 2

    def test_longer_hints_require_longer_reading_time(self, store):
        sid = store.create_session({"role": "SDE"})
        long_text = "x" * 400  # 400 chars ≈ 26s read time > the 8s floor
        store.append_event(sid, "interviewer", "interviewer.utterance",
                           {"hint_for": "help", "text": long_text})
        ts = store.get_events(sid, 0)[-1]["ts"]
        # 10s later: past the floor but NOT past 400 chars of reading.
        assert next_hint(sid, "help", store, now_ms=ts + 10_000).get("hint_throttled") == "help"
        # 30s later: fine.
        assert next_hint(sid, "help", store, now_ms=ts + 30_000).get("hint_for") == "help"

    def test_other_intents_never_throttled(self, store):
        sid = store.create_session({"role": "SDE"})
        store.append_event(sid, "interviewer", "interviewer.utterance",
                           {"hint_for": "thinking", "text": _HINTS["thinking"][0]})
        h = next_hint(sid, "thinking", store)  # immediate re-request
        assert h.get("hint_for") == "thinking"  # acknowledgment lane stays fluid


# ── Wood's contingent help level ─────────────────────────────────────────────

class TestContingentHelp:
    def test_progress_after_hint_holds_the_level_down(self, store):
        """Hint 1 → candidate EDITS CODE (progress) → next help stays at rung 1;
        without progress it would have escalated to rung 2."""
        sid = store.create_session({"role": "SDE"})
        store.append_event(sid, "interviewer", "interviewer.utterance",
                           {"hint_for": "help", "hint_step": 1, "text": "nudge"})
        store.append_event(sid, "candidate", "code.edited", {"editId": "e1", "after": "code"})
        h = next_hint(sid, "help", store, now_ms=LATER)
        assert h["attempt"] == 1 and h["hint_step"] == 1

    def test_no_progress_escalates_as_before(self, store):
        sid = store.create_session({"role": "SDE"})
        store.append_event(sid, "interviewer", "interviewer.utterance",
                           {"hint_for": "help", "hint_step": 1, "text": "nudge"})
        h = next_hint(sid, "help", store, now_ms=LATER)
        assert h["attempt"] == 2 and h["hint_step"] == 2

    def test_mixed_progress_partial_credit(self, store):
        """Two hints, progress after the first only → level 2, not 3."""
        sid = store.create_session({"role": "SDE"})
        store.append_event(sid, "interviewer", "interviewer.utterance",
                           {"hint_for": "help", "hint_step": 1, "text": "h1"})
        store.append_event(sid, "candidate", "code.run", {"runId": "r1", "exitCode": 1})
        store.append_event(sid, "interviewer", "interviewer.utterance",
                           {"hint_for": "help", "hint_step": 1, "text": "h1-again"})
        h = next_hint(sid, "help", store, now_ms=LATER)
        assert h["attempt"] == 2

    def test_substantive_answer_counts_as_progress(self, store):
        sid = store.create_session({"role": "SDE"})
        store.append_event(sid, "interviewer", "interviewer.utterance",
                           {"hint_for": "help", "hint_step": 1, "text": "h1"})
        store.append_event(sid, "system", "conversation.intent.detected",
                           {"intent": "answer", "text": "I think the map stores indices"})
        h = next_hint(sid, "help", store, now_ms=LATER)
        assert h["attempt"] == 1


# ── silence watchdog + stall recovery over the real WS ───────────────────────

def _make_session(client, mode="scripted") -> str:
    intake = {"resumeText": "P.", "jobDescription": "B.", "role": "SDE",
              "seniority": "mid", "languages": ["Python"], "durationMinutes": 45}
    sid = client.post("/vnext/interview/sessions",
                      json={"intake": intake, "mode": mode}).json()["sessionId"]
    client.post(f"/vnext/interview/sessions/{sid}/rubric", json={"intake": intake})
    return sid


def _hello(sid):
    return {"type": "client_hello", "session_id": sid, "client_conn_id": CONN_ID,
            "last_seq": 0, "resume": False}


def test_silence_watchdog_nudges_then_checks_in(client, monkeypatch):
    # Shrink the ladder so the test runs in ~1s of real time.
    monkeypatch.setattr(ws_mod, "_SILENCE_POLL_SECS", 0.05)
    monkeypatch.setattr(ws_mod, "_SILENCE_FIRST_SECS", 0.15)
    monkeypatch.setattr(ws_mod, "_SILENCE_SECOND_SECS", 0.3)
    sid = _make_session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json(); ws.receive_json()
        # Enter an active phase, drain the advance turn.
        ws.send_json({"type": "advance.request", "signal": "session.start"})
        ws.receive_json()  # phase.changed
        ws.receive_json()  # interviewer line
        # Now go silent: the first frame to arrive must be nudge 1, then nudge 2.
        n1 = ws.receive_json()
        assert n1["type"] == "interviewer.utterance" and n1.get("nudgeLevel") == 1
        assert "?" not in _HINTS["help"][0] or "hint" not in n1["text"].lower()  # neutral, not a hint
        n2 = ws.receive_json()
        assert n2.get("nudgeLevel") == 2
        # The ladder stops at 2 — candidate activity resets it.
        ws.send_json({"type": "candidate.text", "text": "sorting the array first"})
        assert ws.receive_json()["type"] == "candidate.utterance"


def test_silence_watchdog_never_fires_before_the_interview_starts(client, monkeypatch):
    monkeypatch.setattr(ws_mod, "_SILENCE_POLL_SECS", 0.05)
    monkeypatch.setattr(ws_mod, "_SILENCE_FIRST_SECS", 0.1)
    sid = _make_session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json(); ws.receive_json()
        # Still in 'ready' phase (no session.start): stay silent, then act.
        time.sleep(0.4)
        ws.send_json({"type": "candidate.text", "text": "hello there, testing"})
        ev = ws.receive_json()
        # No nudge sneaked in ahead of the candidate utterance.
        assert ev["type"] == "candidate.utterance"


def test_llm_stall_recovers_with_scenario_line(client, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    monkeypatch.setattr(ws_mod, "_STALL_TIMEOUT_SECS", 0.3)

    async def hanging_chat(url, headers, body, timeout):
        text = json.dumps(body)
        if "Maya" in text:
            await asyncio.sleep(30)  # never returns within the stall budget
            return json.dumps({"utterance": "too late"})
        return json.dumps({"criteria": [
            {"id": "ps", "name": "PS", "description": "d", "weight": 100,
             "signals": ["a"], "phaseHints": ["coding"]},
        ]})

    monkeypatch.setattr(llm_client, "_chat_completion", hanging_chat)
    sid = _make_session(client, mode="llm")
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "advance.request", "signal": "session.start"})
        assert ws.receive_json()["type"] == "phase.changed"
        assert ws.receive_json()["type"] == "interviewer.turn.started"
        utter = ws.receive_json()
    assert utter["type"] == "interviewer.utterance"
    assert utter.get("stallRecovered") is True
    assert utter["text"] and "too late" not in utter["text"]
