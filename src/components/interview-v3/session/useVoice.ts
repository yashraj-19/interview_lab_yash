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
  speak: (text: string) => void;
  replay: () => void;
  cancel: () => void;
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

  const cancel = useCallback(() => {
    // Invalidate any in-flight synth/playback (barge-in), then stop audio + TTS.
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
    (text: string, gen: number) => {
      const synth = win?.speechSynthesis;
      const Utterance = win?.SpeechSynthesisUtterance;
      if (!synth || !Utterance) {
        if (gen === genRef.current) setSpeaking(false);
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
        u.onend = () => {
          if (gen === genRef.current) setSpeaking(false);
        };
        u.onerror = () => {
          if (gen === genRef.current) setSpeaking(false);
        };
        synth.speak(u);
      } catch {
        if (gen === genRef.current) setSpeaking(false);
      }
    },
    [win],
  );

  const speakText = useCallback(
    async (text: string) => {
      // Sanitize code-ish tokens so the voice pronounces words naturally.
      const clean = sanitizeForSpeech(text);
      if (!clean || !win) return;

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

      // Primary path: ElevenLabs demo voice via the lab endpoint.
      if (fetchImpl && makeUrl) {
        try {
          const res = await fetchImpl(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: clean }),
          });
          if (gen !== genRef.current) return; // barged-in while awaiting
          if (res.ok) {
            const blob = await res.blob();
            if (gen !== genRef.current) return;
            const url = makeUrl(blob);
            const audio = audioFactory(url);
            audio.muted = false;
            audio.volume = INTERVIEWER_AUDIO_VOLUME; // softer interviewer playback
            audioRef.current = audio;
            const finish = () => {
              if (gen === genRef.current) setSpeaking(false);
              revoke?.(url);
            };
            audio.onended = finish;
            audio.onerror = finish;
            await audio.play();
            if (gen === genRef.current) setEngine("sia"); // real ElevenLabs voice
            return;
          }
          // Non-OK (e.g. 503 no key) → fall through to browser TTS.
        } catch {
          if (gen !== genRef.current) return; // canceled, not a real failure
        }
      }

      if (gen !== genRef.current) return;
      fallbackSpeak(clean, gen);
    },
    [win, endpoint, stopBrowserTts, fallbackSpeak],
  );

  // Speak only if not muted; ALWAYS remember the last text so unmute + replay
  // works even for turns that arrived while muted.
  const speak = useCallback(
    (text: string) => {
      const clean = text.trim();
      if (!clean) return;
      lastTextRef.current = clean;
      if (muted) return;
      void speakText(clean);
    },
    [muted, speakText],
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

  // Cancel speech on unmount.
  useEffect(() => () => cancel(), [cancel]);

  return { supported, muted, speaking, engine, toggleMute, speak, replay, cancel };
}
