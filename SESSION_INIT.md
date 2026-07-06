# Session Initialization Guide

## Overview

Session initialization (`SessionInitManager`) ensures candidate readiness before the actual interview begins. It's a **gating mechanism** that blocks interview progression until all checks pass.

---

## Initialization Lifecycle

```
┌─────────────────────────────────┐
│ 1. Session Created              │
│    (session_id, problem_id)     │
└────────────────┬────────────────┘
                 ↓
┌─────────────────────────────────┐
│ 2. Greeting Sent                │
│    "Hi, let's solve [problem]"  │
│    status: session_init.        │
│      greeting_sent = True       │
└────────────────┬────────────────┘
                 ↓
┌─────────────────────────────────┐
│ 3. Audio Check                  │
│    "Can you hear me?"           │
│    Candidate: "Yes"             │
│    status: audio_ok = True      │
└────────────────┬────────────────┘
                 ↓
┌─────────────────────────────────┐
│ 4. Readiness Confirmation       │
│    "Ready to start?"            │
│    Candidate: "Yes"             │
│    status: ready = True         │
└────────────────┬────────────────┘
                 ↓
┌─────────────────────────────────┐
│ 5. Interview Begins             │
│    is_complete() == True        │
│    advance.request allowed      │
└─────────────────────────────────┘
```

---

## State Machine

Each flag is **independent**:

```python
class SessionInitState:
    greeting_sent: bool = False
    audio_ok: bool = False
    ready: bool = False
    
    def is_complete(self) -> bool:
        return self.greeting_sent and self.audio_ok and self.ready
```

### Allowed Transitions

| From State | Event | To State | Notes |
|-----------|-------|----------|-------|
| `{}` | Greeting sent | `{greeting_sent}` | Non-blocking; no order required |
| `{greeting_sent}` | Audio OK | `{greeting_sent, audio_ok}` | Can happen in any order |
| `{audio_ok}` | Greeting sent | `{greeting_sent, audio_ok}` | Idempotent |
| `{greeting_sent, audio_ok}` | Ready | `{all}` | Now `is_complete()` = True |

**No Prerequisites**: Flags can be set in any order.

---

## API

### Backend Code (Python)

```python
from app.vnext.interview.session_init import SessionInitManager
from app.vnext.interview.store import InMemoryInterviewStore

store = InMemoryInterviewStore()

# Get or create manager for a session
manager = SessionInitManager(session_id="abc-123")

# Register completion of each step
manager.register_greeting()  # greeting_sent = True
manager.mark_audio_ok()       # audio_ok = True
manager.mark_ready()           # ready = True

# Check if interview can start
if manager.is_complete():
    # Allow advance.request
    pass

# Query current state
state = manager.get_state()  # Returns: {"greeting_sent": bool, "audio_ok": bool, "ready": bool}
```

### WebSocket Protocol

**Client → Server: After hearing greeting**
```json
{"type": "candidate.text", "text": "yes"}
```

**Server Logic**:
```python
# Detect audio_ok
if "yes" in text.lower() or "hear" in text.lower():
    manager.mark_audio_ok()
    
# Emit event to ledger
store.emit_event(
    session_id="abc-123",
    actor="system",
    event_type="session_init.audio_ok",
    payload={}
)
```

**Server → Client: Readiness confirmation**
```json
{
  "type": "interviewer.utterance",
  "seq": 3,
  "payload": {
    "text": "Great. Ready to start the interview?"
  }
}
```

**Client → Server: Ready**
```json
{"type": "candidate.text", "text": "yes"}
```

**Server Logic**:
```python
manager.mark_ready()

# Now is_complete() == True
if manager.is_complete():
    store.emit_event(
        session_id="abc-123",
        actor="system",
        event_type="session_init.complete",
        payload={}
    )
```

---

## REST API

### Get Init State
```bash
GET /vnext/interview/sessions/{session_id}

Response:
{
  "session_id": "abc-123",
  "session_init": {
    "greeting_sent": true,
    "audio_ok": true,
    "ready": false
  }
}
```

### Check If Interview Can Start
```bash
GET /vnext/interview/sessions/{session_id}/init/complete

Response:
{
  "complete": false,
  "ready": ["greeting_sent", "audio_ok"],
  "pending": ["ready"]
}
```

### Manually Mark Complete (for testing/debugging)
```bash
POST /vnext/interview/sessions/{session_id}/init/complete
Content-Type: application/json

{
  "reason": "manual_override_dev"
}

Response: 200 OK
{
  "session_id": "abc-123",
  "state": {
    "greeting_sent": true,
    "audio_ok": true,
    "ready": true
  }
}
```

---

## Gating: advance.request

The **advance.request** message is blocked until init is complete.

### Without Init Complete
```json
{"type": "advance.request", "signal": "take_question"}
```

**Server Response** (409 Conflict):
```json
{
  "type": "error",
  "code": 409,
  "message": "Cannot advance: session_init not complete",
  "pending": ["ready"]
}
```

### With Init Complete
```json
{"type": "advance.request", "signal": "take_question"}
```

**Server Response** (200 OK, phase advances):
```json
{
  "type": "phase.changed",
  "seq": 10,
  "payload": {
    "from": "rubric",
    "to": "problem_statement",
    "signal": "take_question"
  }
}
```

---

## Ledger Events

All init steps are recorded:

```json
[
  {
    "seq": 1,
    "actor": "system",
    "type": "session_init.greeting_sent",
    "payload": {"problem_id": "two_sum"},
    "timestamp": "2026-07-06T10:30:00Z"
  },
  {
    "seq": 2,
    "actor": "system",
    "type": "session_init.audio_ok",
    "payload": {},
    "timestamp": "2026-07-06T10:30:05Z"
  },
  {
    "seq": 3,
    "actor": "system",
    "type": "session_init.ready",
    "payload": {},
    "timestamp": "2026-07-06T10:30:10Z"
  },
  {
    "seq": 4,
    "actor": "system",
    "type": "session_init.complete",
    "payload": {"duration_ms": 10000},
    "timestamp": "2026-07-06T10:30:10Z"
  }
]
```

---

## Configuration

### Greeting Message
```python
# In ws.py or phase_controller.py
GREETING_TEMPLATE = "Hi {name}! Let's solve {problem_title}. Can you hear me?"
```

### Audio Check Prompt
```python
AUDIO_CHECK_PROMPT = "Can you hear me clearly?"
```

### Readiness Prompt
```python
READINESS_PROMPT = "Ready to start the interview?"
```

### Timeout
```python
INIT_TIMEOUT_MS = 60000  # 60 seconds; after this, candidate forced to ready
```

---

## Edge Cases

### Candidate Disconnects During Init
- Session remains valid
- Reconnect: receive `resume_ready` with current init state
- Continue from where left off

### Candidate Takes Too Long
- **After 60s**: Auto-mark ready, emit warning event
- Continue with interview (best-effort)

### Candidate Says "No" to Audio Check
- Re-prompt: "Let me know when ready"
- Wait for affirmative before proceeding

### Out-of-Order Steps
- **Allowed**: Any step can happen first
- Example: Ready before audio check → still valid
- All three flags must be true for `is_complete()`

---

## Testing

### Unit Test Example
```python
def test_session_init_gates_advance():
    manager = SessionInitManager("test-session-id")
    
    # Not complete yet
    assert not manager.is_complete()
    
    # Mark steps in random order
    manager.mark_ready()
    manager.register_greeting()
    manager.mark_audio_ok()
    
    # Now complete
    assert manager.is_complete()

def test_session_init_allows_partial_completion():
    manager = SessionInitManager("test-session-id")
    manager.register_greeting()
    
    # Still incomplete
    assert not manager.is_complete()
    
    state = manager.get_state()
    assert state["greeting_sent"] is True
    assert state["audio_ok"] is False
    assert state["ready"] is False
```

### Integration Test Example
```python
@pytest.mark.asyncio
async def test_advance_request_blocked_during_init():
    session_id = "test-session"
    client = AsyncWebSocketTestClient("ws://localhost:8000/vnext/interview/ws/test-session")
    
    # Try to advance before init complete
    await client.send({"type": "advance.request", "signal": "take_question"})
    
    response = await client.receive()
    assert response["type"] == "error"
    assert response["code"] == 409
    assert "session_init" in response["message"]

@pytest.mark.asyncio
async def test_advance_allowed_after_init_complete():
    session_id = "test-session"
    client = AsyncWebSocketTestClient("ws://localhost:8000/vnext/interview/ws/test-session")
    
    # Complete init
    await client.send({"type": "candidate.text", "text": "yes"})  # audio OK
    await client.send({"type": "candidate.text", "text": "ready"})  # ready
    
    # Now advance should work
    await client.send({"type": "advance.request", "signal": "take_question"})
    
    response = await client.receive()
    assert response["type"] == "phase.changed"
    assert response["payload"]["to"] == "problem_statement"
```

---

## Monitoring

### Metrics to Track

| Metric | Alert Threshold |
|--------|-----------------|
| Init completion time | > 120s |
| Greeting → Audio OK | > 30s |
| Audio OK → Ready | > 30s |
| Init failure rate | > 5% |

### Log Example
```
[2026-07-06 10:30:00] session_init.greeting_sent session_id=abc-123
[2026-07-06 10:30:05] session_init.audio_ok session_id=abc-123 delay_ms=5000
[2026-07-06 10:30:08] session_init.ready session_id=abc-123 delay_ms=3000
[2026-07-06 10:30:08] session_init.complete session_id=abc-123 total_ms=8000
```

---

## Best Practices

1. **Never Skip Init**: Even in dev/testing, complete init to ensure interview flow works
2. **Idempotent Marking**: `mark_ready()` can be called multiple times safely
3. **Use Events, Not State**: Trust ledger events as source of truth, not memory
4. **Monitor Timeouts**: Alert on > 60s init time (may indicate connectivity issues)
5. **Test Disconnects**: Verify resume works with partial init state

---

**Session Init Version**: 1.0  
**Last Updated**: 2026-07-06
