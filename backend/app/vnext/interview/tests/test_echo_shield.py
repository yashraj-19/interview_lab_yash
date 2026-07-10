"""Server-side echo backstop: Maya must never answer her own (garbled) voice,
regardless of which client build forwarded it."""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.vnext.interview.echo_shield import is_probable_echo, ordered_echo_score
from app.vnext.interview.router import router

NOW = int(time.time() * 1000)
MAYA = ("Inspect the code in the box, which handles payment API retries, and identify "
        "why it sometimes creates duplicate charges on timeout and retry.")


def _events(ts=None):
    return [{"type": "interviewer.utterance", "text": MAYA, "ts": ts if ts is not None else NOW, "seq": 3}]


class TestOrderedEchoScore:
    def test_live_garbles_score_high(self):
        # All three observed in production runs.
        assert ordered_echo_score("sometimes create", MAYA) >= 0.6
        assert ordered_echo_score("inspectacled in the park", MAYA) >= 0.6
        assert ordered_echo_score("inspector in the park", MAYA) >= 0.6

    def test_real_answers_score_low(self):
        assert ordered_echo_score("the check and the insert race between the provider call", MAYA) < 0.6
        assert ordered_echo_score("we should add a unique constraint on the idempotency key", MAYA) < 0.6
        assert ordered_echo_score("I don't know this question", MAYA) < 0.6


class TestIsProbableEcho:
    def test_recent_echo_dropped(self):
        assert is_probable_echo("sometimes create duplicate charges", _events(), now_ms=NOW + 3_000) is True

    def test_old_lines_never_match(self):
        # Same text 30s later: outside the echo window -> candidate quoting her is fine.
        assert is_probable_echo("sometimes create duplicate charges", _events(ts=NOW - 30_000), now_ms=NOW) is False

    def test_short_blips_pass(self):
        assert is_probable_echo("wait", _events(), now_ms=NOW) is False

    def test_real_answer_passes(self):
        assert is_probable_echo("I'd key the lookup by idempotency key instead", _events(), now_ms=NOW + 2_000) is False


def test_ws_drops_echo_and_still_hears_real_answers():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    intake = {"resumeText": "P.", "jobDescription": "B.", "role": "SDE", "seniority": "mid",
              "languages": ["Python"], "durationMinutes": 45}
    sid = client.post("/vnext/interview/sessions",
                      json={"intake": intake, "mode": "scripted", "track": "incident-demo"}).json()["sessionId"]
    client.post(f"/vnext/interview/sessions/{sid}/rubric", json={"intake": intake})
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json({"type": "client_hello", "session_id": sid,
                      "client_conn_id": "conn-echo-test-0001", "last_seq": 0, "resume": False})
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "advance.request", "signal": "session.start"})
        ws.receive_json()                      # phase.changed
        line = ws.receive_json()               # Maya's scenario line (just spoken)
        assert line["type"] == "interviewer.utterance"

        # A garbled fragment of HER OWN line arrives as "candidate" text.
        ws.send_json({"type": "candidate.text", "text": "double charges when the provider times"})
        dropped = ws.receive_json()
        assert dropped["type"] == "echo.dropped"

        # A genuine answer right after still works normally.
        ws.send_json({"type": "candidate.text", "text": "I would key the lookup by the idempotency key"})
        assert ws.receive_json()["type"] == "candidate.utterance"

    ledger = client.get(f"/vnext/interview/sessions/{sid}/ledger").json()["events"]
    cand = [e["text"] for e in ledger if e["type"] == "candidate.utterance"]
    assert all("double charges when the provider" not in t for t in cand)
