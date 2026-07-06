# Architecture: Dynamic Real-Time Interview System

## System Overview

A **production-ready, fully dynamic** AI interview system that adapts in real-time to candidate behavior without hardcoded conversational logic. The system combines:

1. **Event-sourced ledger** for audit trail and reproducibility
2. **Pluggable intent classification** for conversational understanding
3. **Attempt-based hint escalation** (never-reveal policy, matching Voice_Assist judge)
4. **Real-time pause/timing control** with non-blocking scheduling
5. **Dynamic problem catalog** with 6 SDE problems
6. **Barge-in & turn-taking** aligned with interview UX best practices

---

## Core Architecture Layers

```
┌─────────────────────────────────────────────────────────────────┐
│ Frontend (WebSocket Client)                                     │
│ - Real-time bidirectional communication                         │
│ - Sends candidate text, code, run requests                      │
│ - Receives interviewer utterances, hints, pause signals         │
└────────────────┬────────────────────────────────────────────────┘
                 │ WebSocket (/vnext/interview/ws/{session_id})
                 ↓
┌─────────────────────────────────────────────────────────────────┐
│ WebSocket Handler (ws.py)                                       │
│ - Manages session lifecycle                                     │
│ - Routes messages (candidate.text, candidate.code, etc.)        │
│ - Orchestrates intent → hint → pause scheduling                 │
│ - Cancels scheduled utterances on barge-in (cut-in words)      │
└────────────────┬────────────────────────────────────────────────┘
                 │
      ┌──────────┼──────────┐
      ↓          ↓          ↓
┌──────────────┐ ┌───────────────────┐ ┌──────────────────────┐
│ Intent       │ │ Hint Ladder       │ │ Pause Policy         │
│ Classifier   │ │ (Attempt-Based)   │ │ (Pluggable Provider) │
│ (intent.py)  │ │ (hint_ladder.py)  │ │ (pause_policy.py)    │
├──────────────┤ ├───────────────────┤ ├──────────────────────┤
│ - Backchannel│ │ - Attempt 1:      │ │ - Provider Priority  │
│ - Cut-in     │ │   Nudge (no ans)  │ │ - Session Override   │
│ - Help       │ │ - Attempt 2:      │ │ - REST Config        │
│ - Repeat     │ │   Hint (hint)     │ │ - Fallback: 0ms      │
│ - Thinking   │ │ - Attempt 3+:     │ │ - Non-blocking       │
│ - Meta-audio │ │   Reveal (answer) │ │   scheduling         │
│ - Answer     │ │ - Never-reveal    │ │                      │
│              │ │   policy enforced │ │                      │
└──────────────┘ └───────────────────┘ └──────────────────────┘
      │                  │                       │
      └──────────────────┼───────────────────────┘
                         ↓
            ┌─────────────────────────────┐
            │ Session Store (store.py)    │
            ├─────────────────────────────┤
            │ - Session record            │
            │ - Event ledger              │
            │ - Pause policies            │
            │ - Hint overrides            │
            └─────────────────────────────┘
                         ↓
            ┌─────────────────────────────┐
            │ REST API (rest.py)          │
            ├─────────────────────────────┤
            │ POST /sessions              │
            │ GET /sessions/{id}          │
            │ GET /sessions/{id}/ledger   │
            │ PATCH /sessions/{id}/hints  │
            │ PATCH /sessions/{id}/pause  │
            │ POST /hints/provider/...    │
            │ POST /pause/provider/...    │
            └─────────────────────────────┘
```

---

## Event-Sourced Ledger

Every decision is recorded as an immutable event in the session ledger. The ledger is the source of truth.

### Key Event Types

| Event Type | Purpose | Metadata |
|-----------|---------|----------|
| `candidate.utterance` | Candidate speech → text | `text`, `lineId` |
| `conversation.intent.detected` | Intent classification result | `intent`, `text`, `is_backchannel?` |
| `interviewer.utterance` | Interviewer response (hint or question) | `text`, `hint_for?`, `hint_step?`, `attempt?`, `exhausted?` |
| `system.pause.scheduled` | Pause policy applied | `intent`, `delay_ms` |
| `system.pause.cancelled` | Scheduled pause cancelled | `intent`, `delay_ms`, `reason` |
| `system.pause.completed` | Scheduled pause fired, hint emitted | `intent`, `delay_ms` |
| `barge_in.detected` | Cut-in word detected | `intent`, `text` |
| `phase.changed` | Phase transition | `from`, `to`, `signal` |

### Ledger Query Example

```bash
curl http://localhost:8000/vnext/interview/sessions/{session_id}/ledger?since=0
```

Result: array of events, each with `seq` (monotonic), `actor`, `type`, `payload`, `timestamp`.

---

## Pluggable Providers

All decision-making logic is pluggable without code changes:

### Intent Classifier
```python
from app.vnext.interview.intent import IntentClassifier

classifier = IntentClassifier()

# Register custom provider (e.g., LLM-based)
def my_intent_provider(text: str, session_id: str) -> str:
    # Your logic here
    return "help"  # or "repeat", "thinking", etc.

classifier.register_provider(my_intent_provider)
```

**Provider Priority**: Custom provider → Rule-based fallback

### Hint Provider
```python
from app.vnext.interview.hint_provider import register_hint_provider

def my_hint_provider(session_id: str, intent: str) -> Optional[dict]:
    return {
        "text": "Custom hint for " + intent,
        "hint_for": intent,
        "hint_step": 1,
        "exhausted": False,
    }

register_hint_provider(my_hint_provider)
```

**Provider Priority**: Custom provider → Session overrides (REST) → Hardcoded ladder

### Pause Policy
```python
from app.vnext.interview.pause_policy import register_pause_provider

def my_pause_policy(session_id: str, intent: str) -> int:
    # Return pause duration in milliseconds
    if intent == "help":
        return 500  # 500ms before emitting hint
    return 0

register_pause_provider(my_pause_policy)
```

**Provider Priority**: Custom provider → Session overrides (REST) → Default (0ms)

---

## Barge-In & Turn-Taking

Matching Voice_Assist reference patterns for human-like interruption:

### Backchannel Immunity
- Pure filler sounds ("yeah", "okay", "hmm") don't trigger hints
- Logged as `conversation.intent.detected` with `is_backchannel: true`
- No `interviewer.utterance` emitted

### Cut-In Detection
- Explicit interrupt words ("wait", "stop", "sorry") force immediate response
- **Aggressive cancellation**: all scheduled pauses are cancelled
- **Immediate hint**: bypass pause policy, emit hint immediately
- Emits `barge_in.detected` event for latency monitoring

### Selective Scheduling Cancellation
- Only cut-in words trigger aggressive cancellation (not every text)
- Scheduled tasks tracked in WebSocket session
- On cancellation: emit `system.pause.cancelled` with reason

---

## Attempt-Based Hint Escalation

Per Voice_Assist judge pattern, hints escalate based on attempt number:

### Attempt 1: Nudge
- Direct question (make them think)
- No answer-bearing terms revealed
- Example: "What data structure lets you look up items in O(1)?"

### Attempt 2: Hint
- Pointed guidance toward fix (still no answer)
- Example: "Use a hash map. Iterate once, checking if target - num is in the map."

### Attempt 3+: Reveal
- State the key idea or algorithm directly
- Example: "The function should use a two-pointer or hash-based approach."

### Never-Reveal Guarantee
- Auditable via ledger `attempt` field in `interviewer.utterance` events
- If LLM provider tries to reveal on attempt 1/2, the attempt metadata prevents it
- All hints mapped through ledger history for consistency

---

## Problem-Spec Engine (Phase 5)

### Structure
Each problem has:
- **Spec**: signature, constraints, examples, time/space complexity
- **Test Cases**: input/output pairs with descriptions
- **Hint Ladder**: 3 escalation levels per attempt-based system
- **Difficulty**: easy, medium, hard
- **Selection**: by role/seniority (junior → easy, mid → medium, senior → hard)

### 6 Foundational Problems
1. **Two Sum** (easy): Hash map, O(n) time
2. **Valid Parentheses** (easy): Stack, O(n) time
3. **Merge Sorted Arrays** (easy): Two pointers, O(n+m) time
4. **Reverse Linked List** (medium): Pointer manipulation, O(n) time
5. **Binary Search** (medium): Divide & conquer, O(log n) time
6. **Longest Substring Without Repeating** (medium): Sliding window, O(n) time

### Usage
```python
from app.vnext.interview.problem_spec import get_problem, get_problem_for_role

# Get problem by ID
spec = get_problem("two_sum")

# Get hint for a problem
hint = get_hint_for_problem("two_sum", attempt=2)

# Suggest problem by role/seniority
problem_id = get_problem_for_role("SDE", "mid")
```

---

## Session Initialization & Onboarding

Via `SessionInitManager` (session_init.py):
1. Candidate receives greeting
2. Audio/connectivity check ("Can you hear me?")
3. Readiness confirmation ("Ready to start?")
4. `is_complete()` blocks `advance.request` until all checks pass

### State Persistence
- Session record holds `session_init` dict with flags:
  - `greeting_sent`: bool
  - `audio_ok`: bool
  - `ready`: bool

### API
```python
session_init = SessionInitManager(session_id)
session_init.register_greeting()
session_init.mark_audio_ok()
session_init.mark_ready()
session_init.is_complete()  # → bool
```

---

## WebSocket Protocol

### Inbound Messages

**Candidate Text** (trigger intent classification):
```json
{"type": "candidate.text", "text": "I'm stuck, can I get a hint?"}
```

**Candidate Code**:
```json
{"type": "candidate.code", "code": "def foo(): pass"}
```

**Candidate Run**:
```json
{"type": "candidate.run", "code": "def foo(): return 1"}
```

**Barge-In** (interrupt scheduled utterance):
```json
{"type": "barge_in"}
```

**Advance Request** (move to next phase):
```json
{"type": "advance.request", "signal": "take_question"}
```

**Scorecard Request**:
```json
{"type": "scorecard.request"}
```

### Outbound Messages (Server → Client)

**Resume Ready** (handshake):
```json
{"type": "resume_ready", "resumed": true, "from_seq": 0, "snapshot": {"phase": "rubric"}}
```

**Resume Events** (backfill):
```json
{"type": "resume_events", "from_seq": 0, "events": [...]}
```

**Event** (ledger entry):
```json
{
  "type": "interviewer.utterance",
  "seq": 42,
  "actor": "interviewer",
  "payload": {
    "text": "Use a hash map.",
    "hint_for": "help",
    "hint_step": 2,
    "attempt": 2,
    "exhausted": false
  }
}
```

---

## Performance & Latency Budget

Per hard rules in CLAUDE.md:

| Metric | Target | Implementation |
|--------|--------|-----------------|
| User barge-in → TTS silence | < 200ms | Cut-in words force immediate cancellation + emit within 50ms |
| Turn-end → first bot audio | < 800ms | Non-blocking scheduler, async LLM generation |
| Judge verdict latency | < 1.5s | Provider call with 10s timeout, fail-safe CONTINUE |
| Pause scheduling overhead | < 10ms | Sync calculation, asyncio scheduling |

**No LLM calls on hot path**: All pause/intent/hint computations are either deterministic (rule-based) or cached (provider result).

---

## Data Flow: Example Session

1. **Client**: `{"type": "candidate.text", "text": "wait, let me think"}`
2. **Server Intent**: Detects `cut_in` intent
3. **Server Barge-In**: Cancels any scheduled interviewer utterance, emits `barge_in.detected`
4. **Server Hint**: Gets hint for `cut_in` (if applicable), applies `force_immediate=True` (bypass pause)
5. **Server Pause**: Check pause policy → 0ms (cut-in ignores pause)
6. **Server Emit**: `interviewer.utterance` with hint text, `attempt` metadata
7. **Client**: Receives hint immediately in ledger
8. **Ledger**: Records full chain (intent, pause, barge-in, hint) with `seq` numbers

---

## Extension Points

### Add a New Intent
Edit `intent.py` `RuleBasedIntentClassifier`:
- Add regex pattern
- Add case in `classify()` method
- Register hints in `hint_ladder.py`

### Add a New Problem
Edit `problem_spec.py` `PROBLEM_CATALOG`:
- Create `ProblemSpec` with full details
- Add to dict with unique ID

### Add a Custom Hint Provider
```python
from app.vnext.interview.hint_provider import register_hint_provider

def my_provider(session_id, intent):
    # Your logic
    return {"text": "...", "hint_for": intent, "hint_step": 1}

register_hint_provider(my_provider)
```

### Add a Custom Pause Policy
```python
from app.vnext.interview.pause_policy import register_pause_provider

def my_policy(session_id, intent):
    return 500 if intent == "help" else 0

register_pause_provider(my_policy)
```

---

## Testing Strategy

- **Unit Tests**: `test_eval_harness.py` (intent, hints, pause, ledger)
- **Integration Tests**: `test_conversation_director.py` (WS flow)
- **Problem Tests**: `test_problem_spec.py` (catalog, difficulty, complexity)
- **Manual Tests**: PRODUCTION_DEPLOYMENT.md (barge-in, pause, never-reveal)

---

## Deployment Topology

```
┌──────────────┐
│ Frontend     │
│ (Browser)    │
└────────┬─────┘
         │ HTTPS/WSS
         ↓
┌──────────────────┐
│ Reverse Proxy    │
│ (Nginx/HAProxy)  │
└────────┬─────────┘
         │
         ↓
┌──────────────────────────┐
│ Backend (FastAPI)        │
│ - uvicorn workers (3+)   │
│ - WebSocket enabled      │
│ - In-memory store (dev)  │
│   or Supabase (prod)     │
└──────────────────────────┘
```

---

## Production Checklist

- [ ] All intent patterns tested
- [ ] Pause provider error-safe (fallback to 0ms)
- [ ] Scheduled tasks cleaned up on disconnect
- [ ] Ledger entries complete & sequenced
- [ ] WebSocket ping/pong healthy (20s interval)
- [ ] Load test: 10+ concurrent sessions
- [ ] Latency profiling: all operations < 100ms
- [ ] Monitoring: barge-in events, pause accuracy, hint escalation distribution

---

**Architecture Version**: 2.0 (Phase 1-5 Complete)  
**Last Updated**: 2026-07-06
