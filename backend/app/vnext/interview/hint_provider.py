"""Pluggable HintProvider API and runtime registry.

Provides:
- `HintProvider` protocol (callable(session_id, intent) -> Optional[dict])
- `register_hint_provider(fn)` / `unregister_hint_provider()`
- `get_hint_for(session_id, intent)` which prefers a registered provider,
  then per-session override (stored in session record), then fallback to
  the built-in ladder.

The default fallback remains in `hint_ladder.next_hint` and is safe/offline.
"""
from __future__ import annotations

from typing import Callable, Optional

from .store import STORE
from .hint_ladder import next_hint as _fallback_next_hint


HintProvider = Callable[[str, str], Optional[dict]]

_provider: Optional[HintProvider] = None


def register_hint_provider(fn: HintProvider) -> None:
    global _provider
    _provider = fn


def unregister_hint_provider() -> None:
    global _provider
    _provider = None


def get_hint_for(session_id: str, intent: str) -> Optional[dict]:
    # provider has priority
    if _provider is not None:
        try:
            h = _provider(session_id, intent)
            if isinstance(h, dict):
                return h
        except Exception:
            # provider must not crash the flow; fall through to session override
            pass
    # session-level override — delegate to next_hint with the override ladder so
    # escalation/exhaustion behaves identically to the built-in ladder (was: a
    # re-implementation stuck on the final rung forever).
    rec = STORE.get_session(session_id) or {}
    session_hints = rec.get("session_hints") or {}
    ladder = session_hints.get(intent)
    if ladder:
        return _fallback_next_hint(session_id, intent, ladder=list(ladder))
    # fallback
    return _fallback_next_hint(session_id, intent)


__all__ = ["register_hint_provider", "unregister_hint_provider", "get_hint_for"]
