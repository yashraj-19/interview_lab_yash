/**
 * Intake + normalized context models.
 *
 * The Intake is the raw operator/candidate input. The InterviewContext is the
 * normalized projection derived from it — the stable shape rubric generation
 * consumes, so generation never reads raw form fields directly.
 */

export type Seniority = "intern" | "junior" | "mid" | "senior" | "staff";

export interface Intake {
  /** Pasted resume text (Phase A: paste or .txt upload; PDF parsing deferred). */
  resumeText: string;
  resumeFileName?: string;
  jobDescription: string;
  role: string;
  seniority: Seniority;
  /** Programming languages in scope, lowercased on normalize. */
  languages: string[];
  durationMinutes: number;
}

/**
 * Normalized context derived from an {@link Intake}. Deterministic projection —
 * trimmed, lowercased languages, deduped, with cheap derived signals used by
 * rubric generation. No LLM involved.
 */
export interface InterviewContext {
  role: string;
  seniority: Seniority;
  languages: string[];
  durationMinutes: number;
  resumeText: string;
  jobDescription: string;
  /** Coarse signal: does the JD/resume emphasize systems/scale work. */
  emphasizesScale: boolean;
  /** Coarse signal: does the JD/resume emphasize frontend/UI work. */
  emphasizesFrontend: boolean;
}

const SCALE_HINTS = ["scale", "distributed", "latency", "throughput", "infra", "backend"];
const FRONTEND_HINTS = ["react", "frontend", "ui", "css", "typescript", "next"];

function hasAny(haystack: string, needles: string[]): boolean {
  const lower = haystack.toLowerCase();
  return needles.some((n) => lower.includes(n));
}

/** Deterministic normalization. Same intake → identical context. */
export function normalizeIntake(intake: Intake): InterviewContext {
  const languages = Array.from(
    new Set(
      intake.languages
        .map((l) => l.trim().toLowerCase())
        .filter((l) => l.length > 0),
    ),
  );
  const corpus = `${intake.jobDescription}\n${intake.resumeText}\n${languages.join(" ")}`;
  return {
    role: intake.role.trim(),
    seniority: intake.seniority,
    languages,
    durationMinutes: intake.durationMinutes,
    resumeText: intake.resumeText,
    jobDescription: intake.jobDescription,
    emphasizesScale: hasAny(corpus, SCALE_HINTS),
    emphasizesFrontend: hasAny(corpus, FRONTEND_HINTS),
  };
}
