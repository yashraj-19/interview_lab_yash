/**
 * SViam Interview vNext — core session models.
 *
 * Phase A foundation. These types are deliberately storage-agnostic and free of
 * any LLM/network concerns. References to intake/rubric are by id so a session
 * row stays small and the heavy context lives alongside.
 */

import type { Phase } from "./state-machine";

export type SessionStatus =
  | "draft" // created, intake not yet complete
  | "ready" // rubric bound, can start
  | "live" // interview in progress
  | "scoring" // interview done, scorecard being produced
  | "complete" // scorecard finalized
  | "abandoned";

export interface Candidate {
  id: string;
  name: string;
  email?: string;
}

export interface Session {
  id: string;
  candidate: Candidate;
  /** Id of the {@link import("./intake").Intake} that seeded this session. */
  intakeId: string;
  /** Id of the bound {@link import("./rubric").Rubric}, once generated. */
  rubricId: string | null;
  status: SessionStatus;
  phase: Phase;
  /** Source clock epoch ms at creation (injectable for determinism). */
  createdAt: number;
  /** Highest event seq durably applied to this session. 0 = fresh. */
  lastSeq: number;
}
