"use client";

import type { PlaybackSpeed, PlaybackStatus } from "@/lib/interview-v3";

const btnCls =
  "rounded-md border border-[var(--muted)] px-3 py-1.5 text-sm font-medium transition-colors hover:bg-[var(--muted)]/20 disabled:opacity-40 disabled:hover:bg-transparent";

const SPEEDS: PlaybackSpeed[] = ["instant", "1x", "2x", "4x"];

/**
 * Transport controls for timed scripted playback. Every action routes through
 * the PlaybackController; this component is purely presentational.
 */
export function PlaybackBar({
  status,
  speed,
  revealedCount,
  total,
  onStart,
  onPause,
  onResume,
  onStep,
  onSpeed,
}: {
  status: PlaybackStatus;
  speed: PlaybackSpeed;
  revealedCount: number;
  total: number;
  onStart: () => void;
  onPause: () => void;
  onResume: () => void;
  onStep: () => void;
  onSpeed: (speed: PlaybackSpeed) => void;
}) {
  const complete = status === "done";
  const playing = status === "playing";

  return (
    <section className="flex flex-wrap items-center gap-2 rounded-md border border-[var(--muted)] p-3">
      <span className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">
        Playback
      </span>

      {status === "idle" ? (
        <button className={btnCls} onClick={onStart}>
          Start
        </button>
      ) : null}

      {playing ? (
        <button className={btnCls} onClick={onPause}>
          Pause
        </button>
      ) : null}

      {status === "paused" ? (
        <button className={btnCls} onClick={onResume}>
          Resume
        </button>
      ) : null}

      <button className={btnCls} onClick={onStep} disabled={complete}>
        Step
      </button>

      <div className="flex items-center gap-1">
        <span className="text-xs text-[var(--muted-foreground)]">speed</span>
        {SPEEDS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onSpeed(s)}
            className={`rounded border px-2 py-1 text-xs transition-colors ${
              s === speed
                ? "border-[var(--text)] bg-[var(--muted)]/30"
                : "border-[var(--muted)] hover:bg-[var(--muted)]/20"
            }`}
          >
            {s}
          </button>
        ))}
      </div>

      <span className="ml-auto font-mono text-xs text-[var(--muted-foreground)]">
        {revealedCount}/{total} · {status}
      </span>
    </section>
  );
}
