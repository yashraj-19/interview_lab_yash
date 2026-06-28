import { describe, it, expect } from "vitest";
import { MockInterviewAdapter } from "../mock-adapter";
import { normalizeWeights } from "../rubric";
import type { Intake } from "../intake";

const intake: Intake = {
  resumeText: "Built a payments backend handling latency at scale.",
  jobDescription: "Senior backend engineer, distributed systems.",
  role: "Backend Engineer",
  seniority: "senior",
  languages: ["Python", "python", " Go "],
  durationMinutes: 45,
};

describe("rubric generation", () => {
  it("normalizes weights to sum exactly 100", () => {
    expect(normalizeWeights([3, 3, 3]).reduce((a, b) => a + b, 0)).toBe(100);
    expect(normalizeWeights([10, 20, 30, 40]).reduce((a, b) => a + b, 0)).toBe(100);
    expect(normalizeWeights([0, 0]).reduce((a, b) => a + b, 0)).toBe(100);
  });

  it("produces a rubric whose weights sum to 100", async () => {
    const adapter = new MockInterviewAdapter({ sessionId: "t" });
    const rubric = await adapter.generateRubric(intake);
    expect(rubric.criteria.reduce((a, c) => a + c.weight, 0)).toBe(100);
    expect(rubric.generatedBy).toBe("mock");
  });

  it("is deterministic: same intake → identical rubric", async () => {
    const a = await new MockInterviewAdapter({ sessionId: "t" }).generateRubric(intake);
    const b = await new MockInterviewAdapter({ sessionId: "t" }).generateRubric(intake);
    expect(a).toEqual(b);
  });
});
