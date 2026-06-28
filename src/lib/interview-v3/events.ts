/**
 * vNext event vocabulary.
 *
 * Built additively on top of the production protocol: we reuse the exact v1
 * envelope and the existing {@link InterviewEvent} union, then extend it with
 * vNext-only event types. Ordering and idempotency rules are inherited from the
 * production protocol: order by `seq` (monotonic), re-applying `seq <= lastSeq`
 * is a no-op. Renderers must ignore unknown types.
 */

import type { InterviewActor, InterviewEvent } from "@/lib/interview-protocol";
import type { Phase, AdvanceSignal } from "./state-machine";
import type { Rubric } from "./rubric";
import type { CriterionScore, ScorecardDraft } from "./scorecard";

/** Same envelope shape as the production protocol's v1 envelope. */
export interface VNextEnvelope {
  v: 1;
  seq: number;
  ts: number;
  sessionId: string;
  actor: InterviewActor;
}

/** Where a piece of evidence lives in the ledger. */
export interface EvidenceRef {
  kind: "utterance" | "code" | "code_run";
  /** seq of the ledger event the evidence is drawn from. */
  seq: number;
  /** Optional character span within that event's text/code. */
  span?: [number, number];
  excerpt: string;
}

/** vNext-only additive event payloads (sharing the v1 envelope). */
export type VNextOnlyEvent = VNextEnvelope &
  (
    | { type: "phase.changed"; from: Phase; to: Phase; signal: AdvanceSignal }
    | { type: "interviewer.turn.started"; turnId: string; phase: Phase }
    | { type: "interviewer.utterance"; lineId: string; text: string; turnId?: string }
    | { type: "interviewer.cancelled"; turnId: string }
    | {
        type: "code.patch.proposed";
        patchId: string;
        summary: string;
        before: string;
        after: string;
        selection?: { start: number; end: number };
      }
    | {
        type: "code.patch.applied";
        patchId: string;
        before: string;
        after: string;
        acceptedBy: "candidate" | "interviewer_auto";
      }
    | { type: "code.patch.rejected"; patchId: string }
    | { type: "candidate.utterance"; lineId: string; text: string }
    | { type: "code.run"; runId: string; code: string; stdout: string; exitCode: number }
    | { type: "rubric.bound"; rubric: Rubric }
    | { type: "evidence.marker"; ref: EvidenceRef; criterionId: string }
    | { type: "scorecard.criterion.ready"; score: CriterionScore }
    | { type: "scorecard.completed"; draft: ScorecardDraft }
    | { type: "scorecard.failed"; reason: string }
  );

/** Superset: production events plus vNext additions. */
export type VNextEvent = InterviewEvent | VNextOnlyEvent;

export type VNextEventType = VNextEvent["type"];

/** Narrow to a specific vNext event payload by type. */
export type VNextEventOf<T extends VNextEventType> = Extract<VNextEvent, { type: T }>;
