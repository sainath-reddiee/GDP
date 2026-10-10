// Plain module (no "use client"): the server page and the client queue both read these values.
import type { CaseKind, CaseStatus } from "./actions";

export const KINDS: { id: CaseKind; label: string }[] = [
  { id: "DATA_BUG", label: "Data" },
  { id: "CODE_BUG", label: "Code" },
  { id: "DATA_QUALITY", label: "Data quality" },
  { id: "PIPELINE", label: "Pipeline" },
  { id: "QUESTION", label: "Question" },
];

/** The main path, in order; CLOSED and DUPLICATE are side states. */
export const STEPS: CaseStatus[] = ["NEW", "TRIAGED", "IN_PROGRESS", "FIX_PROPOSED", "FIX_APPLIED", "VERIFIED", "RESOLVED"];
export const SIDE: CaseStatus[] = ["CLOSED", "DUPLICATE"];
export const STATUSES: CaseStatus[] = [...STEPS, ...SIDE];
export const OPEN_STATUSES: CaseStatus[] = ["NEW", "TRIAGED", "IN_PROGRESS", "FIX_PROPOSED", "FIX_APPLIED", "VERIFIED"];
/** Statuses that need CASE.RESOLVE to set. */
export const RESOLVE_ONLY: CaseStatus[] = ["VERIFIED", "RESOLVED", "CLOSED", "DUPLICATE"];
export const SEVERITY_IDS = ["P1", "P2", "P3", "P4"] as const;

/** The queue's filters as they sit in the URL of /qa?tab=cases. `status` absent means the open statuses; "any" means all. */
export type CaseQuery = { status?: string; severity?: string; kind?: string; domain?: string; mine?: string; q?: string; sla?: string };
export type CaseFilterState = { status: string[]; severity: string; kind: string; domain: string; mine: boolean; q: string; breached: boolean };

export const DEFAULT_FILTERS: CaseFilterState = { status: OPEN_STATUSES, severity: "", kind: "", domain: "", mine: false, q: "", breached: false };

export function readCaseFilters(sp: CaseQuery): CaseFilterState {
  const status = sp.status === "any" ? [] : sp.status
    ? sp.status.split(",").map((s) => s.trim().toUpperCase()).filter((s) => (STATUSES as string[]).includes(s))
    : OPEN_STATUSES;
  return {
    status,
    severity: (SEVERITY_IDS as readonly string[]).includes((sp.severity ?? "").toUpperCase()) ? sp.severity!.toUpperCase() : "",
    kind: KINDS.some((k) => k.id === (sp.kind ?? "").toUpperCase()) ? sp.kind!.toUpperCase() : "",
    domain: sp.domain ?? "",
    mine: sp.mine === "1" || sp.mine === "true",
    q: (sp.q ?? "").slice(0, 200),
    breached: sp.sla === "breached",
  };
}

const sameSet = (a: string[], b: string[]) => a.length === b.length && a.every((x) => b.includes(x));

/** The URL form of the filters; defaults are left out so a plain /qa?tab=cases stays the open queue. */
export function caseQuery(f: CaseFilterState): Record<keyof CaseQuery, string> {
  return {
    status: sameSet(f.status, OPEN_STATUSES) ? "" : f.status.length ? f.status.join(",") : "any",
    severity: f.severity, kind: f.kind, domain: f.domain, mine: f.mine ? "1" : "", q: f.q.trim(), sla: f.breached ? "breached" : "",
  };
}
