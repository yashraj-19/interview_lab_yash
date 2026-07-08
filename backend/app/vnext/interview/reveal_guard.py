"""Output-side guard for interviewer lines — never confirm, never praise,
never reveal. Pure functions, no I/O.

The persona rules ("no generic praise", "never state correctness") existed
only as prompt text in ``llm/interviewer_llm.py`` — nothing checked what the
model actually returned, so one "That's exactly right!" leak instantly broke
the rigorous-interviewer persona. This module enforces them in CODE, the way
the deployed Voice_Assist RevealGuard does (its design notes apply here too):

- The guard cannot know whether the candidate was right or wrong, so
  replacements must be NEUTRAL probes — never a wrongness assertion.
- The hint ladder is the pushback lane and is exempt; only LLM-generated
  interviewer turns are scanned (deterministic scripted lines are curated).
- Utterances here are emitted whole (not streamed), so the scan is a simple
  sentence filter: drop offending sentences, keep the rest; if nothing
  survives, substitute a rotating neutral probe (deterministic by seq).

``reveal_terms`` is scenario-supplied (which answer words must not be spoken
before the final hint attempt). The incident track publishes its key terms in
the task prompt itself, so it passes none; problem-spec scenarios supply
theirs when they wire in (e.g. "hash map" for two-sum).
"""
from __future__ import annotations

import re
from typing import Iterable, Sequence

# Verdict statements — the interviewer confirming or denying correctness.
# Ported from Voice_Assist judge.py (_CONFIRM_DENY_RE) and widened with the
# leaks observed in its live transcripts ("That's generally true").
_CONFIRM_DENY_RE = re.compile(
    r"(?:\bthat(?:'s| is)\s+(?:absolutely\s+|generally\s+|basically\s+|exactly\s+)?"
    r"(?:right|correct|true|wrong|incorrect|it)\b)"
    r"|(?:\byou(?:'re| are)\s+(?:right|correct|wrong|incorrect|spot on)\b)"
    r"|(?:\bexactly\s+right\b)|(?:\bspot\s+on\b)|(?:\bnot\s+(?:quite|right|correct)\b)"
    r"|(?:\byou\s+(?:got|nailed)\s+it\b)|(?:\bcorrect!\B)|(?:\bincorrect\b)"
    r"|(?:\bthat'?s\s+(?:the\s+)?(?:right|correct)\s+(?:answer|idea|approach)\b)",
    re.IGNORECASE,
)

# Generic praise — mirrors _FORBIDDEN_PRAISE in llm/interviewer_llm.py (kept
# in sync by test_reveal_guard) plus common variants.
_PRAISE_RE = re.compile(
    r"(?:\bthat'?s\s+great\b)|(?:\bgreat\s+to\s+hear\b)|(?:\bsolid\s+approach\b)"
    r"|(?:\bawesome\b)|(?:\bperfect\b)|(?:\bexcellent\b)|(?:\bwell\s+done\b)"
    r"|(?:\bgood\s+job\b)|(?:\bgreat\s+job\b)|(?:\bnice\s+work\b)|(?:\bimpressive\b)",
    re.IGNORECASE,
)

# Neutral probes — deliberately verdict-free (the guard can't tell right from
# wrong, so these must read the same either way). Rotation avoids the robotic
# feel of one fixed replacement line.
NEUTRAL_PROBES: tuple[str, ...] = (
    "Walk me through your reasoning, step by step.",
    "What makes you confident in that? Talk it through.",
    "Take me through it — what happens in the failing case?",
    "Unpack that a bit — how does it behave under two concurrent retries?",
)


def confirms_or_denies(text: str) -> bool:
    """True when the text states a correctness verdict (either direction)."""
    return bool(_CONFIRM_DENY_RE.search(text or ""))


def contains_praise(text: str) -> bool:
    """True when the text contains generic praise the persona forbids."""
    return bool(_PRAISE_RE.search(text or ""))


def reveals_term(text: str, reveal_terms: Iterable[str]) -> bool:
    """True when the text contains a scenario answer term (case-insensitive)."""
    low = (text or "").lower()
    return any(term.lower() in low for term in reveal_terms if term)


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.?!])\s+", (text or "").strip())
    return [p for p in parts if p]


def neutral_probe(seq: int) -> str:
    """Deterministic rotating probe (seeded by ledger seq for replayability)."""
    return NEUTRAL_PROBES[abs(int(seq)) % len(NEUTRAL_PROBES)]


def guard_interviewer_line(
    text: str,
    *,
    seq: int = 0,
    attempt: int = 1,
    reveal_terms: Sequence[str] = (),
) -> tuple[str, list[str]]:
    """Scan an LLM interviewer line before it reaches the ledger.

    Returns ``(safe_text, reasons)`` — ``reasons`` is empty when the line was
    clean (text returned verbatim). Offending sentences are dropped; when
    nothing survives, a rotating neutral probe replaces the whole line.
    Reveal terms are only blocked before the final hint attempt (>= 3 may
    reveal, matching the hint-ladder contract).
    """
    reasons: list[str] = []
    kept: list[str] = []
    for sentence in _split_sentences(text):
        if confirms_or_denies(sentence):
            reasons.append("confirm_deny")
            continue
        if contains_praise(sentence):
            reasons.append("praise")
            continue
        if attempt < 3 and reveal_terms and reveals_term(sentence, reveal_terms):
            reasons.append("reveal_term")
            continue
        kept.append(sentence)

    if not reasons:
        return text, []
    safe = " ".join(kept).strip()
    if not safe:
        safe = neutral_probe(seq)
    # De-duplicate reasons, preserving first-seen order.
    seen: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.append(r)
    return safe, seen


__all__ = [
    "NEUTRAL_PROBES",
    "confirms_or_denies",
    "contains_praise",
    "reveals_term",
    "neutral_probe",
    "guard_interviewer_line",
]
