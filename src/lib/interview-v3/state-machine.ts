/**
 * Interview vNext phase state machine.
 *
 * The Controller is the single authority over phase transitions. The adapter /
 * LLM never set the phase directly — they emit typed structural *signals*
 * requesting an advance, and the Controller validates each request against the
 * transition table (plus optional guards). On success it is what mints the
 * `phase.changed` event payload, keeping phase authority in one place.
 */

export const PHASES = [
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
] as const;

export type Phase = (typeof PHASES)[number];

/** Typed structural signals the adapter/LLM may request. */
export type AdvanceSignal =
  | "intake.submitted"
  | "rubric.generated"
  | "session.start"
  | "intro.done"
  | "calibration.done"
  | "framing.done"
  | "coding.done"
  | "debugging.done"
  | "optimization.done"
  | "wrap.done"
  | "scoring.done";

/** Context a guard may inspect to permit/deny a transition. */
export interface TransitionContext {
  hasRubric: boolean;
  lastSeq: number;
}

export type TransitionGuard = (ctx: TransitionContext) => boolean;

export interface Transition {
  from: Phase;
  on: AdvanceSignal;
  to: Phase;
  guard?: TransitionGuard;
}

/** Linear default path through the interview, plus guards where meaningful. */
export const TRANSITIONS: readonly Transition[] = [
  { from: "intake", on: "intake.submitted", to: "rubric" },
  {
    from: "rubric",
    on: "rubric.generated",
    to: "ready",
    guard: (ctx) => ctx.hasRubric,
  },
  { from: "ready", on: "session.start", to: "intro", guard: (ctx) => ctx.hasRubric },
  { from: "intro", on: "intro.done", to: "resume_calibration" },
  { from: "resume_calibration", on: "calibration.done", to: "problem_framing" },
  { from: "problem_framing", on: "framing.done", to: "coding" },
  { from: "coding", on: "coding.done", to: "debugging" },
  { from: "debugging", on: "debugging.done", to: "optimization" },
  { from: "optimization", on: "optimization.done", to: "wrap_up" },
  { from: "wrap_up", on: "wrap.done", to: "scoring" },
  { from: "scoring", on: "scoring.done", to: "review" },
];

/**
 * Deterministic next structural signal for the linear interview path. Used by
 * the live-llm room's "Continue" control to request the single valid advance
 * out of the current phase. The server PhaseController remains the authority;
 * this only names the signal it expects. Terminal/non-linear phases (intake,
 * rubric, review) return null.
 */
export const NEXT_SIGNAL_BY_PHASE: Partial<Record<Phase, AdvanceSignal>> = {
  ready: "session.start",
  intro: "intro.done",
  resume_calibration: "calibration.done",
  problem_framing: "framing.done",
  coding: "coding.done",
  debugging: "debugging.done",
  optimization: "optimization.done",
  wrap_up: "wrap.done",
  scoring: "scoring.done",
};

/** The next valid advance signal for a phase on the linear path, or null. */
export function nextSignalForPhase(phase: Phase): AdvanceSignal | null {
  return NEXT_SIGNAL_BY_PHASE[phase] ?? null;
}

export type TransitionResult =
  | { ok: true; from: Phase; to: Phase; signal: AdvanceSignal }
  | { ok: false; from: Phase; signal: AdvanceSignal; reason: "no_transition" | "guard_failed" };

/**
 * Owns the current phase and validates every requested advance. Pure: it does
 * not emit events itself — the caller (adapter) turns a successful result into a
 * `phase.changed` envelope so seq/ts/sessionId stay owned by the ledger writer.
 */
export class PhaseController {
  private current: Phase;

  constructor(initial: Phase = "intake") {
    this.current = initial;
  }

  get phase(): Phase {
    return this.current;
  }

  /** Validate a requested advance without mutating state. */
  evaluate(signal: AdvanceSignal, ctx: TransitionContext): TransitionResult {
    const match = TRANSITIONS.find((t) => t.from === this.current && t.on === signal);
    if (!match) {
      return { ok: false, from: this.current, signal, reason: "no_transition" };
    }
    if (match.guard && !match.guard(ctx)) {
      return { ok: false, from: this.current, signal, reason: "guard_failed" };
    }
    return { ok: true, from: this.current, to: match.to, signal };
  }

  /** Validate and, on success, commit the transition. */
  request(signal: AdvanceSignal, ctx: TransitionContext): TransitionResult {
    const result = this.evaluate(signal, ctx);
    if (result.ok) this.current = result.to;
    return result;
  }
}
