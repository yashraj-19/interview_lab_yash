import type { ReactNode } from "react";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Interview vNext — Lab",
  robots: { index: false, follow: false },
};

/**
 * Isolated calm shell for the vNext lab. No homepage Nav, no proctoring, no
 * shared chrome — a plain wrapper so the foundation can be exercised in
 * isolation. Reuses the global design vars only for color.
 */
export default function InterviewV3LabLayout({ children }: { children: ReactNode }) {
  return (
    <div
      className="min-h-screen w-full"
      style={{ background: "var(--bg)", color: "var(--text)" }}
    >
      {/* No width cap here: the room is full-bleed/immersive (like the homepage
          demo); the launcher/intake constrain their own width. */}
      {children}
    </div>
  );
}
