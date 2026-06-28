/**
 * Local persistence for lab sessions (NO backend).
 *
 * The intake→room→review flow is fully client-side and deterministic. To let a
 * hard refresh of /session or /review recover — and to support "copy local
 * review link" so a session can be reopened in another tab — we persist the
 * session payload (intake + rubric + optional ledger + optional scorecard draft)
 * to localStorage, keyed by sessionId.
 *
 * Robustness: every read is defensive. A missing entry returns null; a corrupt
 * entry (bad JSON / wrong shape) returns null and is treated as "not found" —
 * never throws. Writes swallow quota/private-mode errors.
 */

import type { Intake } from "./intake";
import type { Rubric } from "./rubric";
import type { VNextEvent } from "./events";
import type { ScorecardDraft } from "./scorecard";
import type { AdapterMode } from "./make-adapter";

export interface StoredSession {
  sessionId: string;
  intake: Intake;
  rubric: Rubric;
  /** Which adapter drove this session (mock | live | live-llm). */
  mode?: AdapterMode;
  /** TEST-ONLY: live-llm session created with the backend's deterministic
   *  fake-LLM path. Carried so the room rebuilds the adapter with the same flag. */
  fakeLlm?: boolean;
  /** Optional lab-only interview track (e.g. "incident-demo"). */
  track?: string;
  /** Realized ledger, persisted once the room/review has produced it. */
  ledger?: VNextEvent[];
  /** Scorecard draft, persisted once review has generated it. */
  scorecard?: ScorecardDraft;
}

const STORAGE_PREFIX = "iv3-session:";

function storageKey(sessionId: string): string {
  return `${STORAGE_PREFIX}${sessionId}`;
}

function hasLocalStorage(): boolean {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

/** Minimal shape validation so corrupt/foreign entries are treated as missing. */
function isStoredSession(value: unknown): value is StoredSession {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.sessionId === "string" &&
    typeof v.intake === "object" &&
    v.intake !== null &&
    typeof v.rubric === "object" &&
    v.rubric !== null
  );
}

export function saveSession(session: StoredSession): void {
  if (!hasLocalStorage()) return;
  try {
    window.localStorage.setItem(storageKey(session.sessionId), JSON.stringify(session));
  } catch {
    // quota / private mode — persistence is best-effort.
  }
}

export function loadSession(sessionId: string): StoredSession | null {
  if (!hasLocalStorage()) return null;
  try {
    const raw = window.localStorage.getItem(storageKey(sessionId));
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return isStoredSession(parsed) ? parsed : null;
  } catch {
    // corrupt JSON or storage error — treat as missing.
    return null;
  }
}

/** Merge a partial update into the stored session. No-op if nothing is stored. */
export function patchSession(
  sessionId: string,
  patch: Partial<Omit<StoredSession, "sessionId">>,
): void {
  const existing = loadSession(sessionId);
  if (!existing) return;
  saveSession({ ...existing, ...patch });
}

export function clearSession(sessionId: string): void {
  if (!hasLocalStorage()) return;
  try {
    window.localStorage.removeItem(storageKey(sessionId));
  } catch {
    // ignore.
  }
}

/** Absolute URL to the local review workspace for a session. */
export function localReviewLink(sessionId: string): string {
  const path = `/lab/interview-v3/session/${sessionId}/review`;
  if (typeof window !== "undefined") return `${window.location.origin}${path}`;
  return path;
}
