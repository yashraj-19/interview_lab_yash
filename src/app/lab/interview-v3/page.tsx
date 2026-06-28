import Link from "next/link";
import { INCIDENT_INTAKE_URL } from "@/lib/interview-v3/incident";

/** Lab launcher for Interview vNext. The software-engineer incident demo is the
 *  primary path; other adapter modes remain as secondary options. No query-param
 *  knowledge required — the primary CTA carries them. */
export default function InterviewV3LabHome() {
  return (
    <main className="mx-auto max-w-3xl space-y-8 px-6 py-10">
      <header className="space-y-2">
        <p className="text-xs uppercase tracking-widest text-[var(--muted-foreground)]">
          Lab · Interview vNext
        </p>
        <h1 className="text-2xl font-semibold">Interview demo launcher</h1>
        <p className="text-sm text-[var(--muted-foreground)]">
          A live, evidence-native technical interview. Start with the incident
          demo below.
        </p>
      </header>

      {/* Primary path — the incident demo. */}
      <section className="rounded-lg border border-[var(--muted)] p-6 space-y-4">
        <div className="space-y-1">
          <h2 className="text-lg font-semibold">Software engineer incident demo</h2>
          <p className="text-sm text-[var(--muted-foreground)]">
            A senior-style interview that opens with a real production issue.
          </p>
        </div>

        <ul className="grid grid-cols-1 gap-1 text-sm sm:grid-cols-2">
          <li>• Production incident: a payment API double-charges on retry</li>
          <li>• Fix the buggy payment code in the box</li>
          <li>• Voice + barge-in enabled (speak over the interviewer)</li>
          <li>• Evidence-based scorecard tied to the ledger</li>
        </ul>

        <Link
          href={INCIDENT_INTAKE_URL}
          className="inline-flex items-center rounded-md border border-[var(--muted)] bg-[var(--muted)]/20 px-4 py-2 text-sm font-semibold transition-colors hover:bg-[var(--muted)]/40"
        >
          Start software engineer incident demo →
        </Link>

        <p className="text-xs text-[var(--muted-foreground)]">
          Best tested in <strong>Chrome</strong> · allow the microphone when asked
          · try speaking over the interviewer to test barge-in.
        </p>
      </section>

      {/* Secondary options — kept available, visually de-emphasized. */}
      <section className="space-y-2">
        <p className="text-xs uppercase tracking-widest text-[var(--muted-foreground)]">
          Other modes
        </p>
        <div className="flex flex-wrap gap-2 text-sm">
          <Link
            href="/lab/interview-v3/intake"
            className="rounded-md border border-[var(--muted)] px-3 py-1.5 hover:bg-[var(--muted)]/20"
          >
            Mock offline demo
          </Link>
          <Link
            href="/lab/interview-v3/intake?adapter=live"
            className="rounded-md border border-[var(--muted)] px-3 py-1.5 hover:bg-[var(--muted)]/20"
          >
            Live scripted demo
          </Link>
          <Link
            href="/lab/interview-v3/intake?adapter=live-llm"
            className="rounded-md border border-[var(--muted)] px-3 py-1.5 hover:bg-[var(--muted)]/20"
          >
            Generic live LLM demo
          </Link>
        </div>
      </section>
    </main>
  );
}
