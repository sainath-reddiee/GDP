"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

/** Instant bar on internal clicks so navigation never feels like a dead button. */
export function NavProgress() {
  const path = usePathname();
  const [on, setOn] = useState(false);

  useEffect(() => {
    const start = (event: MouseEvent) => {
      const link = (event.target as HTMLElement).closest("a");
      if (!link || link.target === "_blank" || event.metaKey || event.ctrlKey) return;
      const href = link.getAttribute("href");
      if (!href || href.startsWith("#") || href.startsWith("http") || link.hasAttribute("download") || href.startsWith("/bff/")) return;
      const next = new URL(href, window.location.href);
      if (next.origin !== window.location.origin || next.pathname.replace(/\/$/, "") === path.replace(/\/$/, "")) return;
      setOn(true);
      window.setTimeout(() => setOn(false), 8000);  // never left on if the navigation is cancelled
    };
    document.addEventListener("click", start);
    return () => document.removeEventListener("click", start);
  }, [path]);

  useEffect(() => {
    setOn(false);
  }, [path]);

  return (
    <div
      aria-hidden
      className={`pointer-events-none fixed inset-x-0 top-0 z-50 h-0.5 bg-primary transition-opacity ${on ? "opacity-100" : "opacity-0"}`}
    >
      <div className={`h-full bg-primary ${on ? "animate-nav-progress" : ""}`} />
    </div>
  );
}
