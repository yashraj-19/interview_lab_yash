"""Persistence behind a small interface.

E1 ships an IN-MEMORY (process dict) implementation. A Supabase-backed impl can
slot in later by implementing the same ``InterviewStore`` protocol against the
``vnext_sessions`` / ``vnext_events`` / ``vnext_scorecards`` tables (see
backend/migrations/028_vnext_interview.sql). No Supabase is touched in E1/E2.

A "session record" is a small dict:
  {sessionId, intake, rubric, phase, scorecard}
Events are the flat ledger dicts. The store also vends a live ``SessionLedger``
per session so the seq counter has a single owner across REST + WS.
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from typing import Optional, Protocol

from .ledger import SessionLedger

_log = logging.getLogger("sviam")


class InterviewStore(Protocol):
    def create_session(self, intake: dict) -> str: ...
    def get_session(self, session_id: str) -> Optional[dict]: ...
    def put_session(self, session_id: str, record: dict) -> None: ...
    def get_ledger(self, session_id: str) -> Optional[SessionLedger]: ...
    def append_event(self, session_id: str, actor: str, type_: str, payload: dict) -> dict: ...
    def get_events(self, session_id: str, since: int = 0) -> list[dict]: ...
    def put_scorecard(self, session_id: str, scorecard: dict) -> None: ...


class InMemoryInterviewStore:
    """Process-local store. NOT cross-worker safe — by design for E1/E2.

    The vNext WS is single-worker like the existing interview WS.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}
        self._ledgers: dict[str, SessionLedger] = {}

    def create_session(self, intake: dict) -> str:
        session_id = str(uuid.uuid4())
        with self._lock:
            self._sessions[session_id] = {
                "sessionId": session_id,
                "intake": intake,
                "rubric": None,
                "phase": "intake",
                "scorecard": None,
            }
            self._ledgers[session_id] = SessionLedger(session_id)
        return session_id

    def get_session(self, session_id: str) -> Optional[dict]:
        with self._lock:
            rec = self._sessions.get(session_id)
            return dict(rec) if rec is not None else None

    def put_session(self, session_id: str, record: dict) -> None:
        with self._lock:
            self._sessions[session_id] = dict(record)

    def get_ledger(self, session_id: str) -> Optional[SessionLedger]:
        with self._lock:
            return self._ledgers.get(session_id)

    def append_event(self, session_id: str, actor: str, type_: str, payload: dict) -> dict:
        ledger = self.get_ledger(session_id)
        if ledger is None:
            raise KeyError(f"no ledger for session {session_id}")
        # SessionLedger owns its own internal list; appends are serialised here
        # so concurrent REST + WS writers share one monotonic seq stream.
        with self._lock:
            return ledger.append(actor, type_, payload)

    def get_events(self, session_id: str, since: int = 0) -> list[dict]:
        ledger = self.get_ledger(session_id)
        if ledger is None:
            return []
        return ledger.backfill(since)

    def put_scorecard(self, session_id: str, scorecard: dict) -> None:
        with self._lock:
            rec = self._sessions.get(session_id)
            if rec is not None:
                rec["scorecard"] = scorecard


def _supabase_configured() -> bool:
    """True only when Supabase creds are present. Never raises."""
    try:
        from app.config import settings

        return bool(settings.supabase_url and settings.supabase_service_role_key)
    except Exception:  # pragma: no cover - config import guard
        return False


def _build_store() -> tuple[InterviewStore, str]:
    """Select the store from env. DEFAULT memory. Supabase is strictly opt-in
    via ``VNEXT_STORE=supabase`` AND Supabase being configured. Any problem falls
    back to memory with a logged warning — selection NEVER crashes on import.
    """
    mode = os.getenv("VNEXT_STORE", "memory").strip().lower()
    if mode == "supabase":
        if not _supabase_configured():
            _log.warning(
                "VNEXT_STORE=supabase but Supabase is not configured — "
                "falling back to in-memory vnext store."
            )
        else:
            try:
                from .store_supabase import SupabaseInterviewStore

                return SupabaseInterviewStore(), "supabase"
            except Exception as exc:  # pragma: no cover - defensive
                _log.warning(
                    "VNEXT_STORE=supabase but store init failed (%s) — "
                    "falling back to in-memory vnext store.",
                    exc,
                )
    return InMemoryInterviewStore(), "memory"


_STORE_SINGLETON: Optional[InterviewStore] = None
_STORE_MODE: str = "memory"


def get_store() -> InterviewStore:
    """Process-wide store singleton, selected once on first use."""
    global _STORE_SINGLETON, _STORE_MODE
    if _STORE_SINGLETON is None:
        _STORE_SINGLETON, _STORE_MODE = _build_store()
    return _STORE_SINGLETON


def active_store_mode() -> str:
    """``"memory"`` or ``"supabase"`` — the live store mode (for warmup/debug)."""
    get_store()
    return _STORE_MODE


# Module-level singleton shared by REST + WS within the worker. Selection happens
# in exactly one place (get_store) so rest.py/ws.py just import STORE.
STORE: InterviewStore = get_store()
