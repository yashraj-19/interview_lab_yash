/**
 * Adapter factory.
 *
 * The UI never `new`s a concrete adapter — it asks {@link makeAdapter} for one
 * behind the {@link InterviewAdapter} seam. `mock` returns the deterministic
 * in-memory {@link MockInterviewAdapter}; `live` (a.k.a. live-scripted) returns
 * the {@link LiveInterviewAdapter} talking to the scripted E1–E2 backend.
 */

import type { InterviewAdapter } from "./adapter";
import { MockInterviewAdapter, type MockAdapterOptions } from "./mock-adapter";
import { LiveInterviewAdapter, type LiveAdapterOptions } from "./live-adapter";

// "live" === live-scripted (deterministic E1–E2 backend); "live-llm" === the
// OpenRouter-first E4/E5 backend interviewer + scorecard. Both use the same
// LiveInterviewAdapter — only the backend session `mode` differs.
export type AdapterMode = "mock" | "live" | "live-llm";

export interface MakeAdapterOptions extends MockAdapterOptions, Omit<LiveAdapterOptions, "sessionId" | "backendMode"> {
  mode: AdapterMode;
  /** Optional lab-only interview track (e.g. "incident-demo"). live-llm only. */
  track?: string;
}

/** Coerce an unknown (e.g. query-param) value to a valid AdapterMode. */
export function normalizeMode(value: string | null | undefined): AdapterMode {
  if (value === "live") return "live";
  if (value === "live-llm") return "live-llm";
  return "mock";
}

export function makeAdapter(opts: MakeAdapterOptions): InterviewAdapter {
  if (opts.mode === "live" || opts.mode === "live-llm") {
    return new LiveInterviewAdapter({
      sessionId: opts.sessionId,
      apiBase: opts.apiBase,
      wsBase: opts.wsBase,
      socketFactory: opts.socketFactory,
      fetchImpl: opts.fetchImpl,
      randomUUID: opts.randomUUID,
      backendMode: opts.mode === "live-llm" ? "llm" : "scripted",
      fakeLlm: opts.mode === "live-llm" ? opts.fakeLlm : false,
      track: opts.mode === "live-llm" ? opts.track : undefined,
    });
  }
  return new MockInterviewAdapter({
    sessionId: opts.sessionId,
    baseTs: opts.baseTs,
    tickMs: opts.tickMs,
    startSeq: opts.startSeq,
  });
}
