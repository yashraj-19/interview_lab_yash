import { describe, expect, it } from "vitest";

import {
  CONFIRM_COMPLETE_MS,
  CONTINUATION_MS,
  NEUTRAL_MS,
  SUSTAINED_SPEECH_MS,
  decideBargeIn,
  decideTurnEnd,
  endsInContinuation,
  endsInTerminalPunct,
  isCutIn,
  nonBackchannelWordCount,
  pickFiller,
  FILLER_LINES,
} from "../turn-taking";

describe("decideTurnEnd — three-arm rule", () => {
  it("waits long on trailing continuation tokens (mid-thought)", () => {
    for (const t of [
      "so I think we could",
      "the complexity would be",
      "we can use a",
      "I'll start with the",
      "first we check if",
      "it depends on",
      "I'm",
      "then I was thinking",
    ]) {
      const d = decideTurnEnd(t);
      expect(d.reason, t).toBe("continuation");
      expect(d.delayMs).toBe(CONTINUATION_MS);
    }
  });

  it("waits long on a trailing comma", () => {
    expect(decideTurnEnd("first we sort the array,").reason).toBe("continuation");
  });

  it("confirms fast on complete-sounding endings", () => {
    for (const t of ["The time complexity is linear.", "Is that right?", "Done!"]) {
      const d = decideTurnEnd(t);
      expect(d.reason, t).toBe("complete");
      expect(d.delayMs).toBe(CONFIRM_COMPLETE_MS);
    }
  });

  it("uses the neutral middle-ground for unpunctuated complete-sounding text (Web Speech common case)", () => {
    const d = decideTurnEnd("I would use a hash map for constant time lookups");
    expect(d.reason).toBe("neutral");
    expect(d.delayMs).toBe(NEUTRAL_MS);
  });

  it("never hangs: every non-empty decision has a finite delay", () => {
    for (const t of ["and", "done.", "hash map"]) {
      expect(decideTurnEnd(t).delayMs).not.toBeNull();
    }
  });

  it("returns null delay for empty text (nothing to send)", () => {
    expect(decideTurnEnd("").delayMs).toBeNull();
    expect(decideTurnEnd("   ").delayMs).toBeNull();
  });

  it("gerund heuristic: trailing -ing word waits, but short -ing words don't", () => {
    expect(decideTurnEnd("so I was iterating").reason).toBe("continuation");
    expect(decideTurnEnd("we could try using").reason).toBe("continuation");
    // "ring"/"king" style short words are not gerunds — falls through to neutral
    expect(decideTurnEnd("I heard a ring").reason).toBe("neutral");
  });
});

describe("endsInContinuation / endsInTerminalPunct", () => {
  it("handles trailing punctuation after the last word", () => {
    expect(endsInContinuation("we go with the…")).toBe(true); // "the" + ellipsis
    expect(endsInTerminalPunct("done.")).toBe(true);
    expect(endsInTerminalPunct("done")).toBe(false);
  });
});

describe("decideBargeIn — backchannel immunity + cut-ins + sustained speech", () => {
  it("pure backchannels never interrupt", () => {
    for (const t of ["mm-hm", "yeah", "okay", "right", "i see", "got it", "yep sure"]) {
      expect(decideBargeIn(t, 0), t).toBe("ignore");
    }
  });

  it("utterance-initial cut-in words interrupt on a single word", () => {
    for (const t of ["wait", "stop", "hold on", "sorry, one thing", "actually I disagree", "no"]) {
      expect(decideBargeIn(t, 0), t).toBe("interrupt");
    }
  });

  it("cut-in words mid-utterance do not trigger the cut-in path", () => {
    expect(isCutIn("let me wait a moment")).toBe(false);
  });

  it("three or more non-backchannel words interrupt", () => {
    expect(decideBargeIn("that complexity is wrong", 0)).toBe("interrupt");
    // two substantive words is not enough
    expect(decideBargeIn("hash map", 0)).toBe("ignore");
  });

  it("backchannel words don't count toward the interrupt threshold", () => {
    // "yeah okay right" = 0 substantive words
    expect(nonBackchannelWordCount("yeah okay right")).toBe(0);
    expect(decideBargeIn("yeah okay right", 0)).toBe("ignore");
  });

  it("sustained speech interrupts even below the word threshold", () => {
    expect(decideBargeIn("hash map", SUSTAINED_SPEECH_MS)).toBe("interrupt");
    expect(decideBargeIn("hash map", SUSTAINED_SPEECH_MS - 1)).toBe("ignore");
  });
});

describe("pickFiller", () => {
  it("is deterministic by seq and always yields a known line", () => {
    expect(pickFiller(7)).toBe(pickFiller(7));
    expect(FILLER_LINES).toContain(pickFiller(3));
    expect(FILLER_LINES).toContain(pickFiller(-2));
  });
});
