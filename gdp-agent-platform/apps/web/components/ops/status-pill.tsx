import { cn } from "@/lib/utils";
import { FAILURE_STATUS, FIX_STATUS, PERMANENT_FIX_STATUS } from "@/lib/resolve-labels";

export function PermanentFixStatusPill({ status }: { status: string }) {
  const s = PERMANENT_FIX_STATUS[status] ?? { label: status, tone: "bg-muted text-muted-foreground", hint: "" };
  return <span title={s.hint} className={cn("inline-flex rounded-full px-2 py-0.5 text-[11px] font-semibold", s.tone)}>{s.label}</span>;
}

export function FailureStatusPill({ status }: { status: string }) {
  const s = FAILURE_STATUS[status] ?? { label: status, tone: "bg-muted text-muted-foreground", hint: "" };
  return <span title={s.hint} className={cn("inline-flex rounded-full px-2 py-0.5 text-[11px] font-semibold", s.tone)}>{s.label}</span>;
}

export function FixStatusPill({ status }: { status: string }) {
  const s = FIX_STATUS[status] ?? { label: status, tone: "bg-muted text-muted-foreground" };
  return <span className={cn("inline-flex rounded-full px-2 py-0.5 text-[11px] font-semibold", s.tone)}>{s.label}</span>;
}
