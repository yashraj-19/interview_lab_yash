"""Role tracks + interviewer personas — the role changes BEHAVIOR, not a label.

Modeled on the production landing repo's ROLE_TRACKS/PERSONAS (question_bank.py,
interview_ai.py), distilled for the lab:

- A role track carries competency WEIGHTS that reshape the scenario rubric
  deterministically (the scorecard already pins weights to the rubric and
  recomputes the overall server-side, so weights are enforced, not advisory),
  plus a difficulty band for problem auto-selection.
- A persona is a tone contract prepended to every interviewer prompt. The
  anti-sycophancy rules are the distilled "feels like a real interviewer"
  core: no exclamations, no praise words, calm acknowledgments, redirect
  don't rescue.
"""
from __future__ import annotations

import re
from typing import Optional

# Criterion ids here MUST match the problem-scenario rubric factory's ids.
ROLE_TRACKS: dict[str, dict] = {
    "software_engineer": {
        "label": "Software Engineer",
        "competency_weights": {
            "correctness": 30, "approach": 25, "complexity": 20,
            "testing": 15, "communication": 10,
        },
        "difficulty_band": {"junior": ("easy",), "mid": ("easy", "medium"), "senior": ("medium", "hard")},
    },
    "sde_intern": {
        "label": "SDE Intern",
        # Interns: reasoning + communication matter more than optimal bounds.
        "competency_weights": {
            "correctness": 30, "approach": 20, "complexity": 10,
            "testing": 15, "communication": 25,
        },
        "difficulty_band": {"junior": ("easy",), "mid": ("easy",), "senior": ("easy", "medium")},
    },
    "ml_engineer": {
        "label": "ML Engineer",
        # ML engineers: algorithmic reasoning + complexity dominate.
        "competency_weights": {
            "correctness": 25, "approach": 30, "complexity": 25,
            "testing": 10, "communication": 10,
        },
        "difficulty_band": {"junior": ("easy",), "mid": ("easy", "medium"), "senior": ("medium", "hard")},
    },
}

_ROLE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bintern\b", "sde_intern"),
    (r"\b(ml|machine\s*learning|data\s*scien)\w*", "ml_engineer"),
)


def normalize_role(role: str | None) -> str:
    """Map a free-text intake role to a role-track key (default SWE)."""
    low = (role or "").lower()
    for rx, key in _ROLE_PATTERNS:
        if re.search(rx, low):
            return key
    return "software_engineer"


def role_weights(role: str | None) -> dict[str, int]:
    return dict(ROLE_TRACKS[normalize_role(role)]["competency_weights"])


def difficulty_band(role: str | None, seniority: str | None) -> tuple[str, ...]:
    track = ROLE_TRACKS[normalize_role(role)]
    return track["difficulty_band"].get((seniority or "mid").lower(), ("easy", "medium"))


# ── personas ──────────────────────────────────────────────────────────────────

_TONE_RULES = (
    "TONE RULES (hard): no exclamation marks; never use praise words (great, "
    "awesome, perfect, excellent, impressive); acknowledge with calm neutral "
    "phrases ('Okay.', 'Noted.', 'Go on.'); when the candidate struggles, "
    "redirect with a sharper question — do NOT rescue them with the answer."
)

PERSONAS: dict[str, dict] = {
    "collaborative": {
        "name": "Maya",
        "style": "warm but rigorous — thinks WITH the candidate, never for them",
        "prompt_addition": (
            "PERSONA: You are Maya — collaborative and calm. Frame the session "
            "as solving the problem together ('let's', 'walk me through'), keep "
            "the pace unhurried, and give the candidate room to think aloud. "
            + _TONE_RULES
        ),
    },
    "rigorous": {
        "name": "Maya",
        "style": "senior bar-raiser — terse, precise, pressure-tests every claim",
        "prompt_addition": (
            "PERSONA: You are Maya — a terse senior bar-raiser. Every vague "
            "claim gets pressure-tested immediately ('prove it', 'what breaks "
            "it?'). Short sentences. No softening preamble. "
            + _TONE_RULES
        ),
    },
}


def pick_persona(persona: str | None, seniority: str | None) -> dict:
    """Explicit persona wins; otherwise seniors get the bar-raiser."""
    if persona in PERSONAS:
        return PERSONAS[persona]
    return PERSONAS["rigorous" if (seniority or "").lower() in ("senior", "staff") else "collaborative"]


__all__ = [
    "PERSONAS",
    "ROLE_TRACKS",
    "difficulty_band",
    "normalize_role",
    "pick_persona",
    "role_weights",
]
