"""Pluggable IntentClassifier for conversational intent detection.

This module provides a small interface for intent classification. The default
implementation is a lightweight rule-based classifier (for offline/dev use).
A dynamic provider (LLM) can be registered at runtime via
`IntentClassifier.register_provider(callable)`; the callable must accept
(text, session_id) and return an intent string.

Do NOT perform network calls during import; provider calls happen at runtime.
"""
from __future__ import annotations

import re
from typing import Callable, Optional


class IntentClassifier:
    """Pluggable classifier. Use `.classify(text, session_id)` to get intent."""

    def __init__(self) -> None:
        self._provider: Optional[Callable[[str, str], str]] = None
        self._fallback = RuleBasedIntentClassifier()

    def register_provider(self, fn: Callable[[str, str], str]) -> None:
        """Register a dynamic provider callable(text, session_id) -> intent."""
        self._provider = fn

    def unregister_provider(self) -> None:
        self._provider = None

    def classify(self, text: str, session_id: str) -> str:
        # Provider has priority; fall back to rule-based.
        if self._provider is not None:
            try:
                intent = self._provider(text, session_id)
                if isinstance(intent, str) and intent:
                    return intent
            except Exception:
                # provider errors must not crash the flow; fall back
                pass
        return self._fallback.classify(text)

    def is_cut_in(self, text: str) -> bool:
        """Deterministic cut-in check for urgency/cancellation decisions.

        Always rule-based — timing decisions never consult the (possibly slow
        or nondeterministic) provider, mirroring the Voice_Assist rule that no
        LLM sits in a timing path. This lets "wait, I'm stuck" route to the
        'help' ladder while STILL cancelling scheduled interviewer speech.
        """
        return self._fallback.is_cut_in(text)

    def is_backchannel(self, text: str) -> bool:
        """Deterministic backchannel check (same rationale as is_cut_in)."""
        return self._fallback.is_backchannel(text)


class RuleBasedIntentClassifier:
    """Lightweight rule-based fallback. Keep patterns minimal and maintainable.

    This exists only as a safe offline fallback. It intentionally does not
    attempt exhaustive NLP — that's the provider's job.
    """

    def __init__(self) -> None:
        # Backchannel words: common filler sounds that don't require a hint.
        self._backchannel_rx = re.compile(
            r"^\s*(?:hmm|yeah|okay|ok|right|uh-huh|mhm|i see|got it|yep|sure|ah)\s*$",
            re.IGNORECASE,
        )
        # Cut-in words: explicit interrupts that should immediately cancel a scheduled utterance.
        self._cut_in_rx = re.compile(
            r"^\s*(?:wait|stop|hold|hang|sorry|actually|no|hey|pause|excuse)\b",
            re.IGNORECASE,
        )
        self._repeat_rx = re.compile(r"\b(repeat|again|rephrase|say that again|can you repeat)\b", re.IGNORECASE)
        self._help_rx = re.compile(r"\b(stuck|guide me|hint|confused|lost|don't get it|dont get it|need help|not sure)\b", re.IGNORECASE)
        self._thinking_rx = re.compile(r"\b(let me think|give me a sec|give me a second|give me some time|hold on|one moment|thinking|think)\b", re.IGNORECASE)
        self._meta_audio_rx = re.compile(r"\b(hear me|can you hear me|didn't get you|didnt get you|hello|hi|hey)\b", re.IGNORECASE)

    def is_backchannel(self, text: str) -> bool:
        """True if the text is a pure backchannel (no intent to be handled)."""
        return bool(self._backchannel_rx.search(text or ""))

    def is_cut_in(self, text: str) -> bool:
        """True if the text opens with an explicit interrupt word (barge-in signal)."""
        return bool(self._cut_in_rx.search(text or ""))

    def classify(self, text: str) -> str:
        t = (text or "").strip()
        if not t:
            return "answer"
        # Backchannel: don't emit a handler hint for pure fillers.
        if self.is_backchannel(t):
            return "backchannel"
        # Check specific intents BEFORE cut-in (more specific first).
        if self._repeat_rx.search(t):
            return "repeat"
        if self._help_rx.search(t):
            return "help"
        if self._thinking_rx.search(t):
            return "thinking"
        if self._meta_audio_rx.search(t):
            return "meta_audio"
        # Cut-in: urgent interrupt signal (checked last, after specific intents).
        if self.is_cut_in(t):
            return "cut_in"
        return "answer"


# Module singleton for easy import/use in ws.py
_DEFAULT_CLASSIFIER = IntentClassifier()


def get_classifier() -> IntentClassifier:
    return _DEFAULT_CLASSIFIER


__all__ = ["IntentClassifier", "get_classifier"]
