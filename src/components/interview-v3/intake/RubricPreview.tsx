import type { Rubric } from "@/lib/interview-v3";

/** Read-only preview of a generated rubric: criteria, weights, signals. */
export function RubricPreview({ rubric }: { rubric: Rubric }) {
  const total = rubric.criteria.reduce((a, c) => a + c.weight, 0);
  return (
    <section className="space-y-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-lg font-semibold">Rubric preview</h2>
        <span className="text-xs text-[var(--muted-foreground)]">
          {rubric.generatedBy} · v{rubric.version} · weights sum {total}
        </span>
      </div>
      <ul className="space-y-3">
        {rubric.criteria.map((c) => (
          <li
            key={c.id}
            className="rounded-md border border-[var(--muted)] p-4"
          >
            <div className="flex items-center justify-between">
              <span className="font-medium">{c.name}</span>
              <span className="text-sm tabular-nums">{c.weight}</span>
            </div>
            <p className="mt-1 text-sm text-[var(--muted-foreground)]">
              {c.description}
            </p>
            <div className="mt-2 flex flex-wrap gap-1">
              {c.signals.map((s) => (
                <span
                  key={s}
                  className="rounded bg-[var(--muted)]/20 px-2 py-0.5 text-xs text-[var(--muted-foreground)]"
                >
                  {s}
                </span>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
