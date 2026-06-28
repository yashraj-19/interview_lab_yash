import { describe, it, expect } from "vitest";
import {
  PhaseController,
  PHASES,
  type TransitionContext,
} from "../state-machine";

const withRubric: TransitionContext = { hasRubric: true, lastSeq: 0 };
const noRubric: TransitionContext = { hasRubric: false, lastSeq: 0 };

describe("PhaseController", () => {
  it("walks the full linear path with legal signals", () => {
    const c = new PhaseController("intake");
    const path = [
      "intake.submitted",
      "rubric.generated",
      "session.start",
      "intro.done",
      "calibration.done",
      "framing.done",
      "coding.done",
      "debugging.done",
      "optimization.done",
      "wrap.done",
      "scoring.done",
    ] as const;
    for (const sig of path) {
      const r = c.request(sig, withRubric);
      expect(r.ok).toBe(true);
    }
    expect(c.phase).toBe("review");
  });

  it("rejects an illegal transition without mutating phase", () => {
    const c = new PhaseController("intake");
    const r = c.request("coding.done", withRubric);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("no_transition");
    expect(c.phase).toBe("intake");
  });

  it("blocks rubric.generated when the guard fails (no rubric)", () => {
    const c = new PhaseController("rubric");
    const r = c.request("rubric.generated", noRubric);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("guard_failed");
    expect(c.phase).toBe("rubric");
  });

  it("exposes exactly the specified phases in order", () => {
    expect([...PHASES]).toEqual([
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
    ]);
  });
});
