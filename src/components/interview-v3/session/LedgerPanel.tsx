"use client";

import { useState } from "react";
import { orderBySeq, type VNextEvent } from "@/lib/interview-v3";

/**
 * Raw ledger inspector — the replayable evidence stream. Shows every event in
 * seq order with its timestamp so ordering is visible during development.
 */
export function LedgerPanel({ ledger }: { ledger: readonly VNextEvent[] }) {
  const [open, setOpen] = useState(false);
  const ordered = orderBySeq(ledger);

  return (
    <section className="space-y-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between rounded-md border border-[var(--muted)] px-3 py-2 text-sm font-medium"
      >
        <span>Ledger ({ordered.length} events)</span>
        <span className="text-xs text-[var(--muted-foreground)]">{open ? "hide" : "show"}</span>
      </button>
      {open ? (
        <ol className="space-y-1">
          {ordered.map((e) => (
            <li
              key={e.seq}
              className="flex gap-3 rounded border border-[var(--muted)]/60 px-3 py-1.5 text-xs"
            >
              <span className="w-10 shrink-0 font-mono text-[var(--muted-foreground)]">
                #{e.seq}
              </span>
              <span className="w-20 shrink-0 font-mono text-[var(--muted-foreground)]">
                {fmtTs(e.ts)}
              </span>
              <span className="w-16 shrink-0 font-medium">{e.actor}</span>
              <span className="font-mono">{e.type}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}

function fmtTs(ts: number): string {
  return new Date(ts).toISOString().slice(11, 19);
}
