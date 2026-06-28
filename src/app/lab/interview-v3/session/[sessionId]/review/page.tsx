import { ReviewWorkspace } from "@/components/interview-v3/session/ReviewWorkspace";

/**
 * vNext review route. `params` is Promise-based in Next 16 — await it, then
 * hand the id to the client workspace, which reconstructs the session from the
 * client-side handoff and streams the scorecard (no backend, no LLM).
 */
export default async function InterviewV3ReviewPage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = await params;
  return <ReviewWorkspace sessionId={sessionId} />;
}
