import { describe, expect, it } from "vitest";
import {
  appendFinal,
  bigramSimilarity,
  isFuzzyEcho,
  sanitizeForSpeech,
  isLikelySpeechEcho,
  buildInterviewerVoiceRequest,
  isCandidateFloor,
  nextBargeState,
  type BargeState,
  DEMO_INTERVIEWER_VOICE_ID,
  DEMO_VOICE_MODEL_ID,
  DEMO_VOICE_SETTINGS,
  getSpeechRecognitionCtor,
  isSpeechToTextSupported,
  isTextToSpeechSupported,
  nextMicState,
  pickPreferredVoice,
  readRecognitionEvent,
  type MicState,
  type SpeechRecognitionCtor,
  type SpeechRecognitionEventLike,
  type VoiceWindow,
} from "../voice";

class FakeRec {
  lang = "";
  continuous = false;
  interimResults = false;
  start() {}
  stop() {}
  abort() {}
  onstart = null;
  onend = null;
  onerror = null;
  onresult = null;
}

describe("capability detection", () => {
  it("resolves standard SpeechRecognition", () => {
    const win = { SpeechRecognition: FakeRec as unknown as SpeechRecognitionCtor } as VoiceWindow;
    expect(getSpeechRecognitionCtor(win)).toBe(win.SpeechRecognition);
    expect(isSpeechToTextSupported(win)).toBe(true);
  });

  it("falls back to webkitSpeechRecognition", () => {
    const win = { webkitSpeechRecognition: FakeRec as unknown as SpeechRecognitionCtor } as VoiceWindow;
    expect(getSpeechRecognitionCtor(win)).toBe(win.webkitSpeechRecognition);
  });

  it("returns null / false when unsupported", () => {
    expect(getSpeechRecognitionCtor({} as VoiceWindow)).toBeNull();
    expect(getSpeechRecognitionCtor(undefined)).toBeNull();
    expect(isSpeechToTextSupported({} as VoiceWindow)).toBe(false);
  });

  it("detects text-to-speech only when both synth + utterance exist", () => {
    const full = {
      speechSynthesis: { speak() {}, cancel() {} },
      SpeechSynthesisUtterance: class {},
    } as unknown as VoiceWindow;
    expect(isTextToSpeechSupported(full)).toBe(true);
    expect(
      isTextToSpeechSupported({
        speechSynthesis: { speak() {}, cancel() {}, getVoices: () => [] },
      } as VoiceWindow),
    ).toBe(false);
    expect(isTextToSpeechSupported({} as VoiceWindow)).toBe(false);
  });
});

describe("nextMicState", () => {
  it("walks idle → listening → processing → idle", () => {
    let s: MicState = "idle";
    s = nextMicState(s, "start");
    expect(s).toBe("listening");
    s = nextMicState(s, "started");
    expect(s).toBe("listening");
    s = nextMicState(s, "final");
    expect(s).toBe("listening"); // continuous: final keeps listening
    s = nextMicState(s, "stop");
    expect(s).toBe("processing");
    s = nextMicState(s, "ended");
    expect(s).toBe("idle");
  });

  it("any active state goes to error on errored, and reset recovers", () => {
    expect(nextMicState("listening", "errored")).toBe("error");
    expect(nextMicState("error", "reset")).toBe("idle");
    expect(nextMicState("error", "start")).toBe("listening");
  });

  it("unsupported is sticky except on reset", () => {
    expect(nextMicState("unsupported", "start")).toBe("unsupported");
    expect(nextMicState("unsupported", "started")).toBe("unsupported");
    expect(nextMicState("unsupported", "reset")).toBe("idle");
  });
});

describe("readRecognitionEvent", () => {
  function ev(parts: Array<{ t: string; final: boolean }>, resultIndex = 0): SpeechRecognitionEventLike {
    const results = parts.map((p) => ({ 0: { transcript: p.t }, isFinal: p.final, length: 1 }));
    return {
      resultIndex,
      results: { ...results, length: results.length } as unknown as SpeechRecognitionEventLike["results"],
    };
  }

  it("separates final from interim text", () => {
    const { finalText, interimText } = readRecognitionEvent(
      ev([
        { t: "hello world", final: true },
        { t: " and then", final: false },
      ]),
    );
    expect(finalText).toBe("hello world");
    expect(interimText).toBe("and then");
  });

  it("honors resultIndex (only new results)", () => {
    const { finalText } = readRecognitionEvent(
      ev([{ t: "old", final: true }, { t: "new", final: true }], 1),
    );
    expect(finalText).toBe("new");
  });
});

describe("pickPreferredVoice — matches the production demo selection", () => {
  it("prefers an English Google/Samantha/Daniel voice", () => {
    const voices = [
      { name: "Microsoft David", lang: "en-US" },
      { name: "Samantha", lang: "en-US" },
      { name: "Google US English", lang: "en-US" },
    ];
    expect(pickPreferredVoice(voices)?.name).toBe("Samantha");
  });

  it("falls back to any English voice", () => {
    const voices = [
      { name: "Amelie", lang: "fr-FR" },
      { name: "Microsoft David", lang: "en-US" },
    ];
    expect(pickPreferredVoice(voices)?.name).toBe("Microsoft David");
  });

  it("returns null when no English voice exists", () => {
    expect(pickPreferredVoice([{ name: "Amelie", lang: "fr-FR" }])).toBeNull();
    expect(pickPreferredVoice([])).toBeNull();
  });

});

describe("buildInterviewerVoiceRequest — EXACT homepage-demo ElevenLabs voice", () => {
  it("targets the demo interviewer voice id + model + settings", () => {
    const req = buildInterviewerVoiceRequest("Walk me through the schema.");
    // Preferred interviewer voice (ELEVEN_VOICE_INTERVIEWER in .env.local).
    expect(DEMO_INTERVIEWER_VOICE_ID).toBe("oO7sLA3dWfQXsKeSAjpA");
    expect(req.url).toContain(DEMO_INTERVIEWER_VOICE_ID);
    // Honors an explicit voice-id override (the route passes the env value).
    expect(buildInterviewerVoiceRequest("hi", "custom123").url).toContain("custom123");
    expect(req.url).toContain("mp3_44100_128");
    expect(req.body.model_id).toBe(DEMO_VOICE_MODEL_ID);
    expect(req.body.model_id).toBe("eleven_multilingual_v2");
    expect(req.body.text).toBe("Walk me through the schema.");
    // Same voice_settings as the demo generator.
    expect(DEMO_VOICE_SETTINGS).toEqual({
      stability: 0.4,
      similarity_boost: 0.8,
      style: 0.35,
      use_speaker_boost: true,
    });
    expect(req.body.voice_settings).toEqual(DEMO_VOICE_SETTINGS);
  });
});

describe("nextBargeState — full barge-in coordinator", () => {
  it("candidate cuts in while the interviewer is speaking", () => {
    let s: BargeState = "idle";
    s = nextBargeState(s, "interviewer_start");
    expect(s).toBe("interviewer_speaking");
    s = nextBargeState(s, "speech_start"); // candidate barges in
    expect(s).toBe("candidate_interrupting");
    s = nextBargeState(s, "interrupt_done"); // audio cancelled
    expect(s).toBe("interrupted");
    s = nextBargeState(s, "listening");
    expect(s).toBe("listening");
    s = nextBargeState(s, "stop");
    expect(s).toBe("processing");
    s = nextBargeState(s, "answer_ready");
    expect(s).toBe("ready_to_send");
    s = nextBargeState(s, "sent");
    expect(s).toBe("idle");
  });

  it("interviewer finishing cleanly returns to idle", () => {
    const s = nextBargeState("interviewer_speaking", "interviewer_done");
    expect(s).toBe("idle");
  });

  it("answering without an active interviewer just listens", () => {
    expect(nextBargeState("idle", "speech_start")).toBe("listening");
  });

  it("reset always returns idle; unknown pairs are no-ops", () => {
    expect(nextBargeState("listening", "reset")).toBe("idle");
    expect(nextBargeState("idle", "interrupt_done")).toBe("idle");
    expect(nextBargeState("interviewer_speaking", "answer_ready")).toBe("interviewer_speaking");
  });

  it("isCandidateFloor reflects who holds the floor", () => {
    expect(isCandidateFloor("idle")).toBe(false);
    expect(isCandidateFloor("interviewer_speaking")).toBe(false);
    expect(isCandidateFloor("interrupted")).toBe(true);
    expect(isCandidateFloor("listening")).toBe(true);
    expect(isCandidateFloor("ready_to_send")).toBe(true);
  });
});

describe("sanitizeForSpeech", () => {
  it("strips code-ish tokens so the voice reads natural words", () => {
    expect(sanitizeForSpeech("Fix charge_customer() now")).toBe("Fix charge customer now");
    expect(sanitizeForSpeech("the `db.query` call with %s")).toBe("the db query call with");
    expect(sanitizeForSpeech("WHERE idempotency_key = x")).toBe("WHERE idempotency key = x");
  });

  it("respells the adjective 'live' before a noun (homograph → /laɪv/)", () => {
    expect(sanitizeForSpeech("Here's a live issue")).toBe("Here's a lyve issue");
    expect(sanitizeForSpeech("a live demo of the system")).toContain("lyve demo");
    // The verb 'live' (not before those nouns) is untouched.
    expect(sanitizeForSpeech("where do you live")).toBe("where do you live");
  });
});

describe("isLikelySpeechEcho", () => {
  const maya =
    "We've encountered a production issue where the payment API sometimes creates duplicate charges when the provider times out and the client retries.";

  it("detects a partial echo of Maya's current spoken line", () => {
    expect(isLikelySpeechEcho("we have encountered a production issue", maya)).toBe(true);
    expect(isLikelySpeechEcho("payment api creates duplicate charges", maya)).toBe(true);
  });

  it("does not block a genuine candidate interruption", () => {
    expect(isLikelySpeechEcho("hold on let me answer that part first", maya)).toBe(false);
    expect(isLikelySpeechEcho("I think we need a transaction boundary", maya)).toBe(false);
  });
});

describe("appendFinal", () => {
  it("accumulates with single spacing and ignores empties", () => {
    expect(appendFinal("", "hello")).toBe("hello");
    expect(appendFinal("hello", "world")).toBe("hello world");
    expect(appendFinal("hello ", "world")).toBe("hello world");
    expect(appendFinal("hello", "   ")).toBe("hello");
  });
});

describe("isFuzzyEcho — garble-resistant speaker-echo detection", () => {
  const MAYA =
    "Inspect the code in the box and identify why it sometimes creates duplicate charges on retry.";

  it("catches the EXACT live garbles that made Maya cancel herself", () => {
    // Observed in production: her opening line came back from the mic as these.
    expect(isFuzzyEcho("inspectacled in the park", [MAYA])).toBe(true);
    expect(isFuzzyEcho("inspector in the park", [MAYA])).toBe(true);
  });

  it("catches verbatim echo and partial leading echo", () => {
    expect(isFuzzyEcho("inspect the code in the box", [MAYA])).toBe(true);
    expect(isFuzzyEcho("identify why it sometimes creates duplicate", [MAYA])).toBe(true);
  });

  it("does NOT flag a genuine candidate answer", () => {
    expect(isFuzzyEcho("I would use a hash map to store the complements", [MAYA])).toBe(false);
    expect(isFuzzyEcho("hold on, let me answer that part first", [MAYA])).toBe(false);
    expect(isFuzzyEcho("I don't know this question", [MAYA])).toBe(false);
  });

  it("checks against multiple recent lines", () => {
    const nudge = "Talk me through what you're thinking, half-formed is fine.";
    expect(isFuzzyEcho("talk me through what you are thinking", [MAYA, nudge])).toBe(true);
  });

  it("short blips never match (leaves room for quick real interjections)", () => {
    expect(isFuzzyEcho("wait", [MAYA])).toBe(false);
    expect(isFuzzyEcho("hold on", [MAYA])).toBe(false);
  });

  it("topical answers REUSING her vocabulary (reordered) are NOT echo", () => {
    expect(
      isFuzzyEcho("check idempotency key to avoid duplicate charges", [MAYA]),
    ).toBe(false);
    expect(
      isFuzzyEcho(
        "we should check the idempotency key before creating a duplicate charge on retry",
        [MAYA],
      ),
    ).toBe(false);
  });

  it("bigramSimilarity sanity", () => {
    expect(bigramSimilarity("inspect the code", "inspect the code")).toBe(1);
    expect(bigramSimilarity("abc", "xyz")).toBe(0);
  });
});
