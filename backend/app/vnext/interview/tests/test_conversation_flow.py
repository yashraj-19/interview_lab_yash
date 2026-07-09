"""Adaptive interview flow — the 9 behaviors that make it feel real.

These pin the anti-lockstep redesign: Maya listens, reacts, helps, probes, and
only advances a phase when the conversation genuinely earns it — never because
the candidate stopped speaking.

The conversation LLM is faked so behavior is deterministic; the fake answers
the reactive-conversation prompt ({reply,intent,advance}) and the phase-advance
prompt ({utterance}) distinctly.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.vnext.interview.llm.client as llm_client
from app.config import settings
from app.vnext.interview.conversation_memory import ConversationMemory, build_memory
from app.vnext.interview.phase_policy import can_advance, next_signal
from app.vnext.interview.router import router

CONN_ID = "conn-conv-flow-0001"


# ── unit: phase completion gates ──────────────────────────────────────────────

class TestPhasePolicy:
    def _mem(self, turns, code=False):
        m = ConversationMemory()
        m.phase_turn_count = turns
        m.has_code = code
        return m

    def test_never_advances_when_llm_says_incomplete(self):
        assert not can_advance("resume_calibration", self._mem(5), {}, llm_says_complete=False)

    def test_turn_floor_blocks_premature_advance(self):
        # resume_calibration needs >= 2 substantive turns.
        assert not can_advance("resume_calibration", self._mem(1), {"design": True}, llm_says_complete=True)
        assert can_advance("resume_calibration", self._mem(2), {"design": True}, llm_says_complete=True)

    def test_coding_requires_code_evidence(self):
        assert not can_advance("coding", self._mem(3, code=False), {}, llm_says_complete=True)
        assert can_advance("coding", self._mem(3, code=True), {}, llm_says_complete=True)

    def test_no_wrapup_without_hard_evidence(self):
        assert not can_advance("optimization", self._mem(3), {"code": False, "design": False}, llm_says_complete=True)
        assert can_advance("optimization", self._mem(3), {"code": True}, llm_says_complete=True)

    def test_terminal_phase_has_no_next(self):
        assert next_signal("review") is None
        assert not can_advance("review", self._mem(9), {"code": True}, llm_says_complete=True)


# ── unit: conversation memory (no repeated follow-ups / openers) ──────────────

class TestConversationMemory:
    def test_turn_count_scoped_to_current_phase(self):
        events = [
            {"type": "phase.changed", "to": "intro"},
            {"type": "candidate.utterance", "text": "hi"},
            {"type": "phase.changed", "to": "coding"},
            {"type": "candidate.utterance", "text": "here's my approach"},
            {"type": "candidate.utterance", "text": "and the edge case"},
        ]
        mem = build_memory(events, "coding")
        assert mem.phase_turn_count == 2  # reset at the coding phase.changed

    def test_non_substantive_utterances_do_not_count(self):
        events = [
            {"type": "phase.changed", "to": "coding"},
            {"type": "candidate.utterance", "text": "can you repeat", "nonSubstantive": True},
            {"type": "candidate.utterance", "text": "real answer"},
        ]
        assert build_memory(events, "coding").phase_turn_count == 1

    def test_remembers_follow_ups_and_openers_to_avoid_repeats(self):
        events = [
            {"type": "phase.changed", "to": "coding"},
            {"type": "interviewer.utterance", "text": "Walk me through the retry path.", "isFollowUp": True,
             "covered": ["retry"]},
            {"type": "interviewer.utterance", "text": "What breaks under concurrency?", "isFollowUp": True},
        ]
        mem = build_memory(events, "coding")
        assert len(mem.follow_ups_asked) == 2
        assert "retry" in mem.covered
        ctx = mem.as_prompt_context()
        assert "never repeat" in ctx.lower()
        assert "Walk me through the retry path." in ctx

    def test_code_and_run_evidence_tracked(self):
        events = [
            {"type": "phase.changed", "to": "coding"},
            {"type": "code.edited", "editId": "e1"},
            {"type": "code.run", "runId": "r1"},
        ]
        mem = build_memory(events, "coding")
        assert mem.has_code and mem.has_run


# ── integration: the reactive conversation over the real WS ───────────────────

@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    monkeypatch.setattr(settings, "groq_api_key", "", raising=False)
    monkeypatch.setattr(settings, "gemini_api_key", "", raising=False)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _conv_chat(reply="Which part feels unfamiliar — the retry logic or the concurrency?",
               intent="confused", advance=False, slow=False):
    async def fake_chat(url, headers, body, timeout):
        text = json.dumps(body)
        if "CONVERSATION" in text or '"reply"' in text:
            if slow:
                await asyncio.sleep(1.0)
            return json.dumps({"intent": intent, "reply": reply, "advance": advance,
                               "covered": ["topic"], "note": "n"})
        if "Maya" in text:
            return json.dumps({"utterance": "Let's start — walk me through the charge API."})
        return json.dumps({"criteria": [
            {"id": "c1", "name": "Correctness", "description": "d", "weight": 100,
             "signals": ["a"], "phaseHints": ["coding"]}]})
    return fake_chat


def _session(client, track="incident-demo"):
    intake = {"resumeText": "SDE.", "jobDescription": "Backend.", "role": "Backend Engineer",
              "seniority": "senior", "languages": ["Python"], "durationMinutes": 25}
    sid = client.post("/vnext/interview/sessions",
                      json={"intake": intake, "mode": "llm", "track": track}).json()["sessionId"]
    client.post(f"/vnext/interview/sessions/{sid}/rubric", json={"intake": intake})
    return sid


def _hello(sid):
    return {"type": "client_hello", "session_id": sid, "client_conn_id": CONN_ID,
            "last_seq": 0, "resume": False}


def _start(ws, sid):
    ws.send_json(_hello(sid)); ws.receive_json(); ws.receive_json()
    ws.send_json({"type": "advance.request", "signal": "session.start"})
    ws.receive_json(); ws.receive_json(); ws.receive_json()  # phase.changed, turn.started, opening


def test_i_dont_know_stays_in_phase_and_narrows_down(client, monkeypatch):
    """'I don't know' → Maya does NOT move on; she narrows it down."""
    monkeypatch.setattr(llm_client, "_chat_completion", _conv_chat())
    sid = _session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        _start(ws, sid)
        ws.send_json({"type": "candidate.text", "text": "I don't know."})
        assert ws.receive_json()["type"] == "candidate.utterance"
        intent = ws.receive_json()
        assert intent["type"] == "conversation.intent.detected"
        reply = ws.receive_json()
        assert reply["type"] == "interviewer.utterance"
        assert "which part" in reply["text"].lower()
    ledger = client.get(f"/vnext/interview/sessions/{sid}/ledger").json()["events"]
    # No phase.changed after the opening intro transition — Maya stayed put.
    changes = [e for e in ledger if e["type"] == "phase.changed"]
    assert changes and changes[-1]["to"] == "intro"


def test_repeat_restates_the_question_without_advancing(client, monkeypatch):
    monkeypatch.setattr(llm_client, "_chat_completion", _conv_chat())
    sid = _session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        _start(ws, sid)
        ws.send_json({"type": "candidate.text", "text": "can you repeat that please?"})
        assert ws.receive_json()["type"] == "candidate.utterance"
        assert ws.receive_json()["intent"] == "repeat"
        reply = ws.receive_json()
        assert reply["text"].startswith("Sure")
        # Restates the actual opening question.
        assert "charge api" in reply["text"].lower()


def test_help_uses_the_hint_ladder_never_reveals(client, monkeypatch):
    monkeypatch.setattr(llm_client, "_chat_completion", _conv_chat())
    sid = _session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        _start(ws, sid)
        ws.send_json({"type": "candidate.text", "text": "can you help me, I'm not sure where to start"})
        assert ws.receive_json()["type"] == "candidate.utterance"
        assert ws.receive_json()["intent"] == "help"
        reply = ws.receive_json()
        assert reply["hint_for"] == "help"
        assert reply["hint_step"] == 1


def test_partial_answer_gets_a_follow_up_and_stays(client, monkeypatch):
    monkeypatch.setattr(llm_client, "_chat_completion",
                        _conv_chat(reply="What happens under two concurrent retries?", intent="partial"))
    sid = _session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        _start(ws, sid)
        ws.send_json({"type": "candidate.text", "text": "I'd add a second db check after the provider call."})
        ws.receive_json(); ws.receive_json()  # candidate.utterance, intent
        reply = ws.receive_json()
        assert reply["type"] == "interviewer.utterance"
        assert reply.get("isFollowUp") is True
        assert "concurrent retries" in reply["text"].lower()
    ledger = client.get(f"/vnext/interview/sessions/{sid}/ledger").json()["events"]
    assert [e for e in ledger if e["type"] == "phase.changed"][-1]["to"] == "intro"


def test_probes_deeper_before_advancing_a_technical_phase(client, monkeypatch):
    """Even when the model says 'advance', a technical phase won't move on the
    first answer — the turn floor forces at least one real follow-up first."""
    monkeypatch.setattr(llm_client, "_chat_completion", _conv_chat(intent="correct", advance=True))
    sid = _session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        _start(ws, sid)
        # Answer 1 (advance=True): intro floor is 1 -> advances to resume_calibration.
        ws.send_json({"type": "candidate.text", "text": "I own the payments service end to end."})
        ws.receive_json(); ws.receive_json(); ws.receive_json()  # utt, intent, reply
        # Answer 2 (advance=True): resume_calibration turn 1 < floor 2 -> stays.
        ws.send_json({"type": "candidate.text", "text": "I designed the idempotency layer."})
        ws.receive_json(); ws.receive_json(); ws.receive_json()
    ledger = client.get(f"/vnext/interview/sessions/{sid}/ledger").json()["events"]
    phases = [e["to"] for e in ledger if e["type"] == "phase.changed"]
    # Advanced intro->resume_calibration exactly once; did NOT skip ahead on the
    # first calibration answer despite the model volunteering to advance.
    assert phases.count("resume_calibration") == 1
    assert "problem_framing" not in phases


def test_keepalive_ping_is_frame_silent(client, monkeypatch):
    """Transport keepalives must produce NO frames and never disturb the flow —
    the very next candidate message behaves exactly as if the ping never
    happened (and ws.py excludes pings from candidate-activity tracking so the
    silence ladder is not suppressed)."""
    monkeypatch.setattr(llm_client, "_chat_completion", _conv_chat())
    sid = _session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        _start(ws, sid)
        ws.send_json({"type": "ping"})
        ws.send_json({"type": "ping"})
        ws.send_json({"type": "candidate.text", "text": "the check and insert race between them"})
        # First frame after two pings is the candidate utterance — pings were silent.
        assert ws.receive_json()["type"] == "candidate.utterance"


def test_barge_in_cancels_a_reactive_turn_with_nothing_spoken(client, monkeypatch):
    monkeypatch.setattr(llm_client, "_chat_completion", _conv_chat(slow=True))
    sid = _session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        _start(ws, sid)
        ws.send_json({"type": "candidate.text", "text": "here is my full reasoning about the race"})
        assert ws.receive_json()["type"] == "candidate.utterance"
        # Barge in while the reactive turn is still generating.
        ws.send_json({"type": "barge_in"})
        assert ws.receive_json()["type"] == "interviewer.cancelled"
        # The candidate takes the floor again; the cancelled reply never lands.
        ws.send_json({"type": "candidate.text", "text": "let me restate that more carefully"})
        assert ws.receive_json()["type"] == "candidate.utterance"
    ledger = client.get(f"/vnext/interview/sessions/{sid}/ledger").json()["events"]
    # The slow reply text was never emitted as an interviewer utterance.
    assert not any(e["type"] == "interviewer.utterance" and "unfamiliar" in e.get("text", "")
                   for e in ledger)
