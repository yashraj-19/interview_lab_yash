import { describe, it, expect } from "vitest";
import { MockInterviewAdapter } from "../mock-adapter";
import {
  buildReviewModel,
  resolveEvidence,
  evidenceTargetForSeq,
  selectCitedSeqs,
  type ScorecardLike,
} from "../review-selectors";
import type { CriterionScore, ScorecardDraft } from "../scorecard";

const INTAKE = {
  resumeText: "",
  jobDescription: "",
  role: "Engineer",
  seniority: "mid" as const,
  languages: ["python"],
  durationMinutes: 45,
};

async function runScored(sessionId: string) {
  const adapter = new MockInterviewAdapter({ sessionId });
  const rubric = await adapter.generateRubric(INTAKE);
  await adapter.start();
  const scores: CriterionScore[] = [];
  let draft: ScorecardDraft | null = null;
  for await (const u of adapter.generateScorecard()) {
    if (u.kind === "criterion") scores.push(u.score);
    else if (u.kind === "complete") draft = u.draft;
  }
  return { adapter, rubric, scores, draft: draft! };
}

describe("review-selectors", () => {
  it("staged scorecard is deterministic across identical sessions", async () => {
    const a = await runScored("s");
    const b = await runScored("s");
    expect(a.scores).toEqual(b.scores);
    expect(a.draft).toEqual(b.draft);
  });

  it("every evidence seq resolves to an existing ledger event", async () => {
    const { adapter, draft } = await runScored("s");
    const ledger = adapter.getLedger();
    for (const s of draft.scores) {
      for (const ref of s.evidence) {
        const resolved = resolveEvidence(ledger, ref);
        expect(resolved.invalid).toBe(false);
        expect(resolved.event?.seq).toBe(ref.seq);
      }
    }
  });

  it("buildReviewModel returns evidence-linked rows with names from the rubric", async () => {
    const { adapter, rubric, draft } = await runScored("s");
    const model = buildReviewModel(adapter.getLedger(), draft, rubric);
    expect(model.rows.length).toBe(draft.scores.length);
    expect(model.overall).toBe(draft.overall);
    expect(model.invalidEvidenceCount).toBe(0);
    const ps = model.rows.find((r) => r.criterionId === "problem_solving");
    expect(ps?.name).toBe("Problem solving");
    expect(ps?.evidenceCount).toBeGreaterThan(0);
    expect(ps?.evidence.every((e) => e.event !== null)).toBe(true);
  });

  it("evidenceTargetForSeq routes utterance/code/run/missing seqs to panels", async () => {
    const { adapter, draft } = await runScored("s");
    const ledger = adapter.getLedger();
    const byKind = (kind: string) =>
      draft.scores.flatMap((s) => s.evidence).find((e) => e.kind === kind);
    const utter = byKind("utterance");
    const codeRef = byKind("code");
    const runRef = byKind("code_run");
    expect(utter && evidenceTargetForSeq(ledger, utter.seq)).toBe("transcript");
    expect(codeRef && evidenceTargetForSeq(ledger, codeRef.seq)).toBe("code");
    expect(runRef && evidenceTargetForSeq(ledger, runRef.seq)).toBe("run");
    expect(evidenceTargetForSeq(ledger, 99999)).toBe("missing");
  });

  it("selectCitedSeqs collects every distinct cited seq", async () => {
    const { draft } = await runScored("s");
    const cited = selectCitedSeqs(draft.scores);
    for (const s of draft.scores) {
      for (const ref of s.evidence) expect(cited.has(ref.seq)).toBe(true);
    }
  });

  it("reports an unresolvable evidence seq as invalid/missing", async () => {
    const { adapter } = await runScored("s");
    const ledger = adapter.getLedger();
    const bogus = resolveEvidence(ledger, {
      kind: "utterance",
      seq: 99999,
      excerpt: "ghost",
    });
    expect(bogus.event).toBeNull();
    expect(bogus.invalid).toBe(true);
  });

  it("buildReviewModel flags invalid evidence and empty-evidence risks", async () => {
    const { adapter, rubric } = await runScored("s");
    const ledger = adapter.getLedger();
    const card: ScorecardLike = {
      overall: null,
      scores: [
        {
          criterionId: "problem_solving",
          score: 70,
          weight: 30,
          verdict: "mixed",
          evidence: [{ kind: "utterance", seq: 99999, excerpt: "ghost" }],
          gaps: [],
        },
        {
          criterionId: "coding",
          score: 50,
          weight: 30,
          verdict: "insufficient_evidence",
          evidence: [],
          gaps: [],
        },
      ],
    };
    const model = buildReviewModel(ledger, card, rubric);
    expect(model.invalidEvidenceCount).toBe(1);
    const ps = model.rows[0];
    expect(ps.invalidEvidenceCount).toBe(1);
    expect(ps.evidence[0].invalid).toBe(true);
    expect(ps.risks.some((r) => /do not resolve/.test(r))).toBe(true);
    const coding = model.rows[1];
    expect(coding.risks.some((r) => /No evidence/.test(r))).toBe(true);
  });
});
