from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.vnext.interview.router import router
from app.vnext.interview.intent import get_classifier

CONN_ID = "conn-test-intent-0001"


def _hello(sid: str) -> dict:
    return {
        "type": "client_hello",
        "session_id": sid,
        "client_conn_id": CONN_ID,
        "last_seq": 0,
        "resume": False,
    }


def _make_llm_session(client: TestClient) -> str:
    intake = {
        "resumeText": "Payments backend experience.",
        "jobDescription": "Backend engineer",
        "role": "Backend Engineer",
        "seniority": "senior",
        "languages": ["Python"],
        "durationMinutes": 45,
    }
    sid = client.post("/vnext/interview/sessions", json={"intake": intake, "mode": "scripted"}).json()["sessionId"]
    client.post(f"/vnext/interview/sessions/{sid}/rubric", json={"intake": intake})
    return sid


def test_dynamic_provider_used():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    sid = _make_llm_session(client)

    classifier = get_classifier()

    # dynamic provider: if text contains 'please' return 'help'
    def provider(text: str, session_id: str) -> str:
        if "please" in (text or "").lower():
            return "help"
        return "answer"

    classifier.register_provider(provider)

    try:
        with client.websocket_connect(f"/vnext/interview/ws/{sid}") as ws:
            ws.send_json(_hello(sid))
            ws.receive_json()  # resume_ready
            ws.receive_json()  # backfill

            ws.send_json({"type": "candidate.text", "text": "please help me"})
            cand = ws.receive_json()
            assert cand["type"] == "candidate.utterance"

            intent_ev = ws.receive_json()
            assert intent_ev["type"] == "conversation.intent.detected"
            assert intent_ev.get("intent") == "help"
    finally:
        classifier.unregister_provider()
