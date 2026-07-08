# API Reference — vNext Interview (as implemented)

Every endpoint and message below exists in the code and is exercised by the
test suite. Base path: `/vnext/interview`.

## REST

### `POST /sessions` — create a session
```json
{
  "intake": {
    "resumeText": "…", "jobDescription": "…", "role": "Software Engineer",
    "seniority": "mid", "languages": ["Python"], "durationMinutes": 45
  },
  "mode": "scripted",            // "scripted" (deterministic) | "llm"
  "track": "problem:two_sum",    // optional: "incident-demo" | "problem:<id>" | "auto" | null
  "persona": "rigorous",         // optional: "collaborative" | "rigorous" (default: by seniority)
  "onboarding": false,           // optional: opt-in greeting + audio/readiness gate
  "fake_llm": false              // TEST-ONLY; honored only when VNEXT_ALLOW_FAKE_LLM=1
}
→ { "sessionId": "…", "phase": "rubric", "mode": "scripted" }
```
`track: "auto"` resolves a problem scenario from role + seniority (interns
never get "hard"; the pick is stable for the same inputs).

### `POST /sessions/{id}/rubric` — bind the rubric, advance to ready
Body: `{ "intake": { … } }` (optional; defaults to the stored intake).
Scenario tracks get their deterministic scenario rubric (problem rubrics are
reweighted by the intake's role track); `mode: "llm"` without a scenario uses
the LLM rubric with a scripted fallback. → `{ "rubric": { … } }`

### `GET /sessions/{id}`
```json
{
  "sessionId": "…", "intake": { … }, "rubric": { … }, "phase": "coding",
  "lastSeq": 42,
  "scenario": {                  // present only on scenario tracks
    "track": "problem:two_sum", "title": "Two Sum",
    "seedCode": "…", "taskPrompt": "…", "language": "python"
  }
}
```
The backend is the source of truth for scenario content — the frontend
fetches `seedCode`/`taskPrompt` from here.

### `GET /problems`
Lists the problem scenarios available as tracks:
`{ "problems": [{ "track": "problem:two_sum", "title": "Two Sum", "difficulty": "easy", "language": "python", "runnable": true }, …] }`

### `GET /sessions/{id}/ledger?since=0` → `{ "events": [ … ] }`
### `GET /sessions/{id}/review` → `{ "ledger": […], "scorecard": …, "rubric": … }`
### `GET /warmup` → `{ "ok": true, "store": "memory" }`
### `POST /jd` — generate a job description from `{role, seniority?, languages?}` (LLM with template fallback)

### Conversational overrides (lab/dev)
- `PATCH /sessions/{id}/hints` — body is `{ "<intent>": ["rung1", "rung2", …] }`;
  override ladders escalate exactly like built-in ones (same code path).
- `PATCH /sessions/{id}/pause_policies` — `{ "<intent>": <delay_ms> }`.
- `POST /hints/provider/register|unregister`,
  `POST /pause/provider/register|unregister` — **dev-only demo hooks**: they
  install a hardcoded in-process demo provider, take no body, affect all
  sessions, and are unauthenticated. Do not expose publicly.

There is no auth, no rate limiting, and no persistence beyond process memory
— this is a lab.

## WebSocket — `/vnext/interview/ws/{session_id}`

### Handshake
First frame must be
`{ "type": "client_hello", "session_id", "client_conn_id", "last_seq", "resume" }`.
Server replies `resume_ready` (with `{snapshot: {phase}}`) then
`resume_events` (backfill of events with `seq > last_seq`), or
`resume_rejected`. If the session opted into onboarding, a greeting
(`session.greeting` + spoken `interviewer.utterance`) follows, and
`advance.request` is held with a reminder until the audio/readiness checks
pass (any candidate speech marks audio OK; a whole-word, negation-aware
"ready" marks readiness).

### Inbound messages
| Message | Effect |
|---|---|
| `{"type":"candidate.text","text"}` | classified once (intent) → `candidate.utterance`, `conversation.intent.detected`, and possibly a hint (immediate or pause-scheduled). Utterance-initial cut-in words cancel scheduled speech + emit `barge_in.detected` regardless of final intent. |
| `{"type":"candidate.code","code"}` | `code.edited`; on scenario tracks the wrong-code detector may add `selection.set`, `highlight.set`, a probe utterance, and (incident only) `code.patch.proposed`. |
| `{"type":"candidate.run","code"}` | `code.run` — on runnable problem tracks the code is EXECUTED against the scenario's test cases (`passed`, `total`, per-case results, honest exit codes; 2 = timeout). Otherwise a stub with `exitCode: 0`. |
| `{"type":"advance.request","signal"}` | PhaseController validates; on success `phase.changed` + the turn (llm mode: `interviewer.turn.started` then the generated/guarded utterance; scripted: the scenario's deterministic line). |
| `{"type":"barge_in","turnId?"}` | cancels the in-flight LLM turn(s) (`interviewer.cancelled`) and any scheduled utterances (`system.pause.cancelled`). |
| `{"type":"code.patch.accept"/"code.patch.reject","patchId"}` | resolves a proposal → `code.patch.applied` + authoritative `code.edited`, or `code.patch.rejected`; clears selection/highlight. |
| `{"type":"scorecard.request"}` | streams `scorecard.criterion.ready` per criterion then `scorecard.completed` (or a terminal `scorecard.failed`). |

### Notable event payload fields (beyond the shared envelope)
- `interviewer.utterance`: `hint_for`, `hint_step`, `attempt`, `exhausted`
  (hints); `hint_throttled` (gaming throttle — does not advance attempts);
  `nudgeLevel` (silence ladder); `guarded` + `guard_reasons` (output guard);
  `stallRecovered` (12s LLM stall fallback); `turnId`; `codeProbe`.
- `code.run`: `passed`, `total`, `results[{ok, got, expected, description}]`,
  `stdout`, `exitCode` (0 all-pass · 1 failures/error · 2 timeout).
- `system.pause.cancelled`: `reason` (`"cut_in"` | `"superseded"`).
- `barge_in.detected`: `{intent, text}` — for latency analysis.

Unknown inbound types are ignored (forward compatibility). All emitted events
are flat ledger envelopes assigned a server seq.
