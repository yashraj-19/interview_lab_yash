/**
 * Interview Event Protocol — v1
 *
 * The interview room UI is a dumb renderer of this event stream. Sources:
 * scripted demo, live AI (WebSocket), replay (session_events), human mock.
 * See docs/INTERVIEW_ROOM_BUILD_PLAN_2026-06-11.md.
 *
 * Rules:
 * - Order by `seq` (monotonic per session). Never order by `ts` (clock skew).
 * - Events are idempotent to re-apply; re-applying seq <= lastApplied is a no-op.
 * - Renderers MUST ignore unknown event types (forward compatibility).
 * - v1 evolves additively only: new types / new optional fields. Breaking
 *   changes require v2.
 * - `demo.*` events are demo-lab presentation extensions, not part of the
 *   production vocabulary; production sources never emit them.
 */

export type InterviewActor = "interviewer" | "candidate" | "system";
export type InterviewStage =
  | "greeting"
  | "understanding"
  | "board"
  | "coding"
  | "constraint"
  | "rewrite"
  | "complete";

export type BoardSnapshot = {
  visible: boolean;
  input: string[];
  counts: Record<string, number>;
  queue: string[];
  note: string;
  activeIndex: number;
};

type Envelope = {
  v: 1;
  seq: number;
  /** Server epoch ms (source clock for the scripted demo). */
  ts: number;
  sessionId: string;
  actor: InterviewActor;
};

export type InterviewEvent = Envelope &
  (
    | { type: "session.started"; mode: "demo" | "ai" | "human" }
    | {
        type: "session.ended";
        reason: "completed" | "abandoned" | "error" | "integrity";
      }
    | { type: "stage.changed"; stage: InterviewStage }
    | { type: "problem.revealed"; problemId: string }
    | {
        type: "line.started";
        lineId: string;
        /** Full text the line will speak; words reveal via word.spoken. */
        text: string;
        /** Optional status label shown while the line plays. */
        activity?: string;
      }
    | { type: "word.spoken"; lineId: string; wordIndex: number }
    | {
        /** Provisional ASR text for an in-flight candidate line; superseded by finals. */
        type: "transcript.partial";
        lineId: string;
        text: string;
      }
    | { type: "line.ended"; lineId: string; complete: boolean }
    | {
        type: "speaker.interrupted";
        lineId: string;
        byActor: InterviewActor;
        atWord: number;
      }
    | {
        type: "code.edited";
        editId: string;
        /** Full editor text after the edit — idempotent by construction. */
        after: string;
        by: InterviewActor;
        /** Optional status label ("Maya, interviewer editing"). */
        activityLabel?: string;
      }
    | {
        type: "selection.set";
        selection: { start: number; end: number; owner: InterviewActor } | null;
      }
    | { type: "highlight.set"; line: number | null }
    | { type: "control.passed"; from: InterviewActor; to: InterviewActor }
    | { type: "board.step"; state: BoardSnapshot }
    | {
        /** Periodic keyframe so replay can scrub without reducing from zero. */
        type: "code.snapshot";
        full: string;
      }
    | { type: "report.ready"; reportId: string }
    // ---- demo-lab presentation extensions (not production vocabulary) ----
    | { type: "demo.callout"; text: string }
    | { type: "demo.progress"; value: number }
    | { type: "demo.report_shown" }
  );

export type InterviewEventType = InterviewEvent["type"];

/** Convenience: payload of a given event type. */
export type EventOf<T extends InterviewEventType> = Extract<
  InterviewEvent,
  { type: T }
>;
