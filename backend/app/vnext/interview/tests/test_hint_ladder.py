from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.vnext.interview.router import router
from app.vnext.interview.hint_ladder import next_hint
from app.vnext.interview.store import STORE


def test_next_hint_sequence():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # create a session
    intake = {
        "resumeText": "Payments backend experience.",
        "jobDescription": "Backend engineer",
        "role": "Backend Engineer",
        "seniority": "senior",
        "languages": ["Python"],
        "durationMinutes": 45,
    }
    sid = client.post("/vnext/interview/sessions", json={"intake": intake, "mode": "scripted"}).json()["sessionId"]

    # first hint
    h1 = next_hint(sid, "help")
    assert h1 is not None
    assert h1["hint_step"] == 1

    # persist a fake interviewer utterance to simulate emission
    STORE.append_event(sid, "interviewer", "interviewer.utterance", {"text": h1["text"], "hint_for": "help", "hint_step": 1})

    # second hint
    h2 = next_hint(sid, "help")
    assert h2 is not None
    assert h2["hint_step"] == 2

    # exhaust ladder
    STORE.append_event(sid, "interviewer", "interviewer.utterance", {"text": h2["text"], "hint_for": "help", "hint_step": 2})
    h3 = next_hint(sid, "help")
    assert h3 is not None
    assert h3["hint_step"] == 3

    # after exhausting, next returns exhausted True
    STORE.append_event(sid, "interviewer", "interviewer.utterance", {"text": h3["text"], "hint_for": "help", "hint_step": 3})
    h4 = next_hint(sid, "help")
    assert h4 is not None
    assert h4.get("exhausted") is True
