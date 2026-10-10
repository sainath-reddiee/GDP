import type { IncidentFilters } from "./actions";

/** The inbox filters as they live in the URL. Shared by the server page and the client inbox. */
export type InboxNav = { status: string[]; severity: string; team: string; env: string; mine: boolean; q: string; dag: string };

const VALID = ["OPEN", "ACK", "MITIGATED", "RESOLVED", "MUTED"];
/** With no status in the URL the inbox shows what still needs someone: open and acknowledged. */
export const DEFAULT_STATUS = ["OPEN", "ACK"];

export function parseNav(sp: Record<string, string | undefined>): InboxNav {
  const raw = sp.status;
  const status = raw === undefined ? DEFAULT_STATUS
    : raw === "any" ? [] : raw.split(",").map((s) => s.trim().toUpperCase()).filter((s) => VALID.includes(s));
  return {
    status, severity: (sp.severity ?? "").toUpperCase(), team: sp.team ?? "", env: sp.env ?? "",
    mine: sp.mine === "1" || sp.mine === "true", q: sp.q ?? "", dag: sp.dag ?? "",
  };
}

/** URL query values; an empty status list is written as "any" so it survives a reload. */
export function navParams(n: InboxNav): Record<string, string> {
  return {
    status: n.status.length ? n.status.join(",") : "any", severity: n.severity, team: n.team, env: n.env,
    mine: n.mine ? "1" : "", q: n.q, dag: n.dag,
  };
}

/** API query. The API has no DAG parameter, so a DAG filter travels as the search text (dag_id is sent too in case
 *  the API learns it) and the inbox keeps only exact matches. */
export function toFilters(n: InboxNav): IncidentFilters {
  return {
    status: n.status.join(","), severity: n.severity, team_id: n.team, env_id: n.env, mine: n.mine,
    q: n.q.trim() || n.dag, dag_id: n.dag,
  };
}
