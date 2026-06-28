// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useSpeechToText, useTextToSpeech } from "../useVoice";
import type {
  SpeechRecognitionEventLike,
  SpeechRecognitionLike,
  SpeechSynthesisUtteranceLike,
  VoiceWindow,
} from "@/lib/interview-v3/voice";

// ── fakes ─────────────────────────────────────────────────────────────────────

class FakeRecognition implements SpeechRecognitionLike {
  lang = "";
  continuous = false;
  interimResults = false;
  onstart: (() => void) | null = null;
  onend: (() => void) | null = null;
  onerror: ((e: { error?: string }) => void) | null = null;
  onresult: ((e: SpeechRecognitionEventLike) => void) | null = null;
  started = false;
  static last: FakeRecognition | null = null;
  constructor() {
    FakeRecognition.last = this;
  }
  start() {
    this.started = true;
    this.onstart?.();
  }
  stop() {
    this.onend?.();
  }
  abort() {}
  emitFinal(text: string) {
    this.onresult?.({
      resultIndex: 0,
      results: {
        0: { 0: { transcript: text }, isFinal: true, length: 1 },
        length: 1,
      } as unknown as SpeechRecognitionEventLike["results"],
    });
  }
}

class FakeUtterance implements SpeechSynthesisUtteranceLike {
  voice = null as SpeechSynthesisUtteranceLike["voice"];
  rate = 0;
  pitch = 0;
  volume = 0;
  onstart: (() => void) | null = null;
  onend: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public text: string) {}
}

function fakeWin(): { win: VoiceWindow; spoken: FakeUtterance[]; cancels: { n: number } } {
  const spoken: FakeUtterance[] = [];
  const cancels = { n: 0 };
  const win: VoiceWindow = {
    SpeechRecognition: FakeRecognition,
    speechSynthesis: {
      speak: (u) => spoken.push(u as FakeUtterance),
      cancel: () => {
        cancels.n += 1;
      },
      getVoices: () => [
        { name: "Microsoft David", lang: "en-US" },
        { name: "Samantha", lang: "en-US" },
      ],
    },
    SpeechSynthesisUtterance: FakeUtterance,
  };
  return { win, spoken, cancels };
}

// ── STT ────────────────────────────────────────────────────────────────────────

describe("useSpeechToText", () => {
  it("reports supported and pushes a final transcript", () => {
    const onFinal = vi.fn();
    const onSpeechStart = vi.fn();
    const { win } = fakeWin();
    const { result } = renderHook(() =>
      useSpeechToText({ onFinalTranscript: onFinal, onSpeechStart, win }),
    );
    expect(result.current.supported).toBe(true);

    act(() => result.current.start());
    expect(result.current.state).toBe("listening");

    act(() => FakeRecognition.last!.emitFinal("hello there"));
    expect(onSpeechStart).toHaveBeenCalledTimes(1); // barge-in trigger
    expect(onFinal).toHaveBeenCalledWith("hello there");
  });

  it("can ignore echo-like recognition before it triggers barge-in", () => {
    const onFinal = vi.fn();
    const onSpeechStart = vi.fn();
    const shouldIgnoreResult = vi.fn((text: string) => text.includes("production issue"));
    const { win } = fakeWin();
    const { result } = renderHook(() =>
      useSpeechToText({ onFinalTranscript: onFinal, onSpeechStart, shouldIgnoreResult, win }),
    );

    act(() => result.current.start());
    act(() => FakeRecognition.last!.emitFinal("we have encountered a production issue"));

    expect(shouldIgnoreResult).toHaveBeenCalledWith("we have encountered a production issue");
    expect(onSpeechStart).not.toHaveBeenCalled();
    expect(onFinal).not.toHaveBeenCalled();

    act(() => FakeRecognition.last!.emitFinal("hold on let me answer"));
    expect(onSpeechStart).toHaveBeenCalledTimes(1);
    expect(onFinal).toHaveBeenCalledWith("hold on let me answer");
  });

  it("is unsupported with no recognizer", () => {
    const { result } = renderHook(() =>
      useSpeechToText({ onFinalTranscript: vi.fn(), win: {} as VoiceWindow }),
    );
    expect(result.current.supported).toBe(false);
    expect(result.current.state).toBe("unsupported");
  });
});

// ── TTS ──────────────────────────────────────────────────────────────────────

interface FakeAudio {
  muted: boolean;
  onended: (() => void) | null;
  onerror: (() => void) | null;
  played: boolean;
  paused: boolean;
  play: () => Promise<void>;
  pause: () => void;
}

function ttsHarness(over: { fetchOk?: boolean } = {}) {
  const { win, spoken } = fakeWin();
  const audios: FakeAudio[] = [];
  const fetchCalls: Array<{ url: string; body: unknown }> = [];
  const fetchImpl = async (url: string, init?: RequestInit) => {
    fetchCalls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
    return {
      ok: over.fetchOk !== false,
      status: over.fetchOk === false ? 503 : 200,
      blob: async () => new Blob(["audio"], { type: "audio/mpeg" }),
    };
  };
  const audioFactory = (): FakeAudio => {
    const a: FakeAudio = {
      muted: false,
      onended: null,
      onerror: null,
      played: false,
      paused: false,
      play: async () => {
        a.played = true;
      },
      pause: () => {
        a.paused = true;
      },
    };
    audios.push(a);
    return a;
  };
  return {
    win,
    spoken,
    audios,
    fetchCalls,
    opts: {
      win,
      fetchImpl,
      audioFactory: audioFactory as unknown as (url: string) => FakeAudio,
      createObjectURL: () => "blob:fake",
      revokeObjectURL: () => {},
    },
  };
}

describe("useTextToSpeech", () => {
  it("speaks via the lab ElevenLabs endpoint (the EXACT demo voice path)", async () => {
    const h = ttsHarness();
    const { result } = renderHook(() => useTextToSpeech(h.opts));
    expect(result.current.supported).toBe(true);

    await act(async () => {
      result.current.speak("What's your approach?");
    });

    expect(h.fetchCalls).toHaveLength(1);
    expect(h.fetchCalls[0].url).toBe("/api/lab/voice");
    expect((h.fetchCalls[0].body as { text: string }).text).toBe("What's your approach?");
    expect(h.audios).toHaveLength(1);
    expect(h.audios[0].played).toBe(true); // demo-voice mp3 actually played
    expect(result.current.engine).toBe("sia"); // honest readiness signal
  });

  it("falls back to browser speechSynthesis when the endpoint is unavailable", async () => {
    const h = ttsHarness({ fetchOk: false });
    const { result } = renderHook(() => useTextToSpeech(h.opts));

    await act(async () => {
      result.current.speak("fallback please");
    });

    expect(h.audios).toHaveLength(0); // no mp3 played
    expect(h.spoken).toHaveLength(1); // browser TTS used instead
    expect(h.spoken[0].text).toBe("fallback please");
    expect(result.current.engine).toBe("fallback"); // honest readiness signal
  });

  it("muting suppresses speech but replay works after unmute", async () => {
    const h = ttsHarness();
    const { result } = renderHook(() => useTextToSpeech(h.opts));

    act(() => result.current.toggleMute()); // muted
    await act(async () => {
      result.current.speak("muted question");
    });
    expect(h.fetchCalls).toHaveLength(0); // nothing synthesized while muted

    act(() => result.current.toggleMute()); // unmuted
    await act(async () => {
      result.current.replay(); // replays last remembered text
    });
    expect(h.fetchCalls).toHaveLength(1);
    expect((h.fetchCalls[0].body as { text: string }).text).toBe("muted question");
  });
});
