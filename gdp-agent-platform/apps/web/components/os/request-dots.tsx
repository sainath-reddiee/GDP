import { requestSteps, type OnboardingRequest } from "@/lib/os/requests";
import { cn } from "@/lib/utils";

/** A request's path as small bars: done, current (red when blocked), pending. */
export function RequestDots({ r }: { r: Pick<OnboardingRequest, "stage" | "status" | "attention" | "domain_id" | "request_type" | "intent"> }) {
  return (
    <span className="flex items-center gap-1" aria-label={`Stage ${r.stage.toLowerCase()}`}>
      {requestSteps(r).map((s) => (
        <span key={s.stage} title={`${s.label}${s.owner ? ` · ${s.owner}` : ""}`}
              className={cn("h-2 w-5 rounded-full", s.state === "done" ? "bg-[var(--os-leaf)]" : s.state === "current" ? "bg-[var(--os-forest)]"
                : s.state === "attention" ? "bg-[#d85b52]" : "bg-[var(--os-line)]")} />
      ))}
    </span>
  );
}
