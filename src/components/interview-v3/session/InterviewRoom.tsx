"use client";

import { useEffect, useReducer, useRef, useState, type CSSProperties, type ReactNode } from "react";
import Link from "next/link";
import { Check, Mic, Moon, Square, Sun } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  makeAdapter,
  PlaybackController,
  getHandoff,
  nextSignalForPhase,
  type InterviewAdapter,
  type AdapterMode,
  type CriterionScore,
  type ScorecardDraft,
  patchSession,
  selectTranscript,
  selectCode,
  selectRuns,
  selectCurrentPhase,
  type VNextEvent,
  type Phase,
  type Rubric,
  type PlaybackSpeed,
  type PlaybackStatus,
} from "@/lib/interview-v3";
import {
  appendFinal,
  isLikelySpeechEcho,
  nextBargeState,
  type BargeState,
} from "@/lib/interview-v3/voice";
import { decideBargeIn, decideTurnEnd } from "@/lib/interview-v3/turn-taking";
import {
  INCIDENT_SEED_CODE,
  INCIDENT_TASK_PROMPT,
  INCIDENT_TRACK,
} from "@/lib/interview-v3/incident";
import { LedgerPanel } from "./LedgerPanel";
import { PlaybackBar } from "./PlaybackBar";
import { CopyReviewLink } from "./CopyReviewLink";
import { useSpeechToText, useTextToSpeech } from "./useVoice";
import { API_URL } from "@/lib/api-url";

// Console button/input styles (theme-aware via CSS vars; the editor stays dark).
const primaryBtn =
  "inline-flex items-center gap-1.5 rounded-md bg-emerald-500 px-3.5 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-emerald-600 disabled:opacity-40 disabled:hover:bg-emerald-500";
const ghostBtn =
  "inline-flex items-center gap-1.5 rounded-md border border-[var(--rp-edge)] bg-[var(--rp-chip)] px-3 py-1.5 text-sm font-medium text-[var(--rp-ink)] transition-colors hover:border-[var(--rp-ink3)] disabled:opacity-40";
const darkInput =
  "w-full rounded-md border border-[var(--rp-edge)] bg-[var(--rp-chip)] px-3 py-2 text-sm text-[var(--rp-ink)] outline-none placeholder:text-[var(--rp-ink3)] focus:border-[var(--rp-ink3)]";

// Theme tokens — dark (default) and light. The code editor is always dark.
const RP_DARK: CSSProperties = {
  ["--rp-page" as string]: "#070908",
  ["--rp-panel" as string]: "#0d110f",
  ["--rp-panel2" as string]: "rgba(0,0,0,0.25)",
  ["--rp-edge" as string]: "rgba(255,255,255,0.12)",
  ["--rp-chip" as string]: "rgba(255,255,255,0.06)",
  ["--rp-ink" as string]: "rgba(255,255,255,0.88)",
  ["--rp-ink2" as string]: "rgba(255,255,255,0.6)",
  ["--rp-ink3" as string]: "rgba(255,255,255,0.42)",
  ["--rp-accent" as string]: "#6ee7b7",
  ["--rp-accent2" as string]: "#7dd3fc",
  ["--rp-code-bg" as string]: "#0b0e0d",
  ["--rp-code-fg" as string]: "#cdd6e0",
  ["--rp-code-kw" as string]: "#b3a0cf",
  ["--rp-code-str" as string]: "#a3be8c",
  ["--rp-code-num" as string]: "#d6a07a",
  ["--rp-code-builtin" as string]: "#84a9c0",
  ["--rp-code-comment" as string]: "#7d8694",
};
const RP_LIGHT: CSSProperties = {
  ["--rp-page" as string]: "#e9e6de",
  ["--rp-panel" as string]: "#ffffff",
  ["--rp-panel2" as string]: "#f4f2ec",
  ["--rp-edge" as string]: "rgba(20,22,20,0.12)",
  ["--rp-chip" as string]: "rgba(20,22,20,0.05)",
  ["--rp-ink" as string]: "#1b211d",
  ["--rp-ink2" as string]: "rgba(27,33,29,0.64)",
  ["--rp-ink3" as string]: "rgba(27,33,29,0.46)",
  ["--rp-accent" as string]: "#047857",
  ["--rp-accent2" as string]: "#0369a1",
  ["--rp-code-bg" as string]: "#fbfaf6",
  ["--rp-code-fg" as string]: "#2c313a",
  ["--rp-code-kw" as string]: "#8959a8",
  ["--rp-code-str" as string]: "#4a7c3a",
  ["--rp-code-num" as string]: "#aa5d00",
  ["--rp-code-builtin" as string]: "#3b6ea5",
  ["--rp-code-comment" as string]: "#9aa0a8",
};

export function InterviewRoom({ sessionId }: { sessionId: string }) {
  const adapterRef = useRef<InterviewAdapter | null>(null);
  const controllerRef = useRef<PlaybackController | null>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);

  const [mode, setMode] = useState<AdapterMode>("mock");
  const [missing, setMissing] = useState(false);
  const [ready, setReady] = useState(false);
  const [rubric, setRubric] = useState<Rubric | null>(null);

  // Scripted events revealed by the playback controller (deterministic prefix).
  const [revealed, setRevealed] = useState<readonly VNextEvent[]>([]);
  // Live events appended via interactive sends AFTER the scripted run.
  const [liveExtra, setLiveExtra] = useState<readonly VNextEvent[]>([]);
  // live-llm: a single live ledger fed by every server envelope as it arrives.
  const [llmLedger, setLlmLedger] = useState<readonly VNextEvent[]>([]);

  const [status, setStatus] = useState<PlaybackStatus>("idle");
  const [speed, setSpeed] = useState<PlaybackSpeed>("1x");
  const [total, setTotal] = useState(0);

  const [answer, setAnswer] = useState("");
  // Incident-demo track: set after mount (the handoff lives in client-only
  // storage, so reading it during render would break SSR hydration).
  const [isIncident, setIsIncident] = useState(false);
  // Which scenario family — drives arc labels/copy ("Patch" vs "Approach").
  const [trackKind, setTrackKind] = useState<"incident" | "problem" | null>(null);
  // Scenario task card text vended by the backend (single source of truth).
  const [scenarioTask, setScenarioTask] = useState<string | null>(null);
  // Truthful WS connection state (live modes): the transport reports
  // reconnects/failures so the header dot never shows a stale "Connected".
  const [connState, setConnState] = useState<string>("connected");
  const [connAttempts, setConnAttempts] = useState(0);
  const [code, setCode] = useState("");
  const [theme, setTheme] = useState<"dark" | "light">("dark");

  // live-llm: streamed scorecard state.
  const [scorecardCriteria, setScorecardCriteria] = useState<CriterionScore[]>([]);
  const [scorecardDraft, setScorecardDraft] = useState<ScorecardDraft | null>(null);
  const [scoring, setScoring] = useState(false);
  const [scoreError, setScoreError] = useState<string | null>(null);
  const [busyAdvance, setBusyAdvance] = useState(false);

  const isLlm = mode === "live-llm";

  // ── voice demo layer (lab-only) ────────────────────────────────────────────
  // Optional STT/TTS skin over the SAME text flow. Recognized speech fills the
  // answer box (candidate still Sends, unless auto-send is on); interviewer
  // turns are read aloud. No new ledger events — the server stays authoritative.
  // Incident demo is a hands-free voice conversation: one tap to join (browsers
  // require a gesture for mic + audio), then continuous listen → answer → reply.
  const [joined, setJoined] = useState(false);
  const [micPaused, setMicPaused] = useState(false);
  const answerRef = useRef("");
  const voiceAutoRef = useRef(false);
  const sendTimerRef = useRef<number | null>(null);
  const speakingMayaTextRef = useRef("");
  const tts = useTextToSpeech();
  const ttsRef = useRef(tts);
  // Keep "latest value" refs current without mutating during render.
  useEffect(() => {
    answerRef.current = answer;
    voiceAutoRef.current = isIncident && joined && !micPaused;
    ttsRef.current = tts;
  });

  // ── full barge-in coordinator ──────────────────────────────────────────────
  // The candidate may cut in while the interviewer is speaking OR while its next
  // turn is still being generated. When they do, we cancel the interviewer audio,
  // suppress auto-speaking the (possibly in-flight) interviewer turn, and hand the
  // floor over. The ledger is never mutated — a turn the server already emitted
  // simply isn't spoken.
  const [bargeState, dispatchBarge] = useReducer(nextBargeState, "idle" as BargeState);
  const suppressSpeakRef = useRef(false);

  // ── semantic barge-in gate state ───────────────────────────────────────────
  // While Maya is speaking, only a REAL interruption takes the floor: a cut-in
  // word ("wait", "stop"…), ≥3 non-backchannel words, or sustained speech.
  // Backchannels ("mm-hm", "yeah") never cut her off. One barge per speaking
  // episode; re-armed when a new turn starts speaking.
  const speechOnsetRef = useRef<number | null>(null);
  const bargedThisTurnRef = useRef(false);
  const lastSpeakingPingRef = useRef(0);

  const executeBargeIn = () => {
    if (bargedThisTurnRef.current) return;
    bargedThisTurnRef.current = true;
    dispatchBarge("speech_start");
    ttsRef.current.cancel(); // stop interviewer audio + clear the queue (client)
    revealAllHidden(); // any queued-but-unspoken lines: show their text now
    suppressSpeakRef.current = true; // an in-flight / just-arrived turn won't auto-speak
    // Tell the backend to cancel the in-flight interviewer generation so its
    // late LLM output never streams back as the active question.
    const led = adapterRef.current?.getLedger() ?? [];
    let activeTurnId: string | undefined;
    for (let i = led.length - 1; i >= 0; i--) {
      const e = led[i];
      if (e.type === "interviewer.turn.started") {
        activeTurnId = e.turnId;
        break;
      }
    }
    adapterRef.current?.bargeIn(activeTurnId);
    dispatchBarge("interrupt_done");
  };

  const stt = useSpeechToText({
    onFinalTranscript: (chunk) => {
      const merged = appendFinal(answerRef.current, chunk);
      setAnswer(merged);
      dispatchBarge("final");
      dispatchBarge("answer_ready");
      if (voiceAutoRef.current) {
        // Hands-free: semantic turn-end (Voice_Assist rule) instead of a fixed
        // timer — trailing continuation tokens ("so I think we could…") wait
        // long, complete-sounding endings confirm fast, neutral endings sit in
        // between. The delay restarts on any further speech.
        if (sendTimerRef.current) window.clearTimeout(sendTimerRef.current);
        const decision = decideTurnEnd(merged);
        if (decision.delayMs !== null) {
          sendTimerRef.current = window.setTimeout(() => {
            const text = answerRef.current.trim();
            if (text) handleSendAnswer(adapterRef.current?.getPhase() ?? "ready", text);
          }, decision.delayMs);
        }
      }
    },
    shouldIgnoreResult: (text) =>
      ttsRef.current.speaking && isLikelySpeechEcho(text, speakingMayaTextRef.current),
    // Every passing recognition result: manage the auto-send timer and run the
    // barge-in gate. (onSpeechStart is one-shot per recognizer instance, so the
    // per-result stream is what keeps barge-in working all session long.)
    onResult: (heard) => {
      // Candidate is (still) talking — never cut their answer off mid-speech.
      if (sendTimerRef.current) {
        window.clearTimeout(sendTimerRef.current);
        sendTimerRef.current = null;
      }
      // VAD-style voice-activity signal (browser STT interims are our VAD):
      // tell the server the candidate is actively speaking so its silence
      // nudges don't fire while they think out loud. Throttled to ~3s; the
      // server treats it as activity only (no transcript, no turn).
      const now = Date.now();
      if (now - lastSpeakingPingRef.current > 3000) {
        lastSpeakingPingRef.current = now;
        adapterRef.current?.notifySpeaking?.();
      }
      if (ttsRef.current.speaking) {
        if (speechOnsetRef.current === null) speechOnsetRef.current = Date.now();
        const sustainedMs = Date.now() - speechOnsetRef.current;
        if (decideBargeIn(heard, sustainedMs) === "interrupt") executeBargeIn();
      } else {
        // Floor is free: speaking is just answering.
        speechOnsetRef.current = null;
        dispatchBarge("speech_start");
      }
    },
  });

  // Mirror mic activity into the barge-in coordinator.
  useEffect(() => {
    if (stt.state === "listening") dispatchBarge("listening");
    else if (stt.state === "processing") dispatchBarge("stop");
  }, [stt.state]);

  // Hands-free: keep the mic live across pauses. Browser SpeechRecognition ends
  // after silence/timeouts, so we restart it while joined and not paused.
  useEffect(() => {
    if (!(isIncident && joined && !micPaused)) return;
    if (stt.state === "idle") {
      const t = window.setTimeout(() => stt.start(), 350);
      return () => window.clearTimeout(t);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isIncident, joined, micPaused, stt.state]);

  // Auto-start voice the moment we're allowed: if mic permission is ALREADY
  // granted (a prior session), join silently — no button. The very first time
  // (no grant / no Permissions API) we fall back to a tiny "Enable voice" tap.
  useEffect(() => {
    if (!isIncident || !ready || joined || !stt.supported) return;
    let cancelled = false;
    void (async () => {
      try {
        const perm = await navigator.permissions?.query({
          name: "microphone" as PermissionName,
        });
        if (!cancelled && perm?.state === "granted") {
          setJoined(true);
          stt.start();
          window.setTimeout(() => ttsRef.current.replay(), 150);
        }
      } catch {
        // Permissions API unavailable — leave the inline "Enable voice" prompt.
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isIncident, ready, joined, stt.supported]);

  // Interviewer audio finishing naturally returns the floor to idle, and every
  // speaking transition re-arms the barge-in gate for the next episode.
  const prevSpeakingRef = useRef(false);
  useEffect(() => {
    if (prevSpeakingRef.current && !tts.speaking) dispatchBarge("interviewer_done");
    prevSpeakingRef.current = tts.speaking;
    speechOnsetRef.current = null;
    bargedThisTurnRef.current = false;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tts.speaking]);

  // ── voicing interviewer turns ─────────────────────────────────────────────
  // Lines are handed to the TTS hook's SERIAL QUEUE (tts.enqueue): a reactive
  // reply chased by a silence nudge both play, in order — the second never
  // cancels the first mid-synthesis (that race dropped audio live).
  //
  // VOICE/TEXT SYNC: each line stays HIDDEN from the transcript until ITS audio
  // starts (enqueue's onStart), with a failsafe reveal so text is never lost if
  // playback fails or is muted mid-flight.
  const enqueuedSeqRef = useRef(-1);
  const [hiddenSeqs, setHiddenSeqs] = useState<number[]>([]);
  const revealTimersRef = useRef(new Map<number, number>());
  const revealSeq = (seq: number) => {
    const t = revealTimersRef.current.get(seq);
    if (t) window.clearTimeout(t);
    revealTimersRef.current.delete(seq);
    setHiddenSeqs((h) => h.filter((s) => s !== seq));
  };
  const revealAllHidden = () => {
    for (const t of revealTimersRef.current.values()) window.clearTimeout(t);
    revealTimersRef.current.clear();
    setHiddenSeqs([]);
  };
  const hideSeq = (seq: number) => {
    setHiddenSeqs((h) => (h.includes(seq) ? h : [...h, seq]));
    const t = window.setTimeout(() => revealSeq(seq), 6000);
    revealTimersRef.current.set(seq, t);
  };
  useEffect(() => {
    const src = isLlm ? llmLedger : [...revealed, ...liveExtra];
    // Turns the server cancelled (barge-in) must never auto-speak, even on the
    // rare race where the utterance landed just before the cancel was processed.
    const cancelled = new Set<string>();
    for (const e of src) if (e.type === "interviewer.cancelled") cancelled.add(e.turnId);

    for (const e of src) {
      if (e.type !== "interviewer.utterance" || e.seq <= enqueuedSeqRef.current) continue;
      enqueuedSeqRef.current = e.seq;
      if (suppressSpeakRef.current) {
        // Barged-in turn: keep it in the ledger, just don't speak it.
        suppressSpeakRef.current = false;
        continue;
      }
      if (e.turnId && cancelled.has(e.turnId)) continue;
      const seq = e.seq;
      const text = e.text;
      if (tts.supported && !tts.muted) {
        hideSeq(seq);
        ttsRef.current.enqueue(text, () => {
          speakingMayaTextRef.current = text; // echo filter tracks the LIVE line
          revealSeq(seq);
          dispatchBarge("interviewer_start");
        });
      } else {
        ttsRef.current.enqueue(text);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLlm, llmLedger, revealed, liveExtra]);

  // (Latency-masking fillers were removed: a separate speak() competed with the
  // serial queue and could cut a real line short. The turn.started event still
  // drives the "Maya is thinking" UI; dead air is covered by the queue starting
  // the real line as soon as its audio is ready.)

  // Keep the transcript pinned to the latest line.
  useEffect(() => {
    const el = transcriptRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [llmLedger, revealed, liveExtra]);

  // When a Maya patch is APPLIED (accepted), reflect the server's new buffer in
  // the editor — once per patch, so it never fights the candidate's own typing.
  const appliedPatchRef = useRef<string | null>(null);
  useEffect(() => {
    const src = isLlm ? llmLedger : [...revealed, ...liveExtra];
    for (let i = src.length - 1; i >= 0; i--) {
      const e = src[i];
      if (e.type === "code.patch.applied") {
        if (appliedPatchRef.current !== e.patchId) {
          appliedPatchRef.current = e.patchId;
          setCode(e.after);
        }
        return;
      }
    }
  }, [isLlm, llmLedger, revealed, liveExtra]);

  // Paint the page (html/body) to match the console so no light edge/scrollbar
  // gutter shows behind the immersive room. Restored on unmount.
  useEffect(() => {
    const pageBg = theme === "dark" ? "#070908" : "#e9e6de";
    const html = document.documentElement;
    const body = document.body;
    const prev = { html: html.style.background, body: body.style.background };
    html.style.background = pageBg;
    body.style.background = pageBg;
    return () => {
      html.style.background = prev.html;
      body.style.background = prev.body;
    };
  }, [theme]);

  useEffect(() => {
    // StrictMode-safe: no mount-guard ref. Each run is self-contained (its own
    // adapter + controller + `cancelled`), so the surviving second run rebuilds
    // the identical deterministic ledger. Timers live in the controller and are
    // cleared by dispose() in cleanup.
    let cancelled = false;
    let controller: PlaybackController | null = null;
    let unsubController: (() => void) | null = null;
    let unsubAdapter: (() => void) | null = null;
    let adapter: InterviewAdapter | null = null;

    void (async () => {
      const handoff = getHandoff(sessionId);
      if (!handoff) {
        if (!cancelled) setMissing(true);
        return;
      }

      const handoffMode = handoff.mode ?? "mock";
      if (!cancelled) setMode(handoffMode);
      if (!cancelled && handoff.track) {
        // Any scenario track gets the hands-free voice room; incident constants
        // are the instant fallback until the server vends the real content.
        setIsIncident(true);
        setTrackKind(handoff.track === INCIDENT_TRACK ? "incident" : "problem");
        if (handoff.track === INCIDENT_TRACK) {
          setCode(INCIDENT_SEED_CODE);
        }
      }

      adapter = makeAdapter({
        mode: handoffMode,
        sessionId,
        fakeLlm: handoff.fakeLlm,
        track: handoff.track,
        onConnectionState: (state, attempts) => {
          if (cancelled) return;
          setConnState(state);
          setConnAttempts(attempts);
        },
      });
      await adapter.generateRubric(handoff.intake);
      // StrictMode double-mount guard: if this run was already torn down while
      // generateRubric was in flight, never publish this (now-stopped) adapter
      // into adapterRef — otherwise it would clobber the surviving run's live
      // adapter and silently drop every outbound frame.
      if (cancelled) {
        void adapter.stop();
        return;
      }

      // The BACKEND owns scenario content (seed code + task prompt): fetch it
      // once the session exists server-side, so problem tracks (problem:*) need
      // zero frontend constants and the incident stops depending on its
      // duplicated copy. Fallbacks above keep the room usable if this fails.
      if (handoff.track && handoffMode !== "mock") {
        void (async () => {
          try {
            const res = await fetch(`${API_URL}/vnext/interview/sessions/${sessionId}`);
            if (!res.ok || cancelled) return;
            const body = (await res.json()) as {
              scenario?: { seedCode?: string; taskPrompt?: string; title?: string };
            };
            if (cancelled || !body.scenario) return;
            const seed = body.scenario.seedCode;
            if (seed) {
              // Never clobber typing: only preload while the box still holds
              // nothing or the incident fallback constant.
              setCode((prev) => (prev === "" || prev === INCIDENT_SEED_CODE ? seed : prev));
            }
            if (body.scenario.taskPrompt) setScenarioTask(body.scenario.taskPrompt);
          } catch {
            // Backend not reachable — incident fallback text stays.
          }
        })();
      }

      // ── live-llm: candidate-driven, no playback auto-run ──
      if (handoffMode === "live-llm") {
        adapterRef.current = adapter;
        const a = adapter;
        // Subscribe BEFORE start so the first interviewer turn + any backfill is
        // captured into the single live ledger.
        unsubAdapter = a.onEvent((e) => {
          if (cancelled) return;
          setLlmLedger((prev) => [...prev, e]);
        });
        await a.start(); // sends session.start; resolves after first interviewer turn
        if (cancelled) {
          void a.stop();
          return;
        }
        patchSession(sessionId, { ledger: [...a.getLedger()], mode: handoffMode });
        setRubric(handoff.rubric);
        setReady(true);
        return;
      }

      // ── mock / live-scripted: timed playback over the realized ledger ──
      await adapter.start(); // run the scripted session to completion (instant)
      if (cancelled) {
        void adapter.stop();
        return;
      }

      const fullLedger = [...adapter.getLedger()];
      const maxScriptedSeq = fullLedger.length > 0 ? fullLedger[fullLedger.length - 1].seq : 0;
      // Persist the realized ledger so a hard refresh / review link recovers it.
      patchSession(sessionId, { ledger: fullLedger, mode: handoffMode });

      controller = new PlaybackController({ ledger: fullLedger, fallbackPhase: "ready" });
      controllerRef.current = controller;
      adapterRef.current = adapter;

      const c = controller;
      unsubController = c.subscribe(() => {
        if (cancelled) return;
        setRevealed(c.getRevealed());
        setStatus(c.getStatus());
        setSpeed(c.getSpeed());
      });

      // Capture only interactive events appended AFTER the scripted run.
      unsubAdapter = adapter.onEvent((e) => {
        if (cancelled) return;
        if (e.seq > maxScriptedSeq) setLiveExtra((prev) => [...prev, e]);
      });

      setTotal(c.getTotal());
      setRubric(handoff.rubric);
      setRevealed(c.getRevealed());
      setStatus(c.getStatus());
      setSpeed(c.getSpeed());
      setReady(true);
    })();

    return () => {
      cancelled = true;
      unsubController?.();
      unsubAdapter?.();
      controller?.dispose();
      controllerRef.current = null;
      void adapter?.stop();
      adapterRef.current = null;
    };
  }, [sessionId]);

  // live-llm: keep the persisted ledger fresh so review/evidence resolves.
  useEffect(() => {
    if (isLlm && ready) patchSession(sessionId, { ledger: [...llmLedger] });
  }, [isLlm, ready, sessionId, llmLedger]);

  // One-tap join: a user gesture unlocks the mic + audio, then it's hands-free.
  function handleJoin() {
    setJoined(true);
    stt.start();
    // Audio is now unlocked — (re)speak the current question so it's heard.
    window.setTimeout(() => ttsRef.current.replay(), 150);
  }

  function handleSendAnswer(currentPhase: Phase, explicit?: string) {
    const text = (explicit ?? answer).trim();
    if (!text) return;
    // Just send what the candidate said. The BACKEND Conversation Manager now
    // decides Maya's reaction and whether the topic is complete — it no longer
    // advances a phase on every utterance (that made the interview feel like a
    // scripted quiz that ignored the candidate). In scripted/mock mode the
    // legacy per-answer advance still applies below.
    adapterRef.current?.sendCandidateText(text);
    setAnswer("");
    dispatchBarge("sent"); // floor returns to the interviewer for the next turn
    if (isLlm) {
      // Backend-driven conversation: no client advance. Show a brief "thinking".
      setBusyAdvance(true);
      window.setTimeout(() => setBusyAdvance(false), 600);
      return;
    }
    // Scripted/mock: keep the deterministic one-answer-one-turn playback.
    const signal = nextSignalForPhase(currentPhase);
    if (!signal) return;
    setBusyAdvance(true);
    adapterRef.current?.requestAdvance(signal);
    window.setTimeout(() => setBusyAdvance(false), 600);
  }

  function handleSendCode() {
    adapterRef.current?.sendCode(code);
  }

  function handleAcceptPatch(patchId: string) {
    adapterRef.current?.acceptPatch(patchId);
  }

  function handleRejectPatch(patchId: string) {
    adapterRef.current?.rejectPatch(patchId);
  }

  async function handleRunCode() {
    await adapterRef.current?.runCode(code);
  }

  function handleEnd() {
    // Wrap the session: ask the Controller to advance scoring → review. It owns
    // the transition and rejects (no-op) if the phase isn't eligible.
    adapterRef.current?.requestAdvance("scoring.done");
  }

  // live-llm: request the next interviewer turn for the current phase. The
  // server PhaseController validates; the LLM never sets the phase.
  function handleContinue(currentPhase: Phase) {
    const signal = nextSignalForPhase(currentPhase);
    if (!signal) return;
    setBusyAdvance(true);
    adapterRef.current?.requestAdvance(signal);
    // The server echo arrives async via onEvent; clear the busy flag shortly.
    window.setTimeout(() => setBusyAdvance(false), 600);
  }

  // live-llm: stream the staged scorecard from the backend.
  async function handleFinish() {
    const adapter = adapterRef.current;
    if (!adapter) return;
    setScoring(true);
    setScoreError(null);
    setScorecardCriteria([]);
    setScorecardDraft(null);
    try {
      for await (const update of adapter.generateScorecard()) {
        if (update.kind === "criterion") {
          setScorecardCriteria((prev) => [...prev, update.score]);
        } else if (update.kind === "failed") {
          setScoreError(
            update.reason === "timeout"
              ? "Scoring timed out. Please retry."
              : `Scoring failed (${update.reason}). Please retry.`,
          );
        } else {
          setScorecardDraft(update.draft);
          patchSession(sessionId, {
            ledger: [...adapter.getLedger()],
            scorecard: update.draft,
          });
        }
      }
    } catch {
      setScoreError("Scoring failed unexpectedly. Please retry.");
    } finally {
      setScoring(false);
    }
  }

  if (missing) {
    return (
      <main style={RP_DARK} className="flex min-h-screen items-center justify-center bg-[var(--rp-page)] p-6 text-[var(--rp-ink)]">
        <div className="max-w-md space-y-4 rounded-2xl border border-[var(--rp-edge)] bg-[var(--rp-panel)] p-6">
          <h1 className="text-xl font-semibold">Session not found</h1>
          <p className="text-sm text-[var(--rp-ink2)]">
            No interview handoff for <code className="font-mono">{sessionId}</code>. Start from intake.
          </p>
          <Link href="/lab/interview-v3/intake" className={ghostBtn}>
            ← Back to intake
          </Link>
        </div>
      </main>
    );
  }

  const ledger = isLlm ? [...llmLedger] : [...revealed, ...liveExtra];
  // Voice/text sync: unspoken interviewer lines stay out of the visible
  // transcript until their audio starts (or the failsafe reveals them).
  const transcript = selectTranscript(ledger).filter((t) => !hiddenSeqs.includes(t.seq));
  const codeState = selectCode(ledger);
  const runs = selectRuns(ledger);
  const phase: Phase = selectCurrentPhase(ledger, "ready");
  const playbackComplete = status === "done";
  const lastSeq = ledger.length > 0 ? ledger[ledger.length - 1].seq : 0;
  const continueSignal = nextSignalForPhase(phase);
  const currentQuestion = (() => {
    for (let i = transcript.length - 1; i >= 0; i--) {
      if (transcript[i].speaker === "interviewer") return transcript[i].text;
    }
    return "";
  })();
  const hasInterviewerTurn = transcript.some((t) => t.speaker === "interviewer");
  const micListening = stt.state === "listening" || stt.state === "processing";
  const showScorecard =
    isLlm && (scorecardCriteria.length > 0 || !!scorecardDraft || scoring || !!scoreError);

  const voiceOk = tts.engine === "sia" || (tts.engine === null && tts.supported);
  const voiceLabel = tts.engine === "fallback" ? "Backup voice" : tts.supported ? "Voice" : "No voice";
  const arcIdx = arcIndexForPhase(phase);
  const metricUnderstanding = arcIdx >= 1 ? "Engaged" : hasInterviewerTurn ? "Listening" : "Waiting";
  const metricAdaptation = codeState.seq ? "Iterating in code" : "Not started";
  const metricAction = !ready
    ? "Connecting"
    : tts.speaking
      ? "Asking a question"
      : busyAdvance
        ? "Thinking…"
        : micListening
          ? "Listening to you"
          : "Awaiting your move";
  const reviewHref = `/lab/interview-v3/session/${sessionId}/review`;
  const taskText =
    currentQuestion ||
    scenarioTask ||
    (isIncident ? INCIDENT_TASK_PROMPT : "Work through the problem in the editor.");

  // Live AI code actions (Maya): current selection/highlight + the open patch
  // proposal, all derived from the authoritative ledger.
  let codeSelection: { start: number; end: number; owner: "interviewer" | "candidate" } | null = null;
  let codeHighlight: number | null = null;
  const resolvedPatchIds = new Set<string>();
  for (const e of ledger) {
    if (e.type === "selection.set") codeSelection = e.selection as typeof codeSelection;
    else if (e.type === "highlight.set") codeHighlight = e.line;
    else if (e.type === "code.patch.applied" || e.type === "code.patch.rejected")
      resolvedPatchIds.add(e.patchId);
  }
  let openPatch: { patchId: string; summary: string; before: string; after: string } | null = null;
  for (let i = ledger.length - 1; i >= 0; i--) {
    const e = ledger[i];
    if (e.type === "code.patch.proposed" && !resolvedPatchIds.has(e.patchId)) {
      openPatch = { patchId: e.patchId, summary: e.summary, before: e.before, after: e.after };
      break;
    }
  }

  return (
    <main
      style={theme === "dark" ? RP_DARK : RP_LIGHT}
      className="flex min-h-screen flex-col bg-[var(--rp-page)] px-[10vw] py-[10vh] lg:h-screen lg:overflow-hidden"
    >
      <section className="grid min-h-0 flex-1 overflow-hidden rounded-2xl border border-[var(--rp-edge)] bg-[var(--rp-panel)] text-[var(--rp-ink)] shadow-2xl shadow-black/40 lg:grid-cols-[minmax(230px,25%)_minmax(0,1fr)]">
        {/* ── LEFT RAIL: interview context ── */}
        <aside className="hidden min-h-0 flex-col border-[var(--rp-edge)] bg-[var(--rp-panel2)] lg:flex lg:border-r">
          <div className="border-b border-[var(--rp-edge)] p-4">
            <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--rp-ink3)]">
              Interview room
            </p>
            <div className="mt-3 space-y-2">
              <ParticipantPill
                name="Maya, interviewer"
                status={tts.speaking ? "speaking" : ready ? "joined" : "connecting"}
                live={tts.speaking}
                tone="emerald"
              />
              <ParticipantPill
                name="You"
                status={micListening ? "listening" : "joined"}
                live={micListening}
                tone="sky"
              />
            </div>
          </div>

          <div className="border-b border-[var(--rp-edge)] p-4">
            <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--rp-ink3)]">
              Interview arc
            </p>
            <div className="mt-3 space-y-2.5">
              {(trackKind === "problem" ? PROBLEM_ARC : INCIDENT_ARC).map((label, i) => (
                <ArcStep key={label} label={label} done={i < arcIdx} active={i === arcIdx} />
              ))}
            </div>
          </div>

          <div ref={transcriptRef} className="min-h-0 flex-1 overflow-y-auto p-4">
            <p className="sticky top-0 -mx-4 -mt-4 mb-2 bg-[var(--rp-panel)]/95 px-4 pt-4 font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--rp-ink3)] backdrop-blur">
              Live transcript
            </p>
            {transcript.length === 0 ? (
              <p className="text-sm italic text-[var(--rp-ink3)]">Waiting for the first question…</p>
            ) : (
              transcript.map((t) => (
                <div
                  key={`${t.seq}-${t.lineId}`}
                  data-testid="turn"
                  data-speaker={t.speaker}
                  className="mb-3 border-l border-[var(--rp-edge)] pl-3"
                >
                  <span
                    className={cn(
                      "font-mono text-[9px] uppercase tracking-wider",
                      t.speaker === "interviewer" ? "text-[var(--rp-accent)]" : "text-[var(--rp-accent2)]",
                    )}
                  >
                    {t.speaker === "interviewer" ? "Maya" : "You"}
                  </span>
                  <p className="mt-0.5 text-sm leading-5 text-[var(--rp-ink2)]">{t.text}</p>
                </div>
              ))
            )}
          </div>
        </aside>

        {/* ── MAIN: editor is the hero ── */}
        <div className="flex min-h-0 flex-col">
          {/* top bar: file tab + status + voice controls */}
          <div className="flex items-center justify-between gap-3 border-b border-[var(--rp-edge)] px-4 py-2.5">
            <div className="flex items-center gap-2">
              <span className="rounded-md bg-[var(--rp-chip)] px-2.5 py-1 font-mono text-xs text-[var(--rp-ink)]">
                solution.py
              </span>
              <span className="hidden font-mono text-[10px] uppercase tracking-wider text-[var(--rp-ink3)] sm:inline">
                Python
              </span>
            </div>
            <div className="flex items-center gap-3 text-[11px] text-[var(--rp-ink2)]">
              <DarkDot ok={voiceOk} label={voiceLabel} />
              <DarkDot ok={stt.supported} label={stt.supported ? "Mic" : "No mic"} />
              <DarkDot
                ok={ready && connState === "connected"}
                label={
                  !ready
                    ? "Connecting"
                    : connState === "connected"
                      ? "Connected"
                      : connState === "reconnecting"
                        ? `Reconnecting${connAttempts > 1 ? ` (${connAttempts})` : "…"}`
                        : connState === "failed"
                          ? "Offline"
                          : "Connecting"
                }
              />
              {tts.supported ? (
                <>
                  <button className="hover:text-[var(--rp-ink)]" onClick={tts.toggleMute}>
                    {tts.muted ? "Unmute" : "Mute"}
                  </button>
                  <button
                    className="hover:text-[var(--rp-ink)] disabled:opacity-40"
                    onClick={tts.replay}
                    disabled={tts.muted || !hasInterviewerTurn}
                  >
                    Replay
                  </button>
                </>
              ) : null}
              <button
                aria-label="Toggle light/dark theme"
                className="inline-flex items-center hover:text-[var(--rp-ink)]"
                onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
              >
                {theme === "dark" ? <Sun className="size-3.5" /> : <Moon className="size-3.5" />}
              </button>
            </div>
          </div>

          {/* current task / question */}
          <div className="border-b border-[var(--rp-edge)] bg-emerald-300/[0.04] px-4 py-3">
            <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-[var(--rp-accent)]">
              {trackKind === "problem" ? "Problem task" : isIncident ? "Incident task" : "Task"} ·{" "}
              {humanPhase(phase, trackKind !== "problem")}
            </p>
            <p className="mt-1 text-sm leading-6 text-[var(--rp-ink)]">{taskText}</p>
          </div>

          {/* EDITOR (hero) or SCORECARD */}
          {showScorecard ? (
            <div className="flex min-h-0 flex-1 flex-col bg-[var(--rp-panel)]">
              <div className="flex items-baseline justify-between border-b border-[var(--rp-edge)] px-4 py-2.5">
                <span className="font-mono text-xs text-[var(--rp-ink)]">
                  Scorecard{scorecardDraft ? ` · overall ${scorecardDraft.overall ?? "—"}` : ""}
                </span>
                {!scorecardDraft && scoring ? (
                  <span className="text-[11px] text-[var(--rp-ink3)]">scoring…</span>
                ) : null}
              </div>
              <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-4">
                {scoreError ? (
                  <p className="rounded-md border border-rose-400/40 bg-rose-400/10 p-3 text-xs text-rose-300">
                    {scoreError}
                  </p>
                ) : null}
                {scorecardCriteria.map((c) => (
                  <div
                    key={c.criterionId}
                    className="flex items-center justify-between rounded-md border border-[var(--rp-edge)] bg-[var(--rp-chip)] px-3 py-2 text-sm"
                  >
                    <span className="font-medium text-[var(--rp-ink)]">{prettyCriterion(c.criterionId)}</span>
                    <span className="flex items-center gap-3 text-xs text-[var(--rp-ink2)]">
                      {c.evidence.length > 0 ? (
                        <span>
                          {c.evidence.length} linked moment{c.evidence.length === 1 ? "" : "s"}
                        </span>
                      ) : null}
                      <span className="rounded-full bg-[var(--rp-chip)] px-2 py-0.5 font-medium text-[var(--rp-ink)]">
                        {c.score} · {c.verdict.replace(/_/g, " ")}
                      </span>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <CodeEditor code={code} onChange={setCode} selection={codeSelection} highlight={codeHighlight} />
          )}

          {/* Maya's proposed patch — candidate accepts / rejects / edits. */}
          {!showScorecard && openPatch ? (
            <div className="border-t border-[var(--rp-edge)] bg-emerald-300/[0.05] p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-medium text-[var(--rp-accent)]">
                  Maya proposes a fix
                </p>
                <div className="flex items-center gap-2">
                  <button className={primaryBtn} onClick={() => handleAcceptPatch(openPatch!.patchId)}>
                    Accept patch
                  </button>
                  <button className={ghostBtn} onClick={() => handleRejectPatch(openPatch!.patchId)}>
                    Reject
                  </button>
                </div>
              </div>
              <p className="mt-1 text-xs text-[var(--rp-ink2)]">{openPatch.summary}</p>
              <pre className="mt-2 max-h-40 overflow-auto rounded-md bg-[var(--rp-code-bg)] p-3 text-[12px] leading-5 text-[var(--rp-code-fg)] [font-family:var(--font-code),ui-monospace,monospace]">
                <code>{highlightPython(openPatch.after)}</code>
              </pre>
              <p className="mt-1 text-[11px] text-[var(--rp-ink3)]">
                Or keep editing the code yourself — your call.
              </p>
            </div>
          ) : null}

          {/* editor actions (Send/Run sit with the editor) */}
          {!showScorecard ? (
            <div className="flex items-center gap-2 border-t border-[var(--rp-edge)] px-4 py-2.5">
              <button className={primaryBtn} onClick={handleSendCode} disabled={!ready}>
                Send code
              </button>
              <button className={ghostBtn} onClick={() => void handleRunCode()} disabled={!ready}>
                Run code
              </button>
              {runs.length > 0 ? (
                <span className="ml-auto font-mono text-[11px] text-[var(--rp-ink3)]">
                  last run · exit {runs[runs.length - 1].exitCode}
                </span>
              ) : codeState.seq ? (
                <span className="ml-auto text-[11px] text-[var(--rp-ink3)]">saved</span>
              ) : null}
            </div>
          ) : null}

          {/* bottom interaction strip */}
          <div className="space-y-2 border-t border-[var(--rp-edge)] bg-[var(--rp-panel2)] px-4 py-3">
            {isLlm ? (
              <>
                {/* live speech preview (incident voice-first) */}
                {isIncident && (micListening || answer) ? (
                  <p className="rounded-md border border-[var(--rp-edge)] bg-[var(--rp-chip)] px-3 py-2 text-sm text-[var(--rp-ink)]">
                    {answer}
                    {stt.interim ? (
                      <span className="text-[var(--rp-ink3)]">
                        {answer ? " " : ""}
                        {stt.interim}…
                      </span>
                    ) : null}
                    {!answer && !stt.interim ? (
                      <span className="text-[var(--rp-ink3)]">Your words appear here as you speak.</span>
                    ) : null}
                  </p>
                ) : null}

                <div className="flex flex-wrap items-center gap-2">
                  {isIncident ? (
                    !stt.supported ? (
                      <span className="text-xs text-[var(--rp-ink3)]">
                        Voice needs Chrome — use “Type instead”.
                      </span>
                    ) : !joined ? (
                      <button className={ghostBtn} onClick={handleJoin} disabled={!ready}>
                        <Mic className="size-3.5" /> Enable voice
                      </button>
                    ) : (
                      <span className="inline-flex items-center gap-3 text-sm text-[var(--rp-ink)]">
                        <span className="inline-flex items-center gap-2">
                          <span
                            className={cn(
                              "size-2 rounded-full",
                              micListening && !micPaused
                                ? "bg-emerald-300 motion-safe:animate-pulse"
                                : "bg-[var(--rp-edge)]",
                            )}
                          />
                          {micPaused ? "Mic paused" : "Listening — just talk"}
                        </span>
                        <button
                          className="text-xs text-[var(--rp-ink3)] hover:text-[var(--rp-ink)]"
                          onClick={() => {
                            if (micPaused) {
                              setMicPaused(false);
                              stt.start();
                            } else {
                              setMicPaused(true);
                              stt.stop();
                            }
                          }}
                        >
                          {micPaused ? "Resume mic" : "Pause mic"}
                        </button>
                      </span>
                    )
                  ) : (
                    <>
                      <input
                        className={`${darkInput} max-w-xs`}
                        placeholder="Type your answer…"
                        value={answer}
                        onChange={(e) => setAnswer(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            handleSendAnswer(phase);
                          }
                        }}
                      />
                      {stt.supported ? (
                        micListening ? (
                          <button className={ghostBtn} onClick={stt.stop}>
                            <Square className="size-3.5" /> Stop
                          </button>
                        ) : (
                          <button className={ghostBtn} onClick={stt.start} disabled={!ready}>
                            <Mic className="size-3.5" /> Mic
                          </button>
                        )
                      ) : null}
                      <button
                        className={primaryBtn}
                        onClick={() => handleSendAnswer(phase)}
                        disabled={!ready || busyAdvance || !answer.trim()}
                      >
                        {busyAdvance ? "Sending…" : "Send answer"}
                      </button>
                    </>
                  )}

                  <div className="ml-auto flex items-center gap-2">
                    <button
                      className={ghostBtn}
                      onClick={() => handleContinue(phase)}
                      disabled={!ready || !continueSignal || busyAdvance || scoring}
                    >
                      Next question
                    </button>
                    <button
                      className={ghostBtn}
                      onClick={() => void handleFinish()}
                      disabled={!ready || scoring}
                    >
                      {scoring ? "Scoring…" : scoreError ? "Retry scoring" : "Finish interview"}
                    </button>
                    <Link href={reviewHref} className="text-sm font-medium text-[var(--rp-accent)] hover:underline">
                      Open review →
                    </Link>
                  </div>
                </div>

                {/* subtle hints + voice-only fallback */}
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--rp-ink3)]">
                  {tts.speaking ? (
                    <span className="text-[var(--rp-accent)]">You can interrupt by speaking.</span>
                  ) : isIncident && stt.supported ? (
                    <span>Edit the code and talk through your fix.</span>
                  ) : null}
                  {bargeLabel(bargeState) ? (
                    <span
                      aria-live="polite"
                      className={cn(
                        bargeState === "interrupted" || bargeState === "listening"
                          ? "text-[var(--rp-accent)]"
                          : "",
                      )}
                    >
                      {bargeLabel(bargeState)}
                    </span>
                  ) : null}
                  {stt.state === "error" ? (
                    <span className="text-rose-300">Mic error — check permissions.</span>
                  ) : null}
                  {isIncident ? (
                    <details className="inline-block">
                      <summary className="cursor-pointer select-none hover:text-[var(--rp-ink)]">
                        Type instead
                      </summary>
                      <div className="mt-2 flex items-center gap-2">
                        <input
                          className={`${darkInput} max-w-xs`}
                          placeholder="Type your answer…"
                          value={answer}
                          onChange={(e) => setAnswer(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              e.preventDefault();
                              handleSendAnswer(phase);
                            }
                          }}
                        />
                        <button
                          className={ghostBtn}
                          onClick={() => handleSendAnswer(phase)}
                          disabled={!ready || busyAdvance || !answer.trim()}
                        >
                          Send answer
                        </button>
                      </div>
                    </details>
                  ) : null}
                </div>
              </>
            ) : (
              <div className="flex flex-wrap items-center gap-3">
                {ready ? (
                  <PlaybackBar
                    status={status}
                    speed={speed}
                    revealedCount={revealed.length}
                    total={total}
                    onStart={() => controllerRef.current?.start()}
                    onPause={() => controllerRef.current?.pause()}
                    onResume={() => controllerRef.current?.resume()}
                    onStep={() => controllerRef.current?.step()}
                    onSpeed={(s) => controllerRef.current?.setSpeed(s)}
                  />
                ) : (
                  <span className="text-xs text-[var(--rp-ink3)]">Preparing the session…</span>
                )}
                <div className="ml-auto flex items-center gap-2">
                  <button className={ghostBtn} onClick={handleEnd} disabled={!playbackComplete}>
                    Finish interview
                  </button>
                  <Link href={reviewHref} className="text-sm font-medium text-[var(--rp-accent)] hover:underline">
                    Open review →
                  </Link>
                </div>
              </div>
            )}
          </div>

          {/* bottom evaluation strip */}
          <div className="grid border-t border-[var(--rp-edge)] sm:grid-cols-3">
            <Metric label="Understanding" value={metricUnderstanding} />
            <Metric label="Adaptation" value={metricAdaptation} />
            <Metric label="Interviewer action" value={metricAction} wide />
          </div>

          {/* mobile transcript (the left rail is desktop-only) */}
          {transcript.length > 0 ? (
            <div className="border-t border-[var(--rp-edge)] p-4 lg:hidden">
              <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-[var(--rp-ink3)]">
                Transcript
              </p>
              <div className="mt-2 max-h-48 space-y-3 overflow-y-auto">
                {transcript.map((t) => (
                  <div key={`m-${t.seq}-${t.lineId}`} className="border-l border-[var(--rp-edge)] pl-3 text-sm">
                    <span
                      className={cn(
                        "font-mono text-[9px] uppercase tracking-wider",
                        t.speaker === "interviewer" ? "text-[var(--rp-accent)]" : "text-[var(--rp-accent2)]",
                      )}
                    >
                      {t.speaker === "interviewer" ? "Maya" : "You"}
                    </span>
                    <p className="mt-0.5 leading-5 text-[var(--rp-ink2)]">{t.text}</p>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </section>

      {/* Developer details — collapsed; the candidate never sees raw internals. */}
      <details className="mt-2 shrink-0 rounded-lg border border-[var(--rp-edge)] bg-[var(--rp-panel)] px-4 py-2 text-xs text-[var(--rp-ink3)]">
        <summary className="cursor-pointer select-none">Developer details</summary>
        <div className="mt-3 space-y-2">
          <div>
            session <span className="font-mono text-[var(--rp-ink)]">{sessionId}</span> · phase{" "}
            <span className="font-mono text-[var(--rp-ink)]">{phase}</span> · seq{" "}
            <span className="font-mono text-[var(--rp-ink)]">{lastSeq}</span>
          </div>
          {rubric ? (
            <div>
              rubric <span className="font-mono text-[var(--rp-ink)]">{rubric.id}</span> ·{" "}
              {rubric.criteria.length} criteria
            </div>
          ) : null}
          <CopyReviewLink sessionId={sessionId} />
          <LedgerPanel ledger={ledger} />
        </div>
      </details>
    </main>
  );
}

// ── interview arc (scenario-aware, human-facing) ───────────────────────────────
const INCIDENT_ARC = [
  "Incident",
  "Failure mode",
  "Patch",
  "Concurrency",
  "Test",
  "Operations",
  "Wrap-up",
];
// Generic coding-interview arc for problem tracks (Two Sum, Binary Search, …)
// — a Two Sum run must never show "Patch"/"Operations" labels.
const PROBLEM_ARC = [
  "Problem",
  "Approach",
  "Optimize",
  "Coding",
  "Testing",
  "Complexity",
  "Wrap-up",
];
const PHASE_SEQUENCE: Phase[] = [
  "intro",
  "resume_calibration",
  "problem_framing",
  "coding",
  "debugging",
  "optimization",
  "wrap_up",
];
function arcIndexForPhase(phase: Phase): number {
  if (phase === "scoring" || phase === "review") return INCIDENT_ARC.length;
  const i = PHASE_SEQUENCE.indexOf(phase);
  return i; // -1 before the first step (ready)
}

/** Friendly, candidate-facing phase label (no raw state-machine names). */
function humanPhase(phase: Phase, incident: boolean): string {
  const map: Partial<Record<Phase, string>> = incident
    ? {
        ready: "Starting",
        intro: "Opening",
        resume_calibration: "Failure mode",
        problem_framing: "Patch",
        coding: "Implementation",
        debugging: "Testing",
        optimization: "Operations",
        wrap_up: "Wrap-up",
        scoring: "Scoring",
        review: "Review",
      }
    : {
        ready: "Starting",
        intro: "Opening",
        resume_calibration: "Approach",
        problem_framing: "Optimize",
        coding: "Coding",
        debugging: "Testing",
        optimization: "Complexity",
        wrap_up: "Wrap-up",
        scoring: "Scoring",
        review: "Review",
      };
  return map[phase] ?? "In progress";
}

/** Tidy a rubric criterion id into a human label (idempotency_fix → Idempotency fix). */
function prettyCriterion(id: string): string {
  const s = id.replace(/[_-]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function DarkDot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={cn("size-1.5 rounded-full", ok ? "bg-emerald-300" : "bg-[var(--rp-edge)]")} />
      {label}
    </span>
  );
}

function ParticipantPill({
  name,
  status,
  live,
  tone,
}: {
  name: string;
  status: string;
  live: boolean;
  tone: "emerald" | "sky";
}) {
  const dot = tone === "emerald" ? "bg-emerald-300" : "bg-sky-300";
  return (
    <div className="flex items-center gap-2 rounded-lg border border-[var(--rp-edge)] bg-[var(--rp-chip)] px-3 py-2">
      <span className={cn("size-2 rounded-full", live ? `${dot} motion-safe:animate-pulse` : "bg-[var(--rp-edge)]")} />
      <span className="text-xs text-[var(--rp-ink)]">{name}</span>
      <span className="ml-auto font-mono text-[9px] uppercase tracking-wider text-[var(--rp-ink3)]">{status}</span>
    </div>
  );
}

function ArcStep({ label, done, active }: { label: string; done: boolean; active: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <span
        className={cn(
          "flex size-4 shrink-0 items-center justify-center rounded-full border text-[8px]",
          done && "border-emerald-300/40 bg-emerald-300/15 text-[var(--rp-accent)]",
          active && "border-sky-300/50 bg-sky-300/10 text-[var(--rp-accent2)]",
          !done && !active && "border-[var(--rp-edge)] text-[var(--rp-ink3)]",
        )}
      >
        {done ? <Check className="size-2.5" /> : ""}
      </span>
      <span className={cn("text-xs", active ? "text-white" : done ? "text-[var(--rp-ink2)]" : "text-[var(--rp-ink3)]")}>
        {label}
      </span>
    </div>
  );
}

function Metric({ label, value, wide = false }: { label: string; value: string; wide?: boolean }) {
  return (
    <div className={cn("min-w-0 border-[var(--rp-edge)] p-3 sm:border-r", wide && "sm:border-r-0")}>
      <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-[var(--rp-ink3)]">{label}</p>
      <p className="mt-1 truncate text-xs text-[var(--rp-ink)]" title={value}>
        {value}
      </p>
    </div>
  );
}

/** Friendly, candidate-facing barge-in status (null when idle). */
function bargeLabel(s: BargeState): string | null {
  switch (s) {
    case "interviewer_speaking":
      return "You can interrupt by speaking";
    case "candidate_interrupting":
      return "Interrupting…";
    case "interrupted":
      return "Interrupted. Go ahead.";
    case "listening":
      return "Listening…";
    case "processing":
      return "Got it…";
    case "ready_to_send":
      return "Ready to send";
    default:
      return null;
  }
}

/**
 * Editable code area with real syntax highlighting: a colored <pre> sits behind a
 * transparent <textarea> (caret visible), scroll-synced. Keeps editing native
 * while the candidate sees keywords/strings/comments/numbers in distinct colors.
 */
function CodeEditor({
  code,
  onChange,
  selection,
  highlight,
}: {
  code: string;
  onChange: (v: string) => void;
  selection?: { start: number; end: number; owner: "interviewer" | "candidate" } | null;
  highlight?: number | null;
}) {
  const preRef = useRef<HTMLPreElement>(null);
  const lines = code.split("\n");
  return (
    <div className="relative min-h-[180px] flex-1 overflow-hidden bg-[var(--rp-code-bg)]">
      <pre
        ref={preRef}
        aria-hidden
        className="pointer-events-none absolute inset-0 m-0 overflow-auto whitespace-pre p-4 text-[13px] leading-6 text-[var(--rp-code-fg)] [font-family:var(--font-code),ui-monospace,monospace]"
      >
        <code>
          {lines.map((ln, i) => {
            const selected =
              selection != null && i >= selection.start && i <= selection.end;
            const isHi = highlight === i;
            return (
              <div
                key={i}
                className={cn(
                  "min-h-[1.5rem]",
                  isHi && "bg-amber-300/20",
                  selected && !isHi &&
                    (selection?.owner === "interviewer" ? "bg-emerald-300/15" : "bg-sky-300/15"),
                )}
              >
                {highlightPython(ln)}
                {ln.length === 0 ? "​" : ""}
              </div>
            );
          })}
        </code>
      </pre>
      <textarea
        className="absolute inset-0 resize-none overflow-auto whitespace-pre bg-transparent p-4 text-[13px] leading-6 text-transparent caret-emerald-300 outline-none [font-family:var(--font-code),ui-monospace,monospace]"
        placeholder="# write your fix here"
        spellCheck={false}
        wrap="off"
        value={code}
        onChange={(e) => onChange(e.target.value)}
        onScroll={(e) => {
          const pre = preRef.current;
          if (pre) {
            pre.scrollTop = e.currentTarget.scrollTop;
            pre.scrollLeft = e.currentTarget.scrollLeft;
          }
        }}
      />
    </div>
  );
}

const PY_TOKEN =
  /(#[^\n]*)|("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|\b(def|class|return|if|elif|else|for|while|in|not|and|or|import|from|as|with|try|except|finally|raise|pass|break|continue|lambda|yield|global|nonlocal|assert|del|is|None|True|False)\b|\b(self|int|str|float|bool|list|dict|set|tuple|len|range|print)\b|\b(\d+(?:\.\d+)?)\b/g;

/** Lightweight Python colorizer → React nodes for the highlight overlay. */
function highlightPython(code: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  let i = 0;
  let m: RegExpExecArray | null;
  PY_TOKEN.lastIndex = 0;
  while ((m = PY_TOKEN.exec(code)) !== null) {
    if (m.index > last) nodes.push(code.slice(last, m.index));
    const [full, comment, str, kw, builtin, num] = m;
    const key = `t${i++}`;
    if (comment) nodes.push(<span key={key} className="italic text-[var(--rp-code-comment)]">{full}</span>);
    else if (str) nodes.push(<span key={key} className="text-[var(--rp-code-str)]">{full}</span>);
    else if (kw) nodes.push(<span key={key} className="text-[var(--rp-code-kw)]">{full}</span>);
    else if (builtin) nodes.push(<span key={key} className="text-[var(--rp-code-builtin)]">{full}</span>);
    else if (num) nodes.push(<span key={key} className="text-[var(--rp-code-num)]">{full}</span>);
    else nodes.push(full);
    last = PY_TOKEN.lastIndex;
  }
  if (last < code.length) nodes.push(code.slice(last));
  return nodes;
}
