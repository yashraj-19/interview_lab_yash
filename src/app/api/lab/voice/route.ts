/**
 * LAB-ONLY interviewer text-to-speech proxy.
 *
 * Provider chain (keys stay server-side; the browser only receives audio):
 *   1. ElevenLabs ("Sia") — needs a PAID plan (free keys 402).
 *   2. Deepgram Aura-2   — natural voice on Deepgram's generous free credits;
 *      the same TTS family the deployed Voice_Assist interviewer uses.
 *   3. 503 → the client's browser speechSynthesis fallback.
 *
 * This does NOT touch production flows — it is only consumed by
 * `/lab/interview-v3` via the `useTextToSpeech` hook.
 */
import { NextRequest, NextResponse } from "next/server";
import { buildInterviewerVoiceRequest } from "@/lib/interview-v3/voice";

export const runtime = "nodejs";

function audioResponse(audio: ArrayBuffer): NextResponse {
  return new NextResponse(audio, {
    status: 200,
    headers: { "Content-Type": "audio/mpeg", "Cache-Control": "no-store" },
  });
}

async function tryElevenLabs(text: string, apiKey: string): Promise<ArrayBuffer | null> {
  const voiceId = process.env.ELEVEN_VOICE_INTERVIEWER || undefined;
  const { url, body } = buildInterviewerVoiceRequest(text, voiceId);
  try {
    const upstream = await fetch(url, {
      method: "POST",
      headers: { "xi-api-key": apiKey, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!upstream.ok) return null; // e.g. 402 on free keys → next provider
    return await upstream.arrayBuffer();
  } catch {
    return null;
  }
}

async function tryDeepgram(text: string, apiKey: string): Promise<ArrayBuffer | null> {
  // Aura-2 "Thalia": warm, natural en-US voice; overridable via env.
  const model = process.env.DEEPGRAM_TTS_MODEL || "aura-2-thalia-en";
  try {
    const upstream = await fetch(
      `https://api.deepgram.com/v1/speak?model=${encodeURIComponent(model)}&encoding=mp3`,
      {
        method: "POST",
        headers: { Authorization: `Token ${apiKey}`, "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      },
    );
    if (!upstream.ok) return null;
    return await upstream.arrayBuffer();
  } catch {
    return null;
  }
}

export async function POST(req: NextRequest) {
  // Trim keys defensively: a trailing newline in a deploy env var once broke
  // auth headers in production ("Illegal header value b'Bearer …\n'").
  const elevenKey = (process.env.ELEVENLABS_API_KEY || "").trim();
  const deepgramKey = (process.env.DEEPGRAM_API_KEY || "").trim();
  if (!elevenKey && !deepgramKey) {
    // Signal the client to use its browser-TTS fallback.
    return NextResponse.json({ error: "tts_unconfigured" }, { status: 503 });
  }

  let text = "";
  try {
    const body = await req.json();
    text = typeof body?.text === "string" ? body.text.trim() : "";
  } catch {
    return NextResponse.json({ error: "bad_json" }, { status: 400 });
  }
  if (!text) return NextResponse.json({ error: "empty_text" }, { status: 400 });
  if (text.length > 1200) text = text.slice(0, 1200);

  if (elevenKey) {
    const audio = await tryElevenLabs(text, elevenKey);
    if (audio) return audioResponse(audio);
  }
  if (deepgramKey) {
    const audio = await tryDeepgram(text, deepgramKey);
    if (audio) return audioResponse(audio);
  }
  return NextResponse.json({ error: "tts_upstream_failed" }, { status: 502 });
}
