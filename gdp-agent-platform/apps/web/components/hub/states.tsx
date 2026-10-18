import type { ReactNode } from "react";
import { Construction, Inbox, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export function EmptyState({ icon: Icon = Inbox, title, children, action }: { icon?: LucideIcon; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="surface flex flex-col items-center px-6 py-12 text-center">
      <span className="grid h-11 w-11 place-items-center rounded-2xl bg-accent text-accent-foreground"><Icon className="h-5 w-5" /></span>
      <h3 className="mt-3 text-sm font-semibold">{title}</h3>
      {children && <p className="mt-1 max-w-md text-sm text-muted-foreground">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** Shown when the API answers but the hub's tables are not deployed yet (V035/V036). */
export function NotDeployed({ what }: { what: string }) {
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50/70 px-4 py-3 text-sm text-amber-900">
      <Construction className="mr-1.5 inline h-4 w-4" />
      {what} is not deployed in this environment yet. An admin can apply the latest migrations (Admin, Apply) to enable it.
    </div>
  );
}

const STATUS_TONE: Record<string, string> = {
  PASS: "bg-emerald-50 text-emerald-700 ring-emerald-200", READY: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  ACTIVE: "bg-emerald-50 text-emerald-700 ring-emerald-200", APPROVED: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  PUBLISHED: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  FAIL: "bg-rose-50 text-rose-700 ring-rose-200", BLOCKED: "bg-rose-50 text-rose-700 ring-rose-200",
  PUBLISH_FAILED: "bg-rose-50 text-rose-700 ring-rose-200", RETIRED: "bg-slate-100 text-slate-600 ring-slate-200",
  PENDING: "bg-amber-50 text-amber-800 ring-amber-200", PAUSED: "bg-amber-50 text-amber-800 ring-amber-200",
  REVIEW: "bg-amber-50 text-amber-800 ring-amber-200", PENDING_APPROVAL: "bg-amber-50 text-amber-800 ring-amber-200",
  SUPERSEDED: "bg-slate-100 text-slate-500 ring-slate-200", WARN: "bg-amber-50 text-amber-800 ring-amber-200",
  ERROR: "bg-rose-50 text-rose-700 ring-rose-200", NEVER_RUN: "bg-slate-100 text-slate-600 ring-slate-200", DRAFT: "bg-slate-100 text-slate-700 ring-slate-200",
  REGISTERED: "bg-blue-50 text-blue-700 ring-blue-200", FINALIZED: "bg-blue-50 text-blue-700 ring-blue-200",
  // pull request states (Source control)
  OPEN: "bg-sky-50 text-sky-700 ring-sky-200", MERGED: "bg-blue-50 text-blue-700 ring-blue-200",
  CLOSED: "bg-amber-50 text-amber-800 ring-amber-200", FAILED: "bg-rose-50 text-rose-700 ring-rose-200",
  NO_CHANGES: "bg-emerald-50 text-emerald-700 ring-emerald-200",
};

/** A status as text in a tinted pill: never color alone, the word is always shown. */
export function StatusPill({ status, className }: { status: string; className?: string }) {
  return (
    <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset",
      STATUS_TONE[status] ?? "bg-slate-100 text-slate-700 ring-slate-200", className)}>
      {status.toLowerCase().replace(/_/g, " ")}
    </span>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return <p className="rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-sm text-destructive">{children}</p>;
}

/** The message of a server action error; 422 bodies from the hub APIs carry {message, errors|issues}. */
export function explain(error: string): { message: string; fields: Record<string, string>; list: string[] } {
  try {
    const body = JSON.parse(error);
    if (body && typeof body === "object") {
      const fields = body.errors && typeof body.errors === "object" && !Array.isArray(body.errors) ? body.errors : {};
      const list = Array.isArray(body.issues) ? body.issues.map((i: { message?: string } | string) => typeof i === "string" ? i : i.message ?? "") : [];
      return { message: String(body.message ?? error), fields, list };
    }
  } catch { /* plain text */ }
  return { message: error, fields: {}, list: [] };
}
