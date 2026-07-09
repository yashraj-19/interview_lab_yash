"""Provider abstraction for the vNext interview LLM path.

OpenRouter FIRST, OpenAI direct as the ONLY fallback. No Anthropic, ever. The
call shape (endpoint, headers, httpx timeout, error handling) mirrors
``app/services/interview_ai.py:_call_claude`` — but this module never imports or
mutates that file.

Hard rules:
  - Never crash on import when keys/env are missing. Keys and model slugs are
    resolved LAZILY at call time.
  - If no provider is configured, or every configured provider errors, raise a
    typed ``LLMUnavailable`` so the CALLER decides the deterministic fallback.

Models are read from env with sane, widely-available defaults:
  - OPENROUTER_MODEL  (default ``openai/gpt-4o-mini``)
  - OPENAI_MODEL      (default ``gpt-4o-mini``)
Optional OpenRouter attribution headers:
  - OPENROUTER_HTTP_REFERER  -> HTTP-Referer
  - OPENROUTER_X_TITLE       -> X-Title
"""
from __future__ import annotations

import os

import httpx

from app.config import settings

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Google's OpenAI-compatible surface — same request/response shape as the rest.
_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"

_DEFAULT_OPENROUTER_MODEL = "openai/gpt-4o-mini"
_DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
# 2.0-flash has the most generous free-tier quotas of the current Gemini line.
_DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"
_DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


def _clean(key: str) -> str:
    """Strip whitespace from keys. A trailing newline in a Render env var once
    produced 'Illegal header value b\"Bearer …\\n\"' and silently disabled the
    Voice_Assist judge — never trust a pasted key's whitespace."""
    return (key or "").strip()


# Split-brain routing (the Voice_Assist interviewer/judge lesson): different
# call sites have different needs, so each role PREFERS a provider — the rest
# of the chain stays as fallback. Interviewer turns are latency-critical on
# every exchange → Groq (fast inference). Scorecard/rubric/JD run once per
# session and aren't latency-bound → Gemini, which also spreads quota so a
# long interview can't burn one provider's daily cap alone.
# Override per deployment with LLM_PREFER_<ROLE>=<provider> (e.g.
# LLM_PREFER_INTERVIEWER=openrouter).
_DEFAULT_ROLE_PREFERENCE = {
    "interviewer": "groq",
    "scorecard": "gemini",
    "rubric": "gemini",
    "jd": "gemini",
}


def _preferred_provider(role: str) -> str:
    env = os.getenv(f"LLM_PREFER_{(role or '').upper()}", "").strip().lower()
    return env or _DEFAULT_ROLE_PREFERENCE.get(role, "")


class LLMError(Exception):
    """Base error for the vNext LLM path."""


class LLMUnavailable(LLMError):
    """No provider is configured, or every configured provider failed.

    Carries the per-provider error detail so the caller can log it; the caller
    is expected to fall back to the deterministic scripted seed.
    """


def openrouter_model() -> str:
    return os.getenv("OPENROUTER_MODEL", _DEFAULT_OPENROUTER_MODEL)


def groq_model() -> str:
    return os.getenv("GROQ_MODEL", _DEFAULT_GROQ_MODEL)


def gemini_model() -> str:
    return os.getenv("GEMINI_MODEL", _DEFAULT_GEMINI_MODEL)


def openai_model() -> str:
    return os.getenv("OPENAI_MODEL", _DEFAULT_OPENAI_MODEL)


def _openrouter_headers() -> dict:
    headers = {
        "Authorization": f"Bearer {_clean(settings.openrouter_api_key)}",
        "Content-Type": "application/json",
    }
    referer = os.getenv("OPENROUTER_HTTP_REFERER")
    title = os.getenv("OPENROUTER_X_TITLE")
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-Title"] = title
    return headers


async def _chat_completion(url: str, headers: dict, body: dict, timeout: float) -> str:
    """POST an OpenAI-compatible chat/completions request and return content.

    Isolated so unit tests can monkeypatch it with a fake response and never
    touch the network.
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


def _messages_with_system(messages: list[dict]) -> list[dict]:
    # Both providers are OpenAI-compatible; system goes in the messages list.
    return list(messages)


async def call_llm(
    messages: list[dict],
    *,
    model: str | None = None,
    role: str = "interviewer",
    temperature: float = 0.3,
    timeout: float = 30.0,
    max_tokens: int = 1024,
) -> str:
    """Try OpenRouter, then Groq, then OpenAI. Return raw assistant content.

    ``role`` is an opaque tag for logging/telemetry only. ``model`` overrides the
    default for whichever provider is used (caller rarely needs it).

    Raises ``LLMUnavailable`` if no provider is configured or all fail.
    """
    msgs = _messages_with_system(messages)
    attempts: list[tuple[str, str, dict, str]] = []

    if _clean(settings.openrouter_api_key):
        attempts.append(
            ("openrouter", _OPENROUTER_URL, _openrouter_headers(), model or openrouter_model())
        )
    if _clean(settings.groq_api_key):
        # Groq speaks the OpenAI chat/completions schema at its own base URL.
        attempts.append(
            (
                "groq",
                _GROQ_URL,
                {
                    "Authorization": f"Bearer {_clean(settings.groq_api_key)}",
                    "Content-Type": "application/json",
                },
                model or groq_model(),
            )
        )
    if _clean(settings.gemini_api_key):
        # Gemini via Google's OpenAI-compatible endpoint. Sits after Groq so a
        # Groq free-tier 429 fails over here automatically mid-interview (the
        # Voice_Assist lesson: both free tiers have limits — never depend on one).
        attempts.append(
            (
                "gemini",
                _GEMINI_URL,
                {
                    "Authorization": f"Bearer {_clean(settings.gemini_api_key)}",
                    "Content-Type": "application/json",
                },
                model or gemini_model(),
            )
        )
    if _clean(settings.openai_api_key):
        attempts.append(
            (
                "openai",
                _OPENAI_URL,
                {
                    "Authorization": f"Bearer {_clean(settings.openai_api_key)}",
                    "Content-Type": "application/json",
                },
                model or openai_model(),
            )
        )

    if not attempts:
        raise LLMUnavailable("no_provider_configured")

    # Stable reorder: the role's preferred provider (if configured) goes first;
    # everything else keeps its place as the failover chain.
    pref = _preferred_provider(role)
    if pref:
        attempts.sort(key=lambda a: a[0] != pref)

    errors: list[str] = []
    for name, url, headers, mdl in attempts:
        body = {
            "model": mdl,
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            content = await _chat_completion(url, headers, body, timeout)
            if content is None or not str(content).strip():
                raise LLMError("empty_content")
            return content
        except Exception as exc:  # noqa: BLE001 — try the next provider
            errors.append(f"{name}: {type(exc).__name__}: {exc}")

    raise LLMUnavailable("; ".join(errors) or "all_providers_failed")
