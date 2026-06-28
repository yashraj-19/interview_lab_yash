"""vNext interview LLM package.

OpenRouter-first (OpenAI direct fallback) provider abstraction plus the rubric
and adaptive-interviewer generators. NO Anthropic. Everything degrades to the
deterministic scripted seed when no key is configured or the model errors /
returns malformed output — the system must work with ZERO keys.

The PhaseController still owns phase transitions; these helpers only fill
in-phase content behind the existing event contract.
"""
from __future__ import annotations

from .client import LLMError, LLMUnavailable, call_llm
from .interviewer_llm import generate_interviewer_turn
from .jd_llm import generate_jd
from .rubric_llm import generate_rubric_llm, validate_rubric_payload
from .scorecard_llm import (
    build_scorecard_llm,
    build_scripted_scorecard,
    resolve_evidence_ref,
    validate_scorecard_scores,
)

__all__ = [
    "LLMError",
    "LLMUnavailable",
    "call_llm",
    "generate_interviewer_turn",
    "generate_jd",
    "generate_rubric_llm",
    "validate_rubric_payload",
    "build_scorecard_llm",
    "build_scripted_scorecard",
    "resolve_evidence_ref",
    "validate_scorecard_scores",
]
