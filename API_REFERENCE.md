# API Reference: Interview System

## REST Endpoints

### Session Management

#### Create Session
```
POST /vnext/interview/sessions
Content-Type: application/json

{
  "candidate_email": "alice@example.com",
  "role": "SDE",
  "seniority": "mid",
  "problem_id": "two_sum"  // optional, auto-selected by role if omitted
}

Response: 200 OK
{
  "session_id": "uuid-here",
  "problem_id": "two_sum",
  "phase": "rubric",
  "phase_seq": 0,
  "phase_started_at": "2026-07-06T10:30:00Z"
}
```

#### Get Session
```
GET /vnext/interview/sessions/{session_id}

Response: 200 OK
{
  "session_id": "uuid",
  "candidate_email": "alice@example.com",
  "role": "SDE",
  "seniority": "mid",
  "problem_id": "two_sum",
  "phase": "problem_statement",
  "phase_seq": 1,
  "ledger_seq": 15,
  "session_init": {
    "greeting_sent": true,
    "audio_ok": true,
    "ready": true
  }
}
```

#### Get Ledger
```
GET /vnext/interview/sessions/{session_id}/ledger?since=0&limit=100

Response: 200 OK
[
  {
    "seq": 1,
    "actor": "system",
    "type": "session.created",
    "payload": {"role": "SDE", "seniority": "mid"},
    "timestamp": "2026-07-06T10:30:00Z"
  },
  {
    "seq": 2,
    "actor": "interviewer",
    "type": "interviewer.utterance",
    "payload": {
      "text": "Let's solve two sum.",
      "hint_for": null,
      "hint_step": null,
      "attempt": null,
      "exhausted": false
    },
    "timestamp": "2026-07-06T10:30:01Z"
  },
  ...
]
```

---

### Problem Management

#### Get Problem Spec
```
GET /vnext/interview/problems/{problem_id}

Response: 200 OK
{
  "id": "two_sum",
  "title": "Two Sum",
  "difficulty": "easy",
  "description": "Given an array of integers and a target sum...",
  "function_signature": "def two_sum(nums: list[int], target: int) -> list[int]:",
  "constraints": ["Each input has exactly one solution", "..."],
  "test_cases": [
    {
      "input_args": {"nums": [2, 7, 11, 15], "target": 9},
      "expected_output": [0, 1],
      "description": "Indices of 2 and 7"
    }
  ],
  "hints": [
    "What data structure lets you look up items in O(1)?",
    "Try using a hash map to store numbers and their indices.",
    "Iterate through nums once. For each num, check if (target - num) is in the map."
  ],
  "time_complexity": "O(n)",
  "space_complexity": "O(n)"
}
```

#### List Problems
```
GET /vnext/interview/problems?difficulty=medium

Response: 200 OK
[
  {
    "id": "reverse_linked_list",
    "title": "Reverse Linked List",
    "difficulty": "medium",
    ...
  },
  {
    "id": "binary_search",
    "title": "Binary Search",
    "difficulty": "medium",
    ...
  }
]
```

#### Get Problem for Role
```
GET /vnext/interview/problems/suggest?role=SDE&seniority=junior

Response: 200 OK
{
  "problem_id": "two_sum",
  "reason": "Easy problem recommended for junior SDE"
}
```

---

### Hint Configuration

#### Get Hint Overrides
```
GET /vnext/interview/sessions/{session_id}/hints

Response: 200 OK
{
  "custom_hints": {
    "help": {
      "level_1": "Use a hash map.",
      "level_2": "Iterate once, checking target - num.",
      "level_3": "Full solution hint here."
    }
  }
}
```

#### Set Hint Overrides (Session-Level)
```
PATCH /vnext/interview/sessions/{session_id}/hints
Content-Type: application/json

{
  "custom_hints": {
    "help": {
      "level_1": "What data structure is fast for lookups?",
      "level_2": "Hash maps or dictionaries are O(1) for lookups.",
      "level_3": "Use: for num in nums: if target-num in seen_map: return [i, seen_map[target-num]]"
    }
  }
}

Response: 200 OK
{
  "session_id": "uuid",
  "custom_hints": {...}
}
```

#### Register Hint Provider (Global)
```
POST /vnext/interview/hints/provider/register
Content-Type: application/json

{
  "provider_name": "my_llm_provider",
  "endpoint": "http://localhost:5000/get_hint"
}

Response: 200 OK
{
  "status": "registered",
  "provider_name": "my_llm_provider"
}
```

#### Unregister Hint Provider
```
POST /vnext/interview/hints/provider/unregister
Content-Type: application/json

{
  "provider_name": "my_llm_provider"
}

Response: 200 OK
{
  "status": "unregistered"
}
```

---

### Pause/Timing Configuration

#### Get Pause Policies
```
GET /vnext/interview/sessions/{session_id}/pause_policies

Response: 200 OK
{
  "session_id": "uuid",
  "pause_policies": {
    "help": 500,
    "repeat": 300,
    "thinking": 200
  }
}
```

#### Set Pause Policies (Session-Level)
```
PATCH /vnext/interview/sessions/{session_id}/pause_policies
Content-Type: application/json

{
  "pause_policies": {
    "help": 800,
    "repeat": 500,
    "thinking": 300
  }
}

Response: 200 OK
{
  "session_id": "uuid",
  "pause_policies": {...}
}
```

#### Register Pause Provider (Global)
```
POST /vnext/interview/pause/provider/register
Content-Type: application/json

{
  "provider_name": "adaptive_pause",
  "endpoint": "http://localhost:5000/get_pause"
}

Response: 200 OK
{
  "status": "registered",
  "provider_name": "adaptive_pause"
}
```

#### Unregister Pause Provider
```
POST /vnext/interview/pause/provider/unregister
Content-Type: application/json

{
  "provider_name": "adaptive_pause"
}

Response: 200 OK
{
  "status": "unregistered"
}
```

---

## WebSocket Protocol

### Connection
```
WebSocket wss://interview.example.com/vnext/interview/ws/{session_id}
```

### Inbound Messages (Client → Server)

#### Candidate Text
```json
{
  "type": "candidate.text",
  "text": "I think I should use a hash map."
}
```
Triggers: Intent classification → hint generation (if applicable) → scheduling

#### Candidate Code
```json
{
  "type": "candidate.code",
  "code": "def two_sum(nums, target):\n    seen = {}\n    for num in nums:\n        if target - num in seen:\n            return [seen[target - num], nums.index(num)]\n        seen[num] = nums.index(num)\n    return []"
}
```
Triggers: Code storage in ledger, ready for execution tests

#### Candidate Run
```json
{
  "type": "candidate.run",
  "code": "def two_sum(nums, target): ...",
  "test_case_id": 0
}
```
Triggers: Execute code against test case, emit result event

#### Barge-In (Interrupt)
```json
{
  "type": "barge_in"
}
```
Effect: Cancels any scheduled interviewer utterance, forces immediate response

#### Advance Request
```json
{
  "type": "advance.request",
  "signal": "take_question"
}
```
Triggers: Phase transition (if session_init is complete)

#### Scorecard Request
```json
{
  "type": "scorecard.request"
}
```
Triggers: Emit scorecard event with current session evaluation

### Outbound Messages (Server → Client)

#### Resume Ready (Handshake)
```json
{
  "type": "resume_ready",
  "resumed": true,
  "from_seq": 0,
  "snapshot": {
    "phase": "rubric",
    "session_init": {"greeting_sent": true, "audio_ok": true, "ready": false}
  }
}
```
First message after WS connection; indicates client can request resume_events

#### Resume Events (Backfill)
```json
{
  "type": "resume_events",
  "from_seq": 0,
  "events": [
    {"seq": 1, "type": "session.created", "payload": {...}, "timestamp": "..."},
    {"seq": 2, "type": "interviewer.utterance", "payload": {...}, "timestamp": "..."}
  ]
}
```
Fills client state after reconnect

#### Event (Ledger Entry)
```json
{
  "type": "interviewer.utterance",
  "seq": 42,
  "actor": "interviewer",
  "payload": {
    "text": "Good. Now, how would you optimize the space complexity?",
    "hint_for": null,
    "hint_step": null,
    "attempt": null,
    "exhausted": false
  },
  "timestamp": "2026-07-06T10:30:15Z"
}
```

#### Hint Event (with metadata)
```json
{
  "type": "interviewer.utterance",
  "seq": 43,
  "actor": "interviewer",
  "payload": {
    "text": "Try iterating through the array once with a hash map.",
    "hint_for": "help",
    "hint_step": 2,
    "attempt": 2,
    "exhausted": false
  },
  "timestamp": "2026-07-06T10:30:20Z"
}
```
- `attempt`: Which attempt is this hint for? (1, 2, 3)
- `hint_step`: Escalation level of the hint (1=nudge, 2=hint, 3=reveal)
- `exhausted`: Have all 3 hint levels been used?

#### Pause Event
```json
{
  "type": "system.pause.scheduled",
  "seq": 44,
  "actor": "system",
  "payload": {
    "intent": "help",
    "delay_ms": 500
  },
  "timestamp": "2026-07-06T10:30:20Z"
}
```

#### Pause Cancelled Event
```json
{
  "type": "system.pause.cancelled",
  "seq": 45,
  "actor": "system",
  "payload": {
    "intent": "help",
    "delay_ms": 500,
    "reason": "cut_in_detected"
  },
  "timestamp": "2026-07-06T10:30:20Z"
}
```

#### Intent Detected Event
```json
{
  "type": "conversation.intent.detected",
  "seq": 46,
  "actor": "system",
  "payload": {
    "intent": "help",
    "text": "I'm stuck",
    "is_backchannel": false
  },
  "timestamp": "2026-07-06T10:30:21Z"
}
```

#### Barge-In Event
```json
{
  "type": "barge_in.detected",
  "seq": 47,
  "actor": "system",
  "payload": {
    "intent": "cut_in",
    "text": "wait"
  },
  "timestamp": "2026-07-06T10:30:22Z"
}
```

---

## Error Responses

### 400 Bad Request
```json
{
  "detail": "Invalid session_id: must be UUID format"
}
```

### 404 Not Found
```json
{
  "detail": "Session not found: session_id=invalid-uuid"
}
```

### 409 Conflict
```json
{
  "detail": "Cannot advance: session_init not complete"
}
```

### 500 Internal Server Error
```json
{
  "detail": "Provider call failed: endpoint unreachable"
}
```
(Gracefully falls back to safe default, logged)

---

## Rate Limiting

- **Session creation**: 10 per minute per email
- **Ledger queries**: 100 per minute per session
- **WebSocket messages**: 1000 per minute per session

---

## Example Workflow

### 1. Create Session
```bash
curl -X POST http://localhost:8000/vnext/interview/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "candidate_email": "alice@example.com",
    "role": "SDE",
    "seniority": "mid"
  }'
```
Response: `session_id: "abc-123"`

### 2. Connect WebSocket
```bash
wscat -c "ws://localhost:8000/vnext/interview/ws/abc-123"
```

### 3. Receive Handshake
```json
{
  "type": "resume_ready",
  "resumed": true,
  "from_seq": 0
}
```

### 4. Candidate Says "I'm stuck"
```json
{"type": "candidate.text", "text": "I'm stuck"}
```

### 5. System Detects "help" Intent
```json
{
  "type": "conversation.intent.detected",
  "seq": 5,
  "payload": {"intent": "help", "text": "I'm stuck"}
}
```

### 6. System Schedules Hint (500ms delay)
```json
{
  "type": "system.pause.scheduled",
  "seq": 6,
  "payload": {"intent": "help", "delay_ms": 500}
}
```

### 7. Candidate Interrupts ("wait")
```json
{"type": "barge_in"}
```

### 8. System Cancels Pause
```json
{
  "type": "system.pause.cancelled",
  "seq": 7,
  "payload": {"intent": "help", "reason": "cut_in_detected"}
}
```

### 9. System Emits Immediate Hint
```json
{
  "type": "interviewer.utterance",
  "seq": 8,
  "payload": {
    "text": "What data structure lets you look up values in O(1)?",
    "hint_for": "help",
    "hint_step": 1,
    "attempt": 1
  }
}
```

### 10. Get Full Ledger
```bash
curl http://localhost:8000/vnext/interview/sessions/abc-123/ledger
```

---

**API Version**: 2.0  
**Last Updated**: 2026-07-06
