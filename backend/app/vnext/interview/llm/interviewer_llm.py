"""Adaptive interviewer turn generation for the live-llm mode.

Given the intake, rubric, the controller-authorized current phase, and a short
transcript summary, produce ONE interviewer utterance. The model MAY also return
a SUGGESTED advance signal — but it is advisory only: the PhaseController owns
every transition and only it mints ``phase.changed``.

The interviewer is TECHNICAL and ADVERSARIAL by design: it probes for concrete
artifacts (schema/API/pseudocode/complexity), challenges vague or self-reported
claims, forces edge cases and failure modes, and never offers generic praise. An
evidence-aware policy (computed from the ledger) tells the model which proof
types are still MISSING so it asks for those instead of moving on.

This module never emits to the ledger. It returns a validated payload (or None
on malformed/empty/error). The WS layer mints the seq and lineId, and on None
falls back to the scripted line — so malformed LLM output can NEVER reach the
ledger.
"""
from __future__ import annotations

import re
from typing import Optional

from ..roles import pick_persona
from ..scenario import get_scenario
from ..phase_controller import ADVANCE_SIGNALS
from ..conversation_memory import ConversationMemory
from ._parse import extract_json
from .client import LLMUnavailable, call_llm

_VALID_SIGNALS = set(ADVANCE_SIGNALS)


# ── evidence-aware policy (computed in code from the ledger) ───────────────────

# Keyword probes over candidate utterances. Self-report narration mentioning
# these terms is weak proof, but their ABSENCE means we definitely haven't seen
# the artifact yet — so we ask for it.
_DESIGN_RX = re.compile(
    r"\b(schema|index|table|primary key|foreign key|api|endpoint|contract|"
    r"trade.?off|complexity|big.?o|throughput|latency|partition|shard|queue)\b",
    re.IGNORECASE,
)
_DEBUG_RX = re.compile(
    r"\b(bug|debug|root cause|stack ?trace|race condition|deadlock|fix(ed)?|"
    r"reproduce|regression|incident|postmortem)\b",
    re.IGNORECASE,
)
_COLLAB_RX = re.compile(
    r"\b(team|collaborat|stakeholder|cross.?functional|disagree|conflict|"
    r"negotiat|coordinat|aligned?|review(er|ed)?)\b",
    re.IGNORECASE,
)
_OWNERSHIP_RX = re.compile(
    r"\b(i (owned|built|designed|led|implemented|wrote|drove)|my (design|service|"
    r"responsibilit)|i was responsible)\b",
    re.IGNORECASE,
)


def compute_evidence_status(events: list[dict]) -> dict:
    """Which categories of CONCRETE evidence already exist in the ledger.

    ``code`` is hard proof (code.edited / code.run). The others are inferred from
    candidate utterance content — their absence is the signal we care about (ask
    for what's missing); their presence is still only weak self-report proof.
    """
    has_code = False
    cand_text: list[str] = []
    for e in events:
        t = e.get("type")
        if t in ("code.edited", "code.run"):
            has_code = True
        elif t == "candidate.utterance":
            cand_text.append(str(e.get("text", "")))
    blob = "\n".join(cand_text)
    return {
        "code": has_code,
        "design": bool(_DESIGN_RX.search(blob)),
        "debugging": bool(_DEBUG_RX.search(blob)),
        "collaboration": bool(_COLLAB_RX.search(blob)),
        "ownership": bool(_OWNERSHIP_RX.search(blob)),
    }


_EVIDENCE_LABELS = {
    "code": "actual code or detailed pseudocode (NONE yet — make them type in the code box)",
    "design": "a concrete schema/API/data-model or complexity/tradeoff analysis (NONE yet)",
    "debugging": "diagnosing a concrete failure/bug (NONE yet)",
    "collaboration": "conflict / coordination-under-ambiguity / cross-functional decisions (NONE yet)",
    "ownership": "what THEY personally owned vs the team (NOT yet pinned down)",
}


def missing_evidence_summary(status: dict) -> str:
    missing = [_EVIDENCE_LABELS[k] for k in ("code", "design", "debugging", "collaboration", "ownership") if not status.get(k)]
    if not missing:
        return "Solid concrete evidence already exists across the board."
    return "MISSING evidence you must still extract:\n- " + "\n- ".join(missing)


# ── phase-specific behavior ────────────────────────────────────────────────────

PHASE_GUIDANCE: dict[str, str] = {
    "intro": (
        "Keep it to ONE or two sentences, then immediately pivot to calibrate "
        "seniority with a pointed technical question about their actual stack."
    ),
    "resume_calibration": (
        "Pick ONE concrete project and drill into OWNERSHIP: what did THEY design "
        "and write versus what the team did? Demand specifics, not narration."
    ),
    "problem_framing": (
        "Pose a realistic technical scenario for THIS role/JD (e.g. for "
        "backend/payments: idempotent retries, a ledger schema, an API contract "
        "under concurrency). Ask how they'd model and frame it."
    ),
    "coding": (
        "Ask them to WRITE actual code or detailed pseudocode IN THE CODE BOX for "
        "a concrete task. Be specific about inputs/outputs. Do not accept a verbal "
        "hand-wave — insist on code."
    ),
    "debugging": (
        "Introduce a CONCRETE failure or bug (e.g. duplicate charges under "
        "retries, a race on a balance update, a deadlock) and ask them to diagnose "
        "the root cause and propose a fix."
    ),
    "optimization": (
        "Push on scale, concurrency, and latency tradeoffs: where's the "
        "transaction boundary, what breaks at 10x, why this datastore over "
        "another, complexity of their approach."
    ),
    "wrap_up": (
        "ONLY wrap up if real technical evidence (code/design/debugging) already "
        "exists. If it does not, do NOT wrap up — ask one more concrete technical "
        "probe instead and do not suggest advancing."
    ),
}

# Anti-patterns the interviewer must never produce (also asserted by tests).
_FORBIDDEN_PRAISE = (
    "That's great",
    "Great to hear",
    "solid approach",
    "That's a solid approach",
    "Awesome",
    "Perfect",
)

# Templated openers that make turns feel robotic. The prompt forbids leading
# with these; tests assert they're surfaced as prohibitions.
_FORBIDDEN_OPENERS = (
    "Can you",
    "You mentioned",
    "Please provide",
)

# Natural, varied alternatives the interviewer is nudged toward instead.
_VARIED_OPENERS = (
    "Let's make that concrete",
    "Walk me through",
    "Design this with me",
    "Suppose two retries arrive at once",
    "Take the code box and implement",
    "Now pressure-test that",
)


# ── transcript + prompt ─────────────────────────────────────────────────────────

def _summarize_transcript(events: list[dict], *, limit: int = 10) -> str:
    """Compact recent utterances/code into a short, model-friendly summary."""
    lines: list[str] = []
    for e in events:
        t = e.get("type")
        if t == "interviewer.utterance":
            lines.append(f"Interviewer: {e.get('text', '')}")
        elif t == "candidate.utterance":
            lines.append(f"Candidate: {e.get('text', '')}")
        elif t == "code.run":
            lines.append(f"[candidate ran code, exit={e.get('exitCode')}]")
        elif t == "code.edited":
            lines.append("[candidate edited code]")
    return "\n".join(lines[-limit:]) if lines else "(no transcript yet)"


def _last_candidate_answer(events: list[dict]) -> str:
    for e in reversed(events):
        if e.get("type") == "candidate.utterance":
            return str(e.get("text", "")).strip()
    return ""


def _last_interviewer_opening(events: list[dict], *, words: int = 6) -> str:
    """First few words of the most recent interviewer turn, so the prompt can
    tell the model NOT to open the same way twice in a row."""
    for e in reversed(events):
        if e.get("type") == "interviewer.utterance":
            text = str(e.get("text", "")).strip()
            if text:
                return " ".join(text.split()[:words])
    return ""


def _build_messages(
    *,
    phase: str,
    intake: dict,
    rubric: dict,
    transcript: str,
    evidence_summary: str,
    last_answer: str = "",
    prev_opening: str = "",
    track: str | None = None,
    persona: str | None = None,
) -> list[dict]:
    crit = ", ".join(c.get("name", "") for c in (rubric.get("criteria") or []))
    languages = ", ".join(intake.get("languages", []) or []) or "(unspecified)"
    jd = (intake.get("jobDescription", "") or "").strip()
    if len(jd) > 800:
        jd = jd[:800] + "…"
    system = (
        "You are Maya, a SHARP, SENIOR technical interviewer running a live "
        "coding/system interview. You are rigorous and probing, not a recruiter "
        "and not a behavioral screener. Your job is to extract CONCRETE technical "
        "evidence — working code, a data model/schema, an API contract, "
        "pseudocode, a complexity or tradeoff analysis, a real debugging "
        "diagnosis — NOT résumé expansion or feelings.\n"
        "Rules:\n"
        "1. Ask ROLE/JD-specific TECHNICAL probes derived from the role, "
        "seniority, languages, job description, and rubric criteria.\n"
        "2. Demand concrete artifacts. For coding tasks, explicitly tell the "
        "candidate to TYPE THEIR CODE IN THE CODE BOX.\n"
        "3. Build your next question on the candidate's LAST answer and CHALLENGE "
        "anything vague or self-reported: 'what could go wrong with that?', 'why "
        "Postgres not Redis?', 'what happens with two concurrent retries?', "
        "'where's the transaction boundary?', 'what did YOU own vs the team?'.\n"
        "4. Force edge cases, failure modes, concurrency, idempotency, and scale.\n"
        "5. NEVER use generic praise ('That's great', 'Great to hear', 'solid "
        "approach', 'Perfect', 'Awesome'). Stay neutral and probing.\n"
        "6. Do NOT wrap up early. Stay strictly in the CURRENT phase; ask exactly "
        "one focused question or prompt.\n"
        "7b. BE CONCISE — at most TWO short sentences, one focused probe. No "
        "preamble, no stacked multi-part questions. If the candidate dodges "
        "writing code, direct them to the code box; if an answer is vague, ask a "
        "sharper follow-up instead of moving on.\n"
        "7. VARY YOUR PHRASING — sound like a real senior engineer, not a "
        "template. NEVER open a turn with 'Can you…', 'You mentioned…', or "
        "'Please provide…'. Prefer natural, direct openings such as 'Let's make "
        "that concrete…', 'Walk me through…', 'Design this with me…', 'Suppose "
        "two retries arrive at once…', 'Take the code box and implement…', or "
        "'Now pressure-test that…'. Do NOT reuse the same opening pattern in "
        "consecutive turns.\n"
        "Reply as STRICT JSON only: {\"utterance\": string, \"suggestedAdvance\": "
        "optional string}. suggestedAdvance, if present, must be one of the known "
        "advance signals and is only a HINT — the system decides transitions. Do "
        "NOT suggest advancing toward wrap_up while code or design evidence is "
        "still missing."
    )
    # Persona tone contract — explicit persona wins, else derived from
    # seniority. Prepended to EVERY turn so tone stays consistent all session.
    system += "\n" + pick_persona(persona, intake.get("seniority"))["prompt_addition"]
    spec = get_scenario(track)
    if spec is not None:
        system += (
            f"\nTRACK {spec.id}: {spec.task_prompt} Open with the task, drive the "
            "work IN THE CODE BOX, and defer ALL background questions to the very "
            "end. One short probe per turn."
        )
        if spec.reveal_terms:
            system += (
                " HARD RULE: never name the target approach or say any of these "
                f"terms yourself: {', '.join(spec.reveal_terms)} — make the "
                "candidate arrive at it."
            )
    guidance = (
        spec.phase_guidance.get(phase) if spec is not None else None
    ) or PHASE_GUIDANCE.get(phase, "Probe for concrete technical depth relevant to this phase.")
    user = (
        f"Current phase: {phase}\n"
        f"Phase objective: {guidance}\n"
        f"Role: {intake.get('role', '')} ({intake.get('seniority', 'mid')})\n"
        f"Languages: {languages}\n"
        f"Job description:\n{jd or '(none provided)'}\n"
        f"Rubric criteria: {crit}\n\n"
        f"{evidence_summary}\n\n"
        f"Candidate's last answer:\n{last_answer or '(none yet)'}\n\n"
        + (
            f"Your previous turn opened with: \"{prev_opening}…\" — open this turn "
            "DIFFERENTLY (different first words, different shape).\n\n"
            if prev_opening
            else ""
        )
        + f"Recent transcript:\n{transcript}\n\n"
        "Produce your next interviewer turn for the CURRENT phase: a single "
        "pointed, technical, evidence-seeking question that challenges the last "
        "answer and targets the MISSING evidence above."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def generate_interviewer_turn(
    session_id: str,
    *,
    phase: str,
    intake: dict,
    rubric: dict,
    transcript_events: list[dict],
    fake_llm: bool = False,
    track: str | None = None,
    persona: str | None = None,
) -> Optional[dict]:
    """Return ``{"text": str, "suggestedAdvance": str | None}`` or None.

    None means: no provider, an error, or malformed/empty output — the caller
    must emit the scripted fallback instead.

    ``fake_llm`` (TEST-ONLY, gated at session create) returns None without calling
    a provider, so the caller emits the deterministic scripted fallback line.
    """
    if fake_llm:
        return None
    transcript = _summarize_transcript(transcript_events)
    status = compute_evidence_status(transcript_events)
    evidence_summary = missing_evidence_summary(status)
    last_answer = _last_candidate_answer(transcript_events)
    prev_opening = _last_interviewer_opening(transcript_events)
    try:
        content = await call_llm(
            _build_messages(
                phase=phase,
                intake=intake,
                rubric=rubric,
                transcript=transcript,
                evidence_summary=evidence_summary,
                last_answer=last_answer,
                prev_opening=prev_opening,
                track=track,
                persona=persona,
            ),
            role="interviewer",
            temperature=0.5,
            max_tokens=400,
        )
    except LLMUnavailable:
        return None
    except Exception:
        return None

    parsed = extract_json(content)
    if not isinstance(parsed, dict):
        return None
    text = parsed.get("utterance")
    if not isinstance(text, str) or not text.strip():
        return None

    suggested = parsed.get("suggestedAdvance")
    if not (isinstance(suggested, str) and suggested in _VALID_SIGNALS):
        suggested = None

    # Evidence-aware guard: never let the interviewer push toward wrap_up while
    # hard technical proof (code or design) is still missing.
    if suggested in {"optimization.done", "wrap.done"} and not (status["code"] or status["design"]):
        suggested = None

    return {"text": text.strip(), "suggestedAdvance": suggested}


# ── reactive conversation turn (the anti-lockstep response generator) ──────────

# The candidate-intent categories the conversation model classifies each turn
# into. Purely informational to the caller (drives logging/metrics); the reply
# and advance flag are what actually move the interview.
CONVERSATION_INTENTS = (
    "answered", "partial", "correct", "incorrect", "confused",
    "clarification", "off_topic", "conversational",
)

_NEXT_PHASE_OBJECTIVE = {
    "intro": "resume_calibration: drill into what THEY personally owned on a real project.",
    "resume_calibration": "problem_framing: pose a realistic technical scenario for this role and have them frame it.",
    "problem_framing": "coding: get them writing actual code in the code box.",
    "coding": "debugging: introduce a concrete failure and have them diagnose the root cause.",
    "debugging": "optimization: push on scale, concurrency, and tradeoffs.",
    "optimization": "wrap_up: one short question about real production experience.",
    "wrap_up": "(the interview is ending)",
}


def _build_conversation_messages(
    *,
    phase: str,
    intake: dict,
    rubric: dict,
    memory: ConversationMemory,
    transcript: str,
    candidate_text: str,
    evidence_summary: str,
    track: str | None,
    persona: str | None,
) -> list[dict]:
    languages = ", ".join(intake.get("languages", []) or []) or "(unspecified)"
    spec = get_scenario(track)
    guidance = (
        spec.phase_guidance.get(phase) if spec is not None else None
    ) or PHASE_GUIDANCE.get(phase, "Probe for concrete technical depth relevant to this phase.")

    system = (
        "You are Maya, an experienced senior engineer running a LIVE technical "
        "interview. This is a real CONVERSATION, not a questionnaire. You just "
        "heard the candidate's latest response — react the way a thoughtful "
        "senior interviewer actually would:\n"
        "- Answered well: acknowledge briefly, then probe DEEPER (an edge case, a "
        "tradeoff, a 'why', a failure mode). Never move on after a single good "
        "answer.\n"
        "- Partial answer: acknowledge the right part, then push on exactly what's "
        "missing.\n"
        "- Wrong: do NOT say 'wrong' or 'incorrect'. Ask a question that exposes "
        "the gap ('what happens if two retries arrive at once?').\n"
        "- Confused / 'I don't know': do NOT move on and do NOT repeat the same "
        "question. Narrow it down by naming 2-3 specific aspects OF THE CURRENT "
        "problem for them to choose between (always use THIS problem's own "
        "concepts — never terminology from a different domain) and offer to "
        "reason through it together.\n"
        "- Asks a clarifying question: answer it directly in one line, then return "
        "to the topic.\n"
        "- Asks you to repeat: restate the current question in FRESH words.\n"
        "- Off-topic or chit-chat: a brief human acknowledgment, then steer back.\n"
        "Stay on the CURRENT topic until the candidate has genuinely engaged with "
        "it — one real answer plus at least one follow-up they responded to. Only "
        "then set advance=true.\n"
        "HARD RULES: never state correctness verbatim ('that's right/wrong'); "
        "never praise ('great', 'perfect'); at most TWO short sentences; sound "
        "human and VARY your wording — never reuse an opener or a follow-up you "
        "already used (they are listed below); if a coding task, direct them to "
        "the code box when appropriate but do not repeat 'type the code' every "
        "turn.\n"
    )
    system += "\n" + pick_persona(persona, intake.get("seniority"))["prompt_addition"]
    if spec is not None and spec.reveal_terms:
        system += (
            "\nNEVER name the target approach or say any of these terms yourself: "
            f"{', '.join(spec.reveal_terms)} — make the candidate arrive at it."
        )
    system += (
        "\nReturn STRICT JSON only: {\"intent\": one of "
        f"[{', '.join(CONVERSATION_INTENTS)}], "
        "\"reply\": your spoken response, \"advance\": boolean (true ONLY if the "
        "candidate has fully satisfied this topic and you have already probed a "
        "follow-up), \"covered\": [short topic tags the candidate addressed this "
        "turn], \"note\": one short clause on their answer for your own memory}. "
        "If advance is true, make your reply briefly wrap this topic and open the "
        "next area naturally."
    )

    user = (
        f"Current phase: {phase}\n"
        f"This phase's objective: {guidance}\n"
        f"If you advance, the next area is — {_NEXT_PHASE_OBJECTIVE.get(phase, '(end)')}\n"
        f"Role: {intake.get('role', '')} ({intake.get('seniority', 'mid')}); Languages: {languages}\n\n"
        f"YOUR MEMORY OF THIS INTERVIEW:\n{memory.as_prompt_context()}\n\n"
        f"{evidence_summary}\n\n"
        f"Recent conversation:\n{transcript}\n\n"
        f"The candidate JUST said:\n\"{candidate_text}\"\n\n"
        "React to what they actually said, per the rules above, and return the JSON."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def generate_conversation_turn(
    session_id: str,
    *,
    phase: str,
    intake: dict,
    rubric: dict,
    memory: ConversationMemory,
    transcript_events: list[dict],
    candidate_text: str,
    track: str | None = None,
    persona: str | None = None,
) -> Optional[dict]:
    """Reactive interviewer turn: understand the candidate's latest utterance and
    respond in-phase. Returns
    ``{"reply", "intent", "advance", "covered": [...], "note"}`` or None on
    no-provider / malformed / error (the caller then uses a deterministic
    acknowledgment and stays in phase — never a silent drop)."""
    transcript = _summarize_transcript(transcript_events)
    status = compute_evidence_status(transcript_events)
    evidence_summary = missing_evidence_summary(status)
    try:
        content = await call_llm(
            _build_conversation_messages(
                phase=phase, intake=intake, rubric=rubric, memory=memory,
                transcript=transcript, candidate_text=candidate_text,
                evidence_summary=evidence_summary, track=track, persona=persona,
            ),
            role="interviewer",
            temperature=0.6,
            max_tokens=320,
        )
    except (LLMUnavailable, Exception):
        return None

    parsed = extract_json(content)
    if not isinstance(parsed, dict):
        return None
    reply = parsed.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        return None
    intent = parsed.get("intent")
    if intent not in CONVERSATION_INTENTS:
        intent = "answered"
    covered = parsed.get("covered")
    covered = [str(c) for c in covered][:8] if isinstance(covered, list) else []
    note = parsed.get("note")
    note = str(note)[:200] if isinstance(note, str) and note.strip() else ""
    return {
        "reply": reply.strip(),
        "intent": intent,
        "advance": bool(parsed.get("advance")),
        "covered": covered,
        "note": note,
    }
