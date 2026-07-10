/**
 * The InterviewAdapter seam.
 *
 * This is the single interface the vNext UI talks to. Phase A ships a
 * deterministic in-memory mock implementation; later phases swap in a live
 * WebSocket-backed adapter without the UI changing. The adapter owns the
 * ledger, mints seq-ordered events, and routes transition requests through the
 * PhaseController.
 */

import type { Intake } from "./intake";
import type { Rubric } from "./rubric";
import type { Phase, AdvanceSignal } from "./state-machine";
import type { VNextEvent } from "./events";
import type { CriterionScore, ScorecardDraft } from "./scorecard";

export type Unsubscribe = () => void;

/** A staged scorecard step: one criterion at a time, then a final draft. */
export type ScorecardStageUpdate =
  | { kind: "criterion"; score: CriterionScore }
  | { kind: "complete"; draft: ScorecardDraft }
  /** Terminal failure/timeout: the scorecard could not be produced in time. The
   *  UI must show an error/retry state instead of waiting forever. */
  | { kind: "failed"; reason: string };

export interface InterviewAdapter {
  /** Begin the scripted/live session (idempotent). */
  start(): Promise<void>;
  /** Tear down; no further events emitted. */
  stop(): Promise<void>;

  /** Candidate-authored speech/text turn. */
  sendCandidateText(text: string): void;
  /** Candidate code edit (full buffer after edit — idempotent). */
  sendCode(code: string): void;
  /** Request a code run; resolves to the run id placed on the ledger. */
  runCode(code: string): Promise<string>;

  /**
   * Ask the Controller to advance via a typed structural signal. The adapter
   * does NOT decide the transition — the Controller validates it and, on
   * success, the adapter emits the resulting `phase.changed` event.
   */
  requestAdvance(signal: AdvanceSignal): boolean;

  /**
   * Candidate barged in: tell the backend to cancel/obsolete the in-flight
   * interviewer turn (by `turnId` when known) so its late LLM output never takes
   * over as the active question. Best-effort + idempotent; the mock no-ops.
   */
  bargeIn(turnId?: string): void;

  /** VAD-style heartbeat: the candidate is actively speaking (STT interim).
   * Resets the server's silence timer so nudges don't fire mid-thought.
   * Best-effort, throttled by the caller, no transcript. Optional; mock no-ops. */
  notifySpeaking?(): void;

  /** Candidate accepts a Maya-proposed code patch. The server applies it
   *  (emits code.patch.applied + authoritative code.edited); the UI never mints
   *  code. No-op in the mock. */
  acceptPatch(patchId: string): void;
  /** Candidate rejects a proposed patch (server emits code.patch.rejected). */
  rejectPatch(patchId: string): void;

  /** Subscribe to seq-ordered events as they are appended. */
  onEvent(cb: (event: VNextEvent) => void): Unsubscribe;
  /** Subscribe to phase changes. */
  onState(cb: (phase: Phase) => void): Unsubscribe;

  /** Full ordered ledger for replay/inspection. */
  getLedger(): readonly VNextEvent[];
  /** Current phase. */
  getPhase(): Phase;

  /** Deterministic rubric generation from intake (no LLM in the mock). */
  generateRubric(intake: Intake): Promise<Rubric>;
  /** Staged async iterable producing the scorecard criterion-by-criterion. */
  generateScorecard(): AsyncIterable<ScorecardStageUpdate>;
}
