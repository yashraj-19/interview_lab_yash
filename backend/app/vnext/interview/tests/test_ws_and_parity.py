"""WS handshake/resume + scripted-parity tests (FastAPI TestClient)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.vnext.interview.router import router
from app.vnext.interview.seed import SCRIPTED_SESSION

CONN_ID = "conn-test-0001-abcd"  # satisfies the resume conn-id charset/length


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _make_ready_session(client) -> str:
    intake = {
        "resumeText": "Worked on a distributed payments backend at scale.",
        "jobDescription": "Backend engineer, latency-sensitive systems.",
        "role": "Backend Engineer",
        "seniority": "senior",
        "languages": ["Python", "python", "Go"],
        "durationMinutes": 45,
    }
    r = client.post("/vnext/interview/sessions", json={"intake": intake})
    sid = r.json()["sessionId"]
    assert r.json()["phase"] == "rubric"
    rb = client.post(f"/vnext/interview/sessions/{sid}/rubric", json={"intake": intake})
    weights = [c["weight"] for c in rb.json()["rubric"]["criteria"]]
    assert sum(weights) == 100
    assert client.get(f"/vnext/interview/sessions/{sid}").json()["phase"] == "ready"
    return sid


def _hello(sid, last_seq=0, resume=False):
    return {
        "type": "client_hello",
        "session_id": sid,
        "client_conn_id": CONN_ID,
        "last_seq": last_seq,
        "resume": resume,
    }


def test_warmup(client):
    body = client.get("/vnext/interview/warmup").json()
    assert body["ok"] is True
    assert body["store"] in ("memory", "supabase")


def test_handshake_resume_ready_and_backfill(client):
    sid = _make_ready_session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid, last_seq=0))
        ready = ws.receive_json()
        assert ready["type"] == "resume_ready"
        backfill = ws.receive_json()
        assert backfill["type"] == "resume_events"
        # rubric.bound + 2 phase.changed (intake->rubric, rubric->ready) exist.
        types = [e["type"] for e in backfill["events"]]
        assert "rubric.bound" in types
        assert types.count("phase.changed") == 2


def test_handshake_rejects_unknown_session(client):
    with client.websocket_connect("/vnext/interview/ws/does-not-exist") as ws:
        ws.send_json(_hello("does-not-exist"))
        rejected = ws.receive_json()
        assert rejected["type"] == "resume_rejected"


def test_reconnect_backfill_only_newer(client):
    sid = _make_ready_session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid, last_seq=0))
        ws.receive_json()  # resume_ready
        full = ws.receive_json()["events"]
        high = max(e["seq"] for e in full)
        # advance once so there are newer events to backfill.
        ws.send_json({"type": "advance.request", "signal": "session.start"})
        ws.receive_json()  # phase.changed
        ws.receive_json()  # L1 interviewer
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws2:
        ws2.send_json(_hello(sid, last_seq=high, resume=True))
        assert ws2.receive_json()["type"] == "resume_ready"
        newer = ws2.receive_json()["events"]
        assert newer  # at least the new events
        assert all(e["seq"] > high for e in newer)


def test_scripted_parity_and_evidence_resolves(client):
    sid = _make_ready_session(client)
    emitted: list[dict] = []
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid, last_seq=0))
        ws.receive_json()  # resume_ready
        ws.receive_json()  # backfill
        # Drive every scripted signal in order.
        for turn in SCRIPTED_SESSION:
            signal = turn["advance"]
            ws.send_json({"type": "advance.request", "signal": signal})
            # phase.changed + one per scripted event.
            n = 1 + len(turn["events"])
            for _ in range(n):
                emitted.append(ws.receive_json())
        # request the scorecard.
        ws.send_json({"type": "scorecard.request"})
        # 4 criterion.ready + 1 completed.
        for _ in range(5):
            emitted.append(ws.receive_json())

    # Expected TYPE/ACTOR order mirrors MockInterviewAdapter.
    expected = [
        ("phase.changed", "system"), ("interviewer.utterance", "interviewer"),
        ("phase.changed", "system"), ("candidate.utterance", "candidate"), ("interviewer.utterance", "interviewer"),
        ("phase.changed", "system"), ("candidate.utterance", "candidate"), ("interviewer.utterance", "interviewer"),
        ("phase.changed", "system"), ("candidate.utterance", "candidate"),
        ("phase.changed", "system"), ("code.edited", "candidate"), ("code.run", "candidate"),
        ("phase.changed", "system"), ("interviewer.utterance", "interviewer"), ("candidate.utterance", "candidate"),
        ("phase.changed", "system"), ("candidate.utterance", "candidate"),
        ("phase.changed", "system"), ("interviewer.utterance", "interviewer"),
        ("scorecard.criterion.ready", "system"), ("scorecard.criterion.ready", "system"),
        ("scorecard.criterion.ready", "system"), ("scorecard.criterion.ready", "system"),
        ("scorecard.completed", "system"),
    ]
    actual = [(e["type"], e["actor"]) for e in emitted]
    assert actual == expected

    # Every scorecard EvidenceRef.seq must resolve to a real ledger seq (> 0).
    ledger = client.get(f"/vnext/interview/sessions/{sid}/ledger").json()["events"]
    real_seqs = {e["seq"] for e in ledger}
    completed = next(e for e in emitted if e["type"] == "scorecard.completed")
    for score in completed["draft"]["scores"]:
        for ref in score["evidence"]:
            assert ref["seq"] in real_seqs and ref["seq"] > 0


def test_seq_strictly_increasing_across_rest_and_ws(client):
    sid = _make_ready_session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid, last_seq=0))
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "advance.request", "signal": "session.start"})
        ws.receive_json()
        ws.receive_json()
    ledger = client.get(f"/vnext/interview/sessions/{sid}/ledger").json()["events"]
    seqs = [e["seq"] for e in ledger]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))
