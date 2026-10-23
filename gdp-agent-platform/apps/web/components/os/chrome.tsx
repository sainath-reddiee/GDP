"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { OsShell, type ShellProps } from "./shell";

/** The Agentic Data OS screens live under /os. */
export const isOsPath = (path: string | null) => !!path && (path === "/os" || path.startsWith("/os/"));

/** The frame of every signed-in page. The Agentic Data OS shell by default, around its own screens and around every
 *  classic page (with the page's workspace tabs and read-only notice above it). Admins who switched to the classic
 *  layout get the classic sidebar and top bar on the classic pages, exactly as before; /os keeps its own shell. */
export function AppChrome({ classic, shell, sidebar, topBar, banner, tabs, children }: {
  classic: boolean; shell: Omit<ShellProps, "children">;
  sidebar: ReactNode; topBar: ReactNode; banner: ReactNode; tabs: ReactNode; children: ReactNode;
}) {
  const os = isOsPath(usePathname());
  if (classic && !os) {
    return (
      <div className="flex min-h-screen">
        {sidebar}
        <main className="min-w-0 flex-1">
          {topBar}
          <div className="mx-auto w-full max-w-[1600px] px-6 py-7 lg:px-10">
            {banner}
            {tabs}
            {children}
          </div>
        </main>
      </div>
    );
  }
  return (
    <OsShell {...shell}>
      {os ? children : <div data-classic-page>{banner}{tabs}{children}</div>}
    </OsShell>
  );
}
