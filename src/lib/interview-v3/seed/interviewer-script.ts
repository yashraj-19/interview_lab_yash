/**
 * Deterministic seed data for the mock adapter.
 *
 * Two parts:
 *  1. Rubric templates → deterministic criteria + base weights, tuned by the
 *     normalized intake context. NO LLM, NO randomness.
 *  2. A scripted session → the ordered turns the mock replays (intro → resume
 *     calibration → problem framing → coding → debugging → wrap-up).
 *
 * Same inputs always yield the identical rubric and ledger.
 */

import type { InterviewContext } from "../intake";
import type { Criterion } from "../rubric";
import type { Phase, AdvanceSignal } from "../state-machine";

interface CriterionTemplate {
  id: string;
  name: string;
  description: string;
  baseWeight: number;
  signals: string[];
  phaseHints: Phase[];
}

const BASE_TEMPLATES: CriterionTemplate[] = [
  {
    id: "problem_solving",
    name: "Problem solving",
    description: "Decomposes the problem, reasons about edge cases, picks a viable approach.",
    baseWeight: 30,
    signals: ["states assumptions", "enumerates edge cases", "compares approaches"],
    phaseHints: ["problem_framing", "coding"],
  },
  {
    id: "coding",
    name: "Coding ability",
    description: "Writes correct, readable code and translates the plan into working software.",
    baseWeight: 30,
    signals: ["compiles/runs", "handles inputs", "clear naming"],
    phaseHints: ["coding", "debugging"],
  },
  {
    id: "communication",
    name: "Communication",
    description: "Explains thinking clearly and responds well to hints and questions.",
    baseWeight: 20,
    signals: ["thinks aloud", "answers directly", "incorporates feedback"],
    phaseHints: ["intro", "resume_calibration", "wrap_up"],
  },
  {
    id: "system_design",
    name: "Design & tradeoffs",
    description: "Reasons about complexity, scale, and tradeoffs in the chosen design.",
    baseWeight: 20,
    signals: ["analyzes complexity", "discusses tradeoffs", "considers scale"],
    phaseHints: ["optimization", "debugging"],
  },
];

/** Deterministic per-context weight adjustment, applied before normalization. */
export function buildCriteria(ctx: InterviewContext): Criterion[] {
  return BASE_TEMPLATES.map((t) => {
    let weight = t.baseWeight;
    if (t.id === "system_design") {
      if (ctx.seniority === "senior" || ctx.seniority === "staff") weight += 10;
      if (ctx.emphasizesScale) weight += 5;
    }
    if (t.id === "coding" && (ctx.seniority === "intern" || ctx.seniority === "junior")) {
      weight += 10;
    }
    if (t.id === "communication" && ctx.emphasizesFrontend) weight += 3;
    return {
      id: t.id,
      name: t.name,
      description: t.description,
      weight,
      signals: t.signals,
      phaseHints: t.phaseHints,
    };
  });
}

export interface ScriptTurn {
  /** Advance the phase via this signal BEFORE emitting the turn's events. */
  advance?: AdvanceSignal;
  events: ScriptEvent[];
}

export type ScriptEvent =
  | { kind: "interviewer.utterance"; lineId: string; text: string }
  | { kind: "candidate.utterance"; lineId: string; text: string }
  | { kind: "code.edited"; editId: string; after: string }
  | { kind: "code.run"; runId: string; code: string; stdout: string; exitCode: number };

const TWO_SUM = [
  "def two_sum(nums, target):",
  "    seen = {}",
  "    for i, n in enumerate(nums):",
  "        if target - n in seen:",
  "            return [seen[target - n], i]",
  "        seen[n] = i",
  "    return []",
].join("\n");

/**
 * The scripted session. Each turn may request a phase advance, then emits its
 * events. The mock adapter replays this in order with monotonic seqs.
 */
export const SCRIPTED_SESSION: ScriptTurn[] = [
  {
    advance: "session.start",
    events: [
      {
        kind: "interviewer.utterance",
        lineId: "L1",
        text: "Hi, thanks for joining. I'm Maya and I'll be running today's session. Ready to start?",
      },
    ],
  },
  {
    advance: "intro.done",
    events: [
      {
        kind: "candidate.utterance",
        lineId: "L2",
        text: "Yes, ready. Happy to be here.",
      },
      {
        kind: "interviewer.utterance",
        lineId: "L3",
        text: "Great. I saw on your resume you worked on a payments service — tell me about the hardest bug there.",
      },
    ],
  },
  {
    advance: "calibration.done",
    events: [
      {
        kind: "candidate.utterance",
        lineId: "L4",
        text: "We had a race condition double-charging cards under retries; I fixed it with an idempotency key.",
      },
      {
        kind: "interviewer.utterance",
        lineId: "L5",
        text: "Nice. Let's do a coding problem. Given an array and a target, return indices of two numbers that sum to it.",
      },
    ],
  },
  {
    advance: "framing.done",
    events: [
      {
        kind: "candidate.utterance",
        lineId: "L6",
        text: "I'll use a hash map of value to index so it's one pass, O(n) time and O(n) space.",
      },
    ],
  },
  {
    advance: "coding.done",
    events: [
      { kind: "code.edited", editId: "E1", after: TWO_SUM },
      {
        kind: "code.run",
        runId: "R1",
        code: TWO_SUM,
        stdout: "[0, 1]\n",
        exitCode: 0,
      },
    ],
  },
  {
    advance: "debugging.done",
    events: [
      {
        kind: "interviewer.utterance",
        lineId: "L7",
        text: "What happens with an empty array or no valid pair?",
      },
      {
        kind: "candidate.utterance",
        lineId: "L8",
        text: "It returns an empty list — the loop just never finds a match.",
      },
    ],
  },
  {
    advance: "optimization.done",
    events: [
      {
        kind: "candidate.utterance",
        lineId: "L9",
        text: "If the input were sorted I could use two pointers for O(1) space, trading the hash map away.",
      },
    ],
  },
  {
    advance: "wrap.done",
    events: [
      {
        kind: "interviewer.utterance",
        lineId: "L10",
        text: "That's all I had. Thanks — we'll follow up with next steps.",
      },
    ],
  },
];
