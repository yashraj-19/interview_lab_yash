"""Tolerant JSON extraction from an LLM completion.

Models often wrap JSON in ``` fences or add prose. We extract the first
balanced JSON object/array and parse it. Returns None on any failure — callers
treat None as malformed and fall back to the deterministic seed.
"""
from __future__ import annotations

import json
from typing import Any, Optional


def extract_json(text: str) -> Optional[Any]:
    if not isinstance(text, str):
        return None
    s = text.strip()

    # Strip a leading ```json / ``` fence if present.
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.lstrip("`")
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()

    # Fast path: the whole thing is JSON.
    try:
        return json.loads(s)
    except Exception:
        pass

    # Slow path: find the first balanced {...} or [...] span.
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = s.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(s)):
            c = s[i]
            if c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start : i + 1])
                    except Exception:
                        break
    return None
