import type { ReactNode } from "react";

export const metadata = { title: "Mission control" };

/** The Agentic Data OS screens. Their shell (header, sectioned rail, persona, preview) is drawn by the root layout,
 *  around these screens and every classic page alike (components/os/chrome). */
export default function OsLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
