"use client";

import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  makeAdapter,
  normalizeMode,
  makeSessionId,
  putHandoff,
  type Intake,
  type Rubric,
  type Seniority,
} from "@/lib/interview-v3";
import { normalizeTrack, INCIDENT_TRACK, INCIDENT_DEFAULTS } from "@/lib/interview-v3/incident";
import { RubricPreview } from "./RubricPreview";
import { API_URL } from "@/lib/api-url";

const SENIORITIES: Seniority[] = ["intern", "junior", "mid", "senior", "staff"];

const labelCls = "block text-sm font-medium";
const inputCls =
  "mt-1 w-full rounded-md border border-[var(--muted)] bg-transparent px-3 py-2 text-sm outline-none focus:border-[var(--muted-foreground)]";

export function IntakeForm() {
  const searchParams = useSearchParams();
  const mode = normalizeMode(searchParams.get("adapter"));
  // TEST-ONLY: live-llm deterministic backend path (honored only when the
  // backend sets VNEXT_ALLOW_FAKE_LLM=1). Carried through the handoff.
  const fakeLlm = mode === "live-llm" && searchParams.get("fake") === "1";
  // Lab-only interview track (e.g. incident-demo). Backend-driven, so live-llm only.
  const track = mode === "live-llm" ? normalizeTrack(searchParams.get("track")) : undefined;
  const isIncident = track === INCIDENT_TRACK;
  // Incident demo auto-starts (zero setup). `?manual=1` keeps the setup form —
  // used by the e2e suite to exercise the intake → room path deterministically.
  const autostart = isIncident && searchParams.get("manual") !== "1";

  // The incident demo pre-fills sensible defaults so a human never touches setup.
  const [resumeText, setResumeText] = useState("");
  const [resumeFileName, setResumeFileName] = useState<string | undefined>();
  const [jobDescription, setJobDescription] = useState(isIncident ? INCIDENT_DEFAULTS.jobDescription : "");
  const [role, setRole] = useState(isIncident ? INCIDENT_DEFAULTS.role : "Software Engineer");
  const [seniority, setSeniority] = useState<Seniority>(isIncident ? INCIDENT_DEFAULTS.seniority : "mid");
  const [languages, setLanguages] = useState(isIncident ? INCIDENT_DEFAULTS.languages : "python, typescript");
  const [durationMinutes, setDurationMinutes] = useState(isIncident ? INCIDENT_DEFAULTS.durationMinutes : 45);
  const [rubric, setRubric] = useState<Rubric | null>(null);
  const [intake, setIntake] = useState<Intake | null>(null);
  const [busy, setBusy] = useState(false);
  const router = useRouter();

  const [genningJd, setGenningJd] = useState(false);

  async function handleGenerateJd() {
    setGenningJd(true);
    try {
      const res = await fetch(`${API_URL}/vnext/interview/jd`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          role,
          seniority,
          languages: languages.split(",").map((l) => l.trim()).filter(Boolean),
          fake_llm: fakeLlm,
        }),
      });
      if (!res.ok) return;
      const data: { jobDescription?: string } = await res.json();
      if (data.jobDescription) setJobDescription(data.jobDescription);
    } catch {
      // Lab-only: leave the field as-is on failure.
    } finally {
      setGenningJd(false);
    }
  }

  function handleFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setResumeFileName(file.name);
    // Phase A placeholder: read plain text only. PDF parsing is deferred.
    if (file.type === "text/plain" || file.name.endsWith(".txt")) {
      file.text().then(setResumeText);
    }
  }

  function buildIntake(): Intake {
    return {
      resumeText,
      resumeFileName,
      jobDescription,
      role,
      seniority,
      languages: languages.split(",").map((l) => l.trim()).filter(Boolean),
      durationMinutes,
    };
  }

  function enterRoom(nextIntake: Intake, nextRubric: Rubric) {
    const sessionId = makeSessionId();
    putHandoff({ sessionId, intake: nextIntake, rubric: nextRubric, mode, fakeLlm, track });
    const suffix =
      mode === "mock" ? "" : `?adapter=${mode}${fakeLlm ? "&fake=1" : ""}`;
    router.push(`/lab/interview-v3/session/${sessionId}${suffix}`);
  }

  async function handleGenerate() {
    setBusy(true);
    try {
      const nextIntake = buildIntake();
      const adapter = makeAdapter({ mode, sessionId: "lab-intake", fakeLlm, track });
      setRubric(await adapter.generateRubric(nextIntake));
      setIntake(nextIntake);
    } finally {
      setBusy(false);
    }
  }

  function handleProceed() {
    if (!intake || !rubric) return;
    enterRoom(intake, rubric);
  }

  // ── incident demo: zero-setup auto-start ──────────────────────────────────
  // The incident track is a finished demo — no resume, no manual setup. On the
  // very first mount we generate the rubric (which already falls back to a fast
  // deterministic one if the LLM is slow) and drop the candidate straight into
  // the room. A ref guards React StrictMode's double mount so we never mint two
  // sessions. If generation throws entirely, we fall back to the manual form.
  const [autoState, setAutoState] = useState<"idle" | "starting" | "error">(
    autostart ? "starting" : "idle",
  );
  const autoStartedRef = useRef(false);
  useEffect(() => {
    if (!autostart || autoStartedRef.current) return;
    autoStartedRef.current = true;
    let cancelled = false;
    void (async () => {
      try {
        const nextIntake = buildIntake();
        const adapter = makeAdapter({ mode, sessionId: "lab-intake", fakeLlm, track });
        const r = await adapter.generateRubric(nextIntake);
        if (cancelled) return;
        enterRoom(nextIntake, r); // navigates away
      } catch {
        if (cancelled) return;
        autoStartedRef.current = false; // allow a manual retry
        setAutoState("error");
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autostart]);

  // Perceived-performance + safety net for the auto-start. The first launch in
  // dev compiles the room route on demand (can take 10–20s) — so after a short
  // delay we reassure the candidate instead of looking frozen, and after a hard
  // cap we drop to the manual form rather than spin forever.
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (autoState !== "starting") return;
    const slowT = window.setTimeout(() => setSlow(true), 6000);
    const giveUpT = window.setTimeout(() => {
      autoStartedRef.current = false; // allow a manual retry
      setAutoState("error");
    }, 45000);
    return () => {
      window.clearTimeout(slowT);
      window.clearTimeout(giveUpT);
    };
  }, [autoState]);

  // While auto-starting (or recovering from an error) show a clean preparing
  // screen instead of the setup form — the human never sees intake fields.
  if (autostart && autoState !== "error") {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 text-center">
        <div className="size-8 animate-spin rounded-full border-2 border-[var(--muted)] border-t-transparent" />
        <div className="space-y-1">
          <h1 className="text-lg font-semibold">Preparing your interview…</h1>
          <p className="text-sm text-[var(--muted-foreground)]">
            Maya is loading the production incident. Use <strong>Chrome</strong> and
            allow the microphone when asked.
          </p>
          {slow ? (
            <p className="pt-1 text-xs text-[var(--muted-foreground)]">
              First load takes a few extra seconds while the room compiles — hang tight.
            </p>
          ) : null}
        </div>
      </div>
    );
  }

  // 3-way mode cycle: mock → live-scripted → live-llm → mock.
  const MODE_LABEL: Record<string, string> = {
    mock: "mock (in-memory)",
    live: "live-scripted (backend)",
    "live-llm": "live-llm (OpenRouter)",
  };
  const NEXT_MODE: Record<string, string> = { mock: "live", live: "live-llm", "live-llm": "mock" };
  const nextMode = NEXT_MODE[mode];
  const nextHref = nextMode === "mock"
    ? "/lab/interview-v3/intake"
    : `/lab/interview-v3/intake?adapter=${nextMode}`;

  return (
    <div className="space-y-8">
      {isIncident && autoState === "error" ? (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-600 dark:text-amber-400">
          Couldn’t auto-start the interview. Click <strong>Generate rubric → Start
          interview</strong> below to launch it manually.
        </div>
      ) : null}
      {isIncident ? (
        <div className="rounded-lg border border-[var(--muted)] p-5 space-y-2">
          <h2 className="text-lg font-semibold">Software Engineer Incident Demo</h2>
          <p className="text-sm text-[var(--muted-foreground)]">
            Defaults are pre-filled for a payments backend interview. Two steps:{" "}
            <strong>Generate rubric → Start interview</strong>.
          </p>
          <p className="text-xs text-[var(--muted-foreground)]">
            Use <strong>Chrome</strong> · allow the microphone when asked · speak
            over the interviewer to test barge-in.
          </p>
        </div>
      ) : null}
      <div className="flex items-center gap-3 text-xs">
        <span className="uppercase tracking-widest text-[var(--muted-foreground)]">
          adapter
        </span>
        <span className="font-mono">{MODE_LABEL[mode]}</span>
        <a
          href={nextHref}
          className="rounded border border-[var(--muted)] px-2 py-1 hover:bg-[var(--muted)]/20"
        >
          switch to {MODE_LABEL[nextMode]}
        </a>
        {track === INCIDENT_TRACK ? (
          <span className="rounded bg-[var(--muted)]/30 px-2 py-1 font-medium uppercase tracking-wide">
            Incident demo track
          </span>
        ) : null}
      </div>
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          void handleGenerate();
        }}
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className={labelCls} htmlFor="role">
              Role
            </label>
            <input
              id="role"
              className={inputCls}
              value={role}
              onChange={(e) => setRole(e.target.value)}
            />
          </div>
          <div>
            <label className={labelCls} htmlFor="seniority">
              Seniority
            </label>
            <select
              id="seniority"
              className={inputCls}
              value={seniority}
              onChange={(e) => setSeniority(e.target.value as Seniority)}
            >
              {SENIORITIES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className={labelCls} htmlFor="languages">
              Languages (comma separated)
            </label>
            <input
              id="languages"
              className={inputCls}
              value={languages}
              onChange={(e) => setLanguages(e.target.value)}
            />
          </div>
          <div>
            <label className={labelCls} htmlFor="duration">
              Duration (minutes)
            </label>
            <input
              id="duration"
              type="number"
              min={15}
              max={120}
              className={inputCls}
              value={durationMinutes}
              onChange={(e) => setDurationMinutes(Number(e.target.value))}
            />
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between">
            <label className={labelCls} htmlFor="jd">
              Job description
            </label>
            <button
              type="button"
              onClick={() => void handleGenerateJd()}
              disabled={genningJd || !role.trim()}
              className="rounded border border-[var(--muted)] px-2 py-1 text-xs transition-colors hover:bg-[var(--muted)]/20 disabled:opacity-50"
            >
              {genningJd ? "Generating…" : "✨ Generate JD"}
            </button>
          </div>
          <textarea
            id="jd"
            className={`${inputCls} h-24 resize-y`}
            placeholder="Paste a JD, or click “Generate JD” to auto-write one for this role."
            value={jobDescription}
            onChange={(e) => setJobDescription(e.target.value)}
          />
        </div>

        <div>
          <label className={labelCls} htmlFor="resume">
            Resume (paste)
          </label>
          <textarea
            id="resume"
            className={`${inputCls} h-32 resize-y`}
            value={resumeText}
            onChange={(e) => setResumeText(e.target.value)}
          />
          <div className="mt-2 flex items-center gap-3 text-xs text-[var(--muted-foreground)]">
            <input type="file" accept=".txt,text/plain" onChange={handleFile} />
            <span>.txt upload only — PDF parsing deferred to a later phase.</span>
          </div>
          {resumeFileName ? (
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">
              Loaded: {resumeFileName}
            </p>
          ) : null}
        </div>

        <button
          type="submit"
          disabled={busy}
          className="rounded-md border border-[var(--muted)] px-4 py-2 text-sm font-medium transition-colors hover:bg-[var(--muted)]/20 disabled:opacity-50"
        >
          {busy ? "Generating rubric…" : "Generate rubric"}
        </button>
        {busy && mode === "live-llm" ? (
          <p className="text-xs text-[var(--muted-foreground)]">
            Generating rubric… if the AI is slow we switch to a fast fallback
            automatically, so you’re never left waiting.
          </p>
        ) : null}
      </form>

      {rubric ? (
        <div className="space-y-4">
          {mode === "live-llm" && rubric.generatedBy !== "llm" ? (
            <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-600 dark:text-amber-400">
              Used a fast fallback rubric (the AI was slow or unavailable). You
              can proceed — the interview itself still runs live.
            </p>
          ) : null}
          <RubricPreview rubric={rubric} />
          <button
            type="button"
            onClick={handleProceed}
            className="rounded-md border border-[var(--muted)] bg-[var(--muted)]/20 px-4 py-2 text-sm font-semibold transition-colors hover:bg-[var(--muted)]/40"
          >
            {isIncident ? "Start interview →" : "Proceed to interview →"}
          </button>
        </div>
      ) : null}
    </div>
  );
}
