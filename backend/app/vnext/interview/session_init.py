"""Session Initialization layer for conversational onboarding.

This module provides a small, testable manager that controls the pre-interview
session-init flow. It is intentionally decoupled from the PhaseController and
the websocket protocol: callers (REST/WS) will invoke these helpers. The
manager persists its progress into the session record (via STORE) and emits
ledger events so the conversation history is auditable.

Design rules implemented here:
- Minimal, modular state machine with explicit transitions
- Persistence of three flags: greeting, audio_ok, ready
- Idempotent handlers for repeated messages
- No LLM calls, no evaluation logic — strictly onboarding
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from .store import STORE


@dataclass
class SessionInitState:
    greeting_done: bool = False
    audio_ok: bool = False
    ready: bool = False


class SessionInitManager:
    """Manage the Session Initialization sequence for a single session.

    Usage:
        m = SessionInitManager(session_id)
        m.register_greeting("Hello!")
        m.mark_audio_ok()
        m.mark_ready()

    The manager records events to the ledger and writes a small `session_init`
    object into the session record so downstream code can query progress.
    """

    KEY = "session_init"

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id

    def _get_rec(self) -> Dict:
        rec = STORE.get_session(self.session_id)
        if rec is None:
            raise KeyError(f"unknown session {self.session_id}")
        return rec

    def _persist_state(self, state: SessionInitState) -> None:
        rec = self._get_rec()
        rec[self.KEY] = {
            "greeting_done": state.greeting_done,
            "audio_ok": state.audio_ok,
            "ready": state.ready,
        }
        STORE.put_session(self.session_id, rec)

    def _emit(self, actor: str, type_: str, payload: dict) -> dict:
        return STORE.append_event(self.session_id, actor, type_, payload)

    def _load_state(self) -> SessionInitState:
        rec = self._get_rec()
        s = rec.get(self.KEY) or {}
        return SessionInitState(
            greeting_done=bool(s.get("greeting_done")),
            audio_ok=bool(s.get("audio_ok")),
            ready=bool(s.get("ready")),
        )

    # Public handlers — idempotent
    def register_greeting(self, text: str) -> dict:
        """Record a greeting from interviewer and persist the flag.

        This should be called by the interviewer when it emits a greeting. It is
        idempotent and will not create duplicate state transitions.
        """
        state = self._load_state()
        if state.greeting_done:
            # already recorded — emit a lightweight ack
            return self._emit("interviewer", "session.greeting.ack", {"text": text})

        ev = self._emit("interviewer", "session.greeting", {"text": text})
        state.greeting_done = True
        self._persist_state(state)
        return ev

    def mark_audio_ok(self) -> dict:
        """Mark that audio/mic check passed and persist.

        Idempotent.
        """
        state = self._load_state()
        if state.audio_ok:
            return self._emit("system", "session.audio.ack", {"ok": True})
        ev = self._emit("system", "session.audio.ok", {"ok": True})
        state.audio_ok = True
        self._persist_state(state)
        return ev

    def mark_audio_problem(self, problem_text: str) -> dict:
        """Record an audio issue (microphone/speaker) without advancing.

        Returns the emitted event.
        """
        return self._emit("candidate", "session.audio.problem", {"text": problem_text})

    def mark_ready(self) -> dict:
        """Mark candidate readiness.

        Only when greeting and audio_ok are True should callers advance the
        main interview flow. This method is idempotent.
        """
        state = self._load_state()
        if state.ready:
            return self._emit("candidate", "session.ready.ack", {"ready": True})
        state.ready = True
        self._persist_state(state)
        ev = self._emit("candidate", "session.ready", {"ready": True})
        return ev

    def is_complete(self) -> bool:
        s = self._load_state()
        return s.greeting_done and s.audio_ok and s.ready


__all__ = ["SessionInitManager", "SessionInitState"]
