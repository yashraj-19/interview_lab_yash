import Link from "next/link";
import { INCIDENT_INTAKE_URL, scenarioIntakeUrl } from "@/lib/interview-v3/incident";

/** SDE problem tracks served by the backend scenario registry. Listed here for
 *  a zero-fetch server-rendered launcher; the ids mirror GET /vnext/interview/problems
 *  (backend registry stays the source of truth for behavior). */
const PROBLEM_TRACKS: { track: string; title: string; difficulty: string }[] = [
  { track: "problem:two_sum", title: "Two Sum", difficulty: "easy" },
  { track: "problem:valid_parentheses", title: "Valid Parentheses", difficulty: "easy" },
  { track: "problem:merge_sorted_arrays", title: "Merge Sorted Arrays", difficulty: "easy" },
  { track: "problem:reverse_linked_list", title: "Reverse Linked List", difficulty: "medium" },
  { track: "problem:binary_search", title: "Binary Search", difficulty: "medium" },
  { track: "problem:longest_substring_without_repeating", title: "Longest Substring Without Repeating", difficulty: "medium" },
];

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

      {/* Dynamic SDE problem interviews — one generic engine, any problem. */}
      <section className="rounded-lg border border-[var(--muted)] p-6 space-y-4">
        <div className="space-y-1">
          <h2 className="text-lg font-semibold">SDE coding interviews</h2>
          <p className="text-sm text-[var(--muted-foreground)]">
            The same interviewer conducts any problem: voice, barge-in, live code
            review with line highlights, real test-case runs, never-reveal hints,
            and an evidence-cited scorecard.
          </p>
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {PROBLEM_TRACKS.map((p) => (
            <Link
              key={p.track}
              href={scenarioIntakeUrl(p.track)}
              className="group flex items-center justify-between rounded-md border border-[var(--muted)] px-4 py-3 text-sm transition-colors hover:bg-[var(--muted)]/20"
            >
              <span className="font-medium">{p.title}</span>
              <span className="text-xs uppercase tracking-wide text-[var(--muted-foreground)]">
                {p.difficulty} →
              </span>
            </Link>
          ))}
        </div>
        <Link
          href={scenarioIntakeUrl("auto")}
          className="inline-flex items-center rounded-md border border-[var(--muted)] bg-[var(--muted)]/20 px-4 py-2 text-sm font-semibold transition-colors hover:bg-[var(--muted)]/40"
        >
          Match a problem to my role →
        </Link>
        <p className="text-xs text-[var(--muted-foreground)]">
          Role-matched: an SDE intern gets an easier problem scored on reasoning
          and communication; a senior ML engineer gets a harder one scored on
          approach and complexity.
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
