/**
 * Conversation-quality metrics — pure selectors over the event ledger.
 *
 * Every event carries seq + ts (epoch ms), so the review page can compute the
 * numbers conversation researchers actually use — response-gap distribution
 * (humans answer in ~200ms–1s; Stivers et al.), interruption handling, hint
 * escalation, pause adherence, and run progression — with zero new
 * instrumentation. "We measure whether it feels human" is the point.
 */

export interface AnyEvent {
  type: string;
  seq: number;
  ts: number;
  [key: string]: unknown;
}

export interface ConversationMetrics {
  /** ms from each candidate utterance to the next interviewer utterance. */
  responseGapMedianMs: number | null;
  responseGapP90Ms: number | null;
  responseCount: number;
  /** ms from interviewer.turn.started to its own utterance (generation time). */
  generationMedianMs: number | null;
  /** Barge-ins the candidate fired and turns actually cancelled. */
  bargeIns: number;
  cancelledTurns: number;
  /** Scheduled-pause discipline. */
  pausesScheduled: number;
  pausesCompleted: number;
  pausesCancelled: number;
  pausesSuperseded: number;
  /** Hint behavior: deepest rung reached, throttled (gamed) requests, nudges. */
  maxHintStep: number;
  hintCount: number;
  throttledHints: number;
  silenceNudges: number;
  /** Guarded LLM lines (verdict/praise/reveal leaks neutralized). */
  guardedLines: number;
  stallRecoveries: number;
  /** Code-run progression: passed/total per run, in order. */
  runProgression: { passed: number; total: number }[];
  /** Talk balance. */
  candidateUtterances: number;
  interviewerUtterances: number;
}

function median(xs: number[]): number | null {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : Math.round((s[mid - 1] + s[mid]) / 2);
}

function p90(xs: number[]): number | null {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(s.length * 0.9))];
}

export function computeConversationMetrics(events: ReadonlyArray<AnyEvent>): ConversationMetrics {
  const gaps: number[] = [];
  const genLatencies: number[] = [];
  const runProgression: { passed: number; total: number }[] = [];
  const turnStartTs = new Map<string, number>();

  let bargeIns = 0;
  let cancelledTurns = 0;
  let pausesScheduled = 0;
  let pausesCompleted = 0;
  let pausesCancelled = 0;
  let pausesSuperseded = 0;
  let maxHintStep = 0;
  let hintCount = 0;
  let throttledHints = 0;
  let silenceNudges = 0;
  let guardedLines = 0;
  let stallRecoveries = 0;
  let candidateUtterances = 0;
  let interviewerUtterances = 0;

  let pendingCandidateTs: number | null = null;

  for (const e of events) {
    switch (e.type) {
      case "candidate.utterance":
        candidateUtterances += 1;
        pendingCandidateTs = e.ts;
        break;
      case "interviewer.utterance": {
        interviewerUtterances += 1;
        if (pendingCandidateTs !== null) {
          gaps.push(Math.max(0, e.ts - pendingCandidateTs));
          pendingCandidateTs = null;
        }
        const turnId = typeof e.turnId === "string" ? e.turnId : undefined;
        if (turnId && turnStartTs.has(turnId)) {
          genLatencies.push(Math.max(0, e.ts - (turnStartTs.get(turnId) as number)));
          turnStartTs.delete(turnId);
        }
        const step = typeof e.hint_step === "number" ? e.hint_step : 0;
        if (e.hint_for) {
          hintCount += 1;
          if (step > maxHintStep) maxHintStep = step;
        }
        if (e.hint_throttled) throttledHints += 1;
        if (typeof e.nudgeLevel === "number") silenceNudges += 1;
        if (e.guarded) guardedLines += 1;
        if (e.stallRecovered) stallRecoveries += 1;
        break;
      }
      case "interviewer.turn.started":
        if (typeof e.turnId === "string") turnStartTs.set(e.turnId, e.ts);
        break;
      case "barge_in.detected":
        bargeIns += 1;
        break;
      case "interviewer.cancelled":
        cancelledTurns += 1;
        break;
      case "system.pause.scheduled":
        pausesScheduled += 1;
        break;
      case "system.pause.completed":
        pausesCompleted += 1;
        break;
      case "system.pause.cancelled":
        pausesCancelled += 1;
        if (e.reason === "superseded") pausesSuperseded += 1;
        break;
      case "code.run":
        if (typeof e.passed === "number" && typeof e.total === "number") {
          runProgression.push({ passed: e.passed, total: e.total });
        }
        break;
      default:
        break;
    }
  }

  return {
    responseGapMedianMs: median(gaps),
    responseGapP90Ms: p90(gaps),
    responseCount: gaps.length,
    generationMedianMs: median(genLatencies),
    bargeIns,
    cancelledTurns,
    pausesScheduled,
    pausesCompleted,
    pausesCancelled,
    pausesSuperseded,
    maxHintStep,
    hintCount,
    throttledHints,
    silenceNudges,
    guardedLines,
    stallRecoveries,
    runProgression,
    candidateUtterances,
    interviewerUtterances,
  };
}
