import { describe, it, expect } from "vitest";
import { MockInterviewAdapter } from "../mock-adapter";
import {
  PlaybackController,
  type PlaybackScheduler,
} from "../playback-controller";
import type { VNextEvent } from "../events";

async function scriptedLedger(sessionId = "s"): Promise<VNextEvent[]> {
  const adapter = new MockInterviewAdapter({ sessionId });
  await adapter.generateRubric({
    resumeText: "",
    jobDescription: "",
    role: "Engineer",
    seniority: "mid",
    languages: ["python"],
    durationMinutes: 45,
  });
  await adapter.start();
  return [...adapter.getLedger()];
}

/** A scheduler whose callbacks only run when the test flushes them. */
function manualScheduler(): PlaybackScheduler & { flush: () => boolean; pending: () => number } {
  const queue = new Map<number, () => void>();
  let nextId = 1;
  return {
    set(cb) {
      const id = nextId++;
      queue.set(id, cb);
      return id;
    },
    clear(id) {
      queue.delete(id);
    },
    flush() {
      const entry = queue.entries().next();
      if (entry.done) return false;
      const [id, cb] = entry.value;
      queue.delete(id);
      cb();
      return true;
    },
    pending() {
      return queue.size;
    },
  };
}

describe("PlaybackController", () => {
  it("instant mode reveals the identical scripted ledger", async () => {
    const full = await scriptedLedger();
    const c = new PlaybackController({ ledger: full, fallbackPhase: "ready" });
    c.setSpeed("instant");
    c.start();
    expect(c.getRevealed()).toEqual(full);
    expect(c.getStatus()).toBe("done");
    expect(c.isComplete()).toBe(true);
  });

  it("starts empty before any reveal", async () => {
    const full = await scriptedLedger();
    const c = new PlaybackController({ ledger: full, fallbackPhase: "ready" });
    expect(c.getRevealed()).toEqual([]);
    expect(c.getPhase()).toBe("ready");
  });

  it("step appends exactly one event per call, in order", async () => {
    const full = await scriptedLedger();
    const c = new PlaybackController({ ledger: full, fallbackPhase: "ready" });
    for (let i = 1; i <= full.length; i++) {
      expect(c.step()).toBe(true);
      const revealed = c.getRevealed();
      expect(revealed.length).toBe(i);
      expect(revealed).toEqual(full.slice(0, i));
    }
    // No more events to reveal.
    expect(c.step()).toBe(false);
    expect(c.getStatus()).toBe("done");
  });

  it("pause stops timed progression until resumed", async () => {
    const full = await scriptedLedger();
    const sched = manualScheduler();
    const c = new PlaybackController({ ledger: full, fallbackPhase: "ready", scheduler: sched });
    c.start();
    expect(c.getStatus()).toBe("playing");
    // One timer scheduled; flushing reveals one event and schedules the next.
    expect(sched.flush()).toBe(true);
    expect(c.getRevealedCount()).toBe(1);
    expect(sched.pending()).toBe(1);

    c.pause();
    expect(c.getStatus()).toBe("paused");
    // Paused: the pending timer was cleared — nothing to flush, no progression.
    expect(sched.pending()).toBe(0);
    expect(sched.flush()).toBe(false);
    expect(c.getRevealedCount()).toBe(1);

    // Resume re-arms the timer and progression continues.
    c.resume();
    expect(c.getStatus()).toBe("playing");
    expect(sched.flush()).toBe(true);
    expect(c.getRevealedCount()).toBe(2);
  });

  it("dispose clears the timer and stops further reveals", async () => {
    const full = await scriptedLedger();
    const sched = manualScheduler();
    const c = new PlaybackController({ ledger: full, fallbackPhase: "ready", scheduler: sched });
    c.start();
    c.dispose();
    expect(sched.pending()).toBe(0);
    expect(c.step()).toBe(false);
  });

  it("derives phase only from the revealed prefix", async () => {
    const full = await scriptedLedger();
    const c = new PlaybackController({ ledger: full, fallbackPhase: "ready" });
    // Reveal up to the first phase.changed (ready → intro).
    let sawIntro = false;
    while (c.step()) {
      if (c.getPhase() === "intro") {
        sawIntro = true;
        break;
      }
    }
    expect(sawIntro).toBe(true);
    c.revealAll();
    expect(c.getPhase()).toBe("scoring");
  });
});
