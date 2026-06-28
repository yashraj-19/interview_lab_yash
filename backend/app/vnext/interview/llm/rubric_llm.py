"""LLM rubric generation conforming to the EXACT existing Rubric schema.

Contract (src/lib/interview-v3/rubric.ts + models.py):
  Rubric    = {id, criteria[], generatedBy, version}
  Criterion = {id, name, description, weight, signals[], phaseHints[]}
  weights across criteria sum to EXACTLY 100; phaseHints are valid Phase values.

Validation/repair policy:
  - Structurally invalid (no criteria / missing core fields) -> return None.
  - Weights off 100 -> normalize ONCE (largest-remainder) -> still valid.
  - phaseHints filtered to valid phases; a criterion with no valid hint is
    structurally invalid -> None.
On None / any error / no provider, the CALLER falls back to the deterministic
scripted rubric (seed.generate_rubric). This module also exposes a convenience
``generate_rubric_llm`` that performs that fallback itself.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Optional

from ..phase_controller import PHASES
from ..seed import generate_rubric as generate_rubric_scripted
from ..seed import normalize_weights
from ._parse import extract_json
from .client import LLMUnavailable, call_llm

_VALID_PHASES = set(PHASES)
_log = logging.getLogger("sviam")

# Hard wall-clock budget for LLM rubric generation. If the provider(s) don't
# return a usable rubric within this many seconds, we fall back to the
# deterministic scripted rubric IMMEDIATELY so intake never hangs. Override with
# VNEXT_RUBRIC_BUDGET_S (e.g. tests drive it low).
_DEFAULT_RUBRIC_BUDGET_S = 9.0


def _rubric_budget_s() -> float:
    raw = os.getenv("VNEXT_RUBRIC_BUDGET_S")
    if raw:
        try:
            v = float(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return _DEFAULT_RUBRIC_BUDGET_S


def _slug(text: str, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_")
    return s or fallback


def validate_rubric_payload(session_id: str, data: object) -> Optional[dict]:
    """Validate + repair an LLM rubric payload. Return a rubric dict or None.

    Accepts either ``{criteria: [...]}`` or a bare ``[...]`` of criteria.
    """
    if isinstance(data, dict):
        raw_criteria = data.get("criteria")
    elif isinstance(data, list):
        raw_criteria = data
    else:
        return None

    if not isinstance(raw_criteria, list) or not raw_criteria:
        return None

    cleaned: list[dict] = []
    raw_weights: list[float] = []
    seen_ids: set[str] = set()

    for idx, c in enumerate(raw_criteria):
        if not isinstance(c, dict):
            return None
        name = c.get("name")
        description = c.get("description")
        if not isinstance(name, str) or not name.strip():
            return None
        if not isinstance(description, str) or not description.strip():
            return None

        cid = c.get("id")
        cid = _slug(cid if isinstance(cid, str) and cid.strip() else name, f"criterion_{idx}")
        # de-dupe ids deterministically
        base = cid
        n = 2
        while cid in seen_ids:
            cid = f"{base}_{n}"
            n += 1
        seen_ids.add(cid)

        weight = c.get("weight", 0)
        try:
            weight = float(weight)
        except (TypeError, ValueError):
            weight = 0.0
        if weight < 0:
            weight = 0.0

        signals_in = c.get("signals", [])
        signals = [str(s).strip() for s in signals_in if isinstance(s, (str, int, float)) and str(s).strip()] \
            if isinstance(signals_in, list) else []

        hints_in = c.get("phaseHints", [])
        hints = [h for h in hints_in if isinstance(h, str) and h in _VALID_PHASES] \
            if isinstance(hints_in, list) else []
        if not hints:
            # A criterion must point at where it is observable.
            return None

        cleaned.append(
            {
                "id": cid,
                "name": name.strip(),
                "description": description.strip(),
                "weight": weight,
                "signals": signals,
                "phaseHints": hints,
            }
        )
        raw_weights.append(weight)

    # Repair weights ONCE: normalize to sum exactly 100.
    normalized = normalize_weights([int(round(w)) for w in raw_weights])
    for i, c in enumerate(cleaned):
        c["weight"] = normalized[i]

    if sum(c["weight"] for c in cleaned) != 100:
        return None

    return {
        "id": f"rubric-{session_id}",
        "criteria": cleaned,
        "generatedBy": "llm",
        "version": 1,
    }


def _build_messages(intake: dict) -> list[dict]:
    phases = ", ".join(PHASES)
    system = (
        "You are an expert technical-interview designer. Produce a scoring rubric "
        "for the role as STRICT JSON only — no prose, no markdown fences.\n"
        "Schema: {\"criteria\": [ {\"id\": kebab/snake string, \"name\": string, "
        "\"description\": string, \"weight\": integer, \"signals\": [string,...], "
        "\"phaseHints\": [phase,...] } ] }.\n"
        "Rules: 3-6 criteria. Integer weights MUST sum to exactly 100. "
        f"phaseHints MUST be drawn ONLY from this set: [{phases}]. "
        "Each criterion needs at least one phaseHint and at least one signal."
    )
    user = (
        "Design the rubric for this interview.\n"
        f"Role: {intake.get('role', '')}\n"
        f"Seniority: {intake.get('seniority', 'mid')}\n"
        f"Languages: {', '.join(intake.get('languages', []) or [])}\n"
        f"Job description:\n{intake.get('jobDescription', '')}\n\n"
        f"Resume:\n{intake.get('resumeText', '')}\n"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def generate_rubric_llm(session_id: str, intake: dict, *, fake_llm: bool = False) -> dict:
    """LLM rubric with deterministic scripted fallback.

    Falls back to ``seed.generate_rubric`` when no provider is configured, the
    provider errors, or the output is malformed after one repair.

    ``fake_llm`` (TEST-ONLY, gated at session create) skips the provider entirely
    and returns the deterministic scripted rubric — same real REST/store path,
    no OpenRouter latency.
    """
    if fake_llm:
        return generate_rubric_scripted(session_id, intake)

    budget = _rubric_budget_s()
    start = time.monotonic()
    fallback_reason: Optional[str] = None
    rubric: Optional[dict] = None

    try:
        # Hard wall-clock cap across ALL providers — call_llm's per-provider
        # httpx timeout can stack to ~60s over two providers; this guarantees we
        # never make the candidate wait past `budget`.
        content = await asyncio.wait_for(
            call_llm(_build_messages(intake), role="rubric", temperature=0.2, max_tokens=900),
            timeout=budget,
        )
    except asyncio.TimeoutError:
        fallback_reason = "timeout"
    except LLMUnavailable:
        fallback_reason = "no_provider"
    except Exception as exc:  # noqa: BLE001 — any provider error → scripted fallback
        fallback_reason = f"error:{type(exc).__name__}"

    if fallback_reason is None:
        parsed = extract_json(content)
        rubric = validate_rubric_payload(session_id, parsed)
        if rubric is None:
            fallback_reason = "malformed"

    elapsed = time.monotonic() - start
    if fallback_reason is None:
        _log.info(
            "vnext.rubric provider=llm result=ok duration=%.2fs budget=%.1fs session=%s",
            elapsed, budget, session_id,
        )
        return rubric

    _log.info(
        "vnext.rubric provider=llm result=fallback reason=%s duration=%.2fs budget=%.1fs session=%s",
        fallback_reason, elapsed, budget, session_id,
    )
    return generate_rubric_scripted(session_id, intake)
