import { describe, expect, it } from "vitest";

import { computeConversationMetrics, type AnyEvent } from "../metrics";

let seq = 0;
function ev(type: string, ts: number, extra: Record<string, unknown> = {}): AnyEvent {
  seq += 1;
  return { type, seq, ts, ...extra } as AnyEvent;
}

describe("computeConversationMetrics", () => {
  it("computes response gaps candidate → next interviewer line", () => {
    const m = computeConversationMetrics([
      ev("candidate.utterance", 1000),
      ev("interviewer.utterance", 1800),          // gap 800
      ev("candidate.utterance", 5000),
      ev("interviewer.utterance", 5400),          // gap 400
    ]);
    expect(m.responseCount).toBe(2);
    expect(m.responseGapMedianMs).toBe(600);      // median of 400, 800
    expect(m.responseGapP90Ms).toBe(800);
  });

  it("pairs generation latency by turnId", () => {
    const m = computeConversationMetrics([
      ev("interviewer.turn.started", 1000, { turnId: "t1" }),
      ev("interviewer.utterance", 3500, { turnId: "t1" }),
    ]);
    expect(m.generationMedianMs).toBe(2500);
  });

  it("counts barge-ins, cancelled turns, and pause discipline incl. superseded", () => {
    const m = computeConversationMetrics([
      ev("barge_in.detected", 1),
      ev("interviewer.cancelled", 2, { turnId: "t1" }),
      ev("system.pause.scheduled", 3),
      ev("system.pause.cancelled", 4, { reason: "superseded" }),
      ev("system.pause.scheduled", 5),
      ev("system.pause.completed", 6),
    ]);
    expect(m.bargeIns).toBe(1);
    expect(m.cancelledTurns).toBe(1);
    expect(m.pausesScheduled).toBe(2);
    expect(m.pausesCompleted).toBe(1);
    expect(m.pausesCancelled).toBe(1);
    expect(m.pausesSuperseded).toBe(1);
  });

  it("tracks hint depth, throttles, nudges, guards, stalls", () => {
    const m = computeConversationMetrics([
      ev("interviewer.utterance", 1, { hint_for: "help", hint_step: 1 }),
      ev("interviewer.utterance", 2, { hint_throttled: "help" }),
      ev("interviewer.utterance", 3, { hint_for: "help", hint_step: 2 }),
      ev("interviewer.utterance", 4, { nudgeLevel: 1 }),
      ev("interviewer.utterance", 5, { guarded: true, guard_reasons: ["confirm_deny"] }),
      ev("interviewer.utterance", 6, { stallRecovered: true }),
    ]);
    expect(m.hintCount).toBe(2);
    expect(m.maxHintStep).toBe(2);
    expect(m.throttledHints).toBe(1);
    expect(m.silenceNudges).toBe(1);
    expect(m.guardedLines).toBe(1);
    expect(m.stallRecoveries).toBe(1);
  });

  it("records run progression in order", () => {
    const m = computeConversationMetrics([
      ev("code.run", 1, { passed: 0, total: 3 }),
      ev("code.run", 2, { passed: 3, total: 3 }),
    ]);
    expect(m.runProgression).toEqual([{ passed: 0, total: 3 }, { passed: 3, total: 3 }]);
  });

  it("is safe on an empty ledger", () => {
    const m = computeConversationMetrics([]);
    expect(m.responseGapMedianMs).toBeNull();
    expect(m.generationMedianMs).toBeNull();
    expect(m.runProgression).toEqual([]);
  });
});
