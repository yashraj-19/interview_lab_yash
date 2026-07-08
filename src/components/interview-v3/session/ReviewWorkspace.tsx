"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  makeAdapter,
  getHandoff,
  loadSession,
  patchSession,
  buildReviewModel,
  resolveEvidence,
  evidenceTargetForSeq,
  selectCitedSeqs,
  selectTranscript,
  selectCode,
  selectRuns,
  orderBySeq,
  type VNextEvent,
  type Rubric,
  type CriterionScore,
} from "@/lib/interview-v3";
import { ScorecardPanel } from "./ScorecardPanel";
import { EvidenceDrawer } from "./EvidenceDrawer";
import { CopyReviewLink } from "./CopyReviewLink";
import { computeConversationMetrics, type AnyEvent } from "@/lib/interview-v3/metrics";

const btnCls =
  "rounded-md border border-[var(--muted)] px-3 py-1.5 text-sm font-medium transition-colors hover:bg-[var(--muted)]/20 disabled:opacity-40";

/**
 * Standalone review workspace. The room adapter is in-memory and does not
 * survive navigation, so this page deterministically reconstructs the session
 * from the client handoff (intake + rubric), replays it, and streams the
 * scorecard criterion-by-criterion. Same handoff → identical ledger + scores.
 */
export function ReviewWorkspace({ sessionId }: { sessionId: string }) {
  const [missing, setMissing] = useState(false);
  const [rubric, setRubric] = useState<Rubric | null>(null);
  const [ledger, setLedger] = useState<readonly VNextEvent[]>([]);
  const [scores, setScores] = useState<CriterionScore[]>([]);
  const [overall, setOverall] = useState<number | null>(null);
  const [done, setDone] = useState(false);
  const [scoreError, setScoreError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [selectedSeq, setSelectedSeq] = useState<number | null>(null);
  const [evidenceOnly, setEvidenceOnly] = useState(false);

  useEffect(() => {
    // No mount-guard ref: under React StrictMode the effect runs mount→unmount→
    // mount. A `startedRef` would let the first (cancelled) run win and the
    // second never start, leaving the scorecard stuck. Instead each run is
    // self-contained — its own adapter + `cancelled` — and is fully
    // deterministic, so the surviving (second) run reproduces the identical
    // ledger and scores.
    let cancelled = false;
    // Note: the Retry handler already resets scoreError before bumping reloadKey,
    // so we avoid a synchronous setState here.

    const handoff = getHandoff(sessionId);
    if (!handoff) {
      void Promise.resolve().then(() => {
        if (!cancelled) setMissing(true);
      });
      return;
    }

    // live-llm sessions are candidate-driven and CANNOT be deterministically
    // replayed (the human's answers/code are unique). Review reads the realized
    // ledger + scorecard the room persisted to the local store instead.
    if ((handoff.mode ?? "mock") === "live-llm") {
      void Promise.resolve().then(() => {
        if (cancelled) return;
        const stored = loadSession(sessionId);
        setRubric(handoff.rubric);
        setLedger(stored?.ledger ? [...stored.ledger] : []);
        if (stored?.scorecard) {
          setScores([...stored.scorecard.scores]);
          setOverall(stored.scorecard.overall);
          setDone(true);
        } else {
          // The room persists the scorecard when the candidate clicks Finish.
          // None here means scoring never completed (or the session predates it)
          // — show a bounded error/retry state instead of infinite placeholders.
          setScoreError(
            "No scorecard yet for this session. Finish the interview in the room, then retry.",
          );
        }
      });
      return () => {
        cancelled = true;
      };
    }

    void (async () => {
      const adapter = makeAdapter({ mode: handoff.mode ?? "mock", sessionId });
      const r = await adapter.generateRubric(handoff.intake);
      await adapter.start();
      if (cancelled) return;
      setRubric(r);
      setLedger([...adapter.getLedger()]);
      // Stream the scorecard; render each criterion as it arrives.
      for await (const update of adapter.generateScorecard()) {
        if (cancelled) return;
        if (update.kind === "criterion") {
          setScores((prev) => [...prev, update.score]);
        } else if (update.kind === "failed") {
          setScoreError(
            update.reason === "timeout"
              ? "Scoring timed out. Please retry."
              : `Scoring failed (${update.reason}). Please retry.`,
          );
          return;
        } else {
          setOverall(update.draft.overall);
          setDone(true);
          // Persist the realized ledger + scorecard so a hard refresh recovers.
          patchSession(sessionId, {
            ledger: [...adapter.getLedger()],
            scorecard: update.draft,
          });
        }
        setLedger([...adapter.getLedger()]);
      }
    })().catch(() => {
      if (!cancelled) setScoreError("Scoring failed unexpectedly. Please retry.");
    });

    return () => {
      cancelled = true;
    };
  }, [sessionId, reloadKey]);

  const reviewModel = useMemo(
    () => buildReviewModel(ledger, { scores, overall }, rubric),
    [ledger, scores, overall, rubric],
  );

  const selectedEvent = useMemo(() => {
    if (selectedSeq === null) return null;
    return resolveEvidence(ledger, { kind: "utterance", seq: selectedSeq, excerpt: "" }).event;
  }, [ledger, selectedSeq]);

  const citedSeqs = useMemo(() => selectCitedSeqs(scores), [scores]);
  const selectedTarget = useMemo(
    () => (selectedSeq === null ? null : evidenceTargetForSeq(ledger, selectedSeq)),
    [ledger, selectedSeq],
  );

  // Reveal the cited event: scroll its panel (transcript turn, code panel, or
  // run row) into view. Idempotent → StrictMode-safe.
  useEffect(() => {
    if (selectedSeq === null) return;
    const el = document.getElementById(`evt-${selectedSeq}`);
    if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [selectedSeq, selectedTarget]);

  if (missing) {
    return (
      <main className="space-y-4">
        <h1 className="text-2xl font-semibold">Session not found</h1>
        <p className="text-sm text-[var(--muted-foreground)]">
          No intake/rubric handoff for <code>{sessionId}</code>. Start from intake.
        </p>
        <Link href="/lab/interview-v3/intake" className={btnCls}>
          ← Back to intake
        </Link>
      </main>
    );
  }

  const transcript = selectTranscript(ledger);
  const codeState = selectCode(ledger);
  const runs = selectRuns(ledger);
  const metrics = computeConversationMetrics(ledger as unknown as AnyEvent[]);

  const visibleTranscript = evidenceOnly
    ? transcript.filter((t) => citedSeqs.has(t.seq))
    : transcript;
  const visibleRuns = evidenceOnly ? runs.filter((r) => citedSeqs.has(r.seq)) : runs;

  return (
    <main className="space-y-6">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <p className="text-xs uppercase tracking-widest text-[var(--muted-foreground)]">
            Lab · Phase C · review
          </p>
          <h1 className="text-2xl font-semibold">Review workspace</h1>
        </div>
        <div className="flex flex-col items-end gap-2 text-xs text-[var(--muted-foreground)]">
          <div className="font-mono">{sessionId}</div>
          <div className="flex items-center gap-2">
            <Link href={`/lab/interview-v3/session/${sessionId}`} className="underline">
              ← back to room
            </Link>
            <CopyReviewLink sessionId={sessionId} />
          </div>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="space-y-6">
          <ScorecardPanel
            rows={reviewModel.rows}
            overall={done ? reviewModel.overall : null}
            // On error, stop advertising pending slots so we never render
            // placeholder "scoring criterion…" rows forever.
            total={
              scoreError
                ? reviewModel.rows.length
                : rubric?.criteria.length ?? reviewModel.rows.length
            }
            selectedSeq={selectedSeq}
            onSelectEvidence={(seq) =>
              setSelectedSeq((cur) => (cur === seq ? null : seq))
            }
          />
          {scoreError ? (
            <div className="space-y-2 rounded-md border border-rose-500/60 bg-rose-500/10 p-3 text-sm text-rose-500">
              <p>{scoreError}</p>
              <button
                type="button"
                className={btnCls}
                onClick={() => {
                  setScores([]);
                  setOverall(null);
                  setDone(false);
                  setScoreError(null);
                  setReloadKey((k) => k + 1);
                }}
              >
                Retry scoring
              </button>
            </div>
          ) : null}
          <EvidenceDrawer selectedSeq={selectedSeq} event={selectedEvent} />
          {reviewModel.invalidEvidenceCount > 0 ? (
            <p className="text-xs text-rose-500">
              {reviewModel.invalidEvidenceCount} unresolved evidence citation(s) detected.
            </p>
          ) : null}

          {/* Conversation quality — computed from ledger timestamps, no extra
              instrumentation. The numbers a real interviewer would be judged on. */}
          <section className="space-y-2">
            <h2 className="text-sm font-semibold uppercase tracking-widest text-[var(--muted-foreground)]">
              Conversation quality
            </h2>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 rounded-md border border-[var(--muted)] p-3 text-xs sm:grid-cols-3">
              <div>
                <dt className="text-[var(--muted-foreground)]">Response gap (median)</dt>
                <dd className="font-mono tabular-nums">
                  {metrics.responseGapMedianMs !== null ? `${(metrics.responseGapMedianMs / 1000).toFixed(1)}s` : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-[var(--muted-foreground)]">Response gap (p90)</dt>
                <dd className="font-mono tabular-nums">
                  {metrics.responseGapP90Ms !== null ? `${(metrics.responseGapP90Ms / 1000).toFixed(1)}s` : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-[var(--muted-foreground)]">Turn generation (median)</dt>
                <dd className="font-mono tabular-nums">
                  {metrics.generationMedianMs !== null ? `${(metrics.generationMedianMs / 1000).toFixed(1)}s` : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-[var(--muted-foreground)]">Barge-ins honored</dt>
                <dd className="font-mono tabular-nums">{metrics.bargeIns} / {metrics.cancelledTurns} cancelled</dd>
              </div>
              <div>
                <dt className="text-[var(--muted-foreground)]">Hints (deepest rung)</dt>
                <dd className="font-mono tabular-nums">
                  {metrics.hintCount}{metrics.maxHintStep ? ` (rung ${metrics.maxHintStep})` : ""}
                  {metrics.throttledHints ? ` · ${metrics.throttledHints} throttled` : ""}
                </dd>
              </div>
              <div>
                <dt className="text-[var(--muted-foreground)]">Silence nudges</dt>
                <dd className="font-mono tabular-nums">{metrics.silenceNudges}</dd>
              </div>
              <div>
                <dt className="text-[var(--muted-foreground)]">Guarded lines</dt>
                <dd className="font-mono tabular-nums">
                  {metrics.guardedLines}{metrics.stallRecoveries ? ` · ${metrics.stallRecoveries} stall-recovered` : ""}
                </dd>
              </div>
              <div>
                <dt className="text-[var(--muted-foreground)]">Talk balance (cand/int)</dt>
                <dd className="font-mono tabular-nums">{metrics.candidateUtterances} / {metrics.interviewerUtterances}</dd>
              </div>
              <div>
                <dt className="text-[var(--muted-foreground)]">Run progression</dt>
                <dd className="font-mono tabular-nums">
                  {metrics.runProgression.length
                    ? metrics.runProgression.map((r) => `${r.passed}/${r.total}`).join(" → ")
                    : "—"}
                </dd>
              </div>
            </dl>
          </section>
        </div>

        <div className="space-y-6">
          {/* Transcript — selecting a chip scrolls/highlights the matching turn. */}
          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
                Transcript
              </h2>
              <button
                type="button"
                onClick={() => setEvidenceOnly((v) => !v)}
                className={`rounded border px-2 py-1 text-xs transition-colors ${
                  evidenceOnly
                    ? "border-[var(--text)] bg-[var(--muted)]/30"
                    : "border-[var(--muted)] hover:bg-[var(--muted)]/20"
                }`}
              >
                {evidenceOnly ? "showing cited only" : "evidence only"}
              </button>
            </div>
            <ul className="space-y-2">
              {visibleTranscript.map((t) => (
                <li
                  key={`${t.seq}-${t.lineId}`}
                  id={`evt-${t.seq}`}
                  className={`rounded-md border p-3 text-sm ${
                    t.seq === selectedSeq
                      ? "border-[var(--text)] bg-[var(--muted)]/20"
                      : "border-[var(--muted)]"
                  }`}
                >
                  <div className="mb-1 flex items-center justify-between text-xs text-[var(--muted-foreground)]">
                    <span className="font-medium uppercase">{t.speaker}</span>
                    <span className="font-mono">#{t.seq}</span>
                  </div>
                  <p>{t.text}</p>
                </li>
              ))}
              {visibleTranscript.length === 0 ? (
                <li className="text-xs text-[var(--muted-foreground)]">
                  {evidenceOnly ? "No cited transcript turns." : "No turns."}
                </li>
              ) : null}
            </ul>
          </section>

          <section className="space-y-2">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
              Code state (seq #{codeState.seq || "—"})
            </h2>
            <pre
              id={codeState.seq ? `evt-${codeState.seq}` : undefined}
              className={`max-h-48 overflow-auto rounded-md border bg-[var(--muted)]/10 p-3 text-xs ${
                codeState.seq && codeState.seq === selectedSeq
                  ? "border-[var(--text)] ring-1 ring-[var(--text)]"
                  : "border-[var(--muted)]"
              }`}
            >
              <code>{codeState.code || "// empty"}</code>
            </pre>
          </section>

          <section className="space-y-2">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
              Run results
            </h2>
            {visibleRuns.length === 0 ? (
              <p className="text-xs text-[var(--muted-foreground)]">
                {evidenceOnly && runs.length > 0 ? "No cited runs." : "No runs."}
              </p>
            ) : (
              visibleRuns.map((r) => (
                <div
                  key={r.runId}
                  id={`evt-${r.seq}`}
                  className={`rounded-md border p-3 text-xs ${
                    r.seq === selectedSeq
                      ? "border-[var(--text)] bg-[var(--muted)]/20"
                      : "border-[var(--muted)]"
                  }`}
                >
                  <div className="mb-1 flex items-center justify-between text-[var(--muted-foreground)]">
                    <span className="font-mono">{r.runId}</span>
                    <span className="font-mono">
                      #{r.seq} · exit {r.exitCode}
                    </span>
                  </div>
                  <pre className="overflow-auto">
                    <code>{r.stdout || "(no stdout)"}</code>
                  </pre>
                </div>
              ))
            )}
          </section>
        </div>
      </div>

      {/* Raw evidence stream for inspection. */}
      <details className="text-xs">
        <summary className="cursor-pointer text-[var(--muted-foreground)]">
          Ledger ({orderBySeq(ledger).length} events)
        </summary>
        <ol className="mt-2 space-y-1">
          {orderBySeq(ledger).map((e) => (
            <li key={e.seq} className="flex gap-3 font-mono text-[var(--muted-foreground)]">
              <span className="w-10">#{e.seq}</span>
              <span className="w-16">{e.actor}</span>
              <span>{e.type}</span>
            </li>
          ))}
        </ol>
      </details>
    </main>
  );
}
