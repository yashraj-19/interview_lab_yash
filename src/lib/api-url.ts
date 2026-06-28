/**
 * Single source of truth for the backend base URL.
 *
 * Previously `process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"` was
 * copy-pasted in 20+ files. If the env var was ever missing in a production
 * build, every call silently fell back to localhost and failed with no signal.
 * This centralizes it and makes a missing prod value LOUD instead of silent.
 *
 * NEXT_PUBLIC_* vars are inlined at build time, so this check runs against the
 * value baked into the bundle, not the runtime environment.
 */

const FALLBACK = "http://localhost:8000";

export const API_URL = process.env.NEXT_PUBLIC_API_URL || FALLBACK;

if (!process.env.NEXT_PUBLIC_API_URL && process.env.NODE_ENV === "production") {
  // Loud, so a misconfigured deploy is obvious in logs/console instead of
  // every request quietly hitting localhost and failing.
  console.error(
    "[config] NEXT_PUBLIC_API_URL is not set in a production build. " +
      `Falling back to ${FALLBACK}; API calls will fail. Set it in the deploy env.`,
  );
}
