/**
 * Deterministic, pure selectors over a vNext ledger.
 *
 * The room UI never reduces raw events ad-hoc — it asks these helpers. Every
 * selector orders by `seq` (the production ordering rule) and is a pure
 * function of its input, so identical ledgers always yield identical views.
 */

import type { VNextEvent } from "./events";
import type { Phase } from "./state-machine";

/** A single transcript turn rendered in the room. */
export interface TranscriptTurn {
  seq: number;
  ts: number;
  speaker: "interviewer" | "candidate";
  lineId: string;
  text: string;
}

/** Latest code buffer plus the edit that produced it. */
export interface CodeState {
  /** Full editor text after the most recent edit/snapshot, "" if none yet. */
  code: string;
  /** seq of the event that last set the buffer, 0 if none. */
  seq: number;
}

/** A run-result row for the room's run panel. */
export interface RunResult {
  seq: number;
  ts: number;
  runId: string;
  code: string;
  stdout: string;
  exitCode: number;
}

/** Stable ascending-by-seq copy of a ledger. */
export function orderBySeq(ledger: readonly VNextEvent[]): VNextEvent[] {
  return [...ledger].sort((a, b) => a.seq - b.seq);
}

/** Extract the ordered transcript (interviewer + candidate utterances). */
export function selectTranscript(ledger: readonly VNextEvent[]): TranscriptTurn[] {
  const out: TranscriptTurn[] = [];
  for (const e of orderBySeq(ledger)) {
    if (e.type === "interviewer.utterance") {
      out.push({ seq: e.seq, ts: e.ts, speaker: "interviewer", lineId: e.lineId, text: e.text });
    } else if (e.type === "candidate.utterance") {
      out.push({ seq: e.seq, ts: e.ts, speaker: "candidate", lineId: e.lineId, text: e.text });
    }
  }
  return out;
}

/** Reduce edit/snapshot events to the current code buffer. */
export function selectCode(ledger: readonly VNextEvent[]): CodeState {
  let state: CodeState = { code: "", seq: 0 };
  for (const e of orderBySeq(ledger)) {
    if (e.type === "code.edited") {
      state = { code: e.after, seq: e.seq };
    } else if (e.type === "code.snapshot") {
      state = { code: e.full, seq: e.seq };
    }
  }
  return state;
}

/** Extract ordered run results. */
export function selectRuns(ledger: readonly VNextEvent[]): RunResult[] {
  const out: RunResult[] = [];
  for (const e of orderBySeq(ledger)) {
    if (e.type === "code.run") {
      out.push({
        seq: e.seq,
        ts: e.ts,
        runId: e.runId,
        code: e.code,
        stdout: e.stdout,
        exitCode: e.exitCode,
      });
    }
  }
  return out;
}

/** Latest phase implied by `phase.changed` events; `fallback` if none seen. */
export function selectCurrentPhase(
  ledger: readonly VNextEvent[],
  fallback: Phase,
): Phase {
  let phase = fallback;
  for (const e of orderBySeq(ledger)) {
    if (e.type === "phase.changed") phase = e.to;
  }
  return phase;
}
