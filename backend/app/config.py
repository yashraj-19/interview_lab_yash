"""Minimal settings for the standalone interview lab.

Every field is optional so the backend boots with NO configuration at all — it
runs an in-memory store and falls back to deterministic scripted interviewer
lines when no LLM key is present. Set OPENROUTER_API_KEY to use a live model.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM (optional — without a key the lab uses deterministic scripted lines).
    openrouter_api_key: str = ""
    openai_api_key: str = ""

    # Supabase (optional — the lab defaults to an in-memory store, VNEXT_STORE=memory).
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
