import { InterviewRoom } from "@/components/interview-v3/session/InterviewRoom";

/**
 * vNext session room route. `params` is Promise-based in Next 16 — await it,
 * then hand the id to the client room, which recovers the intake + rubric from
 * the client-side handoff store (no backend).
 */
export default async function InterviewV3SessionPage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = await params;
  return <InterviewRoom sessionId={sessionId} />;
}
