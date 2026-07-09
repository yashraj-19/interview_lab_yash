# Speech Recognition — Current State & the Deepgram Streaming Path

The redesign brief asked: replace browser speech recognition with server-side
Deepgram streaming *if feasible without breaking architecture; otherwise
improve the current implementation and document why*. This is that document.

## Verdict

**Not replaced in this pass — deliberately.** Server-side streaming STT is a
new realtime AUDIO pipeline (mic capture → PCM frames → socket → Deepgram →
interim/final events), not a swap of one function. Landing it in the same
change as the adaptive-conversation engine would have put the two riskiest
subsystems in one diff, and the conversation engine was the thing making
interviews feel fake. The current STT was hardened instead, and the full
Deepgram design below is ready to implement as its own stage.

## What the current implementation is

- `useSpeechToText` (useVoice.ts): browser **Web Speech API**, continuous +
  interim results, Chrome-effectively-only.
- Quality traits observed live: accent-sensitive, mis-hears far-field speech
  ("inspectacled in the park"), unpunctuated transcripts, per-instance
  restarts on browser silence timeouts (the room keep-alives it).
- Mitigations already in place:
  - the **semantic turn-end rule** tolerates fragmented finals (accumulates,
    resets timers on any speech, waits on continuation tokens);
  - the **echo heuristic** + barge-in gate keeps Maya's own voice from
    triggering interrupts;
  - the **conversation engine** classifies intent from meaning-bearing words,
    so partial mis-hearings still route usefully;
  - **headphones + Chrome + a decent mic** remain the honest operational
    requirement (documented in the deploy runbook).

## Why Deepgram streaming is the right upgrade (later)

The user already holds a valid Deepgram key; the deployed Voice_Assist proved
nova-3 quality on this exact accent/domain (with keyterm prompting for DSA
vocabulary: "idempotency", "hash map", "race condition", …). Browser Web
Speech cannot be prompted, tuned, or observed server-side.

## The design (one stage, ~a day of focused work)

```
Browser                                   Backend (FastAPI)
───────                                   ─────────────────
getUserMedia (16kHz mono)                 /vnext/interview/stt/{sessionId}
  → AudioWorklet → PCM16 frames             (WS proxy; auth = session exists)
  → WS binary frames ──────────────────►    → Deepgram streaming (nova-3,
                                              smart_format, interim_results,
  ◄────────── {interim|final, text} ────      keyterms from the scenario)
useSpeechToText facade UNCHANGED:
  same callbacks (onResult/onFinal/…)
  engine picked by feature flag:
  deepgram when the key is set, else
  Web Speech (current behavior)
```

Key decisions:
1. **The `useSpeechToText` interface is the seam** — the room's turn-end,
   barge-in gate, and echo filter consume callbacks, not the recognizer, so a
   `DeepgramSpeechToText` drop-in leaves ALL conversation logic untouched.
2. **Proxy through the backend**, never expose the Deepgram key to the
   browser; per-session WS mirrors the interview socket's auth model.
3. **Punctuated finals** (smart_format) upgrade the turn-end rule for free:
   the terminal-punctuation fast-confirm arm (700ms) starts firing, matching
   Voice_Assist's production behavior ("26/28 turns end on terminal punct").
4. **Scenario keyterms**: each ScenarioSpec already knows its vocabulary —
   feed problem titles/terms as Deepgram keyterms per session.
5. **Fallback discipline** (house rule): Deepgram socket fails → seamless
   revert to Web Speech mid-session; the facade hides the switch.

Risks to test explicitly: mic permission UX (getUserMedia vs Web Speech both
prompt), AudioWorklet on older Chrome, Render WS binary throughput, added
~150–300ms interim latency vs local recognition, and double-cost when TTS
plays through speakers (echo — same heuristic applies to Deepgram interims).

## Sequencing recommendation

Ship after the current adaptive-engine milestone is demo-verified. It is the
highest-leverage remaining *quality* upgrade, but the interview is fully
functional without it — and it must not destabilize a working voice demo the
day before a review.
