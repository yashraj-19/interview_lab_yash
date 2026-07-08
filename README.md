# SViam Interview — Lab

A self-contained demo of **Maya**, an evidence-native AI technical interviewer
with a **dynamic scenario engine**: one generic engine conducts the
production-incident demo *and* six SDE coding problems across three role
tracks (SWE / SDE intern / ML engineer).

- **Voice with real turn-taking** — semantic turn-end (no fixed timers),
  barge-in that yields to "hold on—" but talks through "mm-hm".
- **Live code review** — Maya highlights risky lines as you type; on the
  incident she proposes a validated patch you can accept/reject; on problems
  she probes but **never writes the answer**.
- **Real code execution** — "Run code" executes the problem's test cases in a
  sandbox (honest pass/fail, timeouts caught).
- **Never-reveal, enforced in code** — verdicts, praise, and answer terms are
  scrubbed from LLM output; hints escalate contingently and hint-spamming is
  throttled.
- **Evidence-cited scorecard** + a conversation-quality metrics panel
  (response gaps, barge-ins honored, hint depth) computed from the event ledger.

This is a lab extract — no production integration, no database required.
Docs: [ARCHITECTURE.md](ARCHITECTURE.md) · [API_REFERENCE.md](API_REFERENCE.md)
· [PHASES_STATUS.md](PHASES_STATUS.md) · [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md)

## Stack

- **Frontend:** Next.js 16, React 19, TypeScript, Tailwind CSS 4
- **Backend:** FastAPI (Python 3.11+), in-memory store, WebSocket transport
- **Voice:** ElevenLabs (optional) with a browser-speech fallback

## Run it

You need two terminals.

### 1. Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 2. Frontend

```bash
npm install
npm run dev
```

Then open **http://localhost:3000** — it redirects to the lab launcher. Click
**“Start software engineer incident demo”**, pick any of the **six SDE
problems**, or hit **“Match a problem to my role”** — every track auto-starts
(no setup, no resume).

> Use **Chrome** and allow the microphone for the full voice experience. Without
> a mic it falls back to a “Type instead” box. Without any LLM key the backend
> runs a deterministic scripted interviewer — still fully interactive.

Direct URL: `http://localhost:3000/lab/interview-v3/intake?adapter=live-llm&track=incident-demo`

## Optional configuration

Everything works with zero config. To go live:

- `backend/.env` → `OPENROUTER_API_KEY` for generative interviewer/scoring (see `backend/.env.example`)
- `.env.local` → `ELEVENLABS_API_KEY` for Maya's voice (see `.env.example`)

## Tests

```bash
npm test                                   # frontend unit tests (vitest)
cd backend && pytest                       # backend tests
```
