/**
 * Deterministic in-memory InterviewAdapter for vNext Phase A.
 *
 * NO LLM, NO network, NO Date.now()/Math.random() on any path that affects the
 * ledger. seq and ts are minted from an injectable counter + clock so identical
 * inputs always produce a byte-identical ledger. This is the seam the live
 * adapter will later replace.
 */

import type { InterviewActor } from "@/lib/interview-protocol";
import type { Intake } from "./intake";
import { normalizeIntake } from "./intake";
import type { Rubric, Criterion } from "./rubric";
import { normalizeWeights } from "./rubric";
import {
  PhaseController,
  type Phase,
  type AdvanceSignal,
  type TransitionContext,
} from "./state-machine";
import type { EvidenceRef, VNextEvent } from "./events";
import type {
  CriterionScore,
  ScorecardDraft,
  Verdict,
} from "./scorecard";
import { aggregateOverall } from "./scorecard";
import type {
  InterviewAdapter,
  ScorecardStageUpdate,
  Unsubscribe,
} from "./adapter";
import { buildCriteria, SCRIPTED_SESSION, type ScriptEvent } from "./seed/interviewer-script";

export interface MockAdapterOptions {
  sessionId: string;
  /** Fixed base timestamp; each event gets baseTs + seq * tickMs. */
  baseTs?: number;
  tickMs?: number;
  /** First seq to assign (default 1). */
  startSeq?: number;
}

const DEFAULT_BASE_TS = 1_700_000_000_000; // fixed epoch ms — deterministic
const DEFAULT_TICK_MS = 1000;

/** Distributive payload: a VNextEvent shorn of its envelope fields. */
type EnvelopeKey = "v" | "seq" | "ts" | "sessionId" | "actor";
type VNextPayload = VNextEvent extends infer T
  ? T extends VNextEvent
    ? Omit<T, EnvelopeKey>
    : never
  : never;

/** Deterministic per-criterion scoring plan, citing scripted line/edit/run ids. */
interface ScorePlan {
  criterionId: string;
  score: number;
  verdict: Verdict;
  evidence: { kind: EvidenceRef["kind"]; refId: string; excerpt: string }[];
  gaps: string[];
}

const SCORE_PLANS: ScorePlan[] = [
  {
    criterionId: "problem_solving",
    score: 80,
    verdict: "strong",
    evidence: [
      { kind: "utterance", refId: "L6", excerpt: "one pass, O(n) time and O(n) space" },
      { kind: "utterance", refId: "L8", excerpt: "returns an empty list" },
    ],
    gaps: ["Did not discuss hash-collision / duplicate-value handling explicitly."],
  },
  {
    criterionId: "coding",
    score: 88,
    verdict: "strong",
    evidence: [
      { kind: "code", refId: "E1", excerpt: "def two_sum(nums, target):" },
      { kind: "code_run", refId: "R1", excerpt: "[0, 1]" },
    ],
    gaps: [],
  },
  {
    criterionId: "communication",
    score: 82,
    verdict: "strong",
    evidence: [
      { kind: "utterance", refId: "L4", excerpt: "fixed it with an idempotency key" },
      { kind: "utterance", refId: "L9", excerpt: "trading the hash map away" },
    ],
    gaps: [],
  },
  {
    criterionId: "system_design",
    score: 68,
    verdict: "mixed",
    evidence: [
      { kind: "utterance", refId: "L9", excerpt: "two pointers for O(1) space" },
    ],
    gaps: ["Tradeoff discussion stayed at single-problem scope; no broader system framing."],
  },
];

export class MockInterviewAdapter implements InterviewAdapter {
  private readonly sessionId: string;
  private readonly baseTs: number;
  private readonly tickMs: number;
  private readonly controller = new PhaseController("ready");

  private seq: number;
  private lastSeq = 0;
  private ledger: VNextEvent[] = [];
  private rubric: Rubric | null = null;
  private started = false;
  private stopped = false;

  private eventSubs = new Set<(e: VNextEvent) => void>();
  private stateSubs = new Set<(p: Phase) => void>();

  constructor(opts: MockAdapterOptions) {
    this.sessionId = opts.sessionId;
    this.baseTs = opts.baseTs ?? DEFAULT_BASE_TS;
    this.tickMs = opts.tickMs ?? DEFAULT_TICK_MS;
    this.seq = opts.startSeq ?? 1;
  }

  // ── lifecycle ────────────────────────────────────────────────────────────

  async start(): Promise<void> {
    if (this.started || this.stopped) return;
    // The Controller owns the opening transition. Ask it to perform
    // `session.start` FIRST; if it is rejected (e.g. no rubric bound) we emit
    // nothing, leave the phase at "ready", and stay un-started so a later call
    // (after a rubric is bound) can start cleanly.
    if (!this.requestAdvance("session.start")) return;
    this.started = true;
    for (const turn of SCRIPTED_SESSION) {
      // The leading `session.start` advance was already applied above; never
      // request it twice.
      if (turn.advance && turn.advance !== "session.start") {
        this.requestAdvance(turn.advance);
      }
      for (const ev of turn.events) this.appendScriptEvent(ev);
    }
  }

  async stop(): Promise<void> {
    this.stopped = true;
    this.eventSubs.clear();
    this.stateSubs.clear();
  }

  // ── inbound ──────────────────────────────────────────────────────────────

  sendCandidateText(text: string): void {
    this.append("candidate", {
      type: "candidate.utterance",
      lineId: `cand-${this.seq}`,
      text,
    });
  }

  sendCode(code: string): void {
    this.append("candidate", {
      type: "code.edited",
      editId: `edit-${this.seq}`,
      after: code,
      by: "candidate",
    });
  }

  async runCode(code: string): Promise<string> {
    const runId = `run-${this.seq}`;
    this.append("candidate", {
      type: "code.run",
      runId,
      code,
      stdout: "",
      exitCode: 0,
    });
    return runId;
  }

  requestAdvance(signal: AdvanceSignal): boolean {
    const ctx: TransitionContext = { hasRubric: this.rubric !== null, lastSeq: this.lastSeq };
    const result = this.controller.request(signal, ctx);
    if (!result.ok) return false;
    this.append("system", {
      type: "phase.changed",
      from: result.from,
      to: result.to,
      signal: result.signal,
    });
    for (const cb of this.stateSubs) cb(result.to);
    return true;
  }

  /** Mock has no in-flight backend turn to cancel — barge-in is a UI concern. */
  bargeIn(): void {
    // no-op
  }

  /** Mock has no live AI code actions. */
  acceptPatch(): void {
    // no-op
  }

  rejectPatch(): void {
    // no-op
  }

  // ── subscriptions ──────────────────────────────────────────────────────────

  onEvent(cb: (e: VNextEvent) => void): Unsubscribe {
    this.eventSubs.add(cb);
    return () => this.eventSubs.delete(cb);
  }

  onState(cb: (p: Phase) => void): Unsubscribe {
    this.stateSubs.add(cb);
    return () => this.stateSubs.delete(cb);
  }

  getLedger(): readonly VNextEvent[] {
    return this.ledger;
  }

  getPhase(): Phase {
    return this.controller.phase;
  }

  // ── rubric ───────────────────────────────────────────────────────────────

  async generateRubric(intake: Intake): Promise<Rubric> {
    const ctx = normalizeIntake(intake);
    const raw: Criterion[] = buildCriteria(ctx);
    const normalized = normalizeWeights(raw.map((c) => c.weight));
    const criteria = raw.map((c, i) => ({ ...c, weight: normalized[i] }));
    const rubric: Rubric = {
      id: `rubric-${this.sessionId}`,
      criteria,
      generatedBy: "mock",
      version: 1,
    };
    this.rubric = rubric;
    this.append("system", { type: "rubric.bound", rubric });
    return rubric;
  }

  // ── scorecard (staged) ─────────────────────────────────────────────────────

  async *generateScorecard(): AsyncIterable<ScorecardStageUpdate> {
    const rubricId = this.rubric?.id ?? `rubric-${this.sessionId}`;
    const scores: CriterionScore[] = [];
    const weightOf = (id: string) =>
      this.rubric?.criteria.find((c) => c.id === id)?.weight ?? 0;

    for (const plan of SCORE_PLANS) {
      const score: CriterionScore = {
        criterionId: plan.criterionId,
        score: plan.score,
        weight: weightOf(plan.criterionId),
        verdict: plan.verdict,
        evidence: plan.evidence.map((e) => this.resolveEvidence(e.kind, e.refId, e.excerpt)),
        gaps: plan.gaps,
      };
      scores.push(score);
      this.append("system", { type: "scorecard.criterion.ready", score });
      yield { kind: "criterion", score };
    }

    const draft: ScorecardDraft = {
      sessionId: this.sessionId,
      rubricId,
      stage: "complete",
      scores,
      overall: aggregateOverall(scores),
    };
    this.append("system", { type: "scorecard.completed", draft });
    yield { kind: "complete", draft };
  }

  // ── internals ──────────────────────────────────────────────────────────────

  /** Resolve a scripted ref id to its real ledger seq, citing actual evidence. */
  private resolveEvidence(
    kind: EvidenceRef["kind"],
    refId: string,
    excerpt: string,
  ): EvidenceRef {
    const seq = this.findSeqByRefId(refId);
    return { kind, seq, excerpt };
  }

  private findSeqByRefId(refId: string): number {
    for (const e of this.ledger) {
      if (
        (e.type === "interviewer.utterance" || e.type === "candidate.utterance") &&
        e.lineId === refId
      ) {
        return e.seq;
      }
      if (e.type === "code.edited" && e.editId === refId) return e.seq;
      if (e.type === "code.run" && e.runId === refId) return e.seq;
    }
    return 0;
  }

  private appendScriptEvent(ev: ScriptEvent): void {
    switch (ev.kind) {
      case "interviewer.utterance":
        this.append("interviewer", {
          type: "interviewer.utterance",
          lineId: ev.lineId,
          text: ev.text,
        });
        break;
      case "candidate.utterance":
        this.append("candidate", {
          type: "candidate.utterance",
          lineId: ev.lineId,
          text: ev.text,
        });
        break;
      case "code.edited":
        this.append("candidate", {
          type: "code.edited",
          editId: ev.editId,
          after: ev.after,
          by: "candidate",
        });
        break;
      case "code.run":
        this.append("candidate", {
          type: "code.run",
          runId: ev.runId,
          code: ev.code,
          stdout: ev.stdout,
          exitCode: ev.exitCode,
        });
        break;
    }
  }

  /**
   * Mint a seq-ordered, deterministically-timestamped event and append it.
   * Idempotent by construction: seqs only ever increase; `lastSeq` tracks the
   * highest applied, matching the production protocol's re-apply-is-no-op rule.
   */
  private append(actor: InterviewActor, body: VNextPayload): void {
    const seq = this.seq++;
    const event = {
      v: 1 as const,
      seq,
      ts: this.baseTs + seq * this.tickMs,
      sessionId: this.sessionId,
      actor,
      ...body,
    } as VNextEvent;
    this.ledger.push(event);
    this.lastSeq = seq;
    for (const cb of this.eventSubs) cb(event);
  }
}
