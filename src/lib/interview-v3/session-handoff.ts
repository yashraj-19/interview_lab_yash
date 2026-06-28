/**
 * Client-side intake → session handoff (NO backend).
 *
 * The intake page generates a rubric, stashes the intake + rubric under a
 * generated id, and navigates to the session route. The session page reads it
 * back by id. State lives in a module-level map (survives client navigation)
 * with a sessionStorage mirror so a hard refresh of the room can recover.
 *
 * This is deliberately isolated and deterministic — no network, no LLM.
 */

import type { Intake } from "./intake";
import type { Rubric } from "./rubric";
import type { AdapterMode } from "./make-adapter";
import { saveSession, loadSession } from "./session-store";

export interface SessionHandoff {
  sessionId: string;
  intake: Intake;
  rubric: Rubric;
  /** Which adapter drives the room/review for this session. Defaults to "mock". */
  mode?: AdapterMode;
  /** TEST-ONLY: live-llm with the backend's deterministic fake-LLM path. */
  fakeLlm?: boolean;
  /** Optional lab-only interview track (e.g. "incident-demo"). */
  track?: string;
}

const STORAGE_PREFIX = "iv3-handoff:";

const memory = new Map<string, SessionHandoff>();

/** Deterministic-enough id for a lab session; not security-sensitive. */
export function makeSessionId(): string {
  const rand = Math.random().toString(36).slice(2, 8);
  return `iv3-${Date.now().toString(36)}-${rand}`;
}

function storageKey(sessionId: string): string {
  return `${STORAGE_PREFIX}${sessionId}`;
}

export function putHandoff(handoff: SessionHandoff): void {
  memory.set(handoff.sessionId, handoff);
  if (typeof window !== "undefined") {
    try {
      window.sessionStorage.setItem(storageKey(handoff.sessionId), JSON.stringify(handoff));
    } catch {
      // sessionStorage unavailable (private mode / quota) — memory store still works.
    }
  }
  // Mirror to the durable local store so a hard refresh (or new tab via a
  // copied review link) recovers the session even when sessionStorage is gone.
  saveSession({
    sessionId: handoff.sessionId,
    intake: handoff.intake,
    rubric: handoff.rubric,
    mode: handoff.mode,
    fakeLlm: handoff.fakeLlm,
    track: handoff.track,
  });
}

export function getHandoff(sessionId: string): SessionHandoff | null {
  const inMemory = memory.get(sessionId);
  if (inMemory) return inMemory;
  if (typeof window !== "undefined") {
    try {
      const raw = window.sessionStorage.getItem(storageKey(sessionId));
      if (raw) {
        const parsed = JSON.parse(raw) as SessionHandoff;
        memory.set(sessionId, parsed);
        return parsed;
      }
    } catch {
      // ignore parse/storage errors — treat as missing.
    }
  }
  // Fall back to the durable local store (survives hard refresh / new tab).
  const stored = loadSession(sessionId);
  if (stored) {
    const recovered: SessionHandoff = {
      sessionId: stored.sessionId,
      intake: stored.intake,
      rubric: stored.rubric,
      mode: stored.mode,
      fakeLlm: stored.fakeLlm,
      track: stored.track,
    };
    memory.set(sessionId, recovered);
    return recovered;
  }
  return null;
}
