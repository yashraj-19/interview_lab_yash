# Session Onboarding (opt-in)

`SessionInitManager` (backend/app/vnext/interview/session_init.py) implements
a conversational readiness check: greeting → audio confirmation → readiness.
It is **opt-in per session** and OFF by default — the incident and problem
demos auto-start without it, and every test/CI flow skips it.

## Enabling

```json
POST /vnext/interview/sessions   { "intake": {…}, "onboarding": true }
```

## Flow (when enabled)

1. **Greeting** — on WS handshake the server registers and speaks a greeting
   ("Hi, I'm Maya. Quick check before we begin — say something so I can
   confirm your audio, and tell me when you're ready."), emitting
   `session.greeting` + an `interviewer.utterance`.
2. **Audio** — ANY candidate speech proves the mic/STT path works and marks
   `audio_ok` (`session.audio.ok`). Phrases like "muted" / "no sound" /
   "can't hear" instead record `session.audio.problem`. During onboarding,
   candidate text is a setup step only — it never triggers barge-in
   machinery or hints (so "no sound" can't be mistaken for a cut-in).
3. **Readiness** — a whole-word "ready" marks `session.ready`. Matching is
   negation-aware: "I **already** tried that", "I'm **not** ready", and
   "I **don't think** I'm ready" never confirm.
4. **Gate** — until all three flags are set, `advance.request` is answered
   with a spoken reminder instead of a phase change. Once complete, the
   check is cached and never touches the store again on the hot path.

## State

Flags persist on the session record under `session_init`
(`greeting_done` / `audio_ok` / `ready`); every transition also lands in the
ledger, so onboarding is replayable like everything else. Handlers are
idempotent (repeats emit `.ack` events).

## History

An earlier version gated **every** session unconditionally while nothing in
production ever called `register_greeting()` — no interview could start, and
ten WS tests hung instead of failing. The opt-in design (plus
any-speech-marks-audio and negation-aware readiness) is the fix; regression
tests pin all of it.
