# Handoff & Phase Status — SDE AI Interview

This file summarizes the planned phases, what I've implemented in the repository so far, and the remaining work to reach the full SDE scope you defined.

## Timeline (rough)
- Baseline mapped: ✅ done
- Phase 1 (intent layer): ~2–3 days — ✅ done
- Phase 2 (pause/timing model): ~5 days — not started
- Phase 3 (handlers + never-reveal hint ladder): ~7 days — in progress (prototype rule-based handlers present)
- Phase 4 (eval harness): ~8 days — not started
- Phase 5 (5–6 SDE problems via problem-spec engine): ~10 days — not started

Estimated total: ~2 weeks of focused work (with iteration and voice testing).

---

## What I implemented (done)
- Added a pluggable intent classifier (default rule-based fallback): [backend/app/vnext/interview/intent.py](backend/app/vnext/interview/intent.py)
- Replaced the hard-coded regex classifier in the WebSocket flow with the pluggable `IntentClassifier`: modified [backend/app/vnext/interview/ws.py](backend/app/vnext/interview/ws.py)
- Created a `SessionInitManager` to persist onboarding flags and emit ledger events: [backend/app/vnext/interview/session_init.py](backend/app/vnext/interview/session_init.py)
- Wired `SessionInitManager` into the WS flow (blocks `advance.request` until onboarding complete, handles candidate audio/readiness messages): [backend/app/vnext/interview/ws.py](backend/app/vnext/interview/ws.py)
- Added unit tests covering session init lifecycle: [backend/app/vnext/interview/tests/test_session_init.py](backend/app/vnext/interview/tests/test_session_init.py)
- Ensured existing conversation director tests still pass and ran targeted tests: [backend/app/vnext/interview/tests/test_conversation_director.py](backend/app/vnext/interview/tests/test_conversation_director.py)
- Committed and pushed these changes to `origin/main`.

---

## What remains (short-term priorities)
1. Add unit tests for the `IntentClassifier` (pluggable provider behavior and fallback).  
2. Finalize and extend Phase 1 handlers: implement full never-reveal "hint ladder" for HELP/REPEAT/META_AUDIO/THINKING with configurable policies.  
3. Design & implement Phase 2 (pause/timing model): service endpoint, per-intent pause policies, and integration into the live flow (non-blocking, auditable).  
4. Create Phase 4 eval harness: scripted edge-case runner that asserts intent detection, pause behaviors, and never-reveal policy (CI-able).  
5. Author docs: `SESSION_INIT.md`, architecture notes for the Conversational Director, and developer runbook for voice testing and Deepgram integration.  
6. Run the full test suite and CI; iterate until green.  
7. Add 5–6 SDE problems into the problem-spec engine and validate end-to-end with a real human (voice & editor).

---

## Quick next steps I can take now
- Add unit tests for `IntentClassifier` and push.  
- Implement the never-reveal hint ladder for Phase 3 (server-side handlers + tests).  
- Start Phase 2 design doc and a stub endpoint to accept pause policies.  

If you want me to proceed immediately, tell me which of the above to start first (I recommend: `IntentClassifier` tests → Phase 3 handlers → Phase 2 design).

---

## Useful commands
Run targeted tests I added:

```bash
cd backend
.\venv\Scripts\python.exe -m pytest -q app/vnext/interview/tests/test_session_init.py app/vnext/interview/tests/test_conversation_director.py
```

Run the full backend test suite:

```bash
cd backend
.\venv\Scripts\python.exe -m pytest -q
```

---

## Files touched in this round
- [backend/app/vnext/interview/intent.py](backend/app/vnext/interview/intent.py)
- [backend/app/vnext/interview/session_init.py](backend/app/vnext/interview/session_init.py)
- [backend/app/vnext/interview/ws.py](backend/app/vnext/interview/ws.py)
- [backend/app/vnext/interview/tests/test_session_init.py](backend/app/vnext/interview/tests/test_session_init.py)

---

Prepared by: development agent (work in this workspace).  
If you'd like, I can add this file into a PR with a longer `SESSION_INIT.md` design doc, or continue by implementing `IntentClassifier` unit tests and Phase 3 handlers now.
