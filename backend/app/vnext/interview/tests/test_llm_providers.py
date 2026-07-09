"""Provider chain: OpenRouter → Groq → Gemini → OpenAI, with key hygiene.

The failover exists because free tiers rate-limit (the Voice_Assist lesson:
Groq's daily cap and Gemini's RPM both bit in production) — one provider's
429 must roll to the next mid-interview, and a pasted key's stray whitespace
must never corrupt an auth header.
"""
from __future__ import annotations

import json

import pytest

import app.vnext.interview.llm.client as client
from app.config import settings
from app.vnext.interview.llm.client import LLMUnavailable, call_llm


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch):
    for field in ("openrouter_api_key", "groq_api_key", "gemini_api_key", "openai_api_key"):
        monkeypatch.setattr(settings, field, "", raising=False)


def _capture_chat(calls: list, responses: dict[str, str] | None = None, fail: set[str] | None = None):
    async def fake_chat(url, headers, body, timeout):
        calls.append((url, headers.get("Authorization", ""), body.get("model")))
        for name, marker in (("openrouter", "openrouter.ai"), ("groq", "groq.com"),
                             ("gemini", "googleapis.com"), ("openai", "api.openai.com")):
            if marker in url:
                if fail and name in fail:
                    raise RuntimeError(f"{name}_rate_limited")
                return (responses or {}).get(name, f"{name}-reply")
        raise AssertionError(f"unknown provider url {url}")
    return fake_chat


@pytest.mark.anyio
async def test_provider_order_and_failover(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "gsk_x", raising=False)
    monkeypatch.setattr(settings, "gemini_api_key", "AIza_y", raising=False)
    calls: list = []
    # Groq rate-limits -> Gemini answers.
    monkeypatch.setattr(client, "_chat_completion", _capture_chat(calls, fail={"groq"}))
    out = await call_llm([{"role": "user", "content": "hi"}])
    assert out == "gemini-reply"
    assert "groq.com" in calls[0][0] and "googleapis.com" in calls[1][0]


@pytest.mark.anyio
async def test_gemini_uses_openai_compatible_endpoint_and_model(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "AIza_y", raising=False)
    calls: list = []
    monkeypatch.setattr(client, "_chat_completion", _capture_chat(calls))
    await call_llm([{"role": "user", "content": "hi"}])
    url, auth, model = calls[0]
    assert url.endswith("/v1beta/openai/chat/completions")
    assert auth == "Bearer AIza_y"
    assert model.startswith("gemini-")


@pytest.mark.anyio
async def test_keys_are_stripped_before_headers(monkeypatch):
    # The exact production bug: a trailing newline in a deploy env var makes
    # an illegal header and silently kills the provider.
    monkeypatch.setattr(settings, "groq_api_key", "  gsk_x\n", raising=False)
    calls: list = []
    monkeypatch.setattr(client, "_chat_completion", _capture_chat(calls))
    await call_llm([{"role": "user", "content": "hi"}])
    assert calls[0][1] == "Bearer gsk_x"


@pytest.mark.anyio
async def test_split_brain_interviewer_prefers_groq(monkeypatch):
    """The Voice_Assist split: the latency-critical interviewer runs on Groq
    even when other providers sit earlier in the base chain."""
    monkeypatch.setattr(settings, "openrouter_api_key", "or-key", raising=False)
    monkeypatch.setattr(settings, "groq_api_key", "gsk_x", raising=False)
    monkeypatch.setattr(settings, "gemini_api_key", "AIza_y", raising=False)
    calls: list = []
    monkeypatch.setattr(client, "_chat_completion", _capture_chat(calls))
    await call_llm([{"role": "user", "content": "hi"}], role="interviewer")
    assert "groq.com" in calls[0][0]


@pytest.mark.anyio
async def test_split_brain_scorecard_prefers_gemini_with_failover(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "gsk_x", raising=False)
    monkeypatch.setattr(settings, "gemini_api_key", "AIza_y", raising=False)
    calls: list = []
    # Gemini quota-walled (429) -> scorecard still completes on Groq.
    monkeypatch.setattr(client, "_chat_completion", _capture_chat(calls, fail={"gemini"}))
    out = await call_llm([{"role": "user", "content": "hi"}], role="scorecard")
    assert "googleapis.com" in calls[0][0]  # preferred first
    assert out == "groq-reply"              # failover saved the call


@pytest.mark.anyio
async def test_role_preference_env_override(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "gsk_x", raising=False)
    monkeypatch.setattr(settings, "gemini_api_key", "AIza_y", raising=False)
    monkeypatch.setenv("LLM_PREFER_SCORECARD", "groq")
    calls: list = []
    monkeypatch.setattr(client, "_chat_completion", _capture_chat(calls))
    await call_llm([{"role": "user", "content": "hi"}], role="scorecard")
    assert "groq.com" in calls[0][0]


@pytest.mark.anyio
async def test_no_keys_raises_unavailable():
    with pytest.raises(LLMUnavailable):
        await call_llm([{"role": "user", "content": "hi"}])


@pytest.mark.anyio
async def test_whitespace_only_key_counts_as_missing(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "   \n", raising=False)
    with pytest.raises(LLMUnavailable):
        await call_llm([{"role": "user", "content": "hi"}])
