// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from "vitest";
import {
  saveSession,
  loadSession,
  patchSession,
  clearSession,
  type StoredSession,
} from "../session-store";
import { MockInterviewAdapter } from "../mock-adapter";
import type { Intake } from "../intake";

const intake: Intake = {
  resumeText: "built a payments service",
  jobDescription: "backend engineer",
  role: "Engineer",
  seniority: "mid",
  languages: ["python"],
  durationMinutes: 45,
};

async function buildSession(sessionId: string): Promise<StoredSession> {
  const adapter = new MockInterviewAdapter({ sessionId });
  const rubric = await adapter.generateRubric(intake);
  await adapter.start();
  const ledger = [...adapter.getLedger()];
  let scorecard;
  for await (const u of adapter.generateScorecard()) {
    if (u.kind === "complete") scorecard = u.draft;
  }
  return { sessionId, intake, rubric, ledger, scorecard };
}

describe("session-store", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("roundtrips intake + rubric + ledger + scorecard", async () => {
    const session = await buildSession("rt");
    saveSession(session);
    const loaded = loadSession("rt");
    expect(loaded).toEqual(session);
    expect(loaded?.ledger?.length).toBeGreaterThan(0);
    expect(loaded?.scorecard?.stage).toBe("complete");
  });

  it("returns null for a missing entry", () => {
    expect(loadSession("does-not-exist")).toBeNull();
  });

  it("returns null (no throw) for a corrupt entry", () => {
    window.localStorage.setItem("iv3-session:bad", "{not valid json");
    expect(() => loadSession("bad")).not.toThrow();
    expect(loadSession("bad")).toBeNull();
  });

  it("returns null (no throw) for a wrong-shape entry", () => {
    window.localStorage.setItem("iv3-session:shape", JSON.stringify({ foo: 1 }));
    expect(loadSession("shape")).toBeNull();
  });

  it("patchSession merges into an existing entry and no-ops when missing", async () => {
    const session = await buildSession("patch");
    saveSession({ sessionId: "patch", intake: session.intake, rubric: session.rubric });
    patchSession("patch", { ledger: session.ledger, scorecard: session.scorecard });
    expect(loadSession("patch")?.scorecard?.stage).toBe("complete");

    patchSession("nope", { ledger: [] });
    expect(loadSession("nope")).toBeNull();
  });

  it("clearSession removes the entry", async () => {
    const session = await buildSession("clr");
    saveSession(session);
    clearSession("clr");
    expect(loadSession("clr")).toBeNull();
  });
});
