"use client";

import type { ReactNode } from "react";
import { useFormStatus } from "react-dom";
import { ArrowRight, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

/** Primary submit for the sign-in forms: gradient, soft glow, press feedback and a pending state. */
export function LoginButton({
  label = "Continue",
  pendingLabel = "Signing in…",
  icon,
  className,
}: {
  label?: string;
  pendingLabel?: string;
  icon?: ReactNode;
  className?: string;
}) {
  const { pending } = useFormStatus();
  return (
    <button
      type="submit"
      disabled={pending}
      aria-busy={pending}
      className={cn(
        "group relative inline-flex h-11 w-full items-center justify-center gap-2 overflow-hidden rounded-xl",
        "bg-gradient-to-r from-indigo-500 via-indigo-600 to-violet-600 px-5 text-sm font-semibold text-white",
        "shadow-[0_0_0_1px_rgba(129,140,248,0.35),0_8px_24px_-8px_rgba(99,102,241,0.55)] transition-all duration-200",
        "hover:shadow-[0_0_0_1px_rgba(165,180,252,0.5),0_0_24px_rgba(99,102,241,0.35)] hover:brightness-110",
        "active:translate-y-px active:scale-[0.985] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400/60",
        "focus-visible:ring-offset-2 focus-visible:ring-offset-[#080B10] disabled:cursor-wait disabled:opacity-80",
        className,
      )}
    >
      <span aria-hidden
            className="pointer-events-none absolute inset-y-0 -left-1/3 w-1/3 -skew-x-12 bg-white/15 opacity-0 blur-md transition-all duration-700 group-hover:left-[110%] group-hover:opacity-100" />
      {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : icon}
      <span>{pending ? pendingLabel : label}</span>
      {!pending && <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />}
    </button>
  );
}
