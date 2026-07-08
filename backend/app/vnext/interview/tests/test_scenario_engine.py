"""ScenarioSpec engine: the problem catalog conducts REAL interviews.

Covers the registry contract, the sandboxed runner, and a full WS flow on a
problem track: scenario rubric, seed-code vending, deterministic lines,
wrong-code highlight probe (never a patch), per-problem never-reveal hints,
and real test-case execution. The incident track's unchanged behavior is
already pinned by test_incident_track/test_incident_code_actions.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.vnext.interview.problem_spec import PROBLEM_CATALOG
from app.vnext.interview.runner import run_candidate_code
from app.vnext.interview.scenario import (
    PROBLEM_TRACK_PREFIX,
    SCENARIOS,
    get_scenario,
    list_problem_scenarios,
    scenario_for_role,
)
from app.vnext.interview.router import router
from app.vnext.interview.phase_controller import ADVANCE_SIGNALS

CONN_ID = "conn-test-scenario-0001"


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── registry contract ─────────────────────────────────────────────────────────

class TestRegistry:
    def test_every_catalog_problem_has_a_scenario(self):
        for pid in PROBLEM_CATALOG:
            spec = get_scenario(f"{PROBLEM_TRACK_PREFIX}{pid}")
            assert spec is not None, pid
            assert spec.seed_code and spec.task_prompt and spec.rubric_factory

    def test_problem_lines_cover_every_advance_signal_the_ws_uses(self):
        # Signals after session start (intake/rubric happen over REST).
        ws_signals = [s for s in ADVANCE_SIGNALS if s not in ("intake.submitted", "rubric.generated", "wrap.done", "scoring.done")]
        for spec in list_problem_scenarios():
            for sig in ws_signals:
                assert sig in spec.lines_for_signal, f"{spec.id} missing line for {sig}"

    def test_problem_scenarios_never_patch(self):
        for spec in list_problem_scenarios():
            assert spec.build_patch is None, f"{spec.id} must not propose solution patches"

    def test_problem_lines_and_guidance_never_leak_reveal_terms(self):
        """The deterministic content of a scenario must obey its own never-reveal
        contract — a scripted line naming 'hash map' would defeat the guard."""
        for spec in list_problem_scenarios():
            corpus = " ".join(spec.lines_for_signal.values()).lower()
            corpus += " " + spec.task_prompt.lower() + " " + spec.seed_code.lower()
            for term in spec.reveal_terms:
                assert term.lower() not in corpus, f"{spec.id} leaks '{term}' in its own lines"

    def test_unknown_and_default_tracks_have_no_scenario(self):
        assert get_scenario(None) is None
        assert get_scenario("nope") is None

    def test_role_based_selection_respects_difficulty(self):
        junior = scenario_for_role("SDE", "junior")
        senior = scenario_for_role("SDE", "senior")
        assert junior is not None and junior.problem.difficulty == "easy"
        assert senior is not None and senior.problem.difficulty in ("medium", "hard")

    def test_incident_scenario_preserves_original_hooks(self):
        from app.vnext.interview.incident import INCIDENT_SEED_CODE, INCIDENT_TRACK
        spec = SCENARIOS[INCIDENT_TRACK]
        assert spec.seed_code == INCIDENT_SEED_CODE
        assert spec.build_patch is not None  # incident keeps its patch flow


# ── wrong-code detectors ──────────────────────────────────────────────────────

class TestDetectors:
    def test_two_sum_nested_for_flagged(self):
        spec = get_scenario("problem:two_sum")
        brute = "def two_sum(nums, target):\n    for i in range(len(nums)):\n        for j in range(i+1, len(nums)):\n            if nums[i]+nums[j]==target: return [i,j]\n"
        assert spec.code_is_unsafe(brute) is True
        start, end = spec.risky_range(brute)
        assert start == 1 and end >= 2  # the loop lines

    def test_two_sum_optimal_not_flagged(self):
        spec = get_scenario("problem:two_sum")
        optimal = "def two_sum(nums, target):\n    seen = {}\n    for i, n in enumerate(nums):\n        if target-n in seen: return [seen[target-n], i]\n        seen[n] = i\n"
        assert spec.code_is_unsafe(optimal) is False

    def test_sliding_window_while_inside_for_not_flagged(self):
        spec = get_scenario("problem:longest_substring_without_repeating")
        optimal = (
            "def length_of_longest_substring(s):\n    left = 0\n    window = set()\n    best = 0\n"
            "    for right in range(len(s)):\n        while s[right] in window:\n"
            "            window.remove(s[left]); left += 1\n        window.add(s[right])\n"
            "        best = max(best, right-left+1)\n    return best\n"
        )
        assert spec.code_is_unsafe(optimal) is False

    def test_binary_search_linear_scan_flagged_halving_not(self):
        spec = get_scenario("problem:binary_search")
        scan = "def binary_search(nums, target):\n    for i in range(len(nums)):\n        if nums[i]==target: return i\n    return -1\n"
        halving = "def binary_search(nums, target):\n    lo, hi = 0, len(nums)-1\n    while lo <= hi:\n        mid = (lo+hi)//2\n        if nums[mid]==target: return mid\n        if nums[mid]<target: lo = mid+1\n        else: hi = mid-1\n    return -1\n"
        assert spec.code_is_unsafe(scan) is True
        assert spec.code_is_unsafe(halving) is False

    def test_foreign_code_never_flagged(self):
        spec = get_scenario("problem:two_sum")
        assert spec.code_is_unsafe("for a in x:\n    for b in y:\n        pass") is False  # no two_sum fn


# ── sandboxed runner ──────────────────────────────────────────────────────────

class TestRunner:
    def test_correct_solution_passes_all_cases(self):
        p = PROBLEM_CATALOG["two_sum"]
        code = "def two_sum(nums, target):\n    seen = {}\n    for i, n in enumerate(nums):\n        if target-n in seen: return [seen[target-n], i]\n        seen[n] = i\n"
        out = run_candidate_code(code, p, "two_sum")
        assert out["exitCode"] == 0
        assert out["passed"] == out["total"] == len(p.test_cases)

    def test_unordered_result_accepted(self):
        p = PROBLEM_CATALOG["two_sum"]
        code = "def two_sum(nums, target):\n    seen = {}\n    for i, n in enumerate(nums):\n        if target-n in seen: return [i, seen[target-n]]\n        seen[n] = i\n"
        out = run_candidate_code(code, p, "two_sum")  # reversed index order
        assert out["exitCode"] == 0, out["stdout"]

    def test_wrong_solution_reports_failing_case(self):
        p = PROBLEM_CATALOG["valid_parentheses"]
        code = "def is_valid(s):\n    return True\n"  # wrong for mismatched cases
        out = run_candidate_code(code, p, "is_valid")
        assert out["exitCode"] == 1
        assert 0 < out["passed"] < out["total"]
        assert "FAIL" in out["stdout"]

    def test_raising_code_is_reported_not_faked(self):
        p = PROBLEM_CATALOG["two_sum"]
        out = run_candidate_code("def two_sum(nums, target):\n    raise ValueError('boom')\n", p, "two_sum")
        assert out["exitCode"] == 1
        assert out["passed"] == 0

    def test_infinite_loop_times_out(self):
        p = PROBLEM_CATALOG["two_sum"]
        out = run_candidate_code("def two_sum(nums, target):\n    while True: pass\n", p, "two_sum")
        assert out["exitCode"] == 2
        assert "Timed out" in out["stdout"]

    def test_syntax_error_reported(self):
        p = PROBLEM_CATALOG["two_sum"]
        out = run_candidate_code("def two_sum(nums, target)\n    pass\n", p, "two_sum")
        assert out["exitCode"] == 1


# ── full WS flow on a problem track ──────────────────────────────────────────

def _make_problem_session(client, track="problem:two_sum", mode="scripted") -> str:
    intake = {"resumeText": "SDE.", "jobDescription": "Backend.", "role": "SDE",
              "seniority": "mid", "languages": ["Python"], "durationMinutes": 45}
    sid = client.post("/vnext/interview/sessions",
                      json={"intake": intake, "mode": mode, "track": track}).json()["sessionId"]
    client.post(f"/vnext/interview/sessions/{sid}/rubric", json={"intake": intake})
    return sid


def _hello(sid):
    return {"type": "client_hello", "session_id": sid, "client_conn_id": CONN_ID,
            "last_seq": 0, "resume": False}


def test_problems_endpoint_lists_catalog(client):
    problems = client.get("/vnext/interview/problems").json()["problems"]
    assert len(problems) == len(PROBLEM_CATALOG)
    assert all(p["track"].startswith(PROBLEM_TRACK_PREFIX) for p in problems)


def test_problem_session_vends_scenario_and_rubric(client):
    sid = _make_problem_session(client)
    body = client.get(f"/vnext/interview/sessions/{sid}").json()
    assert body["scenario"]["track"] == "problem:two_sum"
    assert "def two_sum" in body["scenario"]["seedCode"]
    assert "Two Sum" in body["scenario"]["taskPrompt"]
    crit_ids = [c["id"] for c in body["rubric"]["criteria"]]
    assert "correctness" in crit_ids and "complexity" in crit_ids


def test_problem_ws_flow_lines_actions_hints_and_run(client):
    sid = _make_problem_session(client)
    with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
        ws.send_json(_hello(sid))
        ws.receive_json(); ws.receive_json()

        # Deterministic scenario line on advance (scripted mode).
        ws.send_json({"type": "advance.request", "signal": "session.start"})
        assert ws.receive_json()["type"] == "phase.changed"
        line = ws.receive_json()
        assert line["type"] == "interviewer.utterance"
        assert "Two Sum" in line["text"]

        # Brute-force code -> highlight + probe, NEVER a patch.
        brute = "def two_sum(nums, target):\n    for i in range(len(nums)):\n        for j in range(i+1, len(nums)):\n            if nums[i]+nums[j]==target: return [i,j]\n"
        ws.send_json({"type": "candidate.code", "code": brute})
        assert ws.receive_json()["type"] == "code.edited"
        sel = ws.receive_json(); assert sel["type"] == "selection.set"
        assert ws.receive_json()["type"] == "highlight.set"
        probe = ws.receive_json()
        assert probe["type"] == "interviewer.utterance"
        assert "complexity" in probe["text"].lower()

        # Same shape again -> no second nag (dedupe), just the edit event.
        ws.send_json({"type": "candidate.code", "code": brute + "\n# tweak"})
        assert ws.receive_json()["type"] == "code.edited"

        # Per-problem never-reveal hint (rung 1 = the catalog's nudge).
        ws.send_json({"type": "candidate.text", "text": "I'm stuck, any hint?"})
        assert ws.receive_json()["type"] == "candidate.utterance"
        assert ws.receive_json()["type"] == "conversation.intent.detected"
        hint = ws.receive_json()
        assert hint["type"] == "interviewer.utterance"
        assert hint["hint_step"] == 1
        assert "o(1)" in hint["text"].lower()          # catalog rung 1
        assert "hash map" not in hint["text"].lower()  # rung 1 never reveals

        # REAL execution: wrong code fails honestly...
        ws.send_json({"type": "candidate.run", "code": "def two_sum(nums, target):\n    return []\n"})
        run1 = ws.receive_json()
        assert run1["type"] == "code.run" and run1["exitCode"] == 1 and run1["passed"] == 0

        # ...correct code passes all cases.
        good = "def two_sum(nums, target):\n    seen = {}\n    for i, n in enumerate(nums):\n        if target-n in seen: return [seen[target-n], i]\n        seen[n] = i\n"
        ws.send_json({"type": "candidate.run", "code": good})
        run2 = ws.receive_json()
        assert run2["exitCode"] == 0 and run2["passed"] == run2["total"] == 3
