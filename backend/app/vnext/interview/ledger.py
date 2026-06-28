"""SessionLedger — the single seq writer for a vNext interview session.

Mirrors the seq/ts/idempotency discipline of the production protocol and the TS
mock (``mock-adapter.ts``): events are FLAT dicts (`{ ...envelope, ...payload }`),
seq is monotonic per session, and re-applying ``seq <= lastSeq`` is a no-op
downstream (order by seq, never ts).

The ledger assigns seq and a millisecond ts. The store owns durability; the
ledger owns ordering.
"""
from __future__ import annotations

import time
from typing import Optional


class SessionLedger:
    """Per-session monotonic seq counter + append-only event list.

    A ledger is the authority for seq within a session. ``ts`` is epoch ms. A
    fixed clock can be injected for deterministic tests.
    """

    def __init__(
        self,
        session_id: str,
        *,
        start_seq: int = 1,
        clock=None,
        events: Optional[list[dict]] = None,
    ) -> None:
        self.session_id = session_id
        self._next_seq = start_seq
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._events: list[dict] = list(events or [])
        if self._events:
            self._next_seq = max(e["seq"] for e in self._events) + 1

    @property
    def last_seq(self) -> int:
        return self._events[-1]["seq"] if self._events else 0

    def append(self, actor: str, type_: str, payload: dict) -> dict:
        """Mint a seq-ordered, ts-stamped flat event and append it.

        ``payload`` carries the type-specific fields (already using the wire
        field names, e.g. ``from``/``exitCode``). The envelope fields are merged
        on top so the result is a single flat dict matching the TS mock.
        """
        seq = self._next_seq
        self._next_seq += 1
        event = {
            "v": 1,
            "seq": seq,
            "ts": self._clock(),
            "sessionId": self.session_id,
            "actor": actor,
            "type": type_,
            **payload,
        }
        self._events.append(event)
        return event

    def append_external(self, event: dict) -> dict:
        """Append an event whose seq was minted EXTERNALLY (DB-atomic path).

        Used by the Supabase store when seq comes from the Postgres function
        ``vnext_append_event`` (a row-locked sequence) rather than this ledger.
        Keeps the in-process cache consistent so ``backfill`` / ``find_seq_by_ref``
        keep working. The ledger does not mint seq here; it only advances its
        high-water mark so any later in-process append stays monotonic.
        """
        self._events.append(event)
        if event["seq"] >= self._next_seq:
            self._next_seq = event["seq"] + 1
        return event

    def backfill(self, last_seq: int) -> list[dict]:
        """Return events with ``seq > last_seq``, ordered by seq."""
        return [e for e in self._events if e["seq"] > last_seq]

    def get_all(self) -> list[dict]:
        return list(self._events)

    def find_seq_by_ref(self, ref_id: str) -> int:
        """Resolve a scripted ref id (lineId/editId/runId) to its ledger seq.

        Mirrors ``findSeqByRefId`` in the TS mock. Returns 0 if not found so a
        caller can detect a dangling evidence reference.
        """
        for e in self._events:
            t = e.get("type")
            if t in ("interviewer.utterance", "candidate.utterance") and e.get("lineId") == ref_id:
                return e["seq"]
            if t == "code.edited" and e.get("editId") == ref_id:
                return e["seq"]
            if t == "code.run" and e.get("runId") == ref_id:
                return e["seq"]
        return 0
