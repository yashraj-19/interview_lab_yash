# SViam Interview — Lab

A self-contained demo of **Maya**, an evidence-native AI technical interviewer.
It runs a production-incident coding interview: the candidate is shown a buggy
payments API that double-charges on retry, edits the code live, talks to Maya by
voice (with barge-in), and Maya can **select risky lines, explain the failure,
and propose a patch** the candidate can accept, reject, or edit. A scorecard
cites real transcript/code evidence.

This is a lab extract — no production integration, no database required.

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
**“Start software engineer incident demo”** and the interview auto-starts (no
setup, no resume).

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
