/**
 * Live (scripted) InterviewAdapter for vNext Phase E3.
 *
 * A true drop-in for {@link MockInterviewAdapter} behind the {@link InterviewAdapter}
 * seam, but instead of replaying the script locally it talks to the already-built
 * E1–E2 scripted backend over REST + WebSocket (NO LLM):
 *
 *  - generateRubric  → POST /vnext/interview/sessions  then POST .../rubric
 *  - start           → connect the WS (resume handshake via InterviewTransport),
 *                      then drive the scripted advances and resolve once the
 *                      final scripted event has streamed back from the server.
 *  - sendCandidateText / sendCode → typed WS frames; the SERVER echoes the
 *                      authoritative envelopes back (no locally-minted seqs).
 *  - runCode         → candidate.run frame; resolves on the server's code.run.
 *  - requestAdvance  → advance.request frame; the synchronous boolean comes from
 *                      a local shadow PhaseController.evaluate (same TS table),
 *                      while the authoritative phase.changed still arrives from
 *                      the server.
 *  - generateScorecard → scorecard.request frame; yields one update per
 *                      scorecard.criterion.ready and a final complete draft.
 *
 * The local ledger is fed exclusively by server envelopes, applied with the
 * production idempotency rule (seq <= lastSeq → no-op) in strict seq order.
 */

import type { Intake } from "./intake";
import type { Rubric } from "./rubric";
import {
  PhaseController,
  type Phase,
  type AdvanceSignal,
} from "./state-machine";
import type { VNextEvent } from "./events";
import type { ScorecardDraft, CriterionScore } from "./scorecard";
import { selectCurrentPhase } from "./ledger";
import type {
  InterviewAdapter,
  ScorecardStageUpdate,
  Unsubscribe,
} from "./adapter";
import { SCRIPTED_SESSION, type ScriptEvent } from "./seed/interviewer-script";
import {
  InterviewTransport,
  type ConnectionState,
  type WebSocketLike,
} from "@/lib/interview-transport";
import { API_URL } from "@/lib/api-url";

type FetchLike = (
  input: string,
  init?: { method?: string; headers?: Record<string, string>; body?: string },
) => Promise<{ ok: boolean; status: number; json: () => Promise<unknown> }>;

export interface LiveAdapterOptions {
  /** UI-facing session id (informational; the backend mints its own). */
  sessionId: string;
  /** REST base, e.g. http://localhost:8000. Defaults to API_URL. */
  apiBase?: string;
  /** WS base, e.g. ws://localhost:8000. Defaults to API_URL with ws scheme. */
  wsBase?: string;
  /** Inject a socket (tests). Forwarded to InterviewTransport. */
  socketFactory?: (url: string) => WebSocketLike;
  /** Inject fetch (tests/SSR). Defaults to globalThis.fetch. */
  fetchImpl?: FetchLike;
  /** Inject a connection-id generator (tests). Defaults to crypto.randomUUID. */
  randomUUID?: () => string;
  /** Backend interviewer mode for the created session: "scripted" (default,
   *  deterministic E1–E2 backend) or "llm" (OpenRouter-first E4/E5 path). */
  backendMode?: "scripted" | "llm";
  /** TEST-ONLY: request the backend's deterministic fake-LLM path (honored only
   *  when the backend sets VNEXT_ALLOW_FAKE_LLM=1). Real WS/store/controller,
   *  canned interviewer/scorecard content, no OpenRouter latency. */
  fakeLlm?: boolean;
  /** Bound (ms) for awaiting scorecard.completed in generateScorecard before
   *  giving up with a terminal "failed" stage. Default 60_000. Configurable so
   *  tests can drive the timeout path without waiting a real minute. */
  scorecardTimeoutMs?: number;
  /** Optional lab-only interview track (e.g. "incident-demo") sent on create. */
  track?: string;
  /** Truthful connection-state stream for the UI (connected / reconnecting /
   *  failed, plus the attempt counter). Optional — defaults to silent. */
  onConnectionState?: (state: ConnectionState, attempts: number) => void;
}

/** Default bound for awaiting scorecard.completed (ms). Matches the backend's
 *  ~25s build budget with generous slack for provider/network latency. */
const DEFAULT_SCORECARD_TIMEOUT_MS = 60_000;

/** Resolve the http base for REST calls. */
function defaultApiBase(): string {
  return API_URL;
}

/** Derive a ws:// base from an http(s):// base. */
function wsFromHttp(httpBase: string): string {
  return httpBase.replace(/^http/i, "ws");
}

/** Does a streamed event correspond to a given scripted event (type + id)? */
function eventMatchesScript(e: VNextEvent, s: ScriptEvent): boolean {
  switch (s.kind) {
    case "interviewer.utterance":
      return e.type === "interviewer.utterance" && e.lineId === s.lineId;
    case "candidate.utterance":
      return e.type === "candidate.utterance" && e.lineId === s.lineId;
    case "code.edited":
      return e.type === "code.edited" && e.editId === s.editId;
    case "code.run":
      return e.type === "code.run" && e.runId === s.runId;
  }
}

const FALLBACK_PHASE: Phase = "ready";

export class LiveInterviewAdapter implements InterviewAdapter {
  private readonly sessionId: string;
  private readonly apiBase: string;
  private readonly wsBase: string;
  private readonly fetchImpl: FetchLike;
  private readonly connId: string;
  private readonly backendMode: "scripted" | "llm";
  private readonly fakeLlm: boolean;
  private readonly track?: string;
  private readonly scorecardTimeoutMs: number;

  private backendSessionId: string | null = null;
  private rubric: Rubric | null = null;

  private transport: InterviewTransport | null = null;
  private readonly socketFactory?: (url: string) => WebSocketLike;

  private ledger: VNextEvent[] = [];
  private lastSeq = 0;

  /** Outbound frames buffered while the transport is not yet resume-ready.
   *  Flushed in order on resume_ready so an early send is never dropped. */
  private outbox: Record<string, unknown>[] = [];

  private startPromise: Promise<void> | null = null;
  private stopped = false;

  private eventSubs = new Set<(e: VNextEvent) => void>();
  private stateSubs = new Set<(p: Phase) => void>();

  /** One-shot waiters keyed on a predicate over freshly-applied events. */
  private waiters: { predicate: (e: VNextEvent) => boolean; resolve: (e: VNextEvent) => void }[] = [];

  private readonly onConnectionState?: (state: ConnectionState, attempts: number) => void;

  constructor(opts: LiveAdapterOptions) {
    this.sessionId = opts.sessionId;
    this.apiBase = (opts.apiBase ?? defaultApiBase()).replace(/\/$/, "");
    this.wsBase = (opts.wsBase ?? wsFromHttp(this.apiBase)).replace(/\/$/, "");
    this.fetchImpl = opts.fetchImpl ?? (globalThis.fetch.bind(globalThis) as FetchLike);
    this.socketFactory = opts.socketFactory;
    this.backendMode = opts.backendMode ?? "scripted";
    this.fakeLlm = opts.fakeLlm ?? false;
    this.track = opts.track;
    this.onConnectionState = opts.onConnectionState;
    this.scorecardTimeoutMs = opts.scorecardTimeoutMs ?? DEFAULT_SCORECARD_TIMEOUT_MS;
    this.connId =
      opts.randomUUID?.() ??
      (typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `conn-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`);
  }

  // ── rubric (session create + rubric bind) ────────────────────────────────

  async generateRubric(intake: Intake): Promise<Rubric> {
    const created = await this.postJson(`/vnext/interview/sessions`, {
      intake,
      mode: this.backendMode,
      ...(this.fakeLlm ? { fake_llm: true } : {}),
      ...(this.track ? { track: this.track } : {}),
    });
    const backendSessionId = (created as { sessionId?: string }).sessionId;
    if (!backendSessionId) throw new Error("vnext: create session returned no sessionId");
    this.backendSessionId = backendSessionId;

    const bound = await this.postJson(
      `/vnext/interview/sessions/${backendSessionId}/rubric`,
      { intake },
    );
    const rubric = (bound as { rubric?: Rubric }).rubric;
    if (!rubric) throw new Error("vnext: rubric bind returned no rubric");
    this.rubric = rubric;
    return rubric;
  }

  // ── lifecycle ─────────────────────────────────────────────────────────────

  async start(): Promise<void> {
    if (this.stopped) return;
    // Like the mock: starting without a bound rubric is a clean no-op so a later
    // call (after generateRubric) can start cleanly.
    if (!this.rubric || !this.backendSessionId) return;
    if (this.startPromise) return this.startPromise;
    this.startPromise = this.run();
    return this.startPromise;
  }

  private async run(): Promise<void> {
    await this.connect();

    // live-llm: do NOT auto-drive the script. Send only the first advance
    // (ready → intro) and resolve once the FIRST interviewer turn has streamed.
    // All subsequent progression is candidate/UI-driven via sendCandidateText /
    // requestAdvance / generateScorecard.
    if (this.backendMode === "llm") {
      const after = this.lastSeq;
      const firstTurn = this.waitFor(
        (e) => e.type === "interviewer.utterance" && e.seq > after,
      );
      this.sendFrame({ type: "advance.request", signal: "session.start" });
      await firstTurn;
      return;
    }

    // Drive the scripted advances; the server emits phase.changed + scripted
    // events for each. Resolve once the final scripted event streams back.
    const lastTurn = SCRIPTED_SESSION[SCRIPTED_SESSION.length - 1];
    const terminal = lastTurn.events[lastTurn.events.length - 1];
    const terminalSeen = this.waitFor((e) => eventMatchesScript(e, terminal));

    for (const turn of SCRIPTED_SESSION) {
      if (turn.advance) this.sendFrame({ type: "advance.request", signal: turn.advance });
    }
    await terminalSeen;
  }

  private connect(): Promise<void> {
    if (this.transport) return Promise.resolve();
    const sid = this.backendSessionId;
    if (!sid) return Promise.reject(new Error("vnext: no backend session"));

    return new Promise<void>((resolve, reject) => {
      const transport = new InterviewTransport({
        url: `${this.wsBase}/vnext/interview/ws/${sid}`,
        socketFactory: this.socketFactory,
        getResume: () => ({
          session_id: sid,
          last_seq: this.lastSeq,
          client_conn_id: this.connId,
        }),
        onMessage: (data) => this.handleMessage(data),
        // Surface truthful connection state (reconnecting/failed + attempts)
        // so the room can show it instead of a stale "Connected" dot.
        onState: (state) => this.onConnectionState?.(state, transport.getAttempts()),
        onAttempt: (n) => this.onConnectionState?.(transport.getState(), n),
        onResumeReady: () => {
          this.flushOutbox();
          resolve();
        },
        onResumeRejected: (msg) =>
          reject(new Error(`vnext: resume rejected (${String(msg.reason ?? "unknown")})`)),
      });
      this.transport = transport;
      transport.connect();
    });
  }

  async stop(): Promise<void> {
    this.stopped = true;
    this.transport?.close();
    this.transport = null;
    this.eventSubs.clear();
    this.stateSubs.clear();
    this.waiters = [];
    this.outbox = [];
  }

  // ── inbound (server echoes authoritative envelopes) ───────────────────────

  sendCandidateText(text: string): void {
    this.sendFrame({ type: "candidate.text", text });
  }

  sendCode(code: string): void {
    this.sendFrame({ type: "candidate.code", code });
  }

  async runCode(code: string): Promise<string> {
    // Only resolve on a NEW run (seq beyond the current head) so an earlier
    // scripted code.run already on the ledger is not mistaken for this one.
    const after = this.lastSeq;
    const ran = this.waitFor((e) => e.type === "code.run" && e.seq > after);
    this.sendFrame({ type: "candidate.run", code });
    const ev = await ran;
    return ev.type === "code.run" ? ev.runId : "";
  }

  requestAdvance(signal: AdvanceSignal): boolean {
    // Synchronous answer from a local shadow controller built at the current
    // (server-derived) phase. The authoritative phase.changed still comes back
    // over the wire and updates the ledger.
    const phase = selectCurrentPhase(this.ledger, FALLBACK_PHASE);
    const result = new PhaseController(phase).evaluate(signal, {
      hasRubric: this.rubric !== null,
      lastSeq: this.lastSeq,
    });
    this.sendFrame({ type: "advance.request", signal });
    return result.ok;
  }

  bargeIn(turnId?: string): void {
    // Tell the server to cancel/obsolete the in-flight interviewer turn so its
    // late LLM output never streams back as the active question.
    this.sendFrame(turnId ? { type: "barge_in", turnId } : { type: "barge_in" });
  }

  notifySpeaking(): void {
    // Activity-only heartbeat (throttled by the room); resets the server's
    // silence timer so nudges don't fire while the candidate thinks aloud.
    this.sendFrame({ type: "candidate.speaking" });
  }

  acceptPatch(patchId: string): void {
    this.sendFrame({ type: "code.patch.accept", patchId });
  }

  rejectPatch(patchId: string): void {
    this.sendFrame({ type: "code.patch.reject", patchId });
  }

  // ── subscriptions ─────────────────────────────────────────────────────────

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
    return selectCurrentPhase(this.ledger, FALLBACK_PHASE);
  }

  // ── scorecard (staged, server-driven) ─────────────────────────────────────

  async *generateScorecard(): AsyncIterable<ScorecardStageUpdate> {
    // Terminal event: completed OR the additive scorecard.failed. We race this
    // against a wall-clock timeout so the iterable NEVER hangs if the backend
    // goes silent (stale socket, stalled provider, dropped connection).
    const terminal = this.waitFor(
      (e) => e.type === "scorecard.completed" || e.type === "scorecard.failed",
    );
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<"timeout">((resolve) => {
      timer = setTimeout(() => resolve("timeout"), this.scorecardTimeoutMs);
    });

    // Collect each criterion as it arrives; the run terminates on completion.
    const pending: CriterionScore[] = [];
    const unsub = this.onEvent((e) => {
      if (e.type === "scorecard.criterion.ready") pending.push(e.score);
    });
    this.sendFrame({ type: "scorecard.request" });

    const outcome = await Promise.race([terminal, timeout]);
    if (timer) clearTimeout(timer);
    unsub();

    if (outcome === "timeout") {
      yield { kind: "failed", reason: "timeout" };
      return;
    }

    // Emit whatever criteria streamed in before the terminal event.
    for (const score of pending) {
      yield { kind: "criterion", score };
    }

    if (outcome.type === "scorecard.failed") {
      yield { kind: "failed", reason: outcome.reason };
      return;
    }

    const draft: ScorecardDraft =
      outcome.type === "scorecard.completed"
        ? outcome.draft
        : { sessionId: this.sessionId, rubricId: "", stage: "complete", scores: pending, overall: null };
    yield { kind: "complete", draft };
  }

  // ── internals ─────────────────────────────────────────────────────────────

  private sendFrame(frame: Record<string, unknown>): void {
    if (this.stopped) return;
    // Only send once the server has confirmed the handshake (resume_ready). A
    // raw-open socket can accept a frame before the session is resumed; queue
    // until ready and flush in order so a frame is never silently dropped.
    const transport = this.transport;
    if (transport && transport.isReady() && transport.send(frame)) return;
    this.outbox.push(frame);
  }

  /** Flush any queued outbound frames once the transport is resume-ready. */
  private flushOutbox(): void {
    const transport = this.transport;
    if (!transport || !transport.isReady()) return;
    const pending = this.outbox;
    this.outbox = [];
    for (let i = 0; i < pending.length; i++) {
      if (!transport.send(pending[i])) {
        // Lost readiness mid-flush: re-queue this frame + the rest, in order.
        this.outbox = pending.slice(i);
        return;
      }
    }
  }

  /** Register a one-shot waiter that resolves on the first matching event. */
  private waitFor(predicate: (e: VNextEvent) => boolean): Promise<VNextEvent> {
    // If an already-applied event matches, resolve immediately.
    for (const e of this.ledger) {
      if (predicate(e)) return Promise.resolve(e);
    }
    return new Promise<VNextEvent>((resolve) => {
      this.waiters.push({ predicate, resolve });
    });
  }

  private handleMessage(data: unknown): void {
    if (!data || typeof data !== "object") return;
    const msg = data as Record<string, unknown>;
    // Resume backfill batch.
    if (msg.type === "resume_events" && Array.isArray(msg.events)) {
      for (const raw of msg.events) this.applyEvent(raw);
      return;
    }
    this.applyEvent(msg);
  }

  /** Apply a server envelope with the production idempotency / order rule. */
  private applyEvent(raw: unknown): void {
    if (!raw || typeof raw !== "object") return;
    const env = raw as Record<string, unknown>;
    const seq = env.seq;
    if (typeof seq !== "number" || typeof env.type !== "string") return;
    if (seq <= this.lastSeq) return; // re-apply is a no-op

    const event = env as unknown as VNextEvent;
    const prevPhase = selectCurrentPhase(this.ledger, FALLBACK_PHASE);
    this.ledger.push(event);
    this.lastSeq = seq;

    for (const cb of this.eventSubs) cb(event);

    if (event.type === "phase.changed") {
      const nextPhase = selectCurrentPhase(this.ledger, FALLBACK_PHASE);
      if (nextPhase !== prevPhase) {
        for (const cb of this.stateSubs) cb(nextPhase);
      }
    }

    // Fire any one-shot waiters whose predicate now matches.
    if (this.waiters.length > 0) {
      const still: typeof this.waiters = [];
      for (const w of this.waiters) {
        if (w.predicate(event)) w.resolve(event);
        else still.push(w);
      }
      this.waiters = still;
    }
  }

  private async postJson(path: string, body: unknown): Promise<unknown> {
    const res = await this.fetchImpl(`${this.apiBase}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`vnext: POST ${path} failed (${res.status})`);
    return res.json();
  }
}
