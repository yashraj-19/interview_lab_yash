"""Incident-first software-engineering demo track (lab-only).

Selected with ``track="incident-demo"`` on session create. It reshapes the
interview so it OPENS with a concrete production bug (a payment API that double-
charges on retry) instead of resume/background questions, and walks a tight
code-review → fix → concurrency → test → ops → tradeoff sequence. Background is
deferred to the very end.

This module is pure data + a deterministic rubric: the interviewer prompt and the
scripted/fake fallback lines both read from here, so the track behaves identically
whether the LLM is live or the deterministic fake path is used (Playwright/CI).
"""
from __future__ import annotations

import re

from .seed import normalize_weights

INCIDENT_TRACK = "incident-demo"

# A realistic, NOT-LeetCode buggy snippet preloaded into the candidate's code box.
# Failure modes on purpose: no idempotency check, non-atomic read-then-insert, a
# retry after a provider timeout can double-charge, weak error handling, no
# transaction boundary.
INCIDENT_SEED_CODE = '''\
# Payments service — charge endpoint (Python).
# Reported: customers are occasionally charged twice when the provider times out
# and the client retries the request. Find and fix the failure mode.

def charge_customer(db, provider, customer_id, amount_cents, idempotency_key):
    # 1) has this charge already been made?
    rows = db.query(
        "SELECT id, provider_ref FROM charges "
        "WHERE customer_id = %s AND amount_cents = %s",
        customer_id, amount_cents,
    )
    if rows:
        return rows[0]

    # 2) call the payment provider (may time out; the client then retries)
    result = provider.charge(customer_id, amount_cents)

    # 3) record the charge
    db.execute(
        "INSERT INTO charges (customer_id, amount_cents, provider_ref) "
        "VALUES (%s, %s, %s)",
        customer_id, amount_cents, result.provider_ref,
    )
    return result
'''

# Short, concrete scenario shown in the room (task card) and fed to the prompt.
INCIDENT_TASK_PROMPT = (
    "Production issue: this payment API sometimes creates DUPLICATE charges when "
    "the provider times out and the client retries. Inspect the code in the box, "
    "find the failure mode, and make a retry with the same idempotency key safe."
)

# Deterministic interviewer line per advance signal (the phase it lands on).
# Used for the fake-provider path AND as the LLM fallback, so the track is
# testable without OpenRouter. Concise, TTS-friendly, no generic praise, varied
# openings, never "Can you…/You mentioned…/Please provide…".
INCIDENT_LINES: dict[str, str] = {
    # session.start -> intro: very short greeting, then straight to the incident.
    "session.start": (
        "Welcome — let's skip the small talk. Here's a production issue: this "
        "payment API double-charges when the provider times out and the client "
        "retries. Open the code box and find the failure mode."
    ),
    # intro.done -> resume_calibration: the race condition.
    "intro.done": (
        "Walk the race with me: two retries land at the same instant — where "
        "exactly do they collide and produce a second charge?"
    ),
    # calibration.done -> problem_framing: the transaction boundary.
    "calibration.done": (
        "Show me the transaction boundary. What has to be atomic here, and what "
        "isn't in the current code?"
    ),
    # framing.done -> coding: write the fix in the box.
    "framing.done": (
        "Patch the smallest unsafe part first — write the idempotent version in "
        "the code box so the same key never charges twice."
    ),
    # coding.done -> debugging: test the duplicate-retry behavior.
    "coding.done": (
        "Now write the test that would have caught the double charge — what does "
        "it assert under two retries with the same key?"
    ),
    # debugging.done -> optimization: ops proof + the tradeoff question.
    "debugging.done": (
        "What would you log or alert to prove the fix holds in production — and "
        "would you reach for a DB unique constraint, a distributed lock, or the "
        "provider's idempotency key? Defend the choice."
    ),
    # optimization.done -> wrap_up: only NOW the short background calibration.
    "optimization.done": (
        "Last thing: where have you dealt with something like this in production?"
    ),
}


def incident_line(signal: str) -> str:
    """Deterministic incident interviewer line for a signal (fallback/fake)."""
    return INCIDENT_LINES.get(signal, "Keep going — tighten the fix.")


# Per-phase guidance injected into the LLM system prompt for this track so the
# live model follows the same incident-first arc as the deterministic lines.
INCIDENT_PHASE_GUIDANCE: dict[str, str] = {
    "intro": (
        "ONE short greeting sentence, then immediately present the duplicate-charge "
        "production incident and tell them to inspect/fix the code IN THE CODE BOX. "
        "Do NOT ask about their background or resume."
    ),
    "resume_calibration": (
        "Do NOT ask about their resume yet. Probe the RACE CONDITION: how two "
        "concurrent retries produce a second charge."
    ),
    "problem_framing": "Pin down the TRANSACTION BOUNDARY and what must be atomic.",
    "coding": (
        "Make them WRITE the idempotent fix in the code box (unique key / atomic "
        "upsert / provider idempotency key). Insist on code, not a verbal sketch."
    ),
    "debugging": "Make them design the TEST that proves a duplicate retry is a no-op.",
    "optimization": (
        "Push on OPS proof (metrics/logs/alerts) and ONE tradeoff: DB unique "
        "constraint vs distributed lock vs provider idempotency key."
    ),
    "wrap_up": (
        "Only now ask ONE short background calibration question about similar "
        "production experience."
    ),
}


def incident_rubric(session_id: str) -> dict:
    """Deterministic rubric that rewards real incident evidence (code, concurrency
    reasoning, tests, ops thinking, tradeoff judgment). Weights sum to 100."""
    criteria = [
        {
            "id": "idempotency_fix",
            "name": "Idempotency fix (implementation)",
            "description": "Writes code that makes a retry with the same key safe — "
            "an atomic upsert, unique constraint, or provider idempotency key.",
            "weight": 30,
            "signals": ["edits code in the box", "removes the read-then-insert race",
                        "same key never double-charges"],
            "phaseHints": ["coding", "problem_framing"],
        },
        {
            "id": "concurrency_reasoning",
            "name": "Concurrency reasoning",
            "description": "Explains how two concurrent retries collide and where "
            "atomicity must hold (transaction boundary).",
            "weight": 25,
            "signals": ["names the race window", "identifies the transaction boundary"],
            "phaseHints": ["debugging", "problem_framing"],
        },
        {
            "id": "test_strategy",
            "name": "Test strategy",
            "description": "Designs a test that asserts a duplicate retry is a no-op.",
            "weight": 20,
            "signals": ["test under two retries", "asserts single charge"],
            "phaseHints": ["debugging"],
        },
        {
            "id": "operational_thinking",
            "name": "Operational thinking",
            "description": "Names the metrics/logs/alerts that prove the fix in prod.",
            "weight": 13,
            "signals": ["duplicate-charge metric", "alert on retry anomalies"],
            "phaseHints": ["optimization"],
        },
        {
            "id": "tradeoff_judgment",
            "name": "Tradeoff judgment",
            "description": "Defends DB unique constraint vs distributed lock vs "
            "provider idempotency key.",
            "weight": 12,
            "signals": ["compares the approaches", "justifies the choice"],
            "phaseHints": ["optimization", "wrap_up"],
        },
    ]
    weights = normalize_weights([c["weight"] for c in criteria])
    for c, w in zip(criteria, weights):
        c["weight"] = w
    return {
        "id": f"rubric-{session_id}",
        "criteria": criteria,
        "generatedBy": "scripted",
        "version": 1,
    }


# ── live AI code actions (Maya selects/highlights + proposes a patch) ──────────

# Deterministic, validated "safe" version Maya proposes when the candidate's code
# is still racy / not idempotent. Used as the patch `after` (and as the LLM
# fallback). Idempotent via a UNIQUE(idempotency_key) guard.
INCIDENT_FIXED_CODE = '''\
def charge_customer(db, provider, customer_id, amount_cents, idempotency_key):
    # Idempotent: look the charge up by its UNIQUE idempotency_key so a retry
    # with the same key returns the existing charge instead of making a new one.
    existing = db.query(
        "SELECT id, provider_ref FROM charges WHERE idempotency_key = %s",
        idempotency_key,
    )
    if existing:
        return existing[0]

    result = provider.charge(customer_id, amount_cents, idempotency_key=idempotency_key)

    # The INSERT is guarded by a UNIQUE(idempotency_key) constraint. Under two
    # concurrent retries the second INSERT loses the race, raises, and we return
    # the charge the winner already recorded — never a duplicate.
    try:
        db.execute(
            "INSERT INTO charges (customer_id, amount_cents, provider_ref, idempotency_key) "
            "VALUES (%s, %s, %s, %s)",
            customer_id, amount_cents, result.provider_ref, idempotency_key,
        )
    except UniqueViolation:
        return db.query(
            "SELECT id, provider_ref FROM charges WHERE idempotency_key = %s",
            idempotency_key,
        )[0]
    return result
'''


def incident_code_is_unsafe(code: str) -> bool:
    """True when the candidate's charge_customer is still the racy read-then-insert
    (no idempotency guard). Deterministic heuristic — the basis for Maya acting."""
    if not code:
        return False
    c = code.lower()
    if "charge_customer" not in c:
        return False  # not the incident code — never act
    guarded = (
        "on conflict" in c
        or "unique" in c
        or "for update" in c
        or "begin" in c
        or "transaction" in c
        or bool(re.search(r"where[^\n]*idempotency_key", c))
    )
    return not guarded


def incident_risky_range(code: str) -> tuple[int, int]:
    """0-based inclusive line range of the read-before-write path to select/
    highlight (the first db.query down to its return)."""
    lines = code.split("\n")
    start = next((i for i, ln in enumerate(lines) if "db.query" in ln), 0)
    end = next((i for i in range(start + 1, len(lines)) if "return" in lines[i]), start + 3)
    return start, min(end, max(len(lines) - 1, 0))


def incident_patch_is_safe(after: str, before: str) -> bool:
    """Validate a proposed patch (esp. an LLM one) before it can reach the ledger.
    Must keep the function, actually guard idempotency, and not gut the code."""
    if not after or not after.strip():
        return False
    if "charge_customer" not in after:
        return False
    a = after.lower()
    guards = ("on conflict" in a) or ("unique" in a) or ("for update" in a) or ("except" in a)
    if not ("idempotency_key" in a and guards):
        return False
    if len(after.strip()) < len(before.strip()) * 0.5:
        return False  # refuse to delete most of the function
    return True


def incident_patch(before: str) -> dict:
    """Build the deterministic safe-patch payload (patchId assigned by the WS)."""
    start, end = incident_risky_range(before)
    return {
        "summary": "Make the charge idempotent: look up + INSERT by a UNIQUE "
        "idempotency_key so a retry with the same key is a no-op.",
        "before": before,
        "after": INCIDENT_FIXED_CODE,
        "selection": {"start": start, "end": end},
    }


# Concise spoken line when Maya flags the risky section.
INCIDENT_PATCH_UTTERANCE = (
    "I'm selecting the read-before-write path. Under two retries this still "
    "double-charges — let me propose an idempotent version; accept it or fix it "
    "yourself."
)
