"""LLM job-description generation for the vNext interview intake.

``generate_jd(role, seniority, languages)`` produces a concise, role-relevant job
description used to seed the interview (rubric + interviewer probes). It mirrors
the other LLM modules: OpenRouter-first via ``call_llm`` (OpenAI direct
fallback), tolerant parsing, and a DETERMINISTIC template fallback when no
provider is configured, the provider errors, or the TEST-ONLY ``fake_llm`` flag
is set. The system must work with ZERO keys.
"""
from __future__ import annotations

from .client import LLMUnavailable, call_llm

_MIN_LEN = 80  # below this we treat the model output as unusable -> fallback


def _fallback_jd(role: str, seniority: str, languages: list[str]) -> str:
    """Deterministic, role-relevant JD template (no provider needed)."""
    role = (role or "Software Engineer").strip()
    seniority = (seniority or "mid").strip()
    langs = ", ".join([l for l in (languages or []) if str(l).strip()]) or "your primary stack"
    return (
        f"{seniority.capitalize()} {role}\n\n"
        f"We are hiring a {seniority} {role} to design, build, and operate "
        f"production systems. You will own features end to end — data modeling, "
        f"API design, implementation in {langs}, testing, and rollout — and be "
        f"accountable for correctness, performance, and reliability at scale.\n\n"
        "Responsibilities:\n"
        f"- Design schemas, APIs, and services and implement them in {langs}.\n"
        "- Reason about concurrency, idempotency, failure modes, and edge cases.\n"
        "- Debug production incidents and drive root-cause fixes.\n"
        "- Make and defend tradeoffs (datastore choice, complexity, latency).\n"
        "- Collaborate cross-functionally and own decisions under ambiguity.\n\n"
        "Requirements:\n"
        f"- Strong fundamentals in data structures, algorithms, and {langs}.\n"
        "- Experience designing and operating scalable backend systems.\n"
        "- Clear written and verbal technical communication."
    )


def _build_messages(role: str, seniority: str, languages: list[str]) -> list[dict]:
    langs = ", ".join([l for l in (languages or []) if str(l).strip()]) or "(unspecified)"
    system = (
        "You are an expert technical recruiter and engineering manager. Write a "
        "concise, realistic job description for the given role. Plain text only "
        "(no markdown fences, no JSON). Cover: a one-line title, a short summary, "
        "concrete technical responsibilities, and requirements. Emphasize "
        "system/API/schema design, coding, debugging, concurrency/scale, and "
        "tradeoffs — not soft fluff. Keep it under ~250 words."
    )
    user = (
        f"Role: {role}\n"
        f"Seniority: {seniority}\n"
        f"Languages/stack: {langs}\n\n"
        "Write the job description now."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def generate_jd(
    role: str,
    seniority: str = "mid",
    languages: list[str] | None = None,
    *,
    fake_llm: bool = False,
) -> str:
    """Return a non-empty, role-relevant job description.

    Falls back to the deterministic template on ``fake_llm``, no provider, error,
    or unusably short model output.
    """
    languages = languages or []
    if fake_llm:
        return _fallback_jd(role, seniority, languages)
    try:
        content = await call_llm(
            _build_messages(role, seniority, languages),
            role="jd",
            temperature=0.4,
            max_tokens=600,
        )
    except LLMUnavailable:
        return _fallback_jd(role, seniority, languages)
    except Exception:
        return _fallback_jd(role, seniority, languages)

    text = (content or "").strip()
    # Strip an accidental code fence.
    if text.startswith("```"):
        text = text.strip("`").strip()
    if len(text) < _MIN_LEN:
        return _fallback_jd(role, seniority, languages)
    return text
