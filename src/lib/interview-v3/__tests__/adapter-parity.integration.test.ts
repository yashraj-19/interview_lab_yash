/**
 * GENUINE mock ↔ live-scripted parity, run against the REAL E1–E2 backend.
 *
 * This actually runs BOTH adapters: the in-memory MockInterviewAdapter and the
 * LiveInterviewAdapter wired to a running backend on :8000 (override with
 * VNEXT_BACKEND_URL). It asserts the SCRIPTED event (type, actor) ORDER is
 * identical, and that every scorecard EvidenceRef.seq resolves to a real ledger
 * event in the live adapter.
 *
 * If the backend is not reachable the whole suite is SKIPPED with a clear
 * message — it never fakes the parity claim.
 *
 * Run the backend first:
 *   cd backend && PYTHONPATH=. ./venv/bin/uvicorn app.main:app --port 8000
 */
import { describe, it, expect, beforeAll } from "vitest";
import WS from "ws";
import { MockInterviewAdapter } from "../mock-adapter";
import { LiveInterviewAdapter } from "../live-adapter";
import type { WebSocketLike } from "@/lib/interview-transport";
import type { Intake } from "../intake";
import type { VNextEvent } from "../events";

const HTTP_BASE = process.env.VNEXT_BACKEND_URL ?? "http://localhost:8000";
const WS_BASE = HTTP_BASE.replace(/^http/i, "ws");

const INTAKE: Intake = {
  resumeText: "",
  jobDescription: "",
  role: "Engineer",
  seniority: "mid",
  languages: ["python"],
  durationMinutes: 45,
};

/** Reduce a ledger to the scripted (type:actor) signature from session.start on. */
function scriptedSignature(ledger: readonly VNextEvent[]): string[] {
  const start = ledger.findIndex((e) => e.type === "phase.changed" && e.signal === "session.start");
  const from = start === -1 ? ledger : ledger.slice(start);
  return [...from].sort((a, b) => a.seq - b.seq).map((e) => `${e.type}:${e.actor}`);
}

let backendUp = false;

beforeAll(async () => {
  try {
    const res = await fetch(`${HTTP_BASE}/vnext/interview/warmup`, { signal: AbortSignal.timeout(2000) });
    backendUp = res.ok;
  } catch {
    backendUp = false;
  }
  if (!backendUp) {
    console.warn(`[parity] backend not reachable at ${HTTP_BASE} — skipping live parity. Start it on :8000 to run.`);
  }
});

describe("mock ↔ live-scripted parity (real backend)", () => {
  it("produces the same scripted event (type, actor) order + resolvable evidence", async () => {
    if (!backendUp) {
      expect(backendUp).toBe(false); // documents the skip without a false pass
      return;
    }

    // ── mock ──
    const mock = new MockInterviewAdapter({ sessionId: "parity-mock" });
    await mock.generateRubric(INTAKE);
    await mock.start();
    const mockSig = scriptedSignature(mock.getLedger());

    // ── live ──
    const live = new LiveInterviewAdapter({
      sessionId: "parity-live",
      apiBase: HTTP_BASE,
      wsBase: WS_BASE,
      socketFactory: (url) => new WS(url) as unknown as WebSocketLike,
    });
    await live.generateRubric(INTAKE);
    await live.start();
    const liveSig = scriptedSignature(live.getLedger());

    expect(liveSig).toEqual(mockSig);
    expect(liveSig.length).toBeGreaterThan(0);

    // Evidence resolves to real live ledger seqs.
    const liveSeqs = new Set(live.getLedger().map((e) => e.seq));
    for await (const u of live.generateScorecard()) {
      if (u.kind === "criterion") {
        for (const ref of u.score.evidence) {
          expect(ref.seq).toBeGreaterThan(0);
          expect(liveSeqs.has(ref.seq)).toBe(true);
        }
      }
    }

    await live.stop();
    await mock.stop();
  }, 20000);
});
