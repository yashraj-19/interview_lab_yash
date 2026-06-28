"""LLM evidence extraction + staged scorecard, conforming to the EXACT existing
ScorecardDraft schema (src/lib/interview-v3/scorecard.ts + models.py).

The whole point of this phase: EVERY score must cite REAL ledger seqs. The LLM
only PROPOSES evidence as ``{seq, kind?, excerpt}``; the backend validates each
ref against the ledger and drops/repairs anything it cannot tie to a real event.
No fake evidence ever reaches the draft.

Contract (CriterionScore):
  {criterionId, score(0..100), weight, verdict, evidence: EvidenceRef[], gaps[]}
EvidenceRef: {kind, seq, span?, excerpt} where kind ∈ {utterance,code,code_run}.

Validation/repair policy (per evidence ref):
  - ``seq`` must resolve to a real ledger event -> else DROP the ref.
  - ``kind`` must match the event type -> else REPAIR kind from the real event.
  - ``excerpt`` must be a substring of the event's text/code/stdout -> else
    REPAIR it to a safe truncation of the REAL event content (never invented).

Per-criterion policy:
  - criterion IDs are pinned to the rubric (extras dropped, missing ones added
    back as insufficient_evidence); weight is FORCED to the rubric weight.
  - a criterion left with NO valid evidence -> verdict "insufficient_evidence".
  - overall is RECOMPUTED server-side (weighted average) — the LLM's number is
    never trusted.

On no provider / error / malformed output / no usable criteria, the CALLER gets
the deterministic SCRIPTED scorecard (SCORE_PLANS) instead.
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional

from ..seed import SCORE_PLANS, aggregate_overall
from ._parse import extract_json
from .client import call_llm

_VERDICTS = {"strong", "mixed", "weak", "insufficient_evidence"}

# Verdict strength ordering (for deterministic downgrades).
_VERDICT_RANK = {"insufficient_evidence": 0, "weak": 1, "mixed": 2, "strong": 3}
_RANK_VERDICT = {v: k for k, v in _VERDICT_RANK.items()}

# Deterministic strictness ceilings (server-side, post-LLM).
_CODING_CEILING_NO_CODE = 70  # a coding/technical criterion w/o code evidence
_COLLAB_CEILING_NO_EVIDENCE = 80  # collaboration w/o conflict/ownership evidence

# ── Hard timeout budget for the LLM scorecard build (reliability fix). ────────
# A single httpx read timeout (30s) does NOT bound a slow-but-steady provider —
# a trickle response can run for minutes. So: bound the per-call_llm read at 20s
# AND wrap the whole LLM build in a 25s wall-clock budget; on either limit we
# return the deterministic rubric-shaped scripted scorecard so the build ALWAYS
# completes within ~25s and the WS never hangs.
_SCORECARD_LLM_TIMEOUT = 20.0   # per call_llm (httpx read) budget
_SCORECARD_BUILD_BUDGET = 25.0  # hard wall-clock for the entire LLM build

# Gap note attached to a coding/technical criterion that was capped for lack of
# real code/run evidence (e.g. the candidate pasted code into chat instead of
# submitting it through the code box / run path).
_CODING_CAP_GAP_NOTE = (
    "Code was discussed/pasted in chat but not submitted through the code box / "
    "run path."
)

# Heuristics: does a rubric criterion look coding/implementation/technical?
_CODING_CRIT_RX = re.compile(
    r"\b(cod(e|ing)|implement|algorithm|program|data ?structure|debug|"
    r"technical competence|engineering rigou?r|software craft)\b",
    re.IGNORECASE,
)
# Explicitly non-coding criteria that should NOT be capped for missing code.
_NONCODING_CRIT_RX = re.compile(
    r"\b(communicat|collaborat|teamwork|behavio|culture|leadership|ownership)\b",
    re.IGNORECASE,
)
# Collaboration-style criteria.
_COLLAB_CRIT_RX = re.compile(
    r"\b(collaborat|communicat|teamwork|cross.?functional|stakeholder)\b",
    re.IGNORECASE,
)
# Real collaboration evidence in candidate utterances (not polished narration).
_COLLAB_EVIDENCE_RX = re.compile(
    r"\b(conflict|disagree|negotiat|coordinat|cross.?functional|stakeholder|"
    r"i (owned|led|drove|decided)|trade.?off with the team|under ambiguity)\b",
    re.IGNORECASE,
)

# Ledger event type -> the EvidenceRef.kind that may cite it.
_KIND_FOR_TYPE = {
    "interviewer.utterance": "utterance",
    "candidate.utterance": "utterance",
    "code.edited": "code",
    "code.run": "code_run",
}

_EXCERPT_MAX = 140


# ── evidence resolution (the heart of the phase) ──────────────────────────────

def _event_text(event: dict) -> str:
    """The searchable content of a ledger event, by type."""
    t = event.get("type")
    if t in ("interviewer.utterance", "candidate.utterance"):
        return str(event.get("text", ""))
    if t == "code.edited":
        return str(event.get("after", ""))
    if t == "code.run":
        # Either the run output or the executed code is fair game.
        return f"{event.get('stdout', '')}\n{event.get('code', '')}"
    return ""


def _safe_excerpt(content: str) -> str:
    snippet = content.strip().splitlines()[0] if content.strip() else content.strip()
    snippet = snippet.strip()
    if len(snippet) > _EXCERPT_MAX:
        snippet = snippet[:_EXCERPT_MAX].rstrip()
    return snippet


def resolve_evidence_ref(ref: object, ledger_by_seq: dict[int, dict]) -> Optional[dict]:
    """Validate + repair ONE proposed evidence ref against the ledger.

    Returns a clean EvidenceRef dict, or None when the ref cannot be tied to a
    real, citable ledger event (then the caller DROPS it).
    """
    if not isinstance(ref, dict):
        return None

    raw_seq = ref.get("seq")
    try:
        seq = int(raw_seq)
    except (TypeError, ValueError):
        return None

    event = ledger_by_seq.get(seq)
    if event is None:
        return None  # seq is not a real ledger event -> DROP (never invent)

    correct_kind = _KIND_FOR_TYPE.get(event.get("type"))
    if correct_kind is None:
        return None  # event type is not citable evidence -> DROP

    content = _event_text(event)
    excerpt = ref.get("excerpt")
    if not (isinstance(excerpt, str) and excerpt.strip() and excerpt.strip() in content):
        # REPAIR excerpt to a safe truncation of the REAL event content.
        excerpt = _safe_excerpt(content)

    return {"kind": correct_kind, "seq": seq, "excerpt": excerpt}


def _verdict_for(score: int, has_evidence: bool, proposed: object) -> str:
    if not has_evidence:
        return "insufficient_evidence"
    if isinstance(proposed, str) and proposed in _VERDICTS and proposed != "insufficient_evidence":
        return proposed
    if score >= 75:
        return "strong"
    if score >= 55:
        return "mixed"
    return "weak"


def _ledger_has_code(ledger_events: list[dict]) -> bool:
    return any(e.get("type") in ("code.edited", "code.run") for e in ledger_events)


def _ledger_has_collab_evidence(ledger_events: list[dict]) -> bool:
    blob = "\n".join(
        str(e.get("text", "")) for e in ledger_events if e.get("type") == "candidate.utterance"
    )
    return bool(_COLLAB_EVIDENCE_RX.search(blob))


def _crit_text(crit: dict) -> str:
    return f"{crit.get('name', '')} {crit.get('description', '')}"


def _is_coding_crit(crit: dict) -> bool:
    ctext = _crit_text(crit)
    return bool(_CODING_CRIT_RX.search(ctext)) and not _NONCODING_CRIT_RX.search(ctext)


def _downgrade(verdict: str, ceiling: str) -> str:
    """Clamp a verdict to at most ``ceiling`` strength."""
    if _VERDICT_RANK.get(verdict, 0) > _VERDICT_RANK[ceiling]:
        return ceiling
    return verdict


def _apply_strictness_caps(
    crit: dict,
    score: int,
    verdict: str,
    has_evidence: bool,
    *,
    has_code: bool,
    has_collab_evidence: bool,
) -> tuple[int, str, bool]:
    """Deterministic strictness: self-description alone cannot buy a strong
    technical or collaboration score. Applied AFTER the LLM, so it is reliable.

    - A coding/technical/implementation criterion with NO code/code_run evidence
      in the ledger is clamped to ``_CODING_CEILING_NO_CODE`` and verdict ≤ mixed
      (unless the criterion is explicitly non-coding, e.g. communication).
    - A collaboration-style criterion above 80 requires real conflict/ownership/
      coordination evidence; otherwise clamp to 80 and verdict ≤ mixed.

    Returns ``(score, verdict, coding_capped)`` where ``coding_capped`` is True
    when the no-code coding cap applied (so the caller can attach a gap note).
    """
    ctext = _crit_text(crit)
    is_coding = _is_coding_crit(crit)
    coding_capped = False
    if is_coding and not has_code:
        coding_capped = True
        if score > _CODING_CEILING_NO_CODE:
            score = _CODING_CEILING_NO_CODE
        verdict = _downgrade(verdict, "mixed")

    is_collab = bool(_COLLAB_CRIT_RX.search(ctext))
    if is_collab and not has_collab_evidence and score > _COLLAB_CEILING_NO_EVIDENCE:
        score = _COLLAB_CEILING_NO_EVIDENCE
        verdict = _downgrade(verdict, "mixed")

    return score, verdict, coding_capped


def validate_scorecard_scores(
    rubric: dict,
    ledger_events: list[dict],
    parsed: object,
) -> Optional[list[dict]]:
    """Validate an LLM scorecard payload against the rubric + ledger.

    Returns a list of CriterionScore dicts in RUBRIC ORDER (ids pinned, weights
    forced), or None when the payload is unusable (no matching criteria at all)
    so the caller falls back to the scripted scorecard.

    Accepts ``{scores:[...]}`` / ``{criteria:[...]}`` / a bare ``[...]``.
    """
    criteria = rubric.get("criteria") or []
    if not criteria:
        return None

    if isinstance(parsed, dict):
        raw = parsed.get("scores")
        if not isinstance(raw, list):
            raw = parsed.get("criteria")
    elif isinstance(parsed, list):
        raw = parsed
    else:
        raw = None
    if not isinstance(raw, list) or not raw:
        return None

    ledger_by_seq = {e["seq"]: e for e in ledger_events if isinstance(e.get("seq"), int)}
    has_code = _ledger_has_code(ledger_events)
    has_collab_evidence = _ledger_has_collab_evidence(ledger_events)

    by_id: dict[str, dict] = {}
    for item in raw:
        if isinstance(item, dict) and isinstance(item.get("criterionId"), str):
            by_id.setdefault(item["criterionId"], item)

    matched = 0
    scores: list[dict] = []
    for crit in criteria:
        cid = crit["id"]
        weight = int(crit["weight"])
        item = by_id.get(cid)
        if item is None:
            # Missing criterion -> add back as honest insufficient_evidence.
            scores.append({
                "criterionId": cid,
                "score": 0,
                "weight": weight,
                "verdict": "insufficient_evidence",
                "evidence": [],
                "gaps": ["No assessment was produced for this criterion."],
            })
            continue

        matched += 1
        try:
            score = int(round(float(item.get("score", 0))))
        except (TypeError, ValueError):
            score = 0
        score = max(0, min(100, score))

        evidence: list[dict] = []
        for ref in (item.get("evidence") or []):
            clean = resolve_evidence_ref(ref, ledger_by_seq)
            if clean is not None:
                evidence.append(clean)

        gaps_in = item.get("gaps", [])
        gaps = [str(g).strip() for g in gaps_in if isinstance(g, (str, int, float)) and str(g).strip()] \
            if isinstance(gaps_in, list) else []

        verdict = _verdict_for(score, bool(evidence), item.get("verdict"))

        # Deterministic strictness caps (post-LLM, so they cannot be argued away).
        capped_score, capped_verdict, coding_capped = _apply_strictness_caps(
            crit, score, verdict, bool(evidence),
            has_code=has_code, has_collab_evidence=has_collab_evidence,
        )
        if capped_score != score or capped_verdict != verdict:
            note = (
                "Score capped: no demonstrated code/design/debugging evidence — "
                "self-description alone cannot justify a strong technical or "
                "collaboration score."
            )
            if note not in gaps:
                gaps = [*gaps, note]
        if coding_capped and _CODING_CAP_GAP_NOTE not in gaps:
            gaps = [*gaps, _CODING_CAP_GAP_NOTE]
        score, verdict = capped_score, capped_verdict

        if not evidence and not gaps:
            gaps = ["No evidence in the ledger supported a score for this criterion."]

        scores.append({
            "criterionId": cid,
            "score": score,
            "weight": weight,
            "verdict": verdict,
            "evidence": evidence,
            "gaps": gaps,
        })

    if matched == 0:
        return None  # LLM cited no real rubric criteria -> scripted fallback
    return scores


# ── scripted fallback (SCORE_PLANS, deterministic, real seqs) ─────────────────

def _pick_real_evidence(crit: dict, ledger_events: list[dict]) -> list[dict]:
    """One REAL ledger evidence ref for a criterion with no scripted plan (e.g.
    an LLM/incident rubric id). Coding criteria cite a code event; others cite the
    latest substantive candidate utterance. Returns [] when nothing real exists —
    the caller then scores it insufficient_evidence. Never invents a seq."""
    if _is_coding_crit(crit):
        for e in reversed(ledger_events):
            if e.get("type") in ("code.edited", "code.run"):
                kind = _KIND_FOR_TYPE.get(e.get("type"))
                if kind:
                    return [{"kind": kind, "seq": e["seq"], "excerpt": _safe_excerpt(_event_text(e))}]
        return []
    for e in reversed(ledger_events):
        if e.get("type") == "candidate.utterance" and str(e.get("text", "")).strip():
            return [{"kind": "utterance", "seq": e["seq"], "excerpt": _safe_excerpt(_event_text(e))}]
    return []


def build_scripted_scorecard(session_id: str, rubric: dict, ledger) -> tuple[list[dict], dict]:
    """Deterministic fallback scorecard, shaped to the ACTIVE rubric.

    Emits exactly one CriterionScore per rubric criterion, in rubric order, with
    the weight forced to the rubric weight. When SCORE_PLANS has a plan for a
    criterion id, its score/verdict/evidence/gaps are reused (evidence still
    cites real ledger seqs via the scripted ref ids). Criteria with no matching
    plan — e.g. an LLM-generated rubric with custom ids — get an honest
    insufficient_evidence score rather than a foreign id with weight 0. No
    evidence is ever fabricated.
    """
    criteria = rubric.get("criteria") or []
    rubric_id = rubric.get("id", f"rubric-{session_id}")
    plans_by_id = {p["criterionId"]: p for p in SCORE_PLANS}
    ledger_events = ledger.get_all() if hasattr(ledger, "get_all") else []
    has_code = _ledger_has_code(ledger_events)

    scores: list[dict] = []
    for crit in criteria:
        cid = crit["id"]
        weight = crit["weight"]
        plan = plans_by_id.get(cid)
        if plan is not None:
            evidence = []
            for e in plan["evidence"]:
                seq = ledger.find_seq_by_ref(e["refId"])
                if seq > 0:
                    evidence.append({"kind": e["kind"], "seq": seq, "excerpt": e["excerpt"]})
            if evidence:
                gaps = list(plan["gaps"])
                if _is_coding_crit(crit) and not has_code and _CODING_CAP_GAP_NOTE not in gaps:
                    gaps.append(_CODING_CAP_GAP_NOTE)
                scores.append({
                    "criterionId": cid,
                    "score": plan["score"],
                    "weight": weight,
                    "verdict": plan["verdict"],
                    "evidence": evidence,
                    "gaps": gaps,
                })
                continue
        # No matching plan (e.g. an incident/LLM rubric id): cite REAL ledger
        # evidence if any exists, scored conservatively. Coding criteria with no
        # code stay insufficient — that's the "cap technical score w/o code" rule.
        real = _pick_real_evidence(crit, ledger_events)
        if real:
            coding = _is_coding_crit(crit)
            scores.append({
                "criterionId": cid,
                "score": 65 if coding else 58,
                "weight": weight,
                "verdict": "mixed",
                "evidence": real,
                "gaps": [] if coding else [
                    "Largely self-reported; a deterministic pass could not verify depth.",
                ],
            })
            continue
        scores.append({
            "criterionId": cid,
            "score": 0,
            "weight": weight,
            "verdict": "insufficient_evidence",
            "evidence": [],
            "gaps": ["No valid scorecard evidence was available for this rubric criterion."],
        })

    draft = {
        "sessionId": session_id,
        "rubricId": rubric_id,
        "stage": "complete",
        "scores": scores,
        "overall": aggregate_overall(scores),
    }
    return scores, draft


# ── prompt ────────────────────────────────────────────────────────────────────

def _render_ledger(ledger_events: list[dict]) -> str:
    lines: list[str] = []
    for e in ledger_events:
        t = e.get("type")
        kind = _KIND_FOR_TYPE.get(t)
        if kind is None:
            continue  # only citable events are shown to the model
        content = _event_text(e).replace("\n", " \\n ").strip()
        if len(content) > 200:
            content = content[:200] + "…"
        lines.append(f"seq={e['seq']} kind={kind} {e.get('actor')}: {content}")
    return "\n".join(lines) if lines else "(no citable events)"


def _build_messages(intake: dict, rubric: dict, ledger_events: list[dict], phase: str) -> list[dict]:
    criteria = rubric.get("criteria") or []
    crit_lines = "\n".join(
        f"- id={c['id']} (weight {c['weight']}): {c.get('name','')} — {c.get('description','')}"
        for c in criteria
    )
    valid_ids = ", ".join(c["id"] for c in criteria)
    system = (
        "You are a rigorous technical-interview assessor. Score the candidate on "
        "the given rubric using ONLY the supplied interview ledger as evidence. "
        "Reply as STRICT JSON only — no prose, no markdown fences.\n"
        'Schema: {"scores": [ {"criterionId": string, "score": integer 0-100, '
        '"verdict": "strong"|"mixed"|"weak"|"insufficient_evidence", '
        '"evidence": [ {"seq": integer, "kind": "utterance"|"code"|"code_run", '
        '"excerpt": string} ], "gaps": [string,...] } ] }.\n'
        "Hard rules: produce EXACTLY one score per rubric criterion id. Every "
        "evidence.seq MUST be a real seq from the ledger below; quote the excerpt "
        "VERBATIM from that event. If a criterion has no ledger support, score it "
        'low with verdict "insufficient_evidence" and empty evidence. Gaps must be '
        "specific and evidence-aware — never generic praise.\n"
        "STRICTNESS (be conservative; prefer 'mixed'/'insufficient_evidence' over "
        "inflated scores):\n"
        "- Self-DESCRIPTION alone NEVER justifies a strong technical score. A "
        "strong technical/coding score requires DEMONSTRATED reasoning: concrete "
        "code, a schema/API/data model, a debugging diagnosis, or an explicit "
        "complexity/tradeoff analysis cited from the ledger.\n"
        "- If there are NO code/code_run events, a coding/implementation criterion "
        "cannot be 'strong'; cap it at 'mixed'.\n"
        "- Collaboration/communication above 80 requires evidence of conflict, "
        "coordination under ambiguity, ownership, or cross-functional decisions — "
        "NOT polished narration.\n"
        "- A thin, self-report-only ledger must yield conservative overall scores."
    )
    user = (
        f"Role: {intake.get('role','')} ({intake.get('seniority','mid')})\n"
        f"Current phase: {phase}\n"
        f"Rubric criteria (use these EXACT ids: {valid_ids}):\n{crit_lines}\n\n"
        f"Interview ledger (cite seqs from here):\n{_render_ledger(ledger_events)}\n\n"
        "Produce the scorecard now."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def build_scorecard_llm(
    session_id: str,
    intake: dict,
    rubric: dict,
    ledger,
    phase: str,
    *,
    fake_llm: bool = False,
) -> tuple[list[dict], dict]:
    """LLM scorecard with deterministic scripted fallback.

    Returns ``(scores, draft)`` where the draft conforms to ScorecardDraft and
    overall is the server-recomputed weighted average. Falls back to the scripted
    scorecard on no provider, error, malformed output, or no usable criteria.

    ``fake_llm`` (TEST-ONLY, gated at session create) skips the provider entirely
    and returns the deterministic scripted scorecard (real seqs, no OpenRouter).
    """
    ledger_events = ledger.get_all()
    rubric_id = rubric.get("id", f"rubric-{session_id}")

    if fake_llm:
        return build_scripted_scorecard(session_id, rubric, ledger)

    async def _attempt() -> Optional[list[dict]]:
        content = await call_llm(
            _build_messages(intake, rubric, ledger_events, phase),
            role="scorecard",
            temperature=0.2,
            max_tokens=1600,
            timeout=_SCORECARD_LLM_TIMEOUT,
        )
        parsed = extract_json(content)
        return validate_scorecard_scores(rubric, ledger_events, parsed)

    try:
        # Hard wall-clock budget: a slow/stalled provider can never exceed this.
        # On timeout / LLMUnavailable / any error / malformed output we return
        # the deterministic rubric-shaped scripted scorecard, so this ALWAYS
        # produces a complete scorecard within ~25s.
        scores = await asyncio.wait_for(_attempt(), timeout=_SCORECARD_BUILD_BUDGET)
    except (asyncio.TimeoutError, Exception):  # noqa: B014 — explicit for clarity
        return build_scripted_scorecard(session_id, rubric, ledger)

    if scores is None:
        return build_scripted_scorecard(session_id, rubric, ledger)

    draft = {
        "sessionId": session_id,
        "rubricId": rubric_id,
        "stage": "complete",
        "scores": scores,
        "overall": aggregate_overall(scores),  # recomputed server-side
    }
    return scores, draft
