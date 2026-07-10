"use client";

/**
 * Voice demo hooks for the lab vNext interview room. LAB-ONLY: these never touch
 * the production interview/transport. They are a thin browser-API skin over the
 * existing text flow — STT fills the answer box (the candidate still Sends), and
 * TTS reads interviewer turns aloud. The server stays the source of truth for
 * seq/events; nothing here forges ledger events.
 *
 * `win` is injectable so the hooks can be driven by a fake window in tests.
 */

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import {
  FALLBACK_VOICE_SETTINGS,
  INTERVIEWER_AUDIO_VOLUME,
  sanitizeForSpeech,
  getSpeechRecognitionCtor,
  nextMicState,
  pickPreferredVoice,
  readRecognitionEvent,
  type MicState,
  type PlayableAudio,
  type SpeechRecognitionLike,
  type VoiceWindow,
} from "@/lib/interview-v3/voice";

function defaultWin(): VoiceWindow | undefined {
  return typeof window === "undefined" ? undefined : (window as unknown as VoiceWindow);
}

/** False on the server and during the first client render; true after mount.
 * Lets browser-only capability flags stay hydration-safe (SSR === first paint). */
function useMounted(): boolean {
  return useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
}

// ── speech-to-text ──────────────────────────────────────────────────────────

export interface UseSpeechToTextOptions {
  /** Called with each finalized transcript chunk (already trimmed). */
  onFinalTranscript: (chunk: string) => void;
  /** Called as interim (not-yet-final) text changes, for a live preview. */
  onInterim?: (text: string) => void;
  /** Return true to discard a recognition result before it can trigger barge-in. */
  shouldIgnoreResult?: (text: string) => boolean;
  /** Fired the moment speech is detected — used for barge-in (stop the TTS). */
  onSpeechStart?: () => void;
  /** Fired for EVERY passing (non-ignored) recognition result with the full
   * heard text (final + interim). Unlike onSpeechStart (one-shot per
   * recognizer instance), this lets the caller run per-result decisions —
   * the semantic barge-in gate and auto-send timer management. */
  onResult?: (heard: string, isFinal: boolean) => void;
  win?: VoiceWindow;
  lang?: string;
}

export interface SpeechToText {
  supported: boolean;
  state: MicState;
  interim: string;
  start: () => void;
  stop: () => void;
}

export function useSpeechToText(opts: UseSpeechToTextOptions): SpeechToText {
  const win = opts.win ?? defaultWin();
  const rawSupported = useMemo(() => getSpeechRecognitionCtor(win) !== null, [win]);
  // Browser-only capability: report it as unsupported until after mount so the
  // first client render matches the SSR HTML (window is undefined on the server).
  // Reading it during render/hydration would mismatch and force React to throw
  // away and rebuild the tree — which tears down the live voice effects.
  const mounted = useMounted();
  const supported = mounted && rawSupported;

  const [state, setState] = useState<MicState>(rawSupported ? "idle" : "unsupported");
  const [interim, setInterim] = useState("");
  const recRef = useRef<SpeechRecognitionLike | null>(null);

  // Keep the latest callbacks without re-subscribing the recognizer.
  const cbRef = useRef(opts);
  useEffect(() => {
    cbRef.current = opts;
  });

  const dispatch = useCallback((event: Parameters<typeof nextMicState>[1]) => {
    setState((s) => nextMicState(s, event));
  }, []);

  const stop = useCallback(() => {
    dispatch("stop");
    try {
      recRef.current?.stop();
    } catch {
      // Already stopped / never started — harmless.
    }
  }, [dispatch]);

  const start = useCallback(() => {
    const Ctor = getSpeechRecognitionCtor(win);
    if (!Ctor) {
      setState("unsupported");
      return;
    }
    // If an instance is already live, do nothing (idempotent start).
    if (recRef.current) return;

    const rec = new Ctor();
    rec.lang = opts.lang ?? "en-US";
    rec.continuous = true;
    rec.interimResults = true;

    let sawSpeech = false;
    rec.onstart = () => dispatch("started");
    rec.onresult = (e) => {
      const { finalText, interimText } = readRecognitionEvent(e);
      const heard = [finalText, interimText].filter(Boolean).join(" ");
      if (heard && cbRef.current.shouldIgnoreResult?.(heard)) {
        setInterim("");
        cbRef.current.onInterim?.("");
        return;
      }
      if (!sawSpeech) {
        sawSpeech = true;
        cbRef.current.onSpeechStart?.();
      }
      if (heard) cbRef.current.onResult?.(heard, Boolean(finalText));
      setInterim(interimText);
      cbRef.current.onInterim?.(interimText);
      if (finalText) {
        dispatch("final");
        setInterim("");
        cbRef.current.onFinalTranscript(finalText);
      }
    };
    rec.onerror = () => dispatch("errored");
    rec.onend = () => {
      recRef.current = null;
      setInterim("");
      dispatch("ended");
    };

    recRef.current = rec;
    dispatch("start");
    try {
      rec.start();
    } catch {
      recRef.current = null;
      dispatch("errored");
    }
  }, [win, opts.lang, dispatch]);

  // Tear down on unmount so a dangling recognizer never keeps the mic hot.
  useEffect(() => {
    return () => {
      try {
        recRef.current?.abort();
      } catch {
        // ignore
      }
      recRef.current = null;
    };
  }, []);

  return { supported, state, interim, start, stop };
}

// ── text-to-speech (ElevenLabs demo voice, browser-TTS fallback) ───────────────

/** Which engine actually produced the last spoken turn. `null` until one plays. */
export type TtsEngine = "sia" | "fallback" | null;

export interface TextToSpeech {
  supported: boolean;
  muted: boolean;
  speaking: boolean;
  /** The engine that voiced the most recent turn (honest readiness signal). */
  engine: TtsEngine;
  toggleMute: () => void;
  /** Speak a line NOW (supersedes anything playing). `onStart` fires the moment
   *  audio actually begins. Use for one-off/replay; interviewer turns should
   *  use {@link enqueue} so lines never cut each other off. */
  speak: (text: string, onStart?: () => void) => void;
  /** Queue a line to play AFTER the current one finishes — serial, race-free.
   *  This is how interviewer turns are voiced: a reply chased by a nudge both
   *  play, in order, instead of the second cancelling the first mid-synthesis.
   *  `onStart` fires when THIS line's audio begins. */
  enqueue: (text: string, onStart?: () => void) => void;
  replay: () => void;
  cancel: () => void;
  /** Pre-fetch and cache the audio for short lines (latency-masking fillers)
   * so a later speak() of the same text plays instantly with zero round-trip.
   * Fire-and-forget; failures (no key/offline) just leave the cache empty. */
  prime: (texts: readonly string[]) => void;
}

interface MinimalResponse {
  ok: boolean;
  status: number;
  blob: () => Promise<Blob>;
}

export interface UseTextToSpeechOptions {
  win?: VoiceWindow;
  /** POST {text} → audio/mpeg. Defaults to global fetch. Injectable for tests. */
  fetchImpl?: (input: string, init?: RequestInit) => Promise<MinimalResponse>;
  /** Build a controllable audio element from an object URL. */
  audioFactory?: (url: string) => PlayableAudio;
  createObjectURL?: (blob: Blob) => string;
  revokeObjectURL?: (url: string) => void;
  /** Lab TTS endpoint. */
  endpoint?: string;
}

const DEFAULT_TTS_ENDPOINT = "/api/lab/voice";

/**
 * Speaks interviewer turns with the active ElevenLabs voice ("Sia"): it POSTs the
 * text to `/api/lab/voice` (server-side key) and plays the
 * returned mp3. If that endpoint is unavailable (no key / offline), it falls back
 * to browser speechSynthesis so the lab still talks. A monotonic generation
 * counter makes every synth abortable mid-flight — the foundation barge-in uses.
 */
export function useTextToSpeech(opts: UseTextToSpeechOptions = {}): TextToSpeech {
  const win = opts.win ?? defaultWin();
  const endpoint = opts.endpoint ?? DEFAULT_TTS_ENDPOINT;
  // In a browser we can always attempt the endpoint; speechSynthesis is a bonus.
  // Gated behind mount so SSR (no window) and the first client render agree —
  // otherwise hydration mismatches and React rebuilds the tree mid-session.
  const mounted = useMounted();
  const supported = mounted && !!win;

  const [muted, setMuted] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [engine, setEngine] = useState<TtsEngine>(null);
  const lastTextRef = useRef<string>("");
  const audioRef = useRef<PlayableAudio | null>(null);
  const genRef = useRef(0);
  // Primed audio blobs keyed by sanitized text — instant playback for fillers.
  const primedRef = useRef<Map<string, Blob>>(new Map());

  const optsRef = useRef(opts);
  useEffect(() => {
    optsRef.current = opts;
  });

  const stopBrowserTts = useCallback(() => {
    if (win?.speechSynthesis) {
      try {
        win.speechSynthesis.cancel();
      } catch {
        // ignore
      }
    }
  }, [win]);

  // Serial speak queue (imperative + ref-based, so it never races React state):
  // interviewer lines play strictly one-at-a-time, in order. speakingRef is the
  // SYNCHRONOUS "a line is playing" flag (state lags a render behind and caused
  // the second of two close lines to cancel the first mid-synthesis).
  const speakingRef = useRef(false);
  const queueRef = useRef<{ text: string; onStart?: () => void }[]>([]);

  const cancel = useCallback(() => {
    // Barge-in / mute: drop the whole queue and invalidate any in-flight
    // synth/playback, then stop audio + TTS.
    queueRef.current = [];
    speakingRef.current = false;
    genRef.current += 1;
    const a = audioRef.current;
    if (a) {
      try {
        a.pause();
      } catch {
        // ignore
      }
      audioRef.current = null;
    }
    stopBrowserTts();
    setSpeaking(false);
  }, [stopBrowserTts]);

  const fallbackSpeak = useCallback(
    (text: string, gen: number, onStart?: () => void, onEnd?: () => void) => {
      const synth = win?.speechSynthesis;
      const Utterance = win?.SpeechSynthesisUtterance;
      if (!synth || !Utterance) {
        if (gen === genRef.current) setSpeaking(false);
        onEnd?.();
        return;
      }
      try {
        synth.cancel();
        if (gen === genRef.current) setEngine("fallback");
        const u = new Utterance(text);
        u.rate = FALLBACK_VOICE_SETTINGS.rate;
        u.pitch = FALLBACK_VOICE_SETTINGS.pitch;
        u.volume = FALLBACK_VOICE_SETTINGS.volume;
        const preferred = pickPreferredVoice(synth.getVoices());
        if (preferred) u.voice = preferred;
        u.onstart = () => {
          if (gen === genRef.current) onStart?.();
        };
        u.onend = () => {
          if (gen === genRef.current) setSpeaking(false);
          onEnd?.();
        };
        u.onerror = () => {
          if (gen === genRef.current) setSpeaking(false);
          onEnd?.();
        };
        synth.speak(u);
      } catch {
        if (gen === genRef.current) setSpeaking(false);
        onEnd?.();
      }
    },
    [win],
  );

  const speakText = useCallback(
    async (text: string, onStart?: () => void, onEnd?: () => void) => {
      // onEnd fires EXACTLY once when this line finishes, fails, or is
      // superseded — it's what advances the serial queue (and resets
      // speakingRef), so it must fire on every exit path.
      let ended = false;
      const finish = () => {
        if (ended) return;
        ended = true;
        onEnd?.();
      };
      // Sanitize code-ish tokens so the voice pronounces words naturally.
      const clean = sanitizeForSpeech(text);
      if (!clean || !win) {
        finish();
        return;
      }

      // New generation: supersede anything currently playing/synthesizing.
      const gen = (genRef.current += 1);
      const prev = audioRef.current;
      if (prev) {
        try {
          prev.pause();
        } catch {
          // ignore
        }
        audioRef.current = null;
      }
      stopBrowserTts();
      setSpeaking(true);

      const o = optsRef.current;
      const fetchImpl =
        o.fetchImpl ??
        (globalThis.fetch?.bind(globalThis) as unknown as UseTextToSpeechOptions["fetchImpl"]);
      const makeUrl =
        o.createObjectURL ??
        (typeof URL !== "undefined" && URL.createObjectURL
          ? (b: Blob) => URL.createObjectURL(b)
          : undefined);
      const revoke =
        o.revokeObjectURL ??
        (typeof URL !== "undefined" && URL.revokeObjectURL ? (u: string) => URL.revokeObjectURL(u) : undefined);
      const audioFactory =
        o.audioFactory ?? ((url: string) => new Audio(url) as unknown as PlayableAudio);

      // Primed cache first: instant playback, no round-trip (fillers).
      const primed = primedRef.current.get(clean);
      if (primed && makeUrl) {
        const url = makeUrl(primed);
        const audio = audioFactory(url);
        audio.muted = false;
        audio.volume = INTERVIEWER_AUDIO_VOLUME;
        audioRef.current = audio;
        const done = () => {
          if (gen === genRef.current) setSpeaking(false);
          revoke?.(url);
          finish();
        };
        audio.onended = done;
        audio.onerror = done;
        try {
          await audio.play();
          if (gen === genRef.current) {
            setEngine("sia");
            onStart?.();
          } else {
            done(); // superseded during the play() promise
          }
          return; // audio still playing — `done` fires when it ends
        } catch {
          if (gen !== genRef.current) {
            finish();
            return; // canceled mid-play
          }
          // fall through to the normal path
        }
      }

      // Primary path: ElevenLabs demo voice via the lab endpoint.
      if (fetchImpl && makeUrl) {
        try {
          const res = await fetchImpl(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: clean }),
          });
          if (gen !== genRef.current) {
            finish();
            return; // barged-in while awaiting
          }
          if (res.ok) {
            const blob = await res.blob();
            if (gen !== genRef.current) {
              finish();
              return;
            }
            const url = makeUrl(blob);
            const audio = audioFactory(url);
            audio.muted = false;
            audio.volume = INTERVIEWER_AUDIO_VOLUME; // softer interviewer playback
            audioRef.current = audio;
            const done = () => {
              if (gen === genRef.current) setSpeaking(false);
              revoke?.(url);
              finish();
            };
            audio.onended = done;
            audio.onerror = done;
            await audio.play();
            if (gen === genRef.current) {
              setEngine("sia"); // real ElevenLabs voice
              onStart?.();
            } else {
              done();
            }
            return; // `done` fires when the audio ends
          }
          // Non-OK (e.g. 503 no key) → fall through to browser TTS.
        } catch {
          if (gen !== genRef.current) {
            finish();
            return; // canceled, not a real failure
          }
        }
      }

      if (gen !== genRef.current) {
        finish();
        return;
      }
      fallbackSpeak(clean, gen, onStart, finish);
    },
    [win, endpoint, stopBrowserTts, fallbackSpeak],
  );

  // Serial pump: play the next queued line only when the current one has fully
  // finished (speakingRef, set/cleared synchronously — no React-state race).
  const pump = useCallback(() => {
    if (speakingRef.current) return;
    const next = queueRef.current.shift();
    if (!next) return;
    speakingRef.current = true;
    void speakText(next.text, next.onStart, () => {
      speakingRef.current = false;
      pump();
    });
  }, [speakText]);

  // Speak NOW, superseding the queue+current line (one-off / replay / filler).
  const speak = useCallback(
    (text: string, onStart?: () => void) => {
      const clean = text.trim();
      if (!clean) return;
      lastTextRef.current = clean;
      if (muted) return;
      void speakText(clean, onStart);
    },
    [muted, speakText],
  );

  // Queue a line to play after the current one — the interviewer-turn path.
  const enqueue = useCallback(
    (text: string, onStart?: () => void) => {
      const clean = text.trim();
      if (!clean) return;
      lastTextRef.current = clean;
      if (muted) return;
      queueRef.current.push({ text: clean, onStart });
      pump();
    },
    [muted, pump],
  );

  const replay = useCallback(() => {
    if (lastTextRef.current) void speakText(lastTextRef.current);
  }, [speakText]);

  const toggleMute = useCallback(() => {
    setMuted((m) => {
      const next = !m;
      if (next) cancel(); // muting stops any in-flight speech immediately
      return next;
    });
  }, [cancel]);

  // Pre-fetch short lines into the blob cache (fire-and-forget). Failures are
  // silent: an unprimed text just takes the normal fetch path later.
  const prime = useCallback(
    (texts: readonly string[]) => {
      const o = optsRef.current;
      const fetchImpl =
        o.fetchImpl ??
        (globalThis.fetch?.bind(globalThis) as unknown as UseTextToSpeechOptions["fetchImpl"]);
      if (!fetchImpl) return;
      for (const text of texts) {
        const clean = sanitizeForSpeech(text);
        if (!clean || primedRef.current.has(clean)) continue;
        void (async () => {
          try {
            const res = await fetchImpl(endpoint, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ text: clean }),
            });
            if (res.ok) primedRef.current.set(clean, await res.blob());
          } catch {
            // offline / no key — cache stays empty, speak() falls back normally
          }
        })();
      }
    },
    [endpoint],
  );

  // Cancel speech on unmount.
  useEffect(() => () => cancel(), [cancel]);

  return { supported, muted, speaking, engine, toggleMute, speak, enqueue, replay, cancel, prime };
}
