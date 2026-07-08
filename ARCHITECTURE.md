# Architecture — Dynamic AI Interview Engine

Maya conducts a live technical interview over voice: she presents a scenario,
listens with real turn-taking, reviews the candidate's code as they type,
gives never-reveal hints, runs their code against real test cases, and
produces an evidence-cited scorecard. One generic engine conducts **seven
scenarios** (the production-incident demo plus six SDE problems) across
**three role tracks** — adding a scenario is data, not engine surgery.

Everything below is implemented and covered by the test suite
(**195 backend / 105 frontend tests, all green**). Known limitations are
listed honestly at the end.

## Layer map

```
Frontend (Next.js)                     Backend (FastAPI, in-memory)
──────────────────                     ─────────────────────────────
InterviewRoom (voice room)             ws.py        WebSocket orchestrator
  turn-taking.ts   semantic turn-end     scenario.py  ScenarioSpec registry
                   + barge-in gate       runner.py    sandboxed code runner
  useVoice.ts      STT/TTS + fillers     intent.py    intent classifier
  live-adapter.ts  REST+WS adapter       hint_ladder  contingent never-reveal
  metrics.ts       quality metrics       hint_provider/pause_policy  plugins
ReviewWorkspace (evidence + metrics)     reveal_guard output persona guard
                                         roles.py     role tracks + personas
Shared foundation: seq-ordered event ledger (ledger.py), 12-phase
PhaseController (parity-tested against state-machine.ts), llm/ (OpenRouter
with deterministic fallback at every call site).
```

## 1. Event-sourced ledger (the spine)

Every fact is a flat event `{v, seq, ts, sessionId, actor, type, ...payload}`
with a per-session monotonic `seq` (never ordered by timestamp) minted by a
single writer (`ledger.py`). Consumers replay events instead of holding
state: hint escalation, patch invariants, evidence resolution, and the
conversation-quality metrics are all pure functions over the ledger. This is
why reconnects are cheap (WS resume handshake + backfill) and why every
interviewer decision is auditable after the fact.

## 2. Phase control

`phase_controller.py` owns a 12-phase linear machine
(intake → rubric → ready → intro → resume_calibration → problem_framing →
coding → debugging → optimization → wrap_up → scoring → review). The
adapter/LLM emit *signals*; only the controller mints `phase.changed`. The
same transition table exists in TypeScript and a parity test fails loudly on
drift.

## 3. ScenarioSpec — problems as behavior, not text

`scenario.py` defines the behavioral contract a scenario must supply:

| Hook | Incident demo | Problem scenarios (6) |
|---|---|---|
| seed code | buggy `charge_customer` | signature + constraints + example |
| per-signal lines | incident narrative | generic coding-interview arc |
| per-phase LLM guidance | race/atomicity probing | brute-force → optimize arc, never names the approach |
| rubric | code/concurrency/test/ops/tradeoff | correctness/approach/complexity/testing/communication, **role-reweighted** |
| wrong-code detector | read-then-insert race | for-in-for brute force; linear scan on sorted input |
| code action | highlight + validated patch | highlight + probe, **never a patch** (a patch would reveal the answer) |
| hints | generic help ladder | the problem's own never-reveal ladder |
| reveal terms | none (fix is named in the task) | e.g. "hash map", "sliding window" — blocked until the final hint attempt |
| test cases | — | executed for real by the runner |

Detectors are deliberately conservative: they fire only on unambiguous
anti-patterns and never act on foreign code. A `while`-inside-`for` sliding
window is correctly *not* flagged — challenging an optimal solution would
punish a correct answer.

Scripted (no-LLM) mode plays each scenario's own deterministic lines, so a
full interview works with **zero API keys** — live-LLM and offline paths stay
behaviorally identical per scenario.

## 4. Turn-taking (client, deterministic — no model in any timing path)

Ported from the deployed Voice_Assist timing brain (`turn-taking.ts`):

- **Semantic turn-end** replaces a fixed send timer: a trailing continuation
  token ("so I think we could…", trailing comma, gerund) waits 4s; a
  complete-sounding ending confirms in 700ms; neutral endings sit at 1.5s.
  Every arm returns a finite delay, so a candidate who trails off can never
  hang the interview.
- **Barge-in gate**: backchannels ("mm-hm", "yeah") never cut Maya off;
  utterance-initial cut-in words ("wait", "hold on", "stop") interrupt on a
  single word; ≥3 substantive words or 700ms of sustained speech take the
  floor. The token sets mirror `intent.py`, so client and server agree.
- **Urgency is deterministic**: the server decides cancellation with
  `is_cut_in(text)` independent of the (possibly LLM-supplied) intent, so
  "wait, I'm stuck" routes to the help ladder *and* cancels scheduled speech.
- Barge-in cancels the in-flight LLM turn server-side (`active_turns` +
  `cancelled_turns`): a cancelled line never reaches the ledger.

## 5. Dead air is architecturally impossible

Four mechanisms cover every silence, in order:

1. **Latency-masking fillers** — short lines in Maya's own voice, pre-fetched
   once per session, played the instant a turn starts generating; the real
   utterance supersedes them via a generation counter.
2. **Stall recovery** — an LLM turn that produces nothing for 12s is
   abandoned and completed with the deterministic scenario line
   (`stallRecovered: true`).
3. **Anti-double gate** — a pause-scheduled hint that another interviewer
   line raced past is cancelled (`reason: "superseded"`), never double-spoken.
4. **Silence ladder** — 25s of *joint* silence earns a neutral
   "talk me through what you're thinking" (think-aloud research: a neutral
   nudge, never a hint), 50s earns one check-in; resets on any candidate
   activity and never fires during setup or generation.

## 6. Never-reveal / never-confirm — enforced in code

Three independent layers, because prompts alone leak:

- **Prompt rules** (`interviewer_llm.py`): persona tone contracts, forbidden
  praise/openers, per-scenario "never name the approach" appendix.
- **Output guard** (`reveal_guard.py`): every LLM line is scanned before it
  reaches the ledger — verdict statements ("that's exactly right/wrong",
  "that's generally true"), generic praise, and scenario reveal terms
  (before the final hint attempt) are dropped sentence-by-sentence; if
  nothing survives, a rotating *neutral* probe replaces the line (neutral
  because the guard cannot know right from wrong). Guarded events carry
  `guarded: true` + reasons for audit. Curated scripted lines and the hint
  ladder (the designated pushback lane) are exempt.
- **Structural**: problem scenarios have no patch path at all — Maya can
  highlight and probe but cannot write the solution.

## 7. Adaptive hints (contingent tutoring)

The help ladder (nudge → hint → reveal) escalates by ledger replay, with two
research-grounded controls:

- **Wood's contingency rule**: a hint followed by real progress (a code
  edit, a run, a substantive answer) earns a credit that steps the level
  back — succeed after help → less help next time; fail → more.
- **Gaming throttle** (Baker/MATHia): a help request arriving faster than
  the previous hint could be read (~15 chars/sec, 8s floor) is refused with
  a restate-and-apply prompt that never advances the attempt counter.

Acknowledgment intents (cut-in, thinking, audio checks) clamp at their final
rung instead of escalating to a "still stuck?" prompt that would wrongly
accuse the candidate. Per-session REST overrides and registered providers
escalate through the same single code path.

## 8. Real code execution

`runner.py` executes the candidate's code against the scenario's test cases
in a separate `python -I` process with a 3s hard timeout, run in a thread so
barge-in handling never blocks. Honest results: per-case pass/fail with
got/expected, order-insensitive comparison where the problem allows it,
timeouts and exceptions reported as failures — never a fake green. Run
results land in the ledger (`passed`/`total`) and feed the metrics panel and
the contingent-hint progress signal.

## 9. Roles and personas

`roles.py`: role tracks (software_engineer / sde_intern / ml_engineer) carry
competency weights that deterministically reshape the problem rubric — and
the scorecard pins weights to the rubric server-side, so this is enforced
scoring, not prompt advice. `track: "auto"` picks a problem from role +
seniority (interns never get "hard"). Personas ("collaborative",
"rigorous" bar-raiser) are tone contracts with hard anti-sycophancy rules
prepended to every interviewer prompt.

## 10. Evidence-cited scoring & conversation metrics

The scorecard validates every LLM-proposed evidence reference against the
ledger (seq must resolve, excerpts repaired, weights pinned, overall
recomputed server-side) and falls back to a deterministic rubric-shaped
scorecard on any failure. The review page adds a **conversation quality**
panel computed purely from ledger timestamps: response-gap median/p90,
turn-generation latency, barge-ins honored, pause discipline, hint depth and
throttles, silence nudges, guarded lines, stall recoveries, run progression,
talk balance.

## Degradation ladder (what happens with no keys / no mic / no LLM)

| Missing | Behavior |
|---|---|
| OpenRouter/OpenAI key | deterministic scenario lines; rubric/scorecard fall back to scripted; interview fully functional |
| ElevenLabs key | browser speechSynthesis voice (audibly robotic but working) |
| Microphone | "Type instead" box; same engine |
| LLM stalls mid-turn | 12s stall recovery with the scenario line |

## Honest limitations

- **In-memory store**: sessions do not survive a backend restart. The
  Supabase store mode is declared but its module is not implemented; it
  falls back to memory. Run demos without `--reload`.
- **STT is browser Web Speech** (effectively Chrome) with a token-overlap
  echo heuristic — use headphones; there is no acoustic echo cancellation.
- **The runner is a dev sandbox** (isolated process + timeout), not a
  hardened jail; a hosted deployment should swap in a container behind the
  same function signature.
- **Review links** read the ledger from the browser that ran the interview
  (localStorage); opening a review link elsewhere shows an empty workspace.
- Onboarding (greeting → audio → ready gate) is **opt-in** per session and
  off by default; the incident/problem demos auto-start without it.
- `models.py`'s typed event union lags the full set of event types the WS
  actually emits (the ledger itself is schemaless by design).
