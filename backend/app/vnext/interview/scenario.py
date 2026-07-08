"""ScenarioSpec — the problem-agnostic behavioral contract for an interview.

incident.py proved the shape by example: a scenario is not just problem TEXT,
it is interviewer BEHAVIOR — seed code in the box, deterministic per-signal
lines (fallback/no-LLM path), per-phase LLM guidance, a rubric, and the live
code-action hooks (wrong-code detection → risky-line highlight → optional
validated patch). This module lifts that contract into one dataclass, wraps
the incident as the first registered scenario (behavior unchanged), and
builds a scenario for every problem in problem_spec.PROBLEM_CATALOG — which
turns the previously-unwired Phase-5 catalog into live interviews.

Design rules:
- The engine (ws.py / rest.py / llm/) consults ONLY this registry — never
  incident.py or problem_spec.py directly — so adding a scenario is data,
  not engine surgery.
- Deterministic-fallback discipline: every spec vends its own lines/rubric,
  so live-LLM and offline paths stay behaviorally identical per scenario.
- Never-reveal: problem scenarios expose `reveal_terms` (enforced by
  reveal_guard before hint attempt 3) and their code actions HIGHLIGHT and
  PROBE but never patch — proposing the solution would reveal it. The
  incident keeps its patch flow: its fix terms are public in the task prompt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Sequence

from .incident import (
    INCIDENT_FIXED_CODE,  # noqa: F401  (re-exported for tests/tools)
    INCIDENT_LINES,
    INCIDENT_PHASE_GUIDANCE,
    INCIDENT_PATCH_UTTERANCE,
    INCIDENT_SEED_CODE,
    INCIDENT_TASK_PROMPT,
    INCIDENT_TRACK,
    incident_code_is_unsafe,
    incident_patch,
    incident_patch_is_safe,
    incident_risky_range,
    incident_rubric,
)
from .problem_spec import PROBLEM_CATALOG, ProblemSpec
from .seed import normalize_weights

# Track ids for problem scenarios: "problem:two_sum" etc.
PROBLEM_TRACK_PREFIX = "problem:"


@dataclass(frozen=True)
class ScenarioSpec:
    """Everything the engine needs to CONDUCT a scenario, in one object."""

    id: str                       # track id ("incident-demo", "problem:two_sum")
    title: str
    seed_code: str                # preloaded into the candidate's code box
    task_prompt: str              # task card + LLM system-prompt appendix
    language: str = "python"
    # Deterministic interviewer line per advance signal (fake/no-LLM fallback).
    lines_for_signal: Mapping[str, str] = field(default_factory=dict)
    default_line: str = "Keep going — walk me through your next step."
    # Per-phase guidance injected into the LLM system prompt.
    phase_guidance: Mapping[str, str] = field(default_factory=dict)
    # session_id -> rubric dict (deterministic).
    rubric_factory: Optional[Callable[[str], dict]] = None
    # Per-scenario never-reveal help ladder (nudge → hint → reveal).
    hint_ladder: Sequence[str] = ()
    # Answer terms reveal_guard blocks before hint attempt 3.
    reveal_terms: Sequence[str] = ()
    # ── live code actions ──
    code_is_unsafe: Optional[Callable[[str], bool]] = None
    risky_range: Optional[Callable[[str], tuple[int, int]]] = None
    build_patch: Optional[Callable[[str], dict]] = None      # None → highlight+probe only
    patch_is_safe: Optional[Callable[[str, str], bool]] = None
    action_utterance: str = ""
    # ── real code execution ──
    problem: Optional[ProblemSpec] = None  # carries test cases for the runner


# ─────────────────────────────────────────────────────────────────────────────
# Problem scenarios: generic templates parameterized by the catalog entry.
# One set of templates conducts ANY problem — no per-problem scripting.
# ─────────────────────────────────────────────────────────────────────────────


def _fn_name(signature: str) -> str:
    m = re.match(r"\s*def\s+(\w+)\s*\(", signature or "")
    return m.group(1) if m else "solve"


def _problem_seed_code(p: ProblemSpec) -> str:
    example = p.test_cases[0] if p.test_cases else None
    lines = [
        f"# {p.title} ({p.difficulty})",
        f"# {p.description}",
        "#",
        *(f"# - {c}" for c in p.constraints[:4]),
    ]
    if example:
        lines.append(f"# Example: {example.input_args} -> {example.expected_output!r}")
    lines += [
        "",
        p.function_signature,
        "    # Your solution here. Talk through your approach as you type.",
        "    pass",
        "",
    ]
    return "\n".join(lines)


def _problem_lines(p: ProblemSpec) -> dict[str, str]:
    """Deterministic per-signal lines — generic interview arc, problem-flavored."""
    return {
        "session.start": (
            f"Let's get into it. {p.title}: {p.description} "
            "The signature is in the code box — before you write anything, "
            "talk me through your first approach."
        ),
        "intro.done": (
            "Start with the brute force: what's the simplest thing that works, "
            "and what's its time complexity?"
        ),
        "calibration.done": (
            "Now beat it. What would get you a faster solution here — and what "
            "does it cost in space?"
        ),
        "framing.done": (
            "Write it in the code box. Keep talking while you type — I want to "
            "hear the reasoning, not just see the result."
        ),
        "coding.done": (
            "Now break your own code: which edge case worries you most? Walk "
            "one input through it by hand."
        ),
        "debugging.done": (
            "What's the exact time and space complexity of what you wrote — and "
            "can either improve without wrecking the other?"
        ),
        "optimization.done": (
            "Last thing: where has this pattern shown up in real work you've done?"
        ),
    }


def _problem_guidance(p: ProblemSpec) -> dict[str, str]:
    """Per-phase LLM guidance. NEVER names the optimal approach — the hint
    ladder owns escalation, and reveal_guard enforces it in code."""
    return {
        "intro": (
            f"ONE short sentence, then present the problem: {p.title} — "
            f"{p.description} Tell them the signature is in the code box and ask "
            "for their first approach OUT LOUD before any code. Do NOT ask about "
            "their background."
        ),
        "resume_calibration": (
            "Do NOT ask about their resume. Make them state the brute-force "
            "approach and its exact time complexity. If they jump to the optimal "
            "solution, make them justify WHY it works."
        ),
        "problem_framing": (
            "Push toward a better-than-brute-force approach WITHOUT naming it. "
            "Probe with constraints (input size, value ranges) and cost questions. "
            f"Target complexity is {p.time_complexity} time / {p.space_complexity} "
            "space — never state this; make THEM derive it."
        ),
        "coding": (
            "Make them WRITE the solution in the code box while thinking aloud. "
            "Insist on code, not a verbal sketch. If they stall silently, ask "
            "them to narrate the next line only."
        ),
        "debugging": (
            "Make them design the edge cases and trace ONE through their actual "
            "code line by line. Use Run results if present — quote the failing "
            "case back at them, never the fix."
        ),
        "optimization": (
            "Make them state the exact time/space complexity of THEIR code and "
            "defend whether it can improve. Challenge any hand-waved bound."
        ),
        "wrap_up": (
            "Only now: ONE short question about where they've used this pattern "
            "in real work."
        ),
    }


def _problem_rubric_factory(p: ProblemSpec) -> Callable[[str], dict]:
    def factory(session_id: str) -> dict:
        criteria = [
            {
                "id": "correctness",
                "name": "Correctness (implementation)",
                "description": f"Working {p.title} implementation that handles the stated constraints and edge cases.",
                "weight": 30,
                "signals": ["writes code in the box", "passes the example cases", "handles edge cases"],
                "phaseHints": ["coding", "debugging"],
            },
            {
                "id": "approach",
                "name": "Approach & data structures",
                "description": "Chooses and justifies an approach that beats brute force; explains WHY it works.",
                "weight": 25,
                "signals": ["states brute force first", "justifies the better approach", "correct data-structure choice"],
                "phaseHints": ["resume_calibration", "problem_framing"],
            },
            {
                "id": "complexity",
                "name": "Complexity analysis",
                "description": f"Derives time/space of their own code (target {p.time_complexity} / {p.space_complexity}) and defends it.",
                "weight": 20,
                "signals": ["states exact bounds", "explains the tradeoff"],
                "phaseHints": ["optimization", "problem_framing"],
            },
            {
                "id": "testing",
                "name": "Testing & edge cases",
                "description": "Proposes edge cases unprompted and traces inputs through their code.",
                "weight": 15,
                "signals": ["names boundary inputs", "hand-traces an example"],
                "phaseHints": ["debugging"],
            },
            {
                "id": "communication",
                "name": "Communication",
                "description": "Thinks aloud, states assumptions, incorporates pushback without collapsing.",
                "weight": 10,
                "signals": ["narrates while coding", "asks clarifying questions"],
                "phaseHints": ["intro", "coding"],
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

    return factory


# ── conservative wrong-code detectors (only fire on unambiguous anti-patterns;
#    silence otherwise — mirroring incident_code_is_unsafe's "never act unless
#    sure" rule). Each returns (detector, risky_range, probe_utterance). ──────


def _detect_nested_loops(fn: str) -> Callable[[str], bool]:
    """for-inside-for only: that's the unambiguous O(n²) brute-force shape.
    A while inside a for is NOT flagged — it's the optimal amortized
    sliding-window pattern, and challenging it would punish a correct answer."""
    def detect(code: str) -> bool:
        if not code or fn not in code:
            return False  # not this problem's code — never act
        for_indents: list[int] = []
        for ln in code.split("\n"):
            if ln.strip().startswith("for "):
                indent = len(ln) - len(ln.lstrip())
                if any(indent > li for li in for_indents):
                    return True  # for nested inside a for
                for_indents.append(indent)
        return False

    return detect


def _nested_loop_range(code: str) -> tuple[int, int]:
    lines = code.split("\n")
    outer = next((i for i, ln in enumerate(lines) if ln.strip().startswith("for ")), 0)
    inner = next(
        (i for i in range(outer + 1, len(lines)) if lines[i].strip().startswith("for ")),
        min(outer + 1, max(len(lines) - 1, 0)),
    )
    return outer, min(inner + 1, max(len(lines) - 1, 0))


def _detect_linear_scan(fn: str) -> Callable[[str], bool]:
    """Binary search written as a for-loop scan (no halving)."""
    def detect(code: str) -> bool:
        if not code or fn not in code:
            return False
        has_for_scan = bool(re.search(r"^\s*for\s+\w+\s+in\s+", code, re.M))
        has_halving = ("//" in code) or ("while" in code and ("mid" in code or "midpoint" in code))
        return has_for_scan and not has_halving

    return detect


def _scan_range(code: str) -> tuple[int, int]:
    lines = code.split("\n")
    start = next((i for i, ln in enumerate(lines) if re.match(r"\s*for\s+\w+\s+in\s+", ln)), 0)
    return start, min(start + 2, max(len(lines) - 1, 0))


_NESTED_LOOP_PROBE = (
    "I'm selecting your loop structure. Before you go further — what's the time "
    "complexity of this shape on the largest input the constraints allow?"
)
_LINEAR_SCAN_PROBE = (
    "I'm selecting this loop. The input is sorted — what does that buy you that "
    "this scan isn't using?"
)

# Which detector applies to which problem (only where detection is reliable).
_PROBLEM_DETECTORS: dict[str, tuple[Callable[[str], Callable[[str], bool]], Callable[[str], tuple[int, int]], str]] = {
    "two_sum": (_detect_nested_loops, _nested_loop_range, _NESTED_LOOP_PROBE),
    "longest_substring_without_repeating": (_detect_nested_loops, _nested_loop_range, _NESTED_LOOP_PROBE),
    "binary_search": (_detect_linear_scan, _scan_range, _LINEAR_SCAN_PROBE),
}


def _problem_scenario(p: ProblemSpec) -> ScenarioSpec:
    fn = _fn_name(p.function_signature)
    detector_entry = _PROBLEM_DETECTORS.get(p.id)
    code_is_unsafe = detector_entry[0](fn) if detector_entry else None
    risky_range = detector_entry[1] if detector_entry else None
    utterance = detector_entry[2] if detector_entry else ""
    return ScenarioSpec(
        id=f"{PROBLEM_TRACK_PREFIX}{p.id}",
        title=p.title,
        seed_code=_problem_seed_code(p),
        task_prompt=(
            f"{p.title}: {p.description} Constraints: {' '.join(p.constraints[:3])} "
            "Write the solution in the code box and think aloud."
        ),
        language="python",
        lines_for_signal=_problem_lines(p),
        phase_guidance=_problem_guidance(p),
        rubric_factory=_problem_rubric_factory(p),
        hint_ladder=list(p.hints),
        reveal_terms=list(p.reveal_terms),
        code_is_unsafe=code_is_unsafe,
        risky_range=risky_range,
        build_patch=None,        # problems NEVER patch — that would reveal the answer
        patch_is_safe=None,
        action_utterance=utterance,
        problem=p,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

_INCIDENT_SCENARIO = ScenarioSpec(
    id=INCIDENT_TRACK,
    title="Production incident: duplicate charges",
    seed_code=INCIDENT_SEED_CODE,
    task_prompt=INCIDENT_TASK_PROMPT,
    language="python",
    lines_for_signal=INCIDENT_LINES,
    default_line="Keep going — tighten the fix.",
    phase_guidance=INCIDENT_PHASE_GUIDANCE,
    rubric_factory=incident_rubric,
    hint_ladder=(),           # incident keeps the generic help ladder
    reveal_terms=(),          # its fix terms are public in the task prompt
    code_is_unsafe=incident_code_is_unsafe,
    risky_range=incident_risky_range,
    build_patch=incident_patch,
    patch_is_safe=incident_patch_is_safe,
    action_utterance=INCIDENT_PATCH_UTTERANCE,
    problem=None,
)

SCENARIOS: dict[str, ScenarioSpec] = {
    _INCIDENT_SCENARIO.id: _INCIDENT_SCENARIO,
    **{s.id: s for s in (_problem_scenario(p) for p in PROBLEM_CATALOG.values())},
}


def get_scenario(track: str | None) -> Optional[ScenarioSpec]:
    """Resolve a session track to its scenario. None for the default flow."""
    if not track:
        return None
    return SCENARIOS.get(track)


def list_problem_scenarios() -> list[ScenarioSpec]:
    return [s for s in SCENARIOS.values() if s.id.startswith(PROBLEM_TRACK_PREFIX)]


def scenario_for_role(role: str, seniority: str) -> Optional[ScenarioSpec]:
    """Role/seniority-aware pick among problem scenarios (difficulty ladder)."""
    order = {"junior": ("easy",), "mid": ("easy", "medium"), "senior": ("medium", "hard")}
    wanted = order.get((seniority or "mid").lower(), ("easy", "medium"))
    pool = [s for s in list_problem_scenarios() if s.problem and s.problem.difficulty in wanted]
    if not pool:
        pool = list_problem_scenarios()
    return pool[0] if pool else None


__all__ = [
    "PROBLEM_TRACK_PREFIX",
    "SCENARIOS",
    "ScenarioSpec",
    "get_scenario",
    "list_problem_scenarios",
    "scenario_for_role",
]
