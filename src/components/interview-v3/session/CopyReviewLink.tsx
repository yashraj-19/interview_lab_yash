"use client";

import { useState } from "react";
import { localReviewLink } from "@/lib/interview-v3";

const btnCls =
  "rounded-md border border-[var(--muted)] px-3 py-1.5 text-sm font-medium transition-colors hover:bg-[var(--muted)]/20";

/**
 * Copies the absolute local review URL for a session to the clipboard. The
 * session is persisted to localStorage, so the copied link reopens it (even in
 * a new tab on the same origin) without any backend.
 */
export function CopyReviewLink({ sessionId }: { sessionId: string }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    const link = localReviewLink(sessionId);
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard unavailable (insecure context / denied) — surface the URL.
      window.prompt("Copy local review link", link);
    }
  }

  return (
    <button type="button" className={btnCls} onClick={() => void handleCopy()}>
      {copied ? "Copied ✓" : "Copy local review link"}
    </button>
  );
}
