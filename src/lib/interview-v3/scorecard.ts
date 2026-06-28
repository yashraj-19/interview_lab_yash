/**
 * Scorecard draft models.
 *
 * A scorecard is produced criterion-by-criterion (staged) so the UI can stream
 * it. Every CriterionScore must cite EvidenceRefs that point at real ledger
 * seqs — no score without evidence.
 */

import type { EvidenceRef } from "./events";

export type Verdict = "strong" | "mixed" | "weak" | "insufficient_evidence";

export interface CriterionScore {
  criterionId: string;
  /** 0..100, comparable to the criterion's weight scale. */
  score: number;
  weight: number;
  verdict: Verdict;
  evidence: EvidenceRef[];
  /** What was missing or unobserved — the honest negative space. */
  gaps: string[];
}

/** Lifecycle of the staged scorecard production. */
export type ScorecardStage = "pending" | "scoring" | "complete";

export interface ScorecardDraft {
  sessionId: string;
  rubricId: string;
  stage: ScorecardStage;
  scores: CriterionScore[];
  /** Weighted aggregate 0..100, present once stage === "complete". */
  overall: number | null;
}

/** Deterministic weighted aggregate of completed criterion scores. */
export function aggregateOverall(scores: CriterionScore[]): number {
  const totalWeight = scores.reduce((a, s) => a + s.weight, 0);
  if (totalWeight <= 0) return 0;
  const weighted = scores.reduce((a, s) => a + s.score * s.weight, 0);
  return Math.round(weighted / totalWeight);
}
