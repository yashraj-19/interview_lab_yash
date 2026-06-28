"""Deterministic seed logic — a faithful mirror of the TS mock seam.

Mirrors, with NO LLM and NO randomness:
  - normalizeIntake            (src/lib/interview-v3/intake.ts)
  - buildCriteria              (src/lib/interview-v3/seed/interviewer-script.ts)
  - normalizeWeights           (src/lib/interview-v3/rubric.ts)
  - SCRIPTED_SESSION           (src/lib/interview-v3/seed/interviewer-script.ts)
  - SCORE_PLANS                (src/lib/interview-v3/mock-adapter.ts)

Same inputs always yield the identical rubric, ledger, and scorecard.
"""
from __future__ import annotations

from typing import Optional

# ── intake normalization (intake.ts) ─────────────────────────────────────────

_SCALE_HINTS = ["scale", "distributed", "latency", "throughput", "infra", "backend"]
_FRONTEND_HINTS = ["react", "frontend", "ui", "css", "typescript", "next"]


def _has_any(haystack: str, needles: list[str]) -> bool:
    lower = haystack.lower()
    return any(n in lower for n in needles)


def normalize_intake(intake: dict) -> dict:
    """Deterministic projection of a raw intake. Same intake -> identical ctx."""
    raw_langs = intake.get("languages") or []
    seen: list[str] = []
    for lang in raw_langs:
        norm = str(lang).strip().lower()
        if norm and norm not in seen:
            seen.append(norm)
    corpus = "\n".join(
        [
            str(intake.get("jobDescription", "")),
            str(intake.get("resumeText", "")),
            " ".join(seen),
        ]
    )
    return {
        "role": str(intake.get("role", "")).strip(),
        "seniority": intake.get("seniority", "mid"),
        "languages": seen,
        "durationMinutes": intake.get("durationMinutes", 45),
        "resumeText": intake.get("resumeText", ""),
        "jobDescription": intake.get("jobDescription", ""),
        "emphasizesScale": _has_any(corpus, _SCALE_HINTS),
        "emphasizesFrontend": _has_any(corpus, _FRONTEND_HINTS),
    }


# ── criterion templates (interviewer-script.ts BASE_TEMPLATES) ────────────────

_BASE_TEMPLATES = [
    {
        "id": "problem_solving",
        "name": "Problem solving",
        "description": "Decomposes the problem, reasons about edge cases, picks a viable approach.",
        "baseWeight": 30,
        "signals": ["states assumptions", "enumerates edge cases", "compares approaches"],
        "phaseHints": ["problem_framing", "coding"],
    },
    {
        "id": "coding",
        "name": "Coding ability",
        "description": "Writes correct, readable code and translates the plan into working software.",
        "baseWeight": 30,
        "signals": ["compiles/runs", "handles inputs", "clear naming"],
        "phaseHints": ["coding", "debugging"],
    },
    {
        "id": "communication",
        "name": "Communication",
        "description": "Explains thinking clearly and responds well to hints and questions.",
        "baseWeight": 20,
        "signals": ["thinks aloud", "answers directly", "incorporates feedback"],
        "phaseHints": ["intro", "resume_calibration", "wrap_up"],
    },
    {
        "id": "system_design",
        "name": "Design & tradeoffs",
        "description": "Reasons about complexity, scale, and tradeoffs in the chosen design.",
        "baseWeight": 20,
        "signals": ["analyzes complexity", "discusses tradeoffs", "considers scale"],
        "phaseHints": ["optimization", "debugging"],
    },
]


def build_criteria(ctx: dict) -> list[dict]:
    """Deterministic per-context weight adjustment, applied before normalization."""
    out: list[dict] = []
    for t in _BASE_TEMPLATES:
        weight = t["baseWeight"]
        seniority = ctx.get("seniority")
        if t["id"] == "system_design":
            if seniority in ("senior", "staff"):
                weight += 10
            if ctx.get("emphasizesScale"):
                weight += 5
        if t["id"] == "coding" and seniority in ("intern", "junior"):
            weight += 10
        if t["id"] == "communication" and ctx.get("emphasizesFrontend"):
            weight += 3
        out.append(
            {
                "id": t["id"],
                "name": t["name"],
                "description": t["description"],
                "weight": weight,
                "signals": list(t["signals"]),
                "phaseHints": list(t["phaseHints"]),
            }
        )
    return out


def normalize_weights(weights: list[int]) -> list[int]:
    """Normalize raw weights to sum to exactly 100 (largest-remainder rounding).

    Mirrors normalizeWeights in rubric.ts; ties broken by array order.
    """
    total = sum(weights)
    if total <= 0:
        n = len(weights)
        base = 100 // n
        out = [base] * n
        rem = 100 - base * n
        i = 0
        while rem > 0:
            out[i] += 1
            i += 1
            rem -= 1
        return out

    scaled = [(w / total) * 100 for w in weights]
    floored = [int(s) for s in scaled]  # floor for non-negative
    remainder = 100 - sum(floored)
    order = sorted(
        ((i, s - int(s)) for i, s in enumerate(scaled)),
        key=lambda p: (-p[1], p[0]),
    )
    out = list(floored)
    k = 0
    while k < len(order) and remainder > 0:
        out[order[k][0]] += 1
        k += 1
        remainder -= 1
    return out


def generate_rubric(session_id: str, intake: dict) -> dict:
    """Deterministic rubric from intake — NO LLM. Weights sum to exactly 100."""
    ctx = normalize_intake(intake)
    raw = build_criteria(ctx)
    normalized = normalize_weights([c["weight"] for c in raw])
    criteria = [{**c, "weight": normalized[i]} for i, c in enumerate(raw)]
    return {
        "id": f"rubric-{session_id}",
        "criteria": criteria,
        "generatedBy": "scripted",
        "version": 1,
    }


# ── scripted session (interviewer-script.ts SCRIPTED_SESSION) ─────────────────

_TWO_SUM = "\n".join(
    [
        "def two_sum(nums, target):",
        "    seen = {}",
        "    for i, n in enumerate(nums):",
        "        if target - n in seen:",
        "            return [seen[target - n], i]",
        "        seen[n] = i",
        "    return []",
    ]
)

# Each turn = (advance signal, [script events]). The leading session.start turn
# emits its events AFTER the controller advances ready -> intro. Mirrors the TS
# SCRIPTED_SESSION exactly (order + actors + text).
SCRIPTED_SESSION: list[dict] = [
    {
        "advance": "session.start",
        "events": [
            {
                "kind": "interviewer.utterance",
                "lineId": "L1",
                "text": "Hi, thanks for joining. I'm Maya and I'll be running today's session. Ready to start?",
            }
        ],
    },
    {
        "advance": "intro.done",
        "events": [
            {"kind": "candidate.utterance", "lineId": "L2", "text": "Yes, ready. Happy to be here."},
            {
                "kind": "interviewer.utterance",
                "lineId": "L3",
                "text": "Great. I saw on your resume you worked on a payments service — tell me about the hardest bug there.",
            },
        ],
    },
    {
        "advance": "calibration.done",
        "events": [
            {
                "kind": "candidate.utterance",
                "lineId": "L4",
                "text": "We had a race condition double-charging cards under retries; I fixed it with an idempotency key.",
            },
            {
                "kind": "interviewer.utterance",
                "lineId": "L5",
                "text": "Nice. Let's do a coding problem. Given an array and a target, return indices of two numbers that sum to it.",
            },
        ],
    },
    {
        "advance": "framing.done",
        "events": [
            {
                "kind": "candidate.utterance",
                "lineId": "L6",
                "text": "I'll use a hash map of value to index so it's one pass, O(n) time and O(n) space.",
            }
        ],
    },
    {
        "advance": "coding.done",
        "events": [
            {"kind": "code.edited", "editId": "E1", "after": _TWO_SUM},
            {"kind": "code.run", "runId": "R1", "code": _TWO_SUM, "stdout": "[0, 1]\n", "exitCode": 0},
        ],
    },
    {
        "advance": "debugging.done",
        "events": [
            {
                "kind": "interviewer.utterance",
                "lineId": "L7",
                "text": "What happens with an empty array or no valid pair?",
            },
            {
                "kind": "candidate.utterance",
                "lineId": "L8",
                "text": "It returns an empty list — the loop just never finds a match.",
            },
        ],
    },
    {
        "advance": "optimization.done",
        "events": [
            {
                "kind": "candidate.utterance",
                "lineId": "L9",
                "text": "If the input were sorted I could use two pointers for O(1) space, trading the hash map away.",
            }
        ],
    },
    {
        "advance": "wrap.done",
        "events": [
            {
                "kind": "interviewer.utterance",
                "lineId": "L10",
                "text": "That's all I had. Thanks — we'll follow up with next steps.",
            }
        ],
    },
]


def script_events_by_signal() -> dict[str, list[dict]]:
    """Map each AdvanceSignal to its turn's scripted events (in order)."""
    return {turn["advance"]: turn["events"] for turn in SCRIPTED_SESSION}


# Actor for each scripted event kind (mirrors appendScriptEvent in the mock).
SCRIPT_KIND_ACTOR = {
    "interviewer.utterance": "interviewer",
    "candidate.utterance": "candidate",
    "code.edited": "candidate",
    "code.run": "candidate",
}


def script_event_to_payload(ev: dict) -> tuple[str, str, dict]:
    """Return (actor, type, payload) for a scripted event dict."""
    kind = ev["kind"]
    actor = SCRIPT_KIND_ACTOR[kind]
    if kind == "interviewer.utterance" or kind == "candidate.utterance":
        return actor, kind, {"lineId": ev["lineId"], "text": ev["text"]}
    if kind == "code.edited":
        return actor, "code.edited", {"editId": ev["editId"], "after": ev["after"], "by": "candidate"}
    if kind == "code.run":
        return actor, "code.run", {
            "runId": ev["runId"],
            "code": ev["code"],
            "stdout": ev["stdout"],
            "exitCode": ev["exitCode"],
        }
    raise ValueError(f"unknown script event kind: {kind}")


# ── deterministic scorecard plans (mock-adapter.ts SCORE_PLANS) ───────────────

SCORE_PLANS: list[dict] = [
    {
        "criterionId": "problem_solving",
        "score": 80,
        "verdict": "strong",
        "evidence": [
            {"kind": "utterance", "refId": "L6", "excerpt": "one pass, O(n) time and O(n) space"},
            {"kind": "utterance", "refId": "L8", "excerpt": "returns an empty list"},
        ],
        "gaps": ["Did not discuss hash-collision / duplicate-value handling explicitly."],
    },
    {
        "criterionId": "coding",
        "score": 88,
        "verdict": "strong",
        "evidence": [
            {"kind": "code", "refId": "E1", "excerpt": "def two_sum(nums, target):"},
            {"kind": "code_run", "refId": "R1", "excerpt": "[0, 1]"},
        ],
        "gaps": [],
    },
    {
        "criterionId": "communication",
        "score": 82,
        "verdict": "strong",
        "evidence": [
            {"kind": "utterance", "refId": "L4", "excerpt": "fixed it with an idempotency key"},
            {"kind": "utterance", "refId": "L9", "excerpt": "trading the hash map away"},
        ],
        "gaps": [],
    },
    {
        "criterionId": "system_design",
        "score": 68,
        "verdict": "mixed",
        "evidence": [
            {"kind": "utterance", "refId": "L9", "excerpt": "two pointers for O(1) space"},
        ],
        "gaps": ["Tradeoff discussion stayed at single-problem scope; no broader system framing."],
    },
]


def _js_round(x: float) -> int:
    """JS Math.round semantics (round half up), not Python banker's rounding."""
    import math

    return int(math.floor(x + 0.5))


def aggregate_overall(scores: list[dict]) -> int:
    """Deterministic weighted aggregate of completed criterion scores."""
    total_weight = sum(s["weight"] for s in scores)
    if total_weight <= 0:
        return 0
    weighted = sum(s["score"] * s["weight"] for s in scores)
    return _js_round(weighted / total_weight)
