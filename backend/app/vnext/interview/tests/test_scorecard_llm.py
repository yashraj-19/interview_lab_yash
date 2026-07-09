"""LLM scorecard: evidence resolution, validation/repair, staged WS emit, and
scripted fallback. NO network — the provider transport is monkeypatched.
"""
import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.vnext.interview.llm.client as llm_client
import app.vnext.interview.llm.scorecard_llm as sc
import app.vnext.interview.ws as ws_mod
from app.config import settings
from app.vnext.interview.llm.scorecard_llm import (
    build_scripted_scorecard,
    resolve_evidence_ref,
    validate_scorecard_scores,
)
from app.vnext.interview.router import router
from app.vnext.interview.seed import aggregate_overall

CONN_ID = "conn-test-0001-abcd"


# ── ledger fixtures ───────────────────────────────────────────────────────────

def _ledger():
    return [
        {"seq": 5, "type": "candidate.utterance", "actor": "candidate", "lineId": "L6",
         "text": "I'll use a hash map of value to index so it's one pass, O(n) time."},
        {"seq": 7, "type": "code.edited", "actor": "candidate", "editId": "E1",
         "after": "def two_sum(nums, target):\n    seen = {}"},
        {"seq": 8, "type": "code.run", "actor": "candidate", "runId": "R1",
         "code": "two_sum([2,7],9)", "stdout": "[0, 1]\n", "exitCode": 0},
        {"seq": 2, "type": "phase.changed", "actor": "system", "from": "ready", "to": "intro"},
    ]


def _rubric():
    return {
        "id": "rubric-s1",
        "criteria": [
            {"id": "problem_solving", "name": "Problem solving", "description": "d", "weight": 60},
            {"id": "coding", "name": "Coding", "description": "d", "weight": 40},
        ],
        "generatedBy": "llm",
        "version": 1,
    }


# ── evidence resolution ───────────────────────────────────────────────────────

def test_valid_evidence_accepted():
    by_seq = {e["seq"]: e for e in _ledger()}
    ref = resolve_evidence_ref({"seq": 5, "kind": "utterance", "excerpt": "one pass"}, by_seq)
    assert ref == {"kind": "utterance", "seq": 5, "excerpt": "one pass"}


def test_invalid_seq_dropped():
    by_seq = {e["seq"]: e for e in _ledger()}
    assert resolve_evidence_ref({"seq": 999, "kind": "utterance", "excerpt": "x"}, by_seq) is None
    assert resolve_evidence_ref({"seq": "nope", "excerpt": "x"}, by_seq) is None


def test_wrong_kind_repaired_from_event():
    by_seq = {e["seq"]: e for e in _ledger()}
    # claims utterance but seq 7 is a code edit -> kind repaired to "code".
    ref = resolve_evidence_ref({"seq": 7, "kind": "utterance", "excerpt": "def two_sum(nums, target):"}, by_seq)
    assert ref["kind"] == "code" and ref["seq"] == 7


def test_bad_excerpt_repaired_to_real_content():
    by_seq = {e["seq"]: e for e in _ledger()}
    ref = resolve_evidence_ref({"seq": 5, "kind": "utterance", "excerpt": "INVENTED QUOTE"}, by_seq)
    # excerpt replaced with a real substring of the event text.
    assert ref["excerpt"] in _ledger()[0]["text"]
    assert ref["excerpt"] != "INVENTED QUOTE"


def test_non_citable_event_dropped():
    by_seq = {e["seq"]: e for e in _ledger()}
    # seq 2 is a phase.changed — not citable evidence.
    assert resolve_evidence_ref({"seq": 2, "kind": "utterance", "excerpt": "x"}, by_seq) is None


# ── score validation / repair ─────────────────────────────────────────────────

def test_valid_scorecard_accepted_weights_forced():
    parsed = {"scores": [
        {"criterionId": "problem_solving", "score": 85, "verdict": "strong", "weight": 999,
         "evidence": [{"seq": 5, "kind": "utterance", "excerpt": "one pass"}], "gaps": ["no edge cases"]},
        {"criterionId": "coding", "score": 90, "verdict": "strong",
         "evidence": [{"seq": 8, "kind": "code_run", "excerpt": "[0, 1]"}], "gaps": []},
    ]}
    scores = validate_scorecard_scores(_rubric(), _ledger(), parsed)
    assert [s["criterionId"] for s in scores] == ["problem_solving", "coding"]
    assert [s["weight"] for s in scores] == [60, 40]  # forced from rubric
    assert all(all(r["seq"] in {5, 7, 8} for r in s["evidence"]) for s in scores)


def test_invalid_evidence_dropped_yields_insufficient():
    parsed = {"scores": [
        {"criterionId": "problem_solving", "score": 80, "verdict": "strong",
         "evidence": [{"seq": 999, "kind": "utterance", "excerpt": "ghost"}], "gaps": []},
        {"criterionId": "coding", "score": 70, "verdict": "mixed",
         "evidence": [{"seq": 8, "kind": "code_run", "excerpt": "[0, 1]"}], "gaps": []},
    ]}
    scores = validate_scorecard_scores(_rubric(), _ledger(), parsed)
    ps = next(s for s in scores if s["criterionId"] == "problem_solving")
    assert ps["evidence"] == [] and ps["verdict"] == "insufficient_evidence"


def test_missing_criterion_added_back():
    parsed = {"scores": [
        {"criterionId": "problem_solving", "score": 80, "verdict": "strong",
         "evidence": [{"seq": 5, "kind": "utterance", "excerpt": "one pass"}], "gaps": []},
    ]}
    scores = validate_scorecard_scores(_rubric(), _ledger(), parsed)
    coding = next(s for s in scores if s["criterionId"] == "coding")
    assert coding["verdict"] == "insufficient_evidence" and coding["evidence"] == []


def test_no_matching_criteria_returns_none():
    parsed = {"scores": [{"criterionId": "totally_unknown", "score": 50, "evidence": []}]}
    assert validate_scorecard_scores(_rubric(), _ledger(), parsed) is None
    assert validate_scorecard_scores(_rubric(), _ledger(), "garbage") is None


def test_overall_recomputed():
    scores = [
        {"criterionId": "problem_solving", "score": 80, "weight": 60, "verdict": "strong", "evidence": [], "gaps": []},
        {"criterionId": "coding", "score": 90, "weight": 40, "verdict": "strong", "evidence": [], "gaps": []},
    ]
    assert aggregate_overall(scores) == 84  # (80*60 + 90*40)/100


# ── WS integration (LLM mode, faked provider) ─────────────────────────────────

def _make_llm_session(client):
    intake = {"role": "Backend Engineer", "seniority": "senior", "languages": ["Python"],
              "resumeText": "payments", "jobDescription": "backend", "durationMinutes": 45}
    sid = client.post("/vnext/interview/sessions", json={"intake": intake, "mode": "llm"}).json()["sessionId"]
    client.post(f"/vnext/interview/sessions/{sid}/rubric", json={"intake": intake})
    return sid


def _hello(sid, last_seq=0, resume=False):
    return {"type": "client_hello", "session_id": sid, "client_conn_id": CONN_ID,
            "last_seq": last_seq, "resume": resume}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_llm_scorecard_staged_emit_real_seqs(client, monkeypatch):
    captured_rubric = {}

    async def fake_chat(url, headers, body, timeout):
        text = json.dumps(body)
        if "assessor" in text:  # scorecard system prompt
            # Cite the candidate utterance the test injects (it gets a real seq).
            return json.dumps({"scores": [
                {"criterionId": captured_rubric["ids"][0], "score": 82, "verdict": "strong",
                 "evidence": [{"seq": captured_rubric["cand_seq"], "kind": "utterance",
                               "excerpt": "hash map"}], "gaps": ["no complexity proof"]},
                {"criterionId": captured_rubric["ids"][1], "score": 70, "verdict": "mixed",
                 "evidence": [{"seq": 999999, "kind": "code", "excerpt": "ghost"}], "gaps": []},
            ]})
        if "Maya" in text:
            return json.dumps({"utterance": "Tell me your approach."})
        # rubric call
        return json.dumps({"criteria": [
            {"id": "ps", "name": "Problem solving", "description": "d", "weight": 60,
             "signals": ["a"], "phaseHints": ["coding"]},
            {"id": "comm", "name": "Communication", "description": "d", "weight": 40,
             "signals": ["b"], "phaseHints": ["intro"]},
        ]})

    monkeypatch.setattr(llm_client, "_chat_completion", fake_chat)

    sid = _make_llm_session(client)
    rub = client.get(f"/vnext/interview/sessions/{sid}").json()["rubric"]
    captured_rubric["ids"] = [c["id"] for c in rub["criteria"]]

    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json()  # resume_ready
        ws.receive_json()  # backfill
        # Real candidate utterance -> gets a real ledger seq.
        ws.send_json({"type": "candidate.text", "text": "I'd use a hash map, one pass."})
        cand = ws.receive_json()
        captured_rubric["cand_seq"] = cand["seq"]
        # Drain the reactive conversation turn (turn.started + intent.detected +
        # Maya's reply) before scoring — the fake chat has no scorecard-shaped
        # 'reply', so it falls back to a deterministic in-phase ack.
        ws.receive_json(); ws.receive_json(); ws.receive_json()

        ws.send_json({"type": "scorecard.request"})
        ready = [ws.receive_json() for _ in range(len(rub["criteria"]))]
        completed = ws.receive_json()

    assert all(e["type"] == "scorecard.criterion.ready" for e in ready)
    assert completed["type"] == "scorecard.completed"
    draft = completed["draft"]

    # criterion ids match the rubric EXACTLY, in order.
    assert [s["criterionId"] for s in draft["scores"]] == captured_rubric["ids"]
    # weights forced from rubric.
    assert [s["weight"] for s in draft["scores"]] == [c["weight"] for c in rub["criteria"]]

    # EVERY emitted evidence seq resolves to a real ledger event.
    ledger = client.get(f"/vnext/interview/sessions/{sid}/ledger").json()["events"]
    real = {e["seq"] for e in ledger}
    for s in draft["scores"]:
        for r in s["evidence"]:
            assert r["seq"] in real
    # the ghost seq 999999 was dropped -> that criterion is insufficient_evidence.
    second = draft["scores"][1]
    assert second["evidence"] == [] and second["verdict"] == "insufficient_evidence"

    # overall = weighted average, recomputed.
    assert draft["overall"] == aggregate_overall(draft["scores"])
    # persisted for review.
    review = client.get(f"/vnext/interview/sessions/{sid}/review").json()
    assert review["scorecard"]["overall"] == draft["overall"]


def test_llm_malformed_scorecard_falls_back_to_scripted(client, monkeypatch):
    async def fake_chat(url, headers, body, timeout):
        text = json.dumps(body)
        if "assessor" in text:
            return "not json at all <<<"
        if "Maya" in text:
            return json.dumps({"utterance": "go"})
        return json.dumps({"criteria": [
            {"id": "problem_solving", "name": "PS", "description": "d", "weight": 30,
             "signals": ["a"], "phaseHints": ["coding"]},
            {"id": "coding", "name": "C", "description": "d", "weight": 30,
             "signals": ["a"], "phaseHints": ["coding"]},
            {"id": "communication", "name": "Comm", "description": "d", "weight": 20,
             "signals": ["a"], "phaseHints": ["intro"]},
            {"id": "system_design", "name": "SD", "description": "d", "weight": 20,
             "signals": ["a"], "phaseHints": ["optimization"]},
        ]})

    monkeypatch.setattr(llm_client, "_chat_completion", fake_chat)

    sid = _make_llm_session(client)
    # Drive the full scripted-shaped session so SCORE_PLANS refs resolve.
    from app.vnext.interview.seed import SCRIPTED_SESSION
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json(); ws.receive_json()
        # In llm mode candidate/code events aren't auto-emitted; inject one so
        # the scripted fallback's ref ids exist in the ledger. A substantive
        # answer drives a reactive turn (candidate.utterance + turn.started +
        # intent.detected + Maya's ack) — drain all four before scoring.
        ws.send_json({"type": "candidate.text", "text": "I'd use a hash map."})
        ws.receive_json(); ws.receive_json(); ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "scorecard.request"})
        # Scripted fallback emits 4 criteria + completed.
        evs = [ws.receive_json() for _ in range(5)]

    completed = evs[-1]
    assert completed["type"] == "scorecard.completed"
    ids = [s["criterionId"] for s in completed["draft"]["scores"]]
    assert ids == ["problem_solving", "coding", "communication", "system_design"]
    assert "<<<" not in json.dumps(completed)


# ── E5 fix: scripted fallback must be RUBRIC-shaped (not SCORE_PLANS ids) ──────

class _FakeLedger:
    """Minimal ledger: resolves scripted ref ids to real seqs (or 0 = unknown)."""
    def __init__(self, refs=None):
        self._refs = refs or {}
    def find_seq_by_ref(self, ref_id: str) -> int:
        return self._refs.get(ref_id, 0)


def _custom_llm_rubric():
    # LLM-generated rubric whose ids are NOT in SCORE_PLANS.
    return {
        "id": "rubric-custom",
        "criteria": [
            {"id": "api_design", "name": "API Design", "description": "d", "weight": 55},
            {"id": "scalability", "name": "Scalability", "description": "d", "weight": 45},
        ],
        "generatedBy": "llm",
        "version": 1,
    }


def test_fallback_matches_custom_rubric_ids_exactly():
    rubric = _custom_llm_rubric()
    scores, draft = build_scripted_scorecard("s1", rubric, _FakeLedger())
    # Exactly one score per rubric criterion, in rubric order, ids matching.
    assert [s["criterionId"] for s in scores] == ["api_design", "scalability"]
    # No foreign SCORE_PLANS ids leaked in.
    assert all(s["criterionId"] in {"api_design", "scalability"} for s in scores)


def test_fallback_weights_match_rubric_not_zero():
    rubric = _custom_llm_rubric()
    scores, _ = build_scripted_scorecard("s1", rubric, _FakeLedger())
    by_id = {s["criterionId"]: s for s in scores}
    assert by_id["api_design"]["weight"] == 55
    assert by_id["scalability"]["weight"] == 45
    # Custom criteria have no plan -> honest insufficient_evidence, empty evidence.
    for s in scores:
        assert s["verdict"] == "insufficient_evidence"
        assert s["evidence"] == []
        assert s["gaps"] == ["No valid scorecard evidence was available for this rubric criterion."]


def test_fallback_overall_recomputed_from_rubric_shaped_scores():
    rubric = _custom_llm_rubric()
    scores, draft = build_scripted_scorecard("s1", rubric, _FakeLedger())
    assert draft["overall"] == aggregate_overall(scores)
    # All-zero custom scores -> overall 0 (no fabricated value).
    assert draft["overall"] == 0
    assert draft["rubricId"] == "rubric-custom"


# ── reliability fix: bounded build + always-emit + paste gap note ─────────────

class _Ledger:
    """Minimal ledger exposing get_all + find_seq_by_ref for build_scorecard_llm."""
    def __init__(self, events=None):
        self._events = events or []

    def get_all(self):
        return list(self._events)

    def find_seq_by_ref(self, ref_id: str) -> int:
        return 0


def test_scorecard_llm_timeout_falls_back_within_budget(monkeypatch):
    """A slow/never-returning provider must NOT hang: the wall-clock budget trips
    and we return the deterministic rubric-shaped scripted scorecard."""
    async def never_returns(*args, **kwargs):
        await asyncio.sleep(5)  # would hang forever without the budget
        return "{}"

    monkeypatch.setattr(sc, "call_llm", never_returns)
    monkeypatch.setattr(sc, "_SCORECARD_BUILD_BUDGET", 0.05)

    rubric = _rubric()
    scores, draft = asyncio.run(
        sc.build_scorecard_llm("s1", {"role": "Backend"}, rubric, _Ledger(_ledger()), "scoring")
    )
    # Rubric-shaped, complete, ids pinned to the rubric.
    assert [s["criterionId"] for s in scores] == ["problem_solving", "coding"]
    assert draft["stage"] == "complete"


def test_scorecard_llm_malformed_falls_back(monkeypatch):
    async def garbage(*args, **kwargs):
        return "not json <<<"

    monkeypatch.setattr(sc, "call_llm", garbage)
    rubric = _rubric()
    scores, draft = asyncio.run(
        sc.build_scorecard_llm("s1", {"role": "Backend"}, rubric, _Ledger(_ledger()), "scoring")
    )
    assert [s["criterionId"] for s in scores] == ["problem_solving", "coding"]
    assert draft["stage"] == "complete"


def test_no_code_coding_criterion_capped_with_paste_note():
    """Code pasted into chat (no code.edited/code.run) -> coding score capped AND
    the new 'pasted in chat, not submitted through the code box' gap note added."""
    ledger = [
        {"seq": 5, "type": "candidate.utterance", "actor": "candidate", "lineId": "L6",
         "text": "Here's my solution: def two_sum(nums, target): seen = {} ..."},
    ]
    parsed = {"scores": [
        {"criterionId": "problem_solving", "score": 60, "verdict": "mixed",
         "evidence": [{"seq": 5, "kind": "utterance", "excerpt": "def two_sum"}], "gaps": []},
        {"criterionId": "coding", "score": 95, "verdict": "strong",
         "evidence": [{"seq": 5, "kind": "utterance", "excerpt": "def two_sum"}], "gaps": []},
    ]}
    scores = validate_scorecard_scores(_rubric(), ledger, parsed)
    coding = next(s for s in scores if s["criterionId"] == "coding")
    assert coding["score"] <= 70  # capped
    assert coding["verdict"] != "strong"
    assert any("not submitted through the code box" in g for g in coding["gaps"])


def test_ws_scorecard_request_emits_failed_when_build_raises(client, monkeypatch):
    """The socket must NEVER hang on scoring: if the build raises, emit a terminal
    scorecard.failed event rather than nothing."""
    async def boom(session_id):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(ws_mod, "_build_scorecard", boom)

    intake = {"role": "Backend Engineer", "seniority": "senior", "languages": ["Python"],
              "resumeText": "payments", "jobDescription": "backend", "durationMinutes": 45}
    sid = client.post("/vnext/interview/sessions", json={"intake": intake}).json()["sessionId"]
    client.post(f"/vnext/interview/sessions/{sid}/rubric", json={"intake": intake})

    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "scorecard.request"})
        ev = ws.receive_json()

    assert ev["type"] == "scorecard.failed"
    assert ev["reason"] == "RuntimeError"


def test_fallback_reuses_plan_for_matching_scripted_rubric_id():
    # Original scripted rubric ids DO match SCORE_PLANS; ensure plan reuse still works
    # and weights come from the rubric.
    rubric = _rubric()  # ids problem_solving (60), coding (40)
    # Resolve at least one scripted evidence ref so a plan produces evidence.
    from app.vnext.interview.seed import SCORE_PLANS
    refs = {}
    for p in SCORE_PLANS:
        for e in p["evidence"]:
            refs[e["refId"]] = 5  # any real seq
    scores, _ = build_scripted_scorecard("s1", rubric, _FakeLedger(refs))
    assert [s["criterionId"] for s in scores] == ["problem_solving", "coding"]
    by_id = {s["criterionId"]: s for s in scores}
    assert by_id["problem_solving"]["weight"] == 60
    assert by_id["coding"]["weight"] == 40
