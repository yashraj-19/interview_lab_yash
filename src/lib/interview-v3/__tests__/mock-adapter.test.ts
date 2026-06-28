import { describe, it, expect } from "vitest";
import { MockInterviewAdapter } from "../mock-adapter";

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

describe("MockInterviewAdapter", () => {
  it("emits a strictly increasing seq sequence", async () => {
    const a = await runSession("s");
    const seqs = a.getLedger().map((e) => e.seq);
    for (let i = 1; i < seqs.length; i++) {
      expect(seqs[i]).toBe(seqs[i - 1] + 1);
    }
  });

  it("is deterministic: same input → identical ledger", async () => {
    const a = await runSession("s");
    const b = await runSession("s");
    expect(a.getLedger()).toEqual(b.getLedger());
  });

  it("uses a fixed clock derived from seq (no wall time)", async () => {
    const a = new MockInterviewAdapter({ sessionId: "s", baseTs: 1000, tickMs: 10 });
    await a.generateRubric({
      resumeText: "",
      jobDescription: "",
      role: "Engineer",
      seniority: "mid",
      languages: ["python"],
      durationMinutes: 45,
    });
    await a.start();
    const first = a.getLedger()[0];
    expect(first.ts).toBe(1000 + first.seq * 10);
  });

  it("start() WITHOUT a bound rubric emits no session events and stays ready", async () => {
    const a = new MockInterviewAdapter({ sessionId: "s" });
    const states: string[] = [];
    a.onState((p) => states.push(p));
    await a.start();
    expect(a.getPhase()).toBe("ready");
    expect(a.getLedger()).toEqual([]);
    expect(states).toEqual([]);
  });

  it("start() retries cleanly once a rubric is bound", async () => {
    const a = new MockInterviewAdapter({ sessionId: "s" });
    await a.start(); // rejected — no rubric yet
    expect(a.getLedger()).toEqual([]);
    await a.generateRubric({
      resumeText: "",
      jobDescription: "",
      role: "Engineer",
      seniority: "mid",
      languages: ["python"],
      durationMinutes: 45,
    });
    await a.start();
    expect(a.getPhase()).toBe("scoring");
  });

  it("start() WITH a bound rubric advances through the scripted session", async () => {
    const a = await runSession("s");
    expect(a.getPhase()).toBe("scoring");
    const phaseChanges = a
      .getLedger()
      .filter((e) => e.type === "phase.changed");
    // session.start → ... → wrap.done: 8 scripted advances landing at scoring.
    expect(phaseChanges.length).toBe(8);
    expect(phaseChanges[0]).toMatchObject({ from: "ready", to: "intro" });
  });

  it("lands at scoring after the scripted run and can advance to review", async () => {
    const a = await runSession("s");
    expect(a.getPhase()).toBe("scoring");
    const drafts = [];
    for await (const u of a.generateScorecard()) drafts.push(u);
    expect(drafts.at(-1)?.kind).toBe("complete");
    expect(a.requestAdvance("scoring.done")).toBe(true);
    expect(a.getPhase()).toBe("review");
  });

  it("scorecard cites real ledger seqs as evidence", async () => {
    const a = await runSession("s");
    await a.generateRubric({
      resumeText: "",
      jobDescription: "",
      role: "Engineer",
      seniority: "mid",
      languages: ["python"],
      durationMinutes: 45,
    });
    const seqs = new Set(a.getLedger().map((e) => e.seq));
    for await (const u of a.generateScorecard()) {
      if (u.kind === "criterion") {
        for (const ev of u.score.evidence) {
          expect(seqs.has(ev.seq)).toBe(true);
          expect(ev.seq).toBeGreaterThan(0);
        }
      }
    }
  });

  it("routes transitions through the controller (illegal advance rejected)", async () => {
    const adapter = new MockInterviewAdapter({ sessionId: "s" });
    // From initial "ready", coding.done has no transition.
    expect(adapter.requestAdvance("coding.done")).toBe(false);
  });
});
