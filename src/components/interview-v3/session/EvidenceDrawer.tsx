"use client";

import type { VNextEvent } from "@/lib/interview-v3";

/**
 * Reveals the ledger event cited by the currently-selected evidence item.
 * Resolution happens upstream via resolveEvidence; this panel only renders.
 */
export function EvidenceDrawer({
  selectedSeq,
  event,
}: {
  selectedSeq: number | null;
  event: VNextEvent | null;
}) {
  return (
    <section className="space-y-2 rounded-md border border-[var(--muted)] p-3">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
        Evidence
      </h2>
      {selectedSeq === null ? (
        <p className="text-xs text-[var(--muted-foreground)]">
          Select an evidence chip to reveal the cited ledger event.
        </p>
      ) : event === null ? (
        <p className="text-xs text-rose-500">
          Missing evidence: seq #{selectedSeq} does not resolve to a ledger event.
        </p>
      ) : (
        <div className="space-y-1 text-sm">
          <div className="flex items-center justify-between text-xs text-[var(--muted-foreground)]">
            <span className="font-mono">#{event.seq}</span>
            <span className="font-medium uppercase">{event.actor}</span>
            <span className="font-mono">{event.type}</span>
          </div>
          <pre className="overflow-auto rounded bg-[var(--muted)]/10 p-2 text-xs">
            <code>{describe(event)}</code>
          </pre>
        </div>
      )}
    </section>
  );
}

function describe(event: VNextEvent): string {
  if (event.type === "interviewer.utterance" || event.type === "candidate.utterance") {
    return event.text;
  }
  if (event.type === "code.edited") return event.after;
  if (event.type === "code.run") {
    return `$ run ${event.runId}\nexit ${event.exitCode}\n${event.stdout || "(no stdout)"}`;
  }
  return JSON.stringify(event, null, 2);
}
