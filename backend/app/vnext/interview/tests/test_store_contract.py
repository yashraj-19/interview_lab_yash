"""Store CONTRACT tests — run against the in-memory store (no network).

These assert the InterviewStore interface end-to-end so any implementation
(memory today, Supabase via E6) can be checked against the same expectations:
create session -> bind rubric -> append events (seq monotonic) -> backfill since
seq -> persist scorecard -> reload session/review from store.
"""
from __future__ import annotations

from app.vnext.interview.store import InMemoryInterviewStore


def test_store_contract_full_lifecycle() -> None:
    store = InMemoryInterviewStore()

    # create
    sid = store.create_session({"role": "swe", "level": "mid"})
    rec = store.get_session(sid)
    assert rec is not None
    assert rec["sessionId"] == sid
    assert rec["phase"] == "intake"
    assert rec["rubric"] is None
    assert rec["scorecard"] is None

    # bind rubric + mode via put_session
    rec["rubric"] = {"id": "r1", "criteria": []}
    rec["mode"] = "scripted"
    rec["phase"] = "ready"
    store.put_session(sid, rec)
    reloaded = store.get_session(sid)
    assert reloaded["rubric"] == {"id": "r1", "criteria": []}
    assert reloaded["phase"] == "ready"
    assert reloaded["mode"] == "scripted"

    # append events -> seq monotonic from 1
    e1 = store.append_event(sid, "system", "rubric.bound", {"rubric": {"id": "r1"}})
    e2 = store.append_event(sid, "interviewer", "interviewer.utterance", {"lineId": "l1", "text": "hi"})
    e3 = store.append_event(sid, "candidate", "candidate.utterance", {"lineId": "c1", "text": "yo"})
    assert [e1["seq"], e2["seq"], e3["seq"]] == [1, 2, 3]
    assert store.get_ledger(sid).last_seq == 3

    # full + backfill since seq
    assert [e["seq"] for e in store.get_events(sid, 0)] == [1, 2, 3]
    assert [e["seq"] for e in store.get_events(sid, 2)] == [3]
    assert store.get_events(sid, 3) == []

    # persist scorecard -> visible on reload/review
    draft = {"overall": 4, "scores": [{"criterion": "c", "score": 4}]}
    store.put_scorecard(sid, draft)
    assert store.get_session(sid)["scorecard"] == draft


def test_store_contract_missing_session() -> None:
    store = InMemoryInterviewStore()
    assert store.get_session("nope") is None
    assert store.get_ledger("nope") is None
    assert store.get_events("nope") == []
