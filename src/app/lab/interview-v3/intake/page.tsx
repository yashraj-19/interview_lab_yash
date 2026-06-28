import { Suspense } from "react";
import { IntakeForm } from "@/components/interview-v3/intake/IntakeForm";

export default function InterviewV3IntakePage() {
  return (
    <main className="mx-auto max-w-3xl space-y-6 px-6 py-10">
      <Suspense fallback={null}>
        <IntakeForm />
      </Suspense>
    </main>
  );
}
