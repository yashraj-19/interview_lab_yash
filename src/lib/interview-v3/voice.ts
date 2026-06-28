/**
 * Voice demo layer — PURE helpers (no React, no direct browser globals).
 *
 * Everything browser-touching is injected (a `VoiceWindow`) so these helpers are
 * unit-testable in node. The React hooks in
 * `components/interview-v3/session/useVoice.ts` wrap these with real effects.
 *
 * Lab-only: this never touches the production interview/transport. Voice is an
 * input/output skin over the SAME text flow — a recognized utterance only
 * becomes a real `candidate.utterance` when the candidate hits Send, and the
 * server stays the sole authority over seq/events (no local ledger forgery).
 */

// ── minimal structural types for the Web Speech API ───────────────────────────
// The DOM lib does not ship SpeechRecognition; we model only what we use so we
// never reach for `any`.

export interface SpeechRecognitionAlternativeLike {
  readonly transcript: string;
}

export interface SpeechRecognitionResultLike {
  readonly isFinal: boolean;
  readonly length: number;
  readonly [index: number]: SpeechRecognitionAlternativeLike;
}

export interface SpeechRecognitionResultListLike {
  readonly length: number;
  readonly [index: number]: SpeechRecognitionResultLike;
}

export interface SpeechRecognitionEventLike {
  readonly resultIndex: number;
  readonly results: SpeechRecognitionResultListLike;
}

export interface SpeechRecognitionErrorEventLike {
  readonly error?: string;
}

export interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  abort(): void;
  onstart: (() => void) | null;
  onend: (() => void) | null;
  onerror: ((e: SpeechRecognitionErrorEventLike) => void) | null;
  onresult: ((e: SpeechRecognitionEventLike) => void) | null;
}

export type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

export interface SpeechSynthesisVoiceLike {
  readonly name: string;
  readonly lang: string;
}

export interface SpeechSynthesisUtteranceLike {
  voice: SpeechSynthesisVoiceLike | null;
  rate: number;
  pitch: number;
  volume: number;
  onstart: (() => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
}

/** Minimal controllable audio element (real `HTMLAudioElement` satisfies it). */
export interface PlayableAudio {
  muted: boolean;
  volume?: number;
  play(): Promise<void> | void;
  pause(): void;
  onended: (() => void) | null;
  onerror: (() => void) | null;
}

/** Playback volume for the interviewer (Sia) audio. 0..1; lowered per request. */
export const INTERVIEWER_AUDIO_VOLUME = 0.5;

export interface SpeechSynthesisLike {
  speak(u: SpeechSynthesisUtteranceLike): void;
  cancel(): void;
  getVoices(): SpeechSynthesisVoiceLike[];
  speaking?: boolean;
  onvoiceschanged?: (() => void) | null;
}

/** The slice of `window` the voice layer depends on (injectable for tests). */
export interface VoiceWindow {
  SpeechRecognition?: SpeechRecognitionCtor;
  webkitSpeechRecognition?: SpeechRecognitionCtor;
  speechSynthesis?: SpeechSynthesisLike;
  SpeechSynthesisUtterance?: new (text: string) => SpeechSynthesisUtteranceLike;
}

// ── EXACT homepage-demo voice (ElevenLabs) ────────────────────────────────────
// The live homepage demo (`/demo-lab/audio/*.mp3`) is rendered by ElevenLabs, NOT
// the browser. To sound IDENTICAL, the lab synthesizes interviewer turns with the
// SAME voice + model + settings. Source of truth: `scripts/generate-demo-audio.ts`.

/**
 * ACTIVE interviewer ElevenLabs voice: "Sia - Sweet & Smart Sales Professional"
 * (`oO7sLA3dWfQXsKeSAjpA`). The server route passes the `ELEVEN_VOICE_INTERVIEWER`
 * env override in; this default keeps the id documented in one place.
 * (Earlier experiments used "Raj" `wJ5MX7uuKXZwFqGdWM4N` and `8qPG2eSETnKl5ezq52Js`
 * — NOT in use now.) NOTE: these are library/professional voices and require a
 * PAID ElevenLabs plan — a free key returns 402 `paid_plan_required`.
 */
export const DEMO_INTERVIEWER_VOICE_ID = "oO7sLA3dWfQXsKeSAjpA";
export const DEMO_VOICE_MODEL_ID = "eleven_multilingual_v2";
export const DEMO_VOICE_OUTPUT_FORMAT = "mp3_44100_128";
/** Interviewer voice_settings, verbatim from the demo generator. */
export const DEMO_VOICE_SETTINGS = {
  stability: 0.4,
  similarity_boost: 0.8,
  style: 0.35,
  use_speaker_boost: true,
} as const;

export interface ElevenVoiceRequest {
  url: string;
  body: {
    text: string;
    model_id: string;
    voice_settings: typeof DEMO_VOICE_SETTINGS;
  };
}

/**
 * Build the ElevenLabs text-to-speech request for an interviewer turn, matching
 * the homepage demo EXACTLY (voice id, model, settings, output format). Pure +
 * testable; the API key is added by the server route, never here.
 */
export function buildInterviewerVoiceRequest(
  text: string,
  voiceId: string = DEMO_INTERVIEWER_VOICE_ID,
): ElevenVoiceRequest {
  return {
    url: `https://api.elevenlabs.io/v1/text-to-speech/${voiceId}?output_format=${DEMO_VOICE_OUTPUT_FORMAT}`,
    body: {
      text,
      model_id: DEMO_VOICE_MODEL_ID,
      voice_settings: DEMO_VOICE_SETTINGS,
    },
  };
}

// ── browser-TTS fallback voice (only when ElevenLabs is unavailable) ───────────

/** Fallback-only settings if we must drop to browser speechSynthesis. */
export const FALLBACK_VOICE_SETTINGS = { rate: 1.0, pitch: 1.0, volume: 1.0 } as const;

/**
 * Pick a reasonable English browser voice for the FALLBACK path only (used when
 * the ElevenLabs endpoint is unreachable). Pure + testable.
 */
export function pickPreferredVoice(
  voices: readonly SpeechSynthesisVoiceLike[],
): SpeechSynthesisVoiceLike | null {
  return (
    voices.find(
      (v) =>
        v.lang.startsWith("en") &&
        (v.name.includes("Google") || v.name.includes("Samantha") || v.name.includes("Daniel")),
    ) ??
    voices.find((v) => v.lang.startsWith("en")) ??
    null
  );
}

// ── capability detection ──────────────────────────────────────────────────────

/** Resolve a SpeechRecognition constructor (standard or webkit), or null. */
export function getSpeechRecognitionCtor(
  win: VoiceWindow | undefined,
): SpeechRecognitionCtor | null {
  if (!win) return null;
  return win.SpeechRecognition ?? win.webkitSpeechRecognition ?? null;
}

export function isSpeechToTextSupported(win: VoiceWindow | undefined): boolean {
  return getSpeechRecognitionCtor(win) !== null;
}

export function isTextToSpeechSupported(win: VoiceWindow | undefined): boolean {
  return !!win && !!win.speechSynthesis && typeof win.SpeechSynthesisUtterance === "function";
}

// ── mic state machine ─────────────────────────────────────────────────────────

export type MicState = "unsupported" | "idle" | "listening" | "processing" | "error";

/**
 * Drivers of the mic state. `start`/`stop` are user intents; `started`/`ended`/
 * `errored` are recognition callbacks; `final` is a finalized transcript chunk.
 */
export type MicEvent = "start" | "started" | "final" | "stop" | "ended" | "errored" | "reset";

/**
 * Pure transition for the mic indicator (idle/listening/processing/error). Kept
 * deliberately small: `processing` is the brief window after the user asks to
 * stop while we wait for the recognizer's final result + `onend`.
 */
export function nextMicState(state: MicState, event: MicEvent): MicState {
  if (state === "unsupported") {
    // Only an explicit reset (e.g. capability re-check) can leave unsupported.
    return event === "reset" ? "idle" : "unsupported";
  }
  switch (event) {
    case "errored":
      return "error";
    case "reset":
      return "idle";
    case "start":
      return "listening";
    case "started":
      return state === "error" ? "listening" : state === "idle" ? "listening" : state;
    case "stop":
      return state === "listening" ? "processing" : state;
    case "final":
      // Final chunks don't end a continuous session; stay in the active state.
      return state === "error" ? "error" : state;
    case "ended":
      return "idle";
    default:
      return state;
  }
}

// ── transcript extraction ─────────────────────────────────────────────────────

/** Split a recognition event into finalized + interim text (both trimmed). */
export function readRecognitionEvent(e: SpeechRecognitionEventLike): {
  finalText: string;
  interimText: string;
} {
  let finalText = "";
  let interimText = "";
  for (let i = e.resultIndex; i < e.results.length; i++) {
    const result = e.results[i];
    if (!result) continue;
    const alt = result[0];
    const chunk = alt?.transcript ?? "";
    if (result.isFinal) finalText += chunk;
    else interimText += chunk;
  }
  return { finalText: finalText.trim(), interimText: interimText.trim() };
}

// ── barge-in state machine ────────────────────────────────────────────────────

/**
 * Coordinates who "has the floor". The candidate may cut in while the interviewer
 * is speaking OR while its next turn is still being generated; barge-in cancels
 * the interviewer audio, ignores the (possibly in-flight) interviewer turn for
 * speaking purposes, and hands the floor to the candidate. The ledger is never
 * mutated — a turn the server already emitted simply isn't auto-spoken.
 */
export type BargeState =
  | "idle"
  | "interviewer_speaking"
  | "candidate_interrupting"
  | "interrupted"
  | "listening"
  | "processing"
  | "ready_to_send";

export type BargeEvent =
  | "interviewer_start" // interviewer began speaking or its turn is generating
  | "interviewer_done" // interviewer audio finished naturally
  | "speech_start" // candidate speech detected
  | "interrupt_done" // cancellation of interviewer audio completed
  | "listening" // mic actively listening
  | "final" // a finalized transcript chunk arrived
  | "stop" // mic stopped → finalize
  | "answer_ready" // there is text ready to send
  | "sent" // candidate answer sent
  | "reset";

/** True when the candidate has taken (or is taking) the floor from the interviewer. */
export function isCandidateFloor(state: BargeState): boolean {
  return (
    state === "candidate_interrupting" ||
    state === "interrupted" ||
    state === "listening" ||
    state === "processing" ||
    state === "ready_to_send"
  );
}

/** Pure transition for the barge-in coordinator. Unknown pairs are no-ops. */
export function nextBargeState(state: BargeState, event: BargeEvent): BargeState {
  if (event === "reset") return "idle";
  switch (state) {
    case "idle":
      if (event === "interviewer_start") return "interviewer_speaking";
      if (event === "speech_start" || event === "listening") return "listening";
      return state;
    case "interviewer_speaking":
      // Candidate cuts in → begin interrupting; or interviewer finishes cleanly.
      if (event === "speech_start") return "candidate_interrupting";
      if (event === "interviewer_done") return "idle";
      return state;
    case "candidate_interrupting":
      if (event === "interrupt_done") return "interrupted";
      return state;
    case "interrupted":
      if (event === "listening" || event === "speech_start") return "listening";
      if (event === "final") return "processing";
      return state;
    case "listening":
      if (event === "stop") return "processing";
      if (event === "answer_ready") return "ready_to_send";
      // a `final` keeps a continuous session listening
      return state;
    case "processing":
      if (event === "answer_ready") return "ready_to_send";
      if (event === "listening" || event === "speech_start") return "listening";
      return state;
    case "ready_to_send":
      if (event === "sent") return "idle";
      if (event === "speech_start" || event === "listening") return "listening";
      return state;
    default:
      return state;
  }
}

/**
 * Merge a freshly finalized chunk into the existing answer-box text. Keeps a
 * single space between fragments and never double-spaces. Used so dictating
 * across several pauses accumulates naturally in the same answer.
 */
/**
 * Clean an interviewer line before it's spoken so the TTS doesn't mangle code-ish
 * tokens: `charge_customer()` → "charge customer", strip backticks/%s/brackets,
 * soften method dots, collapse whitespace. Natural words are untouched.
 */
export function sanitizeForSpeech(text: string): string {
  return text
    .replace(/`+/g, " ")
    .replace(/\(\s*\)/g, " ") // foo() → foo
    .replace(/%[sd]/g, " ") // SQL/printf placeholders
    .replace(/_/g, " ") // snake_case → spaced words
    .replace(/[{}[\]<>|]/g, " ")
    .replace(/\s*\.\s*(?=[a-z])/gi, " ") // db.query → "db query"
    // Homograph fix: the ADJECTIVE "live" (/laɪv/) before a noun — respell so the
    // TTS doesn't say the verb /lɪv/. "live issue/system/demo…" → "lyve …".
    .replace(
      /\blive\b(?=\s+(?:issue|system|interview|demo|production|service|incident|traffic|site|environment|server|session|code|coding|stream|call|run))/gi,
      "lyve",
    )
    .replace(/\s{2,}/g, " ")
    .trim();
}

function speechTokens(text: string): string[] {
  return sanitizeForSpeech(text)
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter((t) => t.length >= 3);
}

/**
 * Web Speech can hear the speaker output and return Maya's own words as if the
 * candidate spoke them. During hands-free mode that false positive cancels TTS
 * via barge-in and may auto-send junk. Treat a short recognition chunk as echo
 * when most of its meaningful words are from the currently spoken Maya line.
 */
export function isLikelySpeechEcho(recognized: string, spoken: string): boolean {
  const heard = speechTokens(recognized);
  if (heard.length < 3) return false;
  const source = new Set(speechTokens(spoken));
  if (source.size < 5) return false;
  const overlap = heard.filter((t) => source.has(t)).length;
  return overlap / heard.length >= 0.72;
}

export function appendFinal(existing: string, chunk: string): string {
  const a = existing.trimEnd();
  const b = chunk.trim();
  if (!b) return existing;
  if (!a) return b;
  return `${a} ${b}`;
}
