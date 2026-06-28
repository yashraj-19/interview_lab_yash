import { describe, it, expect } from "vitest";
import { MockInterviewAdapter } from "../mock-adapter";
import {
  orderBySeq,
  selectTranscript,
  selectCode,
  selectRuns,
  selectCurrentPhase,
} from "../ledger";

async function runSession(sessionId: string) {
  const adapter = new MockInterviewAdapter({ sessionId });
  await adapter.generateRubric({
    resumeText: "",
    jobDescription: "",
    role: "Engineer",
    seniority: "mid",
    languages: ["python"],
    durationMinutes: 45,
  });
  await adapter.start();
  return adapter;
}

describe("ledger selectors", () => {
  it("orderBySeq returns a stable ascending copy without mutating input", async () => {
    const a = await runSession("s");
    const ledger = a.getLedger();
    const shuffled = [...ledger].reverse();
    const ordered = orderBySeq(shuffled);
    expect(ordered.map((e) => e.seq)).toEqual(ledger.map((e) => e.seq));
    // input untouched
    expect(shuffled.map((e) => e.seq)).toEqual([...ledger].reverse().map((e) => e.seq));
  });

  it("selectTranscript yields ordered interviewer/candidate turns", async () => {
    const a = await runSession("s");
    const turns = selectTranscript(a.getLedger());
    expect(turns[0]).toMatchObject({ speaker: "interviewer", lineId: "L1" });
    expect(turns.every((t, i) => i === 0 || turns[i - 1].seq < t.seq)).toBe(true);
    expect(turns.some((t) => t.speaker === "candidate")).toBe(true);
  });

  it("selectCode reduces to the latest code buffer", async () => {
    const a = await runSession("s");
    const state = selectCode(a.getLedger());
    expect(state.code).toContain("def two_sum");
    expect(state.seq).toBeGreaterThan(0);
  });

  it("selectRuns extracts run results", async () => {
    const a = await runSession("s");
    const runs = selectRuns(a.getLedger());
    expect(runs.length).toBe(1);
    expect(runs[0]).toMatchObject({ runId: "R1", exitCode: 0 });
  });

  it("selectCurrentPhase tracks the latest phase.changed", async () => {
    const a = await runSession("s");
    expect(selectCurrentPhase(a.getLedger(), "ready")).toBe("scoring");
    expect(selectCurrentPhase([], "ready")).toBe("ready");
  });
});
