"""Server-side echo backstop — the last line of defense against Maya hearing
herself.

The client already shields echo (fuzzy matching + interrupt-or-discard), but a
stale tab running an old bundle, or any future client bug, can still forward
her own speaker output as candidate text — observed live: her line
"…sometimes creates duplicate charges…" arrived as candidate text
"sometimes create" and she answered herself. The SERVER knows every line she
spoke (the ledger), so it can refuse echo regardless of client version.

Mirror of the client's word-level fuzzy-ORDERED match (voice.ts): echo
preserves her word order (garbled echo scores 0.75–1.0); genuine answers that
merely reuse her vocabulary reorder it (≤0.5). Pure functions, no I/O.
"""
from __future__ import annotations

import re
import time
from typing import Sequence

# Only lines she spoke RECENTLY can echo — a candidate legitimately reusing
# her phrasing a minute later must never be dropped. TTS playback of a long
# line plus the recognizer's lag stays well inside this window.
ECHO_WINDOW_MS = 20_000

_ECHO_THRESHOLD = 0.6
_WORD_SIM_THRESHOLD = 0.6


def _norm_words(text: str) -> list[str]:
    cleaned = re.sub(r"[^a-z0-9]", " ", (text or "").lower())
    return [w for w in cleaned.split() if len(w) >= 2]


def _bigrams(word: str) -> dict[str, int]:
    grams: dict[str, int] = {}
    for i in range(len(word) - 1):
        g = word[i : i + 2]
        grams[g] = grams.get(g, 0) + 1
    return grams


def _word_similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    ga, gb = _bigrams(a), _bigrams(b)
    size_a, size_b = sum(ga.values()), sum(gb.values())
    if not size_a or not size_b:
        return 0.0
    inter = sum(min(n, gb.get(g, 0)) for g, n in ga.items())
    return 2 * inter / (size_a + size_b)


def ordered_echo_score(recognized: str, spoken: str) -> float:
    """Fraction of heard words appearing in the spoken line IN ORDER, allowing
    per-word garble ("inspectacled" ≈ "inspect"). 0..1."""
    heard = _norm_words(recognized)
    src = _norm_words(spoken)
    if not heard or not src:
        return 0.0
    prev = [0] * (len(src) + 1)
    for i in range(1, len(heard) + 1):
        cur = [0] * (len(src) + 1)
        for j in range(1, len(src) + 1):
            if heard[i - 1] == src[j - 1] or _word_similarity(heard[i - 1], src[j - 1]) >= _WORD_SIM_THRESHOLD:
                cur[j] = prev[j - 1] + 1
            else:
                cur[j] = max(prev[j], cur[j - 1])
        prev = cur
    return prev[len(src)] / len(heard)


def is_probable_echo(
    candidate_text: str,
    events: Sequence[dict],
    *,
    now_ms: int | None = None,
) -> bool:
    """True when candidate text is (garbled) echo of a RECENT interviewer line.

    Checks the last few interviewer utterances within ECHO_WINDOW_MS. Very
    short blips (<8 chars) are ignored — they can't be judged reliably and a
    quick real interjection must never be eaten.
    """
    text = (candidate_text or "").strip()
    if len(text) < 8:
        return False
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    checked = 0
    for e in reversed(events):
        if e.get("type") != "interviewer.utterance":
            continue
        ts = e.get("ts")
        if isinstance(ts, (int, float)) and now - ts > ECHO_WINDOW_MS:
            break  # older lines can't be echo
        line = str(e.get("text", ""))
        if line and ordered_echo_score(text, line) >= _ECHO_THRESHOLD:
            return True
        checked += 1
        if checked >= 3:
            break
    return False


__all__ = ["ECHO_WINDOW_MS", "is_probable_echo", "ordered_echo_score"]
