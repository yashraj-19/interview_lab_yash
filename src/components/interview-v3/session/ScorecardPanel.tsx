"use client";

import type { ReviewRow, ResolvedEvidence } from "@/lib/interview-v3";

const verdictCls: Record<string, string> = {
  strong: "text-emerald-500",
  mixed: "text-amber-500",
  weak: "text-rose-500",
  insufficient_evidence: "text-[var(--muted-foreground)]",
};

/**
 * Evidence-first scorecard. Renders one row per scored criterion (with
 * placeholders for criteria still streaming in). Selecting an evidence chip
 * raises the cited ledger seq to the parent for the drawer to reveal.
 */
export function ScorecardPanel({
  rows,
  overall,
  total,
  selectedSeq,
  onSelectEvidence,
}: {
  rows: ReviewRow[];
  overall: number | null;
  /** Total criteria expected (from the rubric) so we can show pending slots. */
  total: number;
  selectedSeq: number | null;
  onSelectEvidence: (seq: number) => void;
}) {
  const pending = Math.max(0, total - rows.length);

  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
          Scorecard
        </h2>
        <span className="text-sm">
          {overall === null ? (
            <span className="text-[var(--muted-foreground)]">scoring…</span>
          ) : (
            <>
              overall <span className="font-mono text-base font-semibold">{overall}</span>/100
            </>
          )}
        </span>
      </div>

      <ul className="space-y-2">
        {rows.map((row) => (
          <li
            key={row.criterionId}
            className="space-y-2 rounded-md border border-[var(--muted)] p-3 text-sm"
          >
            <div className="flex items-baseline justify-between gap-2">
              <div>
                <span className="font-medium">{row.name}</span>{" "}
                <span className={`text-xs font-medium uppercase ${verdictCls[row.verdict] ?? ""}`}>
                  {row.verdict.replace(/_/g, " ")}
                </span>
              </div>
              <span className="font-mono text-xs text-[var(--muted-foreground)]">
                weight {row.weight} · {row.evidenceCount} evidence
              </span>
            </div>

            <div className="flex items-center gap-2">
              <span className="font-mono text-base font-semibold">{row.score}</span>
              <span className="text-xs text-[var(--muted-foreground)]">/100</span>
            </div>

            {/* Evidence chips — each cites a ledger seq. */}
            <div className="flex flex-wrap gap-1.5">
              {row.evidence.map((ev, i) => (
                <EvidenceChip
                  key={`${row.criterionId}-${i}`}
                  ev={ev}
                  selected={!ev.invalid && ev.ref.seq === selectedSeq}
                  onSelect={() => onSelectEvidence(ev.ref.seq)}
                />
              ))}
              {row.evidence.length === 0 ? (
                <span className="text-xs italic text-[var(--muted-foreground)]">
                  no evidence cited
                </span>
              ) : null}
            </div>

            {row.risks.length > 0 ? (
              <ul className="space-y-0.5 text-xs text-rose-500">
                {row.risks.map((r, i) => (
                  <li key={i}>⚠ {r}</li>
                ))}
              </ul>
            ) : null}

            {row.gaps.length > 0 ? (
              <ul className="space-y-0.5 text-xs text-[var(--muted-foreground)]">
                {row.gaps.map((g, i) => (
                  <li key={i}>· {g}</li>
                ))}
              </ul>
            ) : null}
          </li>
        ))}

        {Array.from({ length: pending }).map((_, i) => (
          <li
            key={`pending-${i}`}
            className="animate-pulse rounded-md border border-dashed border-[var(--muted)] p-3 text-xs text-[var(--muted-foreground)]"
          >
            scoring criterion…
          </li>
        ))}
      </ul>
    </section>
  );
}

function EvidenceChip({
  ev,
  selected,
  onSelect,
}: {
  ev: ResolvedEvidence;
  selected: boolean;
  onSelect: () => void;
}) {
  if (ev.invalid) {
    return (
      <span
        className="rounded border border-rose-500/60 bg-rose-500/10 px-1.5 py-0.5 text-xs text-rose-500"
        title="Cited evidence does not resolve to a ledger event"
      >
        invalid evidence (seq {ev.ref.seq})
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`rounded border px-1.5 py-0.5 text-xs transition-colors ${
        selected
          ? "border-[var(--text)] bg-[var(--muted)]/30"
          : "border-[var(--muted)] hover:bg-[var(--muted)]/20"
      }`}
      title={ev.ref.excerpt}
    >
      <span className="font-mono">#{ev.ref.seq}</span> {ev.ref.kind}
    </button>
  );
}
