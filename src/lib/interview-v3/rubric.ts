/**
 * Rubric + competency models.
 *
 * A Rubric is a weighted set of Criteria derived from the intake context. In
 * Phase A it is produced deterministically (see mock-adapter). Weights are
 * normalized to sum to exactly 100.
 */

import type { Phase } from "./state-machine";

export type RubricSource = "mock" | "llm" | "operator";

export interface Criterion {
  id: string;
  name: string;
  description: string;
  /** Relative weight, 0..100. Across a rubric, weights sum to 100. */
  weight: number;
  /** Observable behaviors that count as evidence for/against this criterion. */
  signals: string[];
  /** Phases where this criterion is most observable. */
  phaseHints: Phase[];
}

export interface Rubric {
  id: string;
  criteria: Criterion[];
  generatedBy: RubricSource;
  version: number;
}

/**
 * Normalize raw weights so they sum to exactly 100 while staying deterministic.
 * Uses largest-remainder rounding; ties broken by array order.
 */
export function normalizeWeights(weights: number[]): number[] {
  const total = weights.reduce((a, b) => a + b, 0);
  if (total <= 0) {
    // Degenerate input: distribute evenly, remainder to leading entries.
    const base = Math.floor(100 / weights.length);
    const out = weights.map(() => base);
    let rem = 100 - base * weights.length;
    for (let i = 0; rem > 0; i++, rem--) out[i] += 1;
    return out;
  }
  const scaled = weights.map((w) => (w / total) * 100);
  const floored = scaled.map((s) => Math.floor(s));
  let remainder = 100 - floored.reduce((a, b) => a + b, 0);
  const order = scaled
    .map((s, i) => ({ i, frac: s - Math.floor(s) }))
    .sort((a, b) => b.frac - a.frac || a.i - b.i);
  const out = [...floored];
  for (let k = 0; k < order.length && remainder > 0; k++, remainder--) {
    out[order[k].i] += 1;
  }
  return out;
}
