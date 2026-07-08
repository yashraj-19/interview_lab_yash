# Deployment & Demo Runbook

This is a **lab**: in-memory store, no auth, no rate limiting. It deploys
fine for demos; it is not a multi-tenant production service. What follows is
what actually exists.

## Environment variables

Backend (`backend/.env`, see `.env.example`):

| Var | Effect when set | Effect when missing |
|---|---|---|
| `OPENROUTER_API_KEY` | live LLM interviewer/rubric/scorecard | deterministic scripted paths everywhere (fully functional) |
| `OPENAI_API_KEY` | fallback LLM provider | — |
| `VNEXT_ALLOW_FAKE_LLM=1` | TEST-ONLY: honors per-request `fake_llm` | flag ignored (production-safe default) |
| `VNEXT_STORE=supabase` | **not implemented** — logs a warning and falls back to memory | in-memory store |

Frontend (`.env.local`, see `.env.example`):

| Var | Effect |
|---|---|
| `ELEVENLABS_API_KEY` | Maya's real voice ("Sia"); needs a PAID plan (free keys 402 → browser-TTS fallback) |
| `NEXT_PUBLIC_API_URL` | backend base URL (defaults to `http://localhost:8000`; a missing value in prod builds logs loudly) |

## Run

```bash
# backend — NO --reload for demos: the store is in-memory and a code reload
# wipes every live session mid-interview. Single worker only (per-process state).
cd backend && uvicorn app.main:app --port 8000

# frontend
npm run dev            # or: npm run build && npm start
```

## Demo-day checklist

1. **Headphones, always** — STT is browser Web Speech with a text-overlap
   echo heuristic, not acoustic echo cancellation; on speakers Maya can hear
   herself.
2. **Chrome** for voice; allow the microphone.
3. Morning-of TTS check: `curl -X POST localhost:3000/api/lab/voice -d '{"text":"sound check"}' -H "Content-Type: application/json"`
   → expect 200 `audio/mpeg`; check the ElevenLabs character quota.
4. Open the **review page in the same browser** that ran the interview
   (ledger persistence is localStorage).
5. No key? Fine — scripted mode conducts every scenario deterministically.

## What degrades to what

Voice → browser TTS → text box; LLM → scripted scenario lines (plus a 12s
mid-turn stall fallback); every LLM sub-system (rubric, scorecard, JD) has a
deterministic fallback baked in and tested.

## Verification

`cd backend && pytest app/vnext/interview/tests` (195 tests, ~8s) ·
`npm test` (105 tests) · `npx tsc --noEmit`. The behavioral surface
(barge-in, hints, throttle, silence ladder, runner, guard) is covered by
WS-level tests that run without any network or keys.
