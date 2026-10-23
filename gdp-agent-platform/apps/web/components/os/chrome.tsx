"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

/** The Agentic Data OS experience lives under /os and draws its own shell (app/os/layout.tsx). */
export const isOsPath = (path: string | null) => !!path && (path === "/os" || path.startsWith("/os/"));

/** Part of the classic shell (sidebar, top bar, workspace tabs): drawn everywhere except under /os. */
export function HideOnOs({ children }: { children: ReactNode }) {
  return isOsPath(usePathname()) ? null : <>{children}</>;
}

/** The classic page frame (centred, padded). Under /os the page is passed through untouched. */
export function LegacyFrame({ className, children }: { className: string; children: ReactNode }) {
  return isOsPath(usePathname()) ? <>{children}</> : <div className={className}>{children}</div>;
}
