"""Pydantic mirrors of the vNext wire contract — the source of truth on the
Python side.

These mirror, field-for-field, the TypeScript shapes in:
  - src/lib/interview-v3/events.ts        (VNextEnvelope, EvidenceRef, payloads)
  - src/lib/interview-v3/intake.ts        (Intake)
  - src/lib/interview-v3/rubric.ts        (Criterion, Rubric)
  - src/lib/interview-v3/scorecard.ts     (CriterionScore, ScorecardDraft)

Ledger events are emitted as FLAT dicts (envelope fields + payload fields in one
object), exactly like the TS mock (`{ ...envelope, ...body }`). The event models
below validate those flat dicts; they are the discriminated union keyed on
``type``. JS field names are preserved verbatim (camelCase, ``from``, ``exitCode``)
so a later live frontend adapter reaches byte-level parity on field names.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from .phase_controller import PHASES, ADVANCE_SIGNALS

# Re-expressed as typing Literals for model validation.
Phase = Literal[
    "intake",
    "rubric",
    "ready",
    "intro",
    "resume_calibration",
    "problem_framing",
    "coding",
    "debugging",
    "optimization",
    "wrap_up",
    "scoring",
    "review",
]

AdvanceSignal = Literal[
    "intake.submitted",
    "rubric.generated",
    "session.start",
    "intro.done",
    "calibration.done",
    "framing.done",
    "coding.done",
    "debugging.done",
    "optimization.done",
    "wrap.done",
    "scoring.done",
]

Actor = Literal["interviewer", "candidate", "system"]
Verdict = Literal["strong", "mixed", "weak", "insufficient_evidence"]
RubricSource = Literal["mock", "llm", "operator", "scripted"]
EvidenceKind = Literal["utterance", "code", "code_run"]


# Keep the runtime tables and the typing Literals in lock-step (guards drift).
assert set(PHASES) == set(Phase.__args__)  # type: ignore[attr-defined]
assert set(ADVANCE_SIGNALS) == set(AdvanceSignal.__args__)  # type: ignore[attr-defined]


# ── intake / rubric / scorecard structures ───────────────────────────────────

class Intake(BaseModel):
    """Raw operator/candidate input (mirror of intake.ts Intake)."""

    model_config = ConfigDict(extra="ignore")

    resumeText: str = ""
    resumeFileName: Optional[str] = None
    jobDescription: str = ""
    role: str = ""
    seniority: Literal["intern", "junior", "mid", "senior", "staff"] = "mid"
    languages: list[str] = Field(default_factory=list)
    durationMinutes: int = 45


class Criterion(BaseModel):
    id: str
    name: str
    description: str
    weight: int
    signals: list[str]
    phaseHints: list[Phase]


class Rubric(BaseModel):
    id: str
    criteria: list[Criterion]
    generatedBy: RubricSource
    version: int


class EvidenceRef(BaseModel):
    kind: EvidenceKind
    seq: int
    span: Optional[tuple[int, int]] = None
    excerpt: str


class CriterionScore(BaseModel):
    criterionId: str
    score: int
    weight: int
    verdict: Verdict
    evidence: list[EvidenceRef]
    gaps: list[str]


class ScorecardDraft(BaseModel):
    sessionId: str
    rubricId: str
    stage: Literal["pending", "scoring", "complete"]
    scores: list[CriterionScore]
    overall: Optional[int] = None


# ── event envelope + discriminated payload union ──────────────────────────────

class VNextEnvelope(BaseModel):
    """Same envelope shape as the production v1 envelope (events.ts)."""

    v: Literal[1] = 1
    seq: int
    ts: int
    sessionId: str
    actor: Actor


class PhaseChangedEvent(VNextEnvelope):
    type: Literal["phase.changed"]
    # ``from`` is a Python keyword — alias it, keep the wire name "from".
    from_: Phase = Field(alias="from")
    to: Phase
    signal: AdvanceSignal

    model_config = ConfigDict(populate_by_name=True)


class InterviewerUtteranceEvent(VNextEnvelope):
    type: Literal["interviewer.utterance"]
    lineId: str
    text: str


class CandidateUtteranceEvent(VNextEnvelope):
    type: Literal["candidate.utterance"]
    lineId: str
    text: str


class CodeEditedEvent(VNextEnvelope):
    type: Literal["code.edited"]
    editId: str
    after: str
    by: Actor
    activityLabel: Optional[str] = None


class CodeRunEvent(VNextEnvelope):
    type: Literal["code.run"]
    runId: str
    code: str
    stdout: str
    exitCode: int


class RubricBoundEvent(VNextEnvelope):
    type: Literal["rubric.bound"]
    rubric: Rubric


class EvidenceMarkerEvent(VNextEnvelope):
    type: Literal["evidence.marker"]
    ref: EvidenceRef
    criterionId: str


class ScorecardCriterionReadyEvent(VNextEnvelope):
    type: Literal["scorecard.criterion.ready"]
    score: CriterionScore


class ScorecardCompletedEvent(VNextEnvelope):
    type: Literal["scorecard.completed"]
    draft: ScorecardDraft


VNextEvent = Annotated[
    Union[
        PhaseChangedEvent,
        InterviewerUtteranceEvent,
        CandidateUtteranceEvent,
        CodeEditedEvent,
        CodeRunEvent,
        RubricBoundEvent,
        EvidenceMarkerEvent,
        ScorecardCriterionReadyEvent,
        ScorecardCompletedEvent,
    ],
    Field(discriminator="type"),
]


def validate_event(raw: dict) -> BaseModel:
    """Validate a flat ledger dict against the discriminated union."""
    from pydantic import TypeAdapter

    return TypeAdapter(VNextEvent).validate_python(raw)
