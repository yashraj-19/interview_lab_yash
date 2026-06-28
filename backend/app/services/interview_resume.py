"""Resume handshake + per-session connection ownership for the AI interview WS.

This module holds the *pure*, testable pieces of the reconnect/resume contract
so they can be unit-tested without the live WebSocket, Supabase, Deepgram, Redis,
or an LLM. The live route (``interview_ws_v2.py``) imports and wires these in.

Transport-control vs domain protocol
------------------------------------
``resume_ready`` / ``resume_rejected`` are *transport-control* messages. They are
NOT part of the persisted Interview Event Protocol vocabulary (``event_protocol``)
and are deliberately kept out of ``session_events`` and ``check:protocol`` — they
carry no durable interview meaning, only connection bookkeeping.

OWNERSHIP IS PROCESS-LOCAL
--------------------------
``ConnectionRegistry`` lives in process memory, so it only arbitrates connections
handled by the SAME worker process. With multiple uvicorn workers, two
connections for one session could land on different workers and both believe they
own it. Cross-worker safety requires a shared store (e.g. Redis holding the
generation under ``session:{id}:conn_gen``). The interview WebSocket is currently
deployed single-worker, which this design is correct for; the limitation is
documented rather than hidden.
"""

from __future__ import annotations

import itertools
import re
import threading
from dataclasses import dataclass
from typing import Any

# Handshake input bounds — reject oversized/malformed frames before any work.
_MAX_SESSION_ID = 128
_MAX_CONN_ID = 128
_MIN_CONN_ID = 8
_MAX_LAST_SEQ = 100_000_000
# client_conn_id is a UUID or our `conn-<base36>-<base36>` fallback.
_CONN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")

# ── transport-control message types (NOT persisted protocol events) ──
MSG_RESUME_READY = "resume_ready"
MSG_RESUME_REJECTED = "resume_rejected"

# ── rejection reasons ──
# Terminal on the client (-> "failed", no retry):
REJECT_INVALID = "invalid_handshake"
REJECT_NOT_FOUND = "session_not_found"
REJECT_COMPLETED = "session_completed"
REJECT_IN_USE = "session_in_use"
# Retryable on the client (bounded backoff):
REJECT_BUSY = "server_busy"


@dataclass(frozen=True)
class Hello:
    """A validated ``client_hello`` handshake frame."""

    session_id: str
    client_conn_id: str
    last_seq: int
    resume: bool


@dataclass(frozen=True)
class HandshakeError:
    """A rejected handshake; ``reason`` is one of the ``REJECT_*`` constants."""

    reason: str


def parse_client_hello(raw: Any, *, route_session_id: str) -> "Hello | HandshakeError":
    """Validate the first frame as a ``client_hello``.

    Returns a :class:`Hello` on success or :class:`HandshakeError` with a reason.
    Rejects missing/blank ids, a session that does not match the route param
    (no cross-session hijack), and a negative/non-int ``last_seq``.
    """
    if not isinstance(raw, dict):
        return HandshakeError(REJECT_INVALID)
    if raw.get("type") != "client_hello":
        return HandshakeError(REJECT_INVALID)

    session_id = raw.get("session_id")
    client_conn_id = raw.get("client_conn_id")
    last_seq = raw.get("last_seq", 0)
    resume = bool(raw.get("resume", False))

    if not isinstance(session_id, str) or not session_id or len(session_id) > _MAX_SESSION_ID:
        return HandshakeError(REJECT_INVALID)
    if session_id != route_session_id:
        return HandshakeError(REJECT_INVALID)
    # client_conn_id: bounded length + safe charset (UUID / conn-<base36> fallback).
    if (
        not isinstance(client_conn_id, str)
        or len(client_conn_id) < _MIN_CONN_ID
        or len(client_conn_id) > _MAX_CONN_ID
        or not _CONN_ID_RE.match(client_conn_id)
    ):
        return HandshakeError(REJECT_INVALID)
    # bool is a subclass of int — exclude it explicitly.
    if (
        isinstance(last_seq, bool)
        or not isinstance(last_seq, int)
        or last_seq < 0
        or last_seq > _MAX_LAST_SEQ
    ):
        return HandshakeError(REJECT_INVALID)

    return Hello(
        session_id=session_id,
        client_conn_id=client_conn_id,
        last_seq=last_seq,
        resume=resume,
    )


def decide_rejection(
    hello: Hello,
    *,
    session_exists: bool,
    session_status: str | None,
) -> str | None:
    """Return a rejection reason, or ``None`` to proceed with the handshake.

    A resume against a missing or completed/cancelled session is rejected so the
    client shows the final/terminal state instead of re-running the interview.
    """
    if not session_exists:
        return REJECT_NOT_FOUND
    if session_status in ("completed", "cancelled"):
        return REJECT_COMPLETED
    return None


def should_run_fresh_start(hello: Hello, *, has_live_state: bool) -> bool:
    """Whether the route should run the one-time fresh-start sequence.

    Fresh start = emit ``session.started``, speak the greeting, initialise the
    question, create the voice pipeline, bump session-start counters. A resume
    (client asked to resume, or the worker still holds live in-memory state for
    this session) must NOT repeat any of that.
    """
    if hello.resume:
        return False
    if has_live_state:
        return False
    return True


def select_backfill(events: list[dict], last_seq: int) -> list[dict]:
    """Return persisted protocol envelopes with ``seq > last_seq``, sorted by seq.

    Ordering is ALWAYS by ``seq`` (never timestamp). Rows whose payload lacks a
    numeric ``seq`` are dropped. Each event is expected to be a stored row whose
    ``payload`` is the protocol envelope (matching the existing events route).
    """

    def _seq(env: dict) -> int | None:
        seq = env.get("seq")
        return seq if isinstance(seq, int) and not isinstance(seq, bool) else None

    picked = []
    for row in events:
        env = row.get("payload") if isinstance(row, dict) and "payload" in row else row
        if not isinstance(env, dict):
            continue
        seq = _seq(env)
        if seq is None or seq <= last_seq:
            continue
        picked.append(env)

    picked.sort(key=lambda e: e["seq"])
    return picked


def build_resume_snapshot(
    *,
    phase: str | None,
    current_question: str | None,
    current_code: str | None,
    editor_control: str | None,
    completed: bool,
) -> dict:
    """Minimal server snapshot for state not reconstructible from the event log.

    Deliberately small — the durable event stream remains the source of truth;
    this only carries the few legacy fields the room cannot rebuild from events.
    """
    return {
        "phase": phase,
        "current_question": current_question,
        "current_code": current_code or "",
        "editor_control": editor_control or "candidate",
        "completed": bool(completed),
    }


def resume_ready_message(*, resumed: bool, from_seq: int, snapshot: dict | None) -> dict:
    """Build the ``resume_ready`` control frame (sent before any backfill)."""
    return {
        "type": MSG_RESUME_READY,
        "resumed": resumed,
        "from_seq": from_seq,
        "snapshot": snapshot or {},
    }


def resume_rejected_message(reason: str) -> dict:
    """Build the ``resume_rejected`` control frame."""
    return {"type": MSG_RESUME_REJECTED, "reason": reason}


@dataclass
class _Lease:
    generation: int
    conn_id: str


class ConnectionRegistry:
    """Process-local per-session connection ownership via a generation counter.

    Each new connection for a session gets a monotonically increasing
    ``generation``. Only the highest generation owns the session; older ones are
    stale and must stop consuming input, producing AI turns, mutating state, or
    completing the interview. NOT cross-worker safe (see module docstring).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._leases: dict[str, _Lease] = {}
        self._counter = itertools.count(1)

    def acquire(self, session_id: str, conn_id: str) -> int:
        """Register a new connection and return its generation (now the owner)."""
        with self._lock:
            gen = next(self._counter)
            self._leases[session_id] = _Lease(generation=gen, conn_id=conn_id)
            return gen

    def is_current(self, session_id: str, generation: int) -> bool:
        """True iff ``generation`` is the live owner of ``session_id``."""
        with self._lock:
            lease = self._leases.get(session_id)
            return lease is not None and lease.generation == generation

    def release(self, session_id: str, generation: int) -> bool:
        """Release the lease only if we still own it.

        A stale handler (already replaced by a newer connection) calling release
        returns ``False`` and does NOT remove the newer owner's lease — so a
        stale disconnect can never tear down a successfully resumed session.
        """
        with self._lock:
            lease = self._leases.get(session_id)
            if lease is not None and lease.generation == generation:
                del self._leases[session_id]
                return True
            return False


def can_mutate(registry: ConnectionRegistry, session_id: str, generation: int) -> bool:
    """Guard for any state-mutating action: only the current generation may act."""
    return registry.is_current(session_id, generation)


def should_mark_abandoned(
    registry: ConnectionRegistry,
    session_id: str,
    generation: int,
    *,
    clean_end: bool,
) -> bool:
    """Whether a disconnecting handler should mark the session abandoned.

    Only the *current* owner that did not end cleanly abandons the session. A
    stale generation (already superseded by a live resumed connection) never
    abandons — that would wrongly kill an active interview.
    """
    if clean_end:
        return False
    return registry.is_current(session_id, generation)
