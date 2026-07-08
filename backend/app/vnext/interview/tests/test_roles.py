"""Roles change BEHAVIOR: rubric weights, problem selection, persona tone."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.vnext.interview.llm.interviewer_llm import _build_messages
from app.vnext.interview.roles import (
    PERSONAS,
    difficulty_band,
    normalize_role,
    pick_persona,
    role_weights,
)
from app.vnext.interview.router import router
from app.vnext.interview.scenario import scenario_for_role


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestRoleTracks:
    def test_normalize_role_fuzzy(self):
        assert normalize_role("SDE Intern") == "sde_intern"
        assert normalize_role("Software Engineering Intern") == "sde_intern"
        assert normalize_role("ML Engineer") == "ml_engineer"
        assert normalize_role("Machine Learning Engineer") == "ml_engineer"
        assert normalize_role("Data Scientist") == "ml_engineer"
        assert normalize_role("Backend Engineer") == "software_engineer"
        assert normalize_role(None) == "software_engineer"

    def test_weights_differ_by_role(self):
        swe, intern, ml = role_weights("SWE"), role_weights("SDE Intern"), role_weights("ML Engineer")
        assert intern["communication"] > swe["communication"]
        assert intern["complexity"] < swe["complexity"]
        assert ml["approach"] > swe["approach"]

    def test_intern_band_never_hard(self):
        assert "hard" not in difficulty_band("SDE Intern", "senior")
        assert difficulty_band("SDE Intern", "junior") == ("easy",)

    def test_role_selection_is_stable_and_role_sensitive(self):
        a = scenario_for_role("ML Engineer", "senior")
        b = scenario_for_role("ML Engineer", "senior")
        assert a is not None and a.id == b.id  # deterministic
        intern = scenario_for_role("SDE Intern", "junior")
        assert intern is not None and intern.problem.difficulty == "easy"


class TestPersona:
    def test_explicit_persona_wins_else_seniority_derives(self):
        assert pick_persona("rigorous", "junior") is PERSONAS["rigorous"]
        assert pick_persona(None, "senior") is PERSONAS["rigorous"]
        assert pick_persona(None, "mid") is PERSONAS["collaborative"]
        assert pick_persona("nope", "staff") is PERSONAS["rigorous"]  # unknown -> derived

    def test_persona_tone_contract_reaches_the_system_prompt(self):
        msgs = _build_messages(
            phase="coding", intake={"role": "SWE", "seniority": "mid"}, rubric={},
            transcript="", evidence_summary="", persona="rigorous",
        )
        system = msgs[0]["content"]
        assert "bar-raiser" in system
        assert "TONE RULES" in system


class TestRoleAwareSessions:
    INTAKE_ML = {"resumeText": "ML.", "jobDescription": "ML.", "role": "ML Engineer",
                 "seniority": "senior", "languages": ["Python"], "durationMinutes": 45}
    INTAKE_INTERN = {"resumeText": "Intern.", "jobDescription": "SDE.", "role": "SDE Intern",
                     "seniority": "junior", "languages": ["Python"], "durationMinutes": 45}

    def _rubric_for(self, client, intake, track):
        sid = client.post("/vnext/interview/sessions",
                          json={"intake": intake, "mode": "scripted", "track": track}).json()["sessionId"]
        return client.post(f"/vnext/interview/sessions/{sid}/rubric", json={"intake": intake}).json()["rubric"]

    def test_track_auto_resolves_by_role(self, client):
        for intake, allowed in ((self.INTAKE_ML, ("medium", "hard")), (self.INTAKE_INTERN, ("easy",))):
            sid = client.post("/vnext/interview/sessions",
                              json={"intake": intake, "mode": "scripted", "track": "auto"}).json()["sessionId"]
            body = client.get(f"/vnext/interview/sessions/{sid}").json()
            assert body["scenario"]["track"].startswith("problem:")

    def test_same_problem_different_role_different_weights(self, client):
        track = "problem:two_sum"
        r_ml = self._rubric_for(client, self.INTAKE_ML, track)
        r_intern = self._rubric_for(client, self.INTAKE_INTERN, track)
        w = lambda rub, cid: next(c["weight"] for c in rub["criteria"] if c["id"] == cid)
        assert w(r_intern, "communication") > w(r_ml, "communication")
        assert w(r_ml, "complexity") > w(r_intern, "complexity")
        # Both rubrics stay normalized to 100.
        assert sum(c["weight"] for c in r_ml["criteria"]) == 100
        assert sum(c["weight"] for c in r_intern["criteria"]) == 100
