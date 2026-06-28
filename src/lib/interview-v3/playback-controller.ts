/**
 * Timed playback over a deterministic vNext ledger.
 *
 * The MockInterviewAdapter appends the entire scripted session in one tick. For
 * the room we want to REVEAL those already-deterministic events progressively —
 * without changing their values. This controller takes a fully-realized ledger
 * (the byte-identical output of the mock's instant path) and exposes a prefix of
 * it, advancing one event at a time. Instant mode reveals everything at once, so
 * it is identical to today's mock output. 1x/2x/4x drive `step()` on a timer.
 *
 * Determinism: event seq/ts/values come straight from the input ledger; playback
 * only controls WHEN a given prefix is visible. Tests drive `step()`/an injected
 * scheduler so they never depend on wall-clock timers.
 */

import type { VNextEvent } from "./events";
import { selectCurrentPhase } from "./ledger";
import type { Phase } from "./state-machine";

export type PlaybackSpeed = "instant" | "1x" | "2x" | "4x";
export type PlaybackStatus = "idle" | "playing" | "paused" | "done";

export type PlaybackUnsubscribe = () => void;

/** Pluggable timer so tests can drive playback without real wall-clock delays. */
export interface PlaybackScheduler {
  set(cb: () => void, ms: number): number;
  clear(id: number): void;
}

const realScheduler: PlaybackScheduler = {
  set: (cb, ms) => (typeof window !== "undefined"
    ? window.setTimeout(cb, ms)
    : (setTimeout(cb, ms) as unknown as number)),
  clear: (id) => {
    if (typeof window !== "undefined") window.clearTimeout(id);
    else clearTimeout(id as unknown as NodeJS.Timeout);
  },
};

const SPEED_FACTOR: Record<Exclude<PlaybackSpeed, "instant">, number> = {
  "1x": 1,
  "2x": 2,
  "4x": 4,
};

export interface PlaybackControllerOptions {
  /** The fully-realized deterministic ledger to reveal progressively. */
  ledger: readonly VNextEvent[];
  /** Phase to report before any phase.changed event has been revealed. */
  fallbackPhase: Phase;
  /** Base per-event delay at 1x (ms). Divided by the speed factor. */
  baseDelayMs?: number;
  /** Injectable timer (tests pass a manual scheduler). */
  scheduler?: PlaybackScheduler;
}

const DEFAULT_BASE_DELAY_MS = 700;

export class PlaybackController {
  private readonly ledger: readonly VNextEvent[];
  private readonly fallbackPhase: Phase;
  private readonly baseDelayMs: number;
  private readonly scheduler: PlaybackScheduler;

  private idx = 0;
  private status: PlaybackStatus = "idle";
  private speed: PlaybackSpeed = "1x";
  private timerId: number | null = null;
  private disposed = false;

  private subs = new Set<() => void>();

  constructor(opts: PlaybackControllerOptions) {
    this.ledger = opts.ledger;
    this.fallbackPhase = opts.fallbackPhase;
    this.baseDelayMs = opts.baseDelayMs ?? DEFAULT_BASE_DELAY_MS;
    this.scheduler = opts.scheduler ?? realScheduler;
  }

  // ── reads ──────────────────────────────────────────────────────────────────

  /** The currently-revealed prefix of the ledger (deterministic, in seq order). */
  getRevealed(): VNextEvent[] {
    return this.ledger.slice(0, this.idx);
  }

  getStatus(): PlaybackStatus {
    return this.status;
  }

  getSpeed(): PlaybackSpeed {
    return this.speed;
  }

  getRevealedCount(): number {
    return this.idx;
  }

  getTotal(): number {
    return this.ledger.length;
  }

  isComplete(): boolean {
    return this.idx >= this.ledger.length;
  }

  /** Phase implied by the revealed prefix only. */
  getPhase(): Phase {
    return selectCurrentPhase(this.getRevealed(), this.fallbackPhase);
  }

  // ── subscriptions ────────────────────────────────────────────────────────

  subscribe(cb: () => void): PlaybackUnsubscribe {
    this.subs.add(cb);
    return () => this.subs.delete(cb);
  }

  private emit(): void {
    for (const cb of this.subs) cb();
  }

  // ── controls ───────────────────────────────────────────────────────────────

  /** Reveal exactly one more event. Returns true if it advanced. */
  step(): boolean {
    if (this.disposed || this.isComplete()) {
      if (this.isComplete()) this.markDone();
      return false;
    }
    this.idx += 1;
    if (this.isComplete()) this.status = "done";
    this.emit();
    return true;
  }

  /** Reveal the entire ledger at once (instant mode). */
  revealAll(): void {
    if (this.disposed) return;
    this.clearTimer();
    this.idx = this.ledger.length;
    this.status = "done";
    this.emit();
  }

  /** Begin (or restart) timed playback. Instant speed reveals everything. */
  start(): void {
    if (this.disposed) return;
    if (this.speed === "instant") {
      this.revealAll();
      return;
    }
    if (this.isComplete()) {
      this.markDone();
      return;
    }
    this.status = "playing";
    this.emit();
    this.scheduleNext();
  }

  pause(): void {
    if (this.disposed || this.status !== "playing") return;
    this.clearTimer();
    this.status = "paused";
    this.emit();
  }

  resume(): void {
    if (this.disposed) return;
    if (this.speed === "instant") {
      this.revealAll();
      return;
    }
    if (this.isComplete()) {
      this.markDone();
      return;
    }
    if (this.status === "playing") return;
    this.status = "playing";
    this.emit();
    this.scheduleNext();
  }

  setSpeed(speed: PlaybackSpeed): void {
    if (this.disposed) return;
    this.speed = speed;
    if (speed === "instant") {
      this.revealAll();
      return;
    }
    this.emit();
    if (this.status === "playing") this.scheduleNext();
  }

  dispose(): void {
    this.disposed = true;
    this.clearTimer();
    this.subs.clear();
  }

  // ── internals ────────────────────────────────────────────────────────────

  private markDone(): void {
    if (this.status !== "done") {
      this.status = "done";
      this.emit();
    }
  }

  private delayMs(): number {
    if (this.speed === "instant") return 0;
    return this.baseDelayMs / SPEED_FACTOR[this.speed];
  }

  private scheduleNext(): void {
    this.clearTimer();
    if (this.disposed || this.status !== "playing") return;
    if (this.isComplete()) {
      this.markDone();
      return;
    }
    this.timerId = this.scheduler.set(() => {
      this.timerId = null;
      if (this.disposed || this.status !== "playing") return;
      this.step();
      if (this.status === "playing") this.scheduleNext();
    }, this.delayMs());
  }

  private clearTimer(): void {
    if (this.timerId !== null) {
      this.scheduler.clear(this.timerId);
      this.timerId = null;
    }
  }
}
