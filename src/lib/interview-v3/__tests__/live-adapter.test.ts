/**
 * Deterministic adapter-mechanics test for {@link LiveInterviewAdapter}.
 *
 * Uses an injected fake WebSocket that emulates the E1–E2 backend WS contract
 * (resume handshake → backfill, scripted advances, candidate echo, scorecard
 * streaming). This exercises the adapter's protocol plumbing WITHOUT a live
 * backend so it runs in CI. The genuine mock↔live PARITY claim is proven
 * separately in `adapter-parity.integration.test.ts` against the real backend.
 */
import { describe, it, expect } from "vitest";
import { LiveInterviewAdapter } from "../live-adapter";
import { SCRIPTED_SESSION, type ScriptEvent } from "../seed/interviewer-script";
import { PhaseController } from "../state-machine";
import type { WebSocketLike } from "@/lib/interview-transport";

const BASE_TS = 1_700_000_000_000;

function scriptToPayload(s: ScriptEvent): { actor: string; type: string; payload: Record<string, unknown> } {
  switch (s.kind) {
    case "interviewer.utterance":
      return { actor: "interviewer", type: "interviewer.utterance", payload: { lineId: s.lineId, text: s.text } };
    case "candidate.utterance":
      return { actor: "candidate", type: "candidate.utterance", payload: { lineId: s.lineId, text: s.text } };
    case "code.edited":
      return { actor: "candidate", type: "code.edited", payload: { editId: s.editId, after: s.after, by: "candidate" } };
    case "code.run":
      return {
        actor: "candidate",
        type: "code.run",
        payload: { runId: s.runId, code: s.code, stdout: s.stdout, exitCode: s.exitCode },
      };
  }
}

/** A fake socket that faithfully emulates the scripted backend WS contract. */
class FakeBackendSocket implements WebSocketLike {
  readyState = 1;
  onopen: ((ev: unknown) => void) | null = null;
  onclose: ((ev: { code?: number; reason?: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;

  private seq = 0;
  private ledger: Record<string, unknown>[] = [];
  private controller = new PhaseController("ready");

  constructor(private readonly sessionId: string) {
    queueMicrotask(() => this.onopen?.({}));
  }

  private emit(actor: string, type: string, payload: Record<string, unknown>): Record<string, unknown> {
    this.seq += 1;
    const ev = { v: 1, seq: this.seq, ts: BASE_TS + this.seq * 1000, sessionId: this.sessionId, actor, type, ...payload };
    this.ledger.push(ev);
    return ev;
  }

  private deliver(obj: unknown): void {
    queueMicrotask(() => this.onmessage?.({ data: JSON.stringify(obj) }));
  }

  send(data: string): void {
    const msg = JSON.parse(data) as Record<string, unknown>;
    const type = msg.type;

    if (type === "client_hello") {
      // 3 setup events the REST create+rubric already produced.
      const setup = [
        this.emit("system", "phase.changed", { from: "intake", to: "rubric", signal: "intake.submitted" }),
        this.emit("system", "rubric.bound", {
          rubric: { id: `rubric-${this.sessionId}`, criteria: [], generatedBy: "scripted", version: 1 },
        }),
        this.emit("system", "phase.changed", { from: "rubric", to: "ready", signal: "rubric.generated" }),
      ];
      this.deliver({ type: "resume_ready", resumed: false, from_seq: 0 });
      this.deliver({ type: "resume_events", from_seq: 0, last_seq: this.seq, events: setup });
      return;
    }

    if (type === "advance.request") {
      const signal = String(msg.signal);
      const turn = SCRIPTED_SESSION.find((t) => t.advance === signal);
      const res = this.controller.request(signal as never, { hasRubric: true, lastSeq: this.seq });
      if (!res.ok) return;
      this.deliver(this.emit("system", "phase.changed", { from: res.from, to: res.to, signal: res.signal }));
      for (const ev of turn?.events ?? []) {
        const p = scriptToPayload(ev);
        this.deliver(this.emit(p.actor, p.type, p.payload));
      }
      return;
    }

    if (type === "candidate.text") {
      this.deliver(this.emit("candidate", "candidate.utterance", { lineId: `cand-${this.seq + 1}`, text: String(msg.text) }));
      return;
    }
    if (type === "candidate.code") {
      this.deliver(this.emit("candidate", "code.edited", { editId: `edit-${this.seq + 1}`, after: String(msg.code), by: "candidate" }));
      return;
    }
    if (type === "candidate.run") {
      this.deliver(this.emit("candidate", "code.run", { runId: `run-${this.seq + 1}`, code: String(msg.code), stdout: "", exitCode: 0 }));
      return;
    }
    if (type === "scorecard.request") {
      const l1 = this.ledger.find((e) => e.lineId === "L1");
      const score = {
        criterionId: "communication",
        score: 80,
        weight: 100,
        verdict: "strong",
        evidence: [{ kind: "utterance", seq: (l1?.seq as number) ?? 0, excerpt: "Hi" }],
        gaps: [],
      };
      this.deliver(this.emit("system", "scorecard.criterion.ready", { score }));
      this.deliver(
        this.emit("system", "scorecard.completed", {
          draft: { sessionId: this.sessionId, rubricId: `rubric-${this.sessionId}`, stage: "complete", scores: [score], overall: 80 },
        }),
      );
      return;
    }
  }

  close(): void {
    this.readyState = 3;
  }
}

function makeLive(sessionId = "fake-sid") {
  return new LiveInterviewAdapter({
    sessionId,
    apiBase: "http://test.local",
    wsBase: "ws://test.local",
    randomUUID: () => "conn-00000000-0000-4000-8000-000000000000",
    fetchImpl: async (url) => {
      // POST /sessions → {sessionId}; POST .../rubric → {rubric}
      if (url.endsWith("/sessions")) {
        return { ok: true, status: 200, json: async () => ({ sessionId, phase: "rubric" }) };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({ rubric: { id: `rubric-${sessionId}`, criteria: [], generatedBy: "scripted", version: 1 } }),
      };
    },
    socketFactory: (url) => new FakeBackendSocket(url.split("/").pop() ?? sessionId),
  });
}

describe("LiveInterviewAdapter (fake backend socket)", () => {
  it("drives the scripted session and applies events in strict seq order", async () => {
    const a = makeLive();
    await a.generateRubric({ resumeText: "", jobDescription: "", role: "Engineer", seniority: "mid", languages: ["python"], durationMinutes: 45 });
    await a.start();

    const seqs = a.getLedger().map((e) => e.seq);
    for (let i = 1; i < seqs.length; i++) expect(seqs[i]).toBeGreaterThan(seqs[i - 1]);
    // Ran through to the final scripted turn → backend lands at "scoring".
    expect(a.getPhase()).toBe("scoring");
    expect(a.getLedger().some((e) => e.type === "interviewer.utterance" && e.lineId === "L10")).toBe(true);

    await a.stop();
  });

  it("echoes candidate text/code from the server (no locally minted seqs)", async () => {
    const a = makeLive("echo-sid");
    await a.generateRubric({ resumeText: "", jobDescription: "", role: "Engineer", seniority: "mid", languages: ["python"], durationMinutes: 45 });
    await a.start();

    const before = a.getLedger().length;
    a.sendCandidateText("hello there");
    await new Promise((r) => setTimeout(r, 0));
    const utt = a.getLedger().find((e) => e.type === "candidate.utterance" && e.text === "hello there");
    expect(utt).toBeTruthy();
    expect(a.getLedger().length).toBe(before + 1);

    await a.stop();
  });

  it("runCode resolves with the server-assigned run id", async () => {
    const a = makeLive("run-sid");
    await a.generateRubric({ resumeText: "", jobDescription: "", role: "Engineer", seniority: "mid", languages: ["python"], durationMinutes: 45 });
    await a.start();
    const runId = await a.runCode("print(1)");
    expect(runId).toMatch(/^run-/);
    expect(a.getLedger().some((e) => e.type === "code.run" && e.runId === runId)).toBe(true);
    await a.stop();
  });

  it("streams the scorecard and cites resolvable ledger seqs", async () => {
    const a = makeLive("score-sid");
    await a.generateRubric({ resumeText: "", jobDescription: "", role: "Engineer", seniority: "mid", languages: ["python"], durationMinutes: 45 });
    await a.start();

    const seqs = new Set(a.getLedger().map((e) => e.seq));
    const updates = [];
    for await (const u of a.generateScorecard()) {
      updates.push(u);
      if (u.kind === "criterion") {
        for (const ref of u.score.evidence) {
          expect(ref.seq).toBeGreaterThan(0);
          expect(seqs.has(ref.seq)).toBe(true);
        }
      }
    }
    expect(updates.at(-1)?.kind).toBe("complete");
    await a.stop();
  });

  it("start() without a bound rubric is a clean no-op", async () => {
    const a = makeLive("noop-sid");
    await a.start();
    expect(a.getLedger()).toEqual([]);
    expect(a.getPhase()).toBe("ready");
    await a.stop();
  });
});

/**
 * live-llm fake backend: each advance.request emits ONE phase.changed + ONE
 * interviewer.utterance (mirroring `_drive_advance_llm`), candidate frames are
 * echoed, and it never auto-drives the whole script.
 */
class FakeLlmSocket implements WebSocketLike {
  readyState = 1;
  onopen: ((ev: unknown) => void) | null = null;
  onclose: ((ev: { code?: number; reason?: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;

  private seq = 0;
  private controller = new PhaseController("ready");

  constructor(private readonly sessionId: string) {
    queueMicrotask(() => this.onopen?.({}));
  }

  private emit(actor: string, type: string, payload: Record<string, unknown>): Record<string, unknown> {
    this.seq += 1;
    return { v: 1, seq: this.seq, ts: BASE_TS + this.seq * 1000, sessionId: this.sessionId, actor, type, ...payload };
  }

  private deliver(obj: unknown): void {
    queueMicrotask(() => this.onmessage?.({ data: JSON.stringify(obj) }));
  }

  readonly sent: Record<string, unknown>[] = [];

  send(data: string): void {
    const msg = JSON.parse(data) as Record<string, unknown>;
    this.sent.push(msg);
    const type = msg.type;
    if (type === "client_hello") {
      const setup = [
        this.emit("system", "phase.changed", { from: "intake", to: "rubric", signal: "intake.submitted" }),
        this.emit("system", "phase.changed", { from: "rubric", to: "ready", signal: "rubric.generated" }),
      ];
      this.deliver({ type: "resume_ready", resumed: false, from_seq: 0 });
      this.deliver({ type: "resume_events", from_seq: 0, last_seq: this.seq, events: setup });
      return;
    }
    if (type === "advance.request") {
      const signal = String(msg.signal);
      const res = this.controller.request(signal as never, { hasRubric: true, lastSeq: this.seq });
      if (!res.ok) return;
      const pc = this.emit("system", "phase.changed", { from: res.from, to: res.to, signal: res.signal });
      this.deliver(pc);
      const turnId = `turn-${(pc as { seq: number }).seq}`;
      this.deliver(this.emit("system", "interviewer.turn.started", { turnId, phase: res.to }));
      this.deliver(this.emit("interviewer", "interviewer.utterance", { lineId: `llm-${this.seq + 1}`, text: `turn for ${res.to}`, turnId }));
      return;
    }
    if (type === "barge_in") {
      this.deliver(this.emit("system", "interviewer.cancelled", { turnId: String(msg.turnId ?? "") }));
      return;
    }
    if (type === "candidate.text") {
      this.deliver(this.emit("candidate", "candidate.utterance", { lineId: `cand-${this.seq + 1}`, text: String(msg.text) }));
      return;
    }
    if (type === "candidate.code") {
      this.deliver(this.emit("candidate", "code.edited", { editId: `edit-${this.seq + 1}`, after: String(msg.code), by: "candidate" }));
      this.deliver(this.emit("interviewer", "selection.set", { selection: { start: 1, end: 3, owner: "interviewer" } }));
      this.deliver(this.emit("interviewer", "highlight.set", { line: 1 }));
      this.deliver(this.emit("interviewer", "code.patch.proposed", { patchId: "patch-1", summary: "idempotent fix", before: String(msg.code), after: "def charge_customer(): pass  # idempotency_key unique" }));
      return;
    }
    if (type === "code.patch.accept") {
      this.deliver(this.emit("candidate", "code.patch.applied", { patchId: String(msg.patchId), before: "x", after: "def charge_customer(): pass  # applied", acceptedBy: "candidate" }));
      this.deliver(this.emit("candidate", "code.edited", { editId: `edit-${this.seq + 1}`, after: "def charge_customer(): pass  # applied", by: "candidate" }));
      return;
    }
    if (type === "code.patch.reject") {
      this.deliver(this.emit("system", "code.patch.rejected", { patchId: String(msg.patchId) }));
      return;
    }
    if (type === "scorecard.request") {
      const score = {
        criterionId: "communication",
        score: 70,
        weight: 100,
        verdict: "adequate",
        evidence: [{ kind: "utterance", seq: 1, excerpt: "x" }],
        gaps: [],
      };
      this.deliver(this.emit("system", "scorecard.criterion.ready", { score }));
      this.deliver(
        this.emit("system", "scorecard.completed", {
          draft: { sessionId: this.sessionId, rubricId: `rubric-${this.sessionId}`, stage: "complete", scores: [score], overall: 70 },
        }),
      );
      return;
    }
  }

  close(): void {
    this.readyState = 3;
  }
}

function makeLlm(sessionId = "llm-sid") {
  return new LiveInterviewAdapter({
    sessionId,
    apiBase: "http://test.local",
    wsBase: "ws://test.local",
    backendMode: "llm",
    randomUUID: () => "conn-00000000-0000-4000-8000-000000000000",
    fetchImpl: async (url) => {
      if (url.endsWith("/sessions")) {
        return { ok: true, status: 200, json: async () => ({ sessionId, phase: "rubric" }) };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({ rubric: { id: `rubric-${sessionId}`, criteria: [], generatedBy: "llm", version: 1 } }),
      };
    },
    socketFactory: (url) => new FakeLlmSocket(url.split("/").pop() ?? sessionId),
  });
}

const INTAKE = { resumeText: "", jobDescription: "", role: "Engineer", seniority: "mid" as const, languages: ["python"], durationMinutes: 45 };

describe("LiveInterviewAdapter (live-llm, candidate-driven)", () => {
  it("start emits ONLY the first interviewer turn (no full-script auto-drive)", async () => {
    const a = makeLlm();
    await a.generateRubric(INTAKE);
    await a.start();

    const interviewerTurns = a.getLedger().filter((e) => e.type === "interviewer.utterance");
    expect(interviewerTurns).toHaveLength(1);
    expect(a.getPhase()).toBe("intro");
    await a.stop();
  });

  it("a candidate answer is echoed as a candidate.utterance", async () => {
    const a = makeLlm("llm-echo");
    await a.generateRubric(INTAKE);
    await a.start();

    a.sendCandidateText("my answer");
    await new Promise((r) => setTimeout(r, 0));
    expect(a.getLedger().some((e) => e.type === "candidate.utterance" && e.text === "my answer")).toBe(true);
    await a.stop();
  });

  it("bargeIn() sends a barge_in frame and the server cancels that turn", async () => {
    let sock: FakeLlmSocket | null = null;
    const a = new LiveInterviewAdapter({
      sessionId: "llm-barge",
      apiBase: "http://test.local",
      wsBase: "ws://test.local",
      backendMode: "llm",
      randomUUID: () => "conn-00000000-0000-4000-8000-000000000000",
      fetchImpl: async (url) =>
        url.endsWith("/sessions")
          ? { ok: true, status: 200, json: async () => ({ sessionId: "llm-barge", phase: "rubric" }) }
          : { ok: true, status: 200, json: async () => ({ rubric: { id: "r", criteria: [], generatedBy: "llm", version: 1 } }) },
      socketFactory: (url) => (sock = new FakeLlmSocket(url.split("/").pop() ?? "llm-barge")),
    });
    await a.generateRubric(INTAKE);
    await a.start();

    // The active turn id is the latest interviewer.turn.started in the ledger.
    const started = [...a.getLedger()].reverse().find((e) => e.type === "interviewer.turn.started");
    const turnId = started && started.type === "interviewer.turn.started" ? started.turnId : undefined;
    a.bargeIn(turnId);
    await new Promise((r) => setTimeout(r, 0));

    expect(sock!.sent.some((m) => m.type === "barge_in" && m.turnId === turnId)).toBe(true);
    expect(
      a.getLedger().some((e) => e.type === "interviewer.cancelled" && e.turnId === turnId),
    ).toBe(true);
    await a.stop();
  });

  it("sendCode → Maya proposes a patch; acceptPatch applies it via the server ledger", async () => {
    const a = makeLlm("llm-patch");
    await a.generateRubric(INTAKE);
    await a.start();

    a.sendCode("def charge_customer(): rows = db.query(...)");
    await new Promise((r) => setTimeout(r, 0));
    const proposed = a.getLedger().find((e) => e.type === "code.patch.proposed");
    expect(proposed && proposed.type === "code.patch.proposed" ? proposed.patchId : null).toBe("patch-1");
    expect(a.getLedger().some((e) => e.type === "selection.set")).toBe(true);
    expect(a.getLedger().some((e) => e.type === "highlight.set")).toBe(true);

    a.acceptPatch("patch-1");
    await new Promise((r) => setTimeout(r, 0));
    expect(a.getLedger().some((e) => e.type === "code.patch.applied" && e.patchId === "patch-1")).toBe(true);
    // The applied buffer arrives as an authoritative code.edited (server, not UI).
    const lastEdit = [...a.getLedger()].reverse().find((e) => e.type === "code.edited");
    expect(lastEdit && lastEdit.type === "code.edited" ? lastEdit.after : "").toContain("applied");
    await a.stop();
  });

  it("rejectPatch emits code.patch.rejected and applies no code edit", async () => {
    const a = makeLlm("llm-reject");
    await a.generateRubric(INTAKE);
    await a.start();
    a.sendCode("def charge_customer(): rows = db.query(...)");
    await new Promise((r) => setTimeout(r, 0));
    const editsBefore = a.getLedger().filter((e) => e.type === "code.edited").length;
    a.rejectPatch("patch-1");
    await new Promise((r) => setTimeout(r, 0));
    expect(a.getLedger().some((e) => e.type === "code.patch.rejected" && e.patchId === "patch-1")).toBe(true);
    expect(a.getLedger().filter((e) => e.type === "code.edited").length).toBe(editsBefore);
    await a.stop();
  });

  it("Continue (next signal) appends the next interviewer turn", async () => {
    const a = makeLlm("llm-cont");
    await a.generateRubric(INTAKE);
    await a.start();
    expect(a.getPhase()).toBe("intro");

    a.requestAdvance("intro.done");
    await new Promise((r) => setTimeout(r, 0));
    expect(a.getPhase()).toBe("resume_calibration");
    expect(a.getLedger().filter((e) => e.type === "interviewer.utterance")).toHaveLength(2);
    await a.stop();
  });

  it("queues an outbound frame sent before resume_ready and flushes it once ready", async () => {
    const a = makeLlm("llm-queue");
    await a.generateRubric(INTAKE);
    // Sent BEFORE start(): no transport / not resume-ready yet. It must be
    // QUEUED (not silently dropped) and flushed in order once the handshake
    // completes — this is the E8 race fix.
    a.sendCandidateText("queued answer");
    await a.start();
    await new Promise((r) => setTimeout(r, 0));
    expect(
      a.getLedger().some((e) => e.type === "candidate.utterance" && e.text === "queued answer"),
    ).toBe(true);
    await a.stop();
  });

  it("scorecard.request streams a staged scorecard citing real ledger seqs", async () => {
    const a = makeLlm("llm-score");
    await a.generateRubric(INTAKE);
    await a.start();

    const seqs = new Set(a.getLedger().map((e) => e.seq));
    const updates = [];
    for await (const u of a.generateScorecard()) {
      updates.push(u);
      if (u.kind === "criterion") {
        for (const ref of u.score.evidence) expect(seqs.has(ref.seq)).toBe(true);
      }
    }
    expect(updates.filter((u) => u.kind === "criterion")).toHaveLength(1);
    expect(updates.at(-1)?.kind).toBe("complete");
    await a.stop();
  });
});

/** A socket that completes the handshake but NEVER answers scorecard.request,
 *  modelling a stalled provider / silent backend. */
class SilentScorecardSocket implements WebSocketLike {
  readyState = 1;
  onopen: ((ev: unknown) => void) | null = null;
  onclose: ((ev: { code?: number; reason?: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;

  constructor(private readonly sessionId: string) {
    queueMicrotask(() => this.onopen?.({}));
  }

  send(data: string): void {
    const msg = JSON.parse(data) as Record<string, unknown>;
    if (msg.type === "client_hello") {
      queueMicrotask(() => this.onmessage?.({ data: JSON.stringify({ type: "resume_ready", resumed: false, from_seq: 0 }) }));
      queueMicrotask(() =>
        this.onmessage?.({ data: JSON.stringify({ type: "resume_events", from_seq: 0, last_seq: 0, events: [] }) }),
      );
    }
    // scorecard.request: intentionally never answered.
  }

  close(): void {
    this.readyState = 3;
  }
}

describe("LiveInterviewAdapter scorecard never hangs", () => {
  it("generateScorecard terminates with a failed stage when completion never arrives", async () => {
    const a = new LiveInterviewAdapter({
      sessionId: "stall-sid",
      apiBase: "http://test.local",
      wsBase: "ws://test.local",
      backendMode: "llm",
      scorecardTimeoutMs: 20, // bound the wait so the test is fast
      randomUUID: () => "conn-00000000-0000-4000-8000-000000000000",
      fetchImpl: async (url) => {
        if (url.endsWith("/sessions")) {
          return { ok: true, status: 200, json: async () => ({ sessionId: "stall-sid", phase: "rubric" }) };
        }
        return { ok: true, status: 200, json: async () => ({ rubric: { id: "rubric-stall", criteria: [], generatedBy: "llm", version: 1 } }) };
      },
      socketFactory: (url) => new SilentScorecardSocket(url.split("/").pop() ?? "stall-sid"),
    });
    await a.generateRubric(INTAKE);
    // Note: we do NOT await start() here — in this stalled-socket model the
    // first interviewer turn never arrives, so start() would not resolve. The
    // scorecard request is what we are bounding; frames buffer harmlessly.

    const updates = [];
    for await (const u of a.generateScorecard()) updates.push(u);
    const last = updates.at(-1);
    expect(last?.kind).toBe("failed");
    if (last?.kind === "failed") expect(last.reason).toBe("timeout");
    await a.stop();
  });
});
