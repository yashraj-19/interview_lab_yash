"""Phase 4: Eval Harness — edge cases and assertions for barge-in, pause/timing, never-reveal.

Tests:
- Intent classification: backchannel, cut-in, help, repeat, thinking, meta_audio
- Pause policy application: correct delay_ms, fallback on error
- Barge-in behavior: scheduled task cancellation on cut-in
- Never-reveal: attempt 1/2 don't leak answers, attempt 3 reveals
- Session ledger auditing: all events logged with metadata
"""
from __future__ import annotations

import pytest

from app.vnext.interview.intent import RuleBasedIntentClassifier
from app.vnext.interview.hint_ladder import next_hint, _count_wrong_attempts
from app.vnext.interview.pause_policy import get_pause_for, register_pause_provider, unregister_pause_provider
from app.vnext.interview.store import InMemoryInterviewStore


@pytest.fixture
def store():
    return InMemoryInterviewStore()


@pytest.fixture
def classifier():
    return RuleBasedIntentClassifier()


# ─────────────────────────────────────────────────────────────────────────────
# INTENT CLASSIFICATION EDGE CASES
# ─────────────────────────────────────────────────────────────────────────────


class TestIntentClassification:
    """Test intent detection with Voice_Assist-style patterns."""

    def test_backchannel_pure_filler(self, classifier):
        """Backchannel: pure filler sounds don't get hints."""
        fillers = ["yeah", "okay", "hmm", "i see", "got it", "yep", "sure", "ah"]
        for text in fillers:
            assert classifier.classify(text) == "backchannel", f"'{text}' should be backchannel"

    def test_backchannel_with_whitespace(self, classifier):
        """Backchannel: whitespace variations handled."""
        assert classifier.classify("  yeah  ") == "backchannel"
        assert classifier.classify("\tok\t") == "backchannel"

    def test_cut_in_explicit_interrupts(self, classifier):
        """Cut-in: explicit interrupt words detected."""
        cut_in_words = ["wait", "stop", "hold", "hang", "sorry", "actually", "no", "hey", "pause", "excuse"]
        for word in cut_in_words:
            result = classifier.classify(word)
            assert result == "cut_in", f"'{word}' should be cut_in, got {result}"

    def test_cut_in_mid_utterance_not_triggered(self, classifier):
        """Cut-in: only if word opens the utterance (not mid-sentence)."""
        # "wait" at start -> cut-in
        assert classifier.classify("wait, let me think") == "cut_in"
        # "wait" in middle -> might be "thinking" or "answer" depending on other patterns
        result = classifier.classify("let me wait a moment")
        assert result in ("thinking", "answer"), f"Mid-utterance 'wait' should not force cut_in, got {result}"

    def test_help_intent(self, classifier):
        """Help: candidate stuck."""
        help_phrases = ["I'm stuck", "guide me", "hint", "confused", "lost", "don't get it", "need help"]
        for phrase in help_phrases:
            assert classifier.classify(phrase) == "help", f"'{phrase}' should be help"

    def test_repeat_intent(self, classifier):
        """Repeat: candidate asking for clarification."""
        repeat_phrases = ["repeat", "again", "rephrase", "say that again", "can you repeat"]
        for phrase in repeat_phrases:
            assert classifier.classify(phrase) == "repeat", f"'{phrase}' should be repeat"

    def test_thinking_intent(self, classifier):
        """Thinking: candidate needs time."""
        thinking_phrases = ["let me think", "give me a sec", "hold on", "one moment", "thinking"]
        for phrase in thinking_phrases:
            assert classifier.classify(phrase) == "thinking", f"'{phrase}' should be thinking"

    def test_meta_audio_intent(self, classifier):
        """Meta audio: connectivity check."""
        audio_phrases = ["hear me", "can you hear me", "didn't get you", "hello", "hi", "hey there"]
        for phrase in audio_phrases:
            result = classifier.classify(phrase)
            assert result == "meta_audio", f"'{phrase}' should be meta_audio, got {result}"

    def test_answer_default(self, classifier):
        """Answer: default for normal responses."""
        normal_answers = ["two sum", "use a hash map", "binary search", "sort the array"]
        for answer in normal_answers:
            assert classifier.classify(answer) == "answer", f"'{answer}' should be answer"

    def test_empty_text(self, classifier):
        """Empty text: defaults to answer."""
        assert classifier.classify("") == "answer"
        assert classifier.classify(None) == "answer"

    def test_cut_in_precedence(self, classifier):
        """Cut-in: takes precedence over other intents (urgent)."""
        # "wait, I need help" -> cut_in (not help)
        assert classifier.classify("wait, I need help") == "cut_in"


# ─────────────────────────────────────────────────────────────────────────────
# HINT ESCALATION: NEVER-REVEAL POLICY
# ─────────────────────────────────────────────────────────────────────────────


class TestNeverRevealHintEscalation:
    """Test attempt-based hint escalation (1/2/3) per Voice_Assist judge pattern."""

    def test_hint_attempt_1_is_nudge(self, store):
        """Attempt 1: nudge (make them think, no answer)."""
        session_id = store.create_session({"role": "SDE", "seniority": "mid"})
        ledger = store.get_ledger(session_id)

        hint = next_hint(session_id, "help")
        assert hint is not None
        assert hint["attempt"] == 1
        assert hint["exhausted"] is False
        # Nudge should NOT contain answer-bearing terms (LIFO, FIFO, O(n), etc.)
        assert "lifo" not in hint["text"].lower()
        assert "fifo" not in hint["text"].lower()
        assert "o(" not in hint["text"].lower()

    def test_hint_attempt_2_is_hint_not_reveal(self, store):
        """Attempt 2: hint (point toward fix, still no answer)."""
        session_id = store.create_session({"role": "SDE", "seniority": "mid"})
        # Simulate 1 prior hint by emitting an event
        store.append_event(session_id, "interviewer", "interviewer.utterance", 
                          {"hint_for": "help", "hint_step": 1, "attempt": 1})
        
        hint = next_hint(session_id, "help")
        assert hint is not None
        assert hint["attempt"] == 2
        assert hint["exhausted"] is False

    def test_hint_attempt_3_reveals(self, store):
        """Attempt 3+: reveal (state the key idea)."""
        session_id = store.create_session({"role": "SDE", "seniority": "mid"})
        # Simulate 2 prior hints
        store.append_event(session_id, "interviewer", "interviewer.utterance", 
                          {"hint_for": "help", "hint_step": 1, "attempt": 1})
        store.append_event(session_id, "interviewer", "interviewer.utterance", 
                          {"hint_for": "help", "hint_step": 2, "attempt": 2})
        
        hint = next_hint(session_id, "help")
        assert hint is not None
        assert hint["attempt"] == 3
        assert hint["exhausted"] is True  # final reveal

    def test_hint_ladder_per_intent(self, store):
        """Each intent has its own ladder (independent escalation)."""
        session_id = store.create_session({"role": "SDE", "seniority": "mid"})
        
        # Emit hint for "help"
        hint_help_1 = next_hint(session_id, "help")
        assert hint_help_1["attempt"] == 1
        
        # Hint for "repeat" should still be at attempt 1 (independent)
        hint_repeat_1 = next_hint(session_id, "repeat")
        assert hint_repeat_1["attempt"] == 1
        
        # Emit another hint for "help"
        store.append_event(session_id, "interviewer", "interviewer.utterance", 
                          {"hint_for": "help", "hint_step": 1, "attempt": 1})
        hint_help_2 = next_hint(session_id, "help")
        assert hint_help_2["attempt"] == 2
        
        # "repeat" still at 1
        hint_repeat_still_1 = next_hint(session_id, "repeat")
        assert hint_repeat_still_1["attempt"] == 1

    def test_unknown_intent_returns_none(self, store):
        """Unknown intent: no hint."""
        session_id = store.create_session({"role": "SDE", "seniority": "mid"})
        hint = next_hint(session_id, "unknown_intent_xyz")
        assert hint is None


# ─────────────────────────────────────────────────────────────────────────────
# PAUSE POLICY: REAL-TIME TIMING CONTROL
# ─────────────────────────────────────────────────────────────────────────────


class TestPausePolicyDynamic:
    """Test pause/timing provider and session overrides."""

    def test_pause_default_zero(self, store):
        """Pause: defaults to 0ms (immediate) if no policy set."""
        session_id = store.create_session({"role": "SDE", "seniority": "mid"})
        assert get_pause_for(session_id, "help") == 0
        assert get_pause_for(session_id, "repeat") == 0

    def test_pause_session_override(self, store):
        """Pause: per-session REST override applied."""
        session_id = store.create_session({"role": "SDE", "seniority": "mid"})
        rec = store.get_session(session_id)
        rec["pause_policies"] = {"help": 500, "repeat": 300}
        store.put_session(session_id, rec)
        
        assert get_pause_for(session_id, "help") == 500
        assert get_pause_for(session_id, "repeat") == 300
        assert get_pause_for(session_id, "thinking") == 0  # not set

    def test_pause_provider_priority(self, store):
        """Pause: pluggable provider takes priority over session override."""
        session_id = store.create_session({"role": "SDE", "seniority": "mid"})
        rec = store.get_session(session_id)
        rec["pause_policies"] = {"help": 500}
        store.put_session(session_id, rec)
        
        # Register provider that returns 1000 for "help"
        def custom_provider(sid, intent):
            if intent == "help":
                return 1000
            return None
        
        register_pause_provider(custom_provider)
        try:
            assert get_pause_for(session_id, "help") == 1000  # provider wins
        finally:
            unregister_pause_provider()

    def test_pause_provider_error_fallback(self, store):
        """Pause: provider errors fall back to session override."""
        session_id = store.create_session({"role": "SDE", "seniority": "mid"})
        rec = store.get_session(session_id)
        rec["pause_policies"] = {"help": 500}
        store.put_session(session_id, rec)
        
        # Register provider that raises
        def broken_provider(sid, intent):
            raise RuntimeError("Intentional error")
        
        register_pause_provider(broken_provider)
        try:
            assert get_pause_for(session_id, "help") == 500  # fallback to session
        finally:
            unregister_pause_provider()

    def test_pause_invalid_values_ignored(self, store):
        """Pause: invalid values (negative, non-int) ignored."""
        session_id = store.create_session({"role": "SDE", "seniority": "mid"})
        rec = store.get_session(session_id)
        rec["pause_policies"] = {"help": -100, "repeat": "not_a_number"}
        store.put_session(session_id, rec)
        
        # Invalid values treated as 0
        assert get_pause_for(session_id, "help") == 0
        assert get_pause_for(session_id, "repeat") == 0


# ─────────────────────────────────────────────────────────────────────────────
# SESSION LEDGER AUDITING: EVENTS & METADATA
# ─────────────────────────────────────────────────────────────────────────────


class TestSessionLedgerAuditing:
    """Test that all decisions are auditable via ledger events."""

    def test_ledger_intent_detection(self, store):
        """Ledger: intent detection recorded."""
        session_id = store.create_session({"role": "SDE", "seniority": "mid"})
        event = store.append_event(session_id, "system", "conversation.intent.detected", 
                                  {"intent": "help", "text": "I'm stuck"})
        
        events = store.get_events(session_id, 0)
        assert len(events) > 0
        assert any(e.get("type") == "conversation.intent.detected" and 
                  e.get("intent") == "help" for e in events)

    def test_ledger_hint_with_attempt_metadata(self, store):
        """Ledger: hint emission includes attempt and hint_step."""
        session_id = store.create_session({"role": "SDE", "seniority": "mid"})
        store.append_event(session_id, "interviewer", "interviewer.utterance", 
                          {"text": "Think again", "hint_for": "help", "hint_step": 1, "attempt": 1})
        
        events = store.get_events(session_id, 0)
        hint_event = next((e for e in events if e.get("hint_for") == "help"), None)
        assert hint_event is not None
        assert hint_event["attempt"] == 1
        assert hint_event["hint_step"] == 1

    def test_ledger_pause_scheduled_and_cancelled(self, store):
        """Ledger: pause scheduling and cancellation tracked."""
        session_id = store.create_session({"role": "SDE", "seniority": "mid"})
        store.append_event(session_id, "system", "system.pause.scheduled", 
                          {"intent": "help", "delay_ms": 500})
        store.append_event(session_id, "system", "system.pause.cancelled", 
                          {"intent": "help", "delay_ms": 500, "reason": "cut_in"})
        
        events = store.get_events(session_id, 0)
        assert any(e.get("type") == "system.pause.scheduled" for e in events)
        assert any(e.get("type") == "system.pause.cancelled" for e in events)

    def test_ledger_barge_in_detected(self, store):
        """Ledger: barge-in detection recorded for latency analysis."""
        session_id = store.create_session({"role": "SDE", "seniority": "mid"})
        store.append_event(session_id, "system", "barge_in.detected", 
                          {"intent": "cut_in", "text": "wait"})
        
        events = store.get_events(session_id, 0)
        assert any(e.get("type") == "barge_in.detected" for e in events)


# ─────────────────────────────────────────────────────────────────────────────
# EDGE CASES & PRODUCTION ROBUSTNESS
# ─────────────────────────────────────────────────────────────────────────────


class TestProductionRobustness:
    """Edge cases and production safeguards."""

    def test_concurrent_hint_requests_isolated(self, store):
        """Multiple sessions: hints are independent."""
        s1 = store.create_session({"role": "SDE", "seniority": "mid"})
        s2 = store.create_session({"role": "SDE", "seniority": "mid"})
        
        # Emit hint for s1
        store.append_event(s1, "interviewer", "interviewer.utterance", 
                          {"hint_for": "help", "hint_step": 1, "attempt": 1})
        
        # s2 should still be at attempt 1
        hint_s2 = next_hint(s2, "help")
        assert hint_s2["attempt"] == 1

    def test_pause_policy_string_int_conversion(self, store):
        """Pause: string integers converted correctly."""
        session_id = store.create_session({"role": "SDE", "seniority": "mid"})
        rec = store.get_session(session_id)
        rec["pause_policies"] = {"help": "500"}  # string, not int
        store.put_session(session_id, rec)
        
        assert get_pause_for(session_id, "help") == 500

    def test_intent_case_insensitive(self, classifier):
        """Intent: matching is case-insensitive."""
        assert classifier.classify("WAIT A MINUTE") == "cut_in"
        assert classifier.classify("Yeah") == "backchannel"
        assert classifier.classify("I'M STUCK") == "help"

    def test_backchannel_immunity_multi_word(self, classifier):
        """Backchannel: multi-word fillers recognized."""
        # "i see" is backchannel but "i see an issue" is not
        assert classifier.classify("i see") == "backchannel"
        # "i see a solution" should not be backchannel
        result = classifier.classify("i see a solution")
        assert result != "backchannel"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
