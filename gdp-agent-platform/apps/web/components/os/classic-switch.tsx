"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { LayoutGrid } from "lucide-react";
import { CLASSIC_COOKIE } from "@/lib/os/access";
import { cn } from "@/lib/utils";

/** The admin-only switch between the Agentic Data OS shell and the classic sidebar layout, for this browser. The root
 *  layout honours the cookie only for admins, so for anyone else it changes nothing. */
export function ClassicLayoutSwitch({ className }: { className?: string }) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [on, setOn] = useState(false);
  useEffect(() => setOn(document.cookie.split("; ").includes(`${CLASSIC_COOKIE}=1`)), []);
  const toggle = () => {
    const next = !on;
    document.cookie = next ? `${CLASSIC_COOKIE}=1; path=/; max-age=31536000; samesite=lax` : `${CLASSIC_COOKIE}=; path=/; max-age=0; samesite=lax`;
    setOn(next);
    start(() => router.refresh());
  };
  return (
    <button type="button" role="switch" aria-checked={on} onClick={toggle} disabled={pending} data-testid="classic-layout-switch"
            title="Admins only: show the classic sidebar and top bar on the classic pages, in this browser"
            className={cn("inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium transition hover:bg-muted disabled:opacity-60", className)}>
      <LayoutGrid className="h-3.5 w-3.5" />
      Classic layout
      <span className={cn("relative h-4 w-7 rounded-full transition", on ? "bg-primary" : "bg-muted-foreground/30")}>
        <span className={cn("absolute top-0.5 h-3 w-3 rounded-full bg-white shadow transition-all", on ? "left-3.5" : "left-0.5")} />
      </span>
    </button>
  );
}
