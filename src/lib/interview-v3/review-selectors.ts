/**
 * Pure selectors for the vNext review workspace.
 *
 * Evidence-first is the whole point: every criterion score must carry evidence
 * refs that resolve to real ledger seqs. These helpers link a scorecard to the
 * ledger so the UI can render evidence rows and reveal the cited event — and so
 * unresolved evidence is surfaced explicitly instead of silently dropped.
 *
 * Deterministic and pure: identical inputs always yield identical models.
 */

import type { EvidenceRef, VNextEvent } from "./events";
import type { CriterionScore, Verdict } from "./scorecard";
import type { Rubric } from "./rubric";

/** An evidence ref paired with the ledger event it cites (or null if missing). */
export interface ResolvedEvidence {
  ref: EvidenceRef;
  /** The cited ledger event, or null when `ref.seq` resolves to nothing. */
  event: VNextEvent | null;
  /** True when `ref.seq` does not resolve to a real ledger event. */
  invalid: boolean;
}

/** A scorecard-shaped input; both ScorecardDraft and a partial stream satisfy it. */
export interface ScorecardLike {
  scores: CriterionScore[];
  overall: number | null;
}

/** One evidence-linked criterion row for the review UI. */
export interface ReviewRow {
  criterionId: string;
  /** Human label from the rubric; falls back to the id when unknown. */
  name: string;
  description: string;
  score: number;
  weight: number;
  verdict: Verdict;
  evidence: ResolvedEvidence[];
  evidenceCount: number;
  /** Count of cited evidence that does not resolve to a ledger event. */
  invalidEvidenceCount: number;
  /** Honest negative space from the scorer plus derived integrity risks. */
  gaps: string[];
  /** Derived risks (e.g. no evidence, unresolved citations) — never silent. */
  risks: string[];
}

export interface ReviewModel {
  rows: ReviewRow[];
  overall: number | null;
  /** Total unresolved evidence citations across all rows. */
  invalidEvidenceCount: number;
}

/** Which panel a cited ledger seq should reveal in the review workspace. */
export type EvidenceTarget = "transcript" | "code" | "run" | "missing";

/**
 * Classify the panel that should highlight/scroll when an evidence seq is
 * selected. Utterances → transcript, code edits/snapshots → code panel, runs →
 * run rows. An unresolved seq is "missing" so the UI can surface it honestly.
 */
export function evidenceTargetForSeq(
  ledger: readonly VNextEvent[],
  seq: number,
): EvidenceTarget {
  const event = ledger.find((e) => e.seq === seq);
  if (!event) return "missing";
  if (event.type === "interviewer.utterance" || event.type === "candidate.utterance") {
    return "transcript";
  }
  if (event.type === "code.edited" || event.type === "code.snapshot") return "code";
  if (event.type === "code.run") return "run";
  return "missing";
}

/** All distinct ledger seqs cited as evidence across a set of criterion scores. */
export function selectCitedSeqs(scores: readonly CriterionScore[]): Set<number> {
  const out = new Set<number>();
  for (const s of scores) {
    for (const ref of s.evidence) out.add(ref.seq);
  }
  return out;
}

/** Resolve a single evidence ref against the ledger by seq. */
export function resolveEvidence(
  ledger: readonly VNextEvent[],
  ref: EvidenceRef,
): ResolvedEvidence {
  const event = ledger.find((e) => e.seq === ref.seq) ?? null;
  return { ref, event, invalid: event === null };
}

/**
 * Build evidence-linked rows for a scorecard. A score with empty evidence is
 * surfaced as a risk (never a clean score); each cited ref is resolved against
 * the ledger and unresolved refs are flagged.
 */
export function buildReviewModel(
  ledger: readonly VNextEvent[],
  scorecard: ScorecardLike,
  rubric?: Rubric | null,
): ReviewModel {
  let invalidEvidenceCount = 0;

  const rows: ReviewRow[] = scorecard.scores.map((s) => {
    const criterion = rubric?.criteria.find((c) => c.id === s.criterionId) ?? null;
    const evidence = s.evidence.map((ref) => resolveEvidence(ledger, ref));
    const rowInvalid = evidence.filter((e) => e.invalid).length;
    invalidEvidenceCount += rowInvalid;

    const risks: string[] = [];
    if (s.evidence.length === 0) {
      risks.push("No evidence cited for this score.");
    }
    if (rowInvalid > 0) {
      risks.push(`${rowInvalid} cited evidence item(s) do not resolve to a ledger event.`);
    }

    return {
      criterionId: s.criterionId,
      name: criterion?.name ?? s.criterionId,
      description: criterion?.description ?? "",
      score: s.score,
      weight: s.weight,
      verdict: s.verdict,
      evidence,
      evidenceCount: s.evidence.length,
      invalidEvidenceCount: rowInvalid,
      gaps: s.gaps,
      risks,
    };
  });

  return { rows, overall: scorecard.overall, invalidEvidenceCount };
}
