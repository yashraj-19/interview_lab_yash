/**
 * LAB-ONLY interviewer text-to-speech proxy.
 *
 * Synthesizes an interviewer turn with the EXACT same ElevenLabs voice as the
 * live homepage demo (`scripts/generate-demo-audio.ts`) so the lab vNext
 * interview sounds identical. The API key stays server-side; the browser only
 * ever receives audio bytes.
 *
 * This does NOT touch production flows — it is only consumed by
 * `/lab/interview-v3` via the `useTextToSpeech` hook, which falls back to browser
 * speechSynthesis if this endpoint is unavailable (e.g. no key in an env).
 */
import { NextRequest, NextResponse } from "next/server";
import { buildInterviewerVoiceRequest } from "@/lib/interview-v3/voice";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const apiKey = process.env.ELEVENLABS_API_KEY;
  if (!apiKey) {
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

  // Active interviewer voice: "Sia" (oO7sLA3dWfQXsKeSAjpA), via env override.
  const voiceId = process.env.ELEVEN_VOICE_INTERVIEWER || undefined;
  const { url, body } = buildInterviewerVoiceRequest(text, voiceId);
  let upstream: Response;
  try {
    upstream = await fetch(url, {
      method: "POST",
      headers: { "xi-api-key": apiKey, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    return NextResponse.json({ error: "tts_upstream_unreachable" }, { status: 502 });
  }

  if (!upstream.ok) {
    return NextResponse.json({ error: `tts_upstream_${upstream.status}` }, { status: 502 });
  }

  const audio = await upstream.arrayBuffer();
  return new NextResponse(audio, {
    status: 200,
    headers: {
      "Content-Type": "audio/mpeg",
      "Cache-Control": "no-store",
    },
  });
}
