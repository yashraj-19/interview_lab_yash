/**
 * Semantic turn-taking for the hands-free voice room — pure functions only.
 *
 * Ported from the production-tuned Voice_Assist timing brain (barge_in.py):
 * deterministic, transcript-driven, no model in any timing path.
 *
 * Two concerns:
 *
 * 1. TURN-END (`decideTurnEnd`) — replaces the old fixed 1600ms auto-send
 *    timer. Three arms, decided from how the accumulated answer ENDS:
 *      - trailing continuation token ("so I think we could…", "and", comma)
 *        → the candidate is mid-thought: wait much longer before sending;
 *      - complete-sounding ending (terminal punctuation) → confirm fast;
 *      - neutral ending → a middle-ground pause (Web Speech usually emits no
 *        punctuation, so this is the common arm — keep it near the old feel).
 *    Every arm returns a delay (never "never") so a candidate who stops
 *    mid-thought can't hang the interview: the timer is a grace period that
 *    resets on any further speech, not a hard gate.
 *
 * 2. BARGE-IN GATE (`decideBargeIn`) — while the interviewer is speaking,
 *    only real interruptions take the floor: an utterance-initial cut-in word
 *    ("wait", "stop"…), ≥3 non-backchannel words, or sustained speech. Pure
 *    backchannels ("mm-hm", "yeah") never assassinate the interviewer
 *    mid-sentence. Token sets mirror backend intent.py so client and server
 *    agree on what counts as a backchannel / cut-in.
 */

// ── token sets (Voice_Assist barge_in.py:632-673, verbatim) ─────────────────

const CONJUNCTIONS = new Set([
  "and", "but", "or", "so", "because", "nor", "yet", "while", "if", "when",
  "since", "although", "though", "unless", "as", "whereas", "plus", "also",
  "then", "that", "which",
]);

const PREPOSITIONS = new Set([
  "in", "on", "at", "to", "for", "with", "of", "from", "by", "about", "into",
  "onto", "over", "under", "between", "through", "during", "before", "after",
  "above", "below", "near", "off", "without", "within", "upon", "per", "like",
  "than", "toward", "towards", "around",
]);

const ARTICLES_DETERMINERS = new Set([
  "a", "an", "the", "my", "your", "his", "her", "their", "its", "our",
  "this", "these", "those",
]);

const AUX_VERBS_CONTRACTIONS = new Set([
  "is", "are", "was", "were", "be", "been", "being", "am", "have", "has",
  "had", "do", "does", "did", "will", "would", "shall", "should", "can",
  "could", "may", "might", "must", "i'm", "i've", "i'd", "it's", "that's",
  "there's", "we're", "they're", "you're", "i'll",
]);

/** Words that mark an utterance as mid-thought when they END it. */
const CONTINUATION_WORDS = new Set([
  ...CONJUNCTIONS,
  ...PREPOSITIONS,
  ...ARTICLES_DETERMINERS,
  ...AUX_VERBS_CONTRACTIONS,
]);

/** Pure filler sounds — never an interruption (mirrors intent.py backchannels). */
const BACKCHANNEL_WORDS = new Set([
  "hmm", "yeah", "okay", "ok", "right", "uh-huh", "mhm", "yep", "sure", "ah",
  // two-word backchannels are handled as phrases in nonBackchannelWordCount
  "i", "see", "got", "it",
]);

/** Utterance-INITIAL interrupt words: one of these opening the utterance is an
 * immediate barge-in even as a single word (mirrors intent.py cut-ins). */
const CUT_IN_RX = /^\s*(?:wait|stop|hold|hang|sorry|actually|no|hey|pause|excuse)\b/i;

// ── turn-end decision ────────────────────────────────────────────────────────

/** Complete-sounding ending: confirm quickly. */
export const CONFIRM_COMPLETE_MS = 700;
/** Neutral ending (no punctuation — the Web Speech common case): near the old
 * fixed-timer feel, slightly faster. */
export const NEUTRAL_MS = 1500;
/** Trailing continuation token: the candidate is mid-thought — wait long, but
 * never forever (grace period, resets on further speech). */
export const CONTINUATION_MS = 4000;

export interface TurnEndDecision {
  /** Why this delay was chosen — logged/testable, and useful for UI copy. */
  reason: "complete" | "neutral" | "continuation" | "empty";
  /** Milliseconds of silence to wait (from the latest final chunk) before
   * auto-sending. `null` for empty text (nothing to send). */
  delayMs: number | null;
}

export function endsInTerminalPunct(text: string): boolean {
  return /[.?!]\s*$/.test(text.trim());
}

export function endsInContinuation(text: string): boolean {
  const t = text.trim().toLowerCase();
  if (!t) return false;
  if (t.endsWith(",")) return true;
  // Last word, keeping apostrophes ("i'm") — trailing punctuation stripped.
  const m = t.match(/([a-z']+)[^a-z']*$/);
  if (!m) return false;
  const last = m[1];
  if (CONTINUATION_WORDS.has(last)) return true;
  // Gerund heuristic: "…so I was thinking" trails off more often than not.
  if (last.endsWith("ing") && last.length > 4) return true;
  return false;
}

/** Decide how long to wait (after the latest final STT chunk) before
 * auto-sending the accumulated answer. */
export function decideTurnEnd(text: string): TurnEndDecision {
  const t = (text ?? "").trim();
  if (!t) return { reason: "empty", delayMs: null };
  if (endsInContinuation(t)) return { reason: "continuation", delayMs: CONTINUATION_MS };
  if (endsInTerminalPunct(t)) return { reason: "complete", delayMs: CONFIRM_COMPLETE_MS };
  return { reason: "neutral", delayMs: NEUTRAL_MS };
}

// ── barge-in gate ────────────────────────────────────────────────────────────

/** Sustained-speech threshold: even soft/slow speech takes the floor after
 * this long (Voice_Assist production value). */
export const SUSTAINED_SPEECH_MS = 700;
/** Minimum non-backchannel words for an instant interrupt. */
export const MIN_INTERRUPT_WORDS = 3;

/** True when the utterance OPENS with an explicit interrupt word. */
export function isCutIn(text: string): boolean {
  return CUT_IN_RX.test(text ?? "");
}

/** Count words that are not pure backchannel filler ("mm-hm", "yeah", "i see"). */
export function nonBackchannelWordCount(text: string): number {
  const words = (text ?? "").toLowerCase().split(/[^a-z']+/).filter(Boolean);
  return words.filter((w) => !BACKCHANNEL_WORDS.has(w)).length;
}

export type BargeDecision = "interrupt" | "ignore";

/**
 * While the interviewer is speaking, decide whether heard candidate speech is
 * a real interruption or a backchannel to talk through.
 */
export function decideBargeIn(heard: string, sustainedMs = 0): BargeDecision {
  if (isCutIn(heard)) return "interrupt";
  if (nonBackchannelWordCount(heard) >= MIN_INTERRUPT_WORDS) return "interrupt";
  if (sustainedMs >= SUSTAINED_SPEECH_MS) return "interrupt";
  return "ignore";
}

// ── latency-masking fillers ──────────────────────────────────────────────────

/** Short, neutral thinking sounds spoken (from a pre-fetched cache) the moment
 * an interviewer turn STARTS generating, so the room never sits in dead air
 * while the LLM + TTS round-trip completes. Deterministic pick by seq. */
export const FILLER_LINES = [
  "Hm.",
  "Okay.",
  "Right, let me think.",
  "Mm, one moment.",
] as const;

export function pickFiller(seq: number): string {
  return FILLER_LINES[Math.abs(seq) % FILLER_LINES.length];
}
