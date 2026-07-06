import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.vnext.interview.router import router
from app.vnext.interview.session_init import SessionInitManager


def test_session_init_lifecycle():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # create a session via REST
    intake = {"resumeText": "Payments.", "jobDescription": "Backend.", "role": "Backend Engineer", "seniority": "senior", "languages": ["Python"], "durationMinutes": 45}
    sid = client.post("/vnext/interview/sessions", json={"intake": intake, "mode": "scripted"}).json()["sessionId"]

    m = SessionInitManager(sid)
    # initial not complete
    assert not m.is_complete()

    # register greeting
    ev = m.register_greeting("Hello! Welcome to the interview.")
    assert ev["type"] == "session.greeting"
    assert not m.is_complete()

    # audio problem should emit event but not complete
    p = m.mark_audio_problem("mic muted")
    assert p["type"] == "session.audio.problem"
    assert not m.is_complete()

    # mark audio ok
    a = m.mark_audio_ok()
    assert a["type"] == "session.audio.ok"
    assert not m.is_complete()

    # mark ready
    r = m.mark_ready()
    assert r["type"] == "session.ready"
    assert m.is_complete()

    # idempotence
    r2 = m.mark_ready()
    assert r2["type"] == "session.ready.ack"


def test_session_init_persistence():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    intake = {"resumeText": "Payments.", "jobDescription": "Backend.", "role": "Backend Engineer", "seniority": "senior", "languages": ["Python"], "durationMinutes": 45}
    sid = client.post("/vnext/interview/sessions", json={"intake": intake, "mode": "scripted"}).json()["sessionId"]

    m = SessionInitManager(sid)
    assert not m.is_complete()
    m.register_greeting("hi")
    m.mark_audio_ok()

    # reload manager on same session and check state persisted
    m2 = SessionInitManager(sid)
    assert not m2.is_complete()
    m2.mark_ready()
    assert m2.is_complete()
