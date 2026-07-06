"""Pause/timing provider and session overrides.

Provides a pluggable pause provider interface and session-level overrides.
`get_pause_for(session_id, intent)` returns pause milliseconds (int).
"""
from __future__ import annotations

from typing import Callable, Optional

from .store import STORE

PauseProvider = Callable[[str, str], Optional[int]]

_provider: Optional[PauseProvider] = None


def register_pause_provider(fn: PauseProvider) -> None:
    global _provider
    _provider = fn


def unregister_pause_provider() -> None:
    global _provider
    _provider = None


def get_pause_for(session_id: str, intent: str) -> int:
    # provider priority
    if _provider is not None:
        try:
            p = _provider(session_id, intent)
            if isinstance(p, int) and p >= 0:
                return p
        except Exception:
            pass
    # session override
    rec = STORE.get_session(session_id) or {}
    policies = rec.get("pause_policies") or {}
    val = policies.get(intent)
    try:
        if isinstance(val, int) and val >= 0:
            return val
        if isinstance(val, str) and val.isdigit():
            return int(val)
    except Exception:
        pass
    return 0


__all__ = ["register_pause_provider", "unregister_pause_provider", "get_pause_for"]
