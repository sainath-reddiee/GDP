import { api } from "@/lib/api";
import { can, type RunSummary, type WhoAmI } from "@/lib/types";
import type { ProfileSearch, SttmSummary } from "@/lib/hub-types";

export const dynamic = "force-dynamic";

export type SearchHit = { kind: "run" | "table" | "sttm" | "qa" | "dq" | "dbt" | "test" | "jira" | "case"; title: string; detail: string; href: string };
type CaseHit = { case_id: string; number: string; title: string; status: string; severity: string; domain_name: string | null };
type JiraHit = { key: string; summary: string; status: string | null; type: string | null; assignee: string | null };

/** Command palette search: runs, profiled tables, STTM mappings, QA packs, DQ tables, dbt projects and run QA tests in one call. Each source
 *  fails soft, so a hub that is not deployed only drops its own hits. */
export async function GET(req: Request) {
  const q = (new URL(req.url).searchParams.get("q") ?? "").trim();
  if (q.length < 2) return Response.json({ hits: [] });
  const e = encodeURIComponent(q);
  const ql = q.toLowerCase();
  const [runs, profiles, sttm, packs, dq, dbt, tests, jira, cases] = await Promise.all([
    api<{ runs: RunSummary[] }>(`/api/runs?q=${e}&limit=6`).then((r) => r.runs ?? [], () => []),
    api<ProfileSearch>(`/api/hub/profiles?q=${e}&limit=6`).then((r) => r.profiles ?? [], () => []),
    api<{ mappings: SttmSummary[] }>(`/api/hub/sttm?q=${e}&limit=6`).then((r) => r.mappings ?? [], () => []),
    api<{ packs: { pack_id: string; name: string; domain_name?: string | null; status: string; tags?: string[] }[] }>("/api/qa-packs")
      .then((r) => r.packs ?? [], () => []),
    api<{ tables: { table: string; checks: number }[] }>(`/api/dq/catalog?q=${e}`).then((r) => r.tables ?? [], () => []),
    api<{ generations: { generation_id: string; run_name: string | null; target_model: string | null; generation_version: number; generation_status: string }[] }>(`/api/hub/dbt?q=${e}`)
      .then((r) => r.generations ?? [], () => []),
    api<{ tests: { test_id: string; title: string; run_name: string | null; target_fqn: string | null; outcome: string | null }[] }>(`/api/qa/run-tests?q=${e}`)
      .then((r) => r.tests ?? [], () => []),
    // Jira tickets, as the caller (a key, or text of three characters or more); not connected or no JIRA.READ: none
    q.length >= 3 || /^[A-Za-z][A-Za-z0-9_]*-\d+$/.test(q)
      ? api<WhoAmI>("/api/auth/me").then((me) => (can(me, "JIRA.READ")
        ? api<{ issues: JiraHit[] }>(`/api/jira/issues?scope=search&q=${e}`).then((r) => r.issues ?? [], () => [])
        : []), () => [] as JiraHit[])
      : Promise.resolve([] as JiraHit[]),
    api<{ cases: CaseHit[] }>(`/api/cases?q=${e}&status=any&limit=5`).then((r) => r.cases ?? [], () => []),
  ]);
  const words = (s: string) => s.toLowerCase().replace(/_/g, " ");
  const hits: SearchHit[] = [
    ...jira.slice(0, 5).map((i) => ({ kind: "jira" as const, title: `${i.key} ${i.summary}`, detail: `Jira${i.type ? ` · ${i.type}` : ""}${i.status ? ` · ${i.status}` : ""}${i.assignee ? ` · ${i.assignee}` : ""}`, href: `/tickets?tab=ticket&key=${encodeURIComponent(i.key)}` })),
    ...cases.map((c) => ({ kind: "case" as const, title: `${c.number} ${c.title}`, detail: `Case · ${c.severity} · ${words(c.status)}${c.domain_name ? ` · ${c.domain_name}` : ""}`, href: `/qa/cases/${c.case_id}` })),
    ...runs.map((r) => ({ kind: "run" as const, title: r.run_name, detail: `Run · ${words(r.current_state)}${r.source_system_name ? ` · ${r.source_system_name}` : ""}`, href: `/runs/${r.run_id}` })),
    ...profiles.map((p) => ({ kind: "table" as const, title: p.table_fqn, detail: `Profiled table · ${p.column_count ?? "?"} columns${p.domain_name ? ` · ${p.domain_name}` : ""}`, href: `/dashboard/profiling?table=${encodeURIComponent(p.table_name)}` })),
    ...sttm.map((m) => ({ kind: "sttm" as const, title: m.target_entity, detail: `STTM v${m.version} · ${words(m.status)}${m.domain_name ? ` · ${m.domain_name}` : ""}`, href: `/dashboard/sttm/${m.catalog_id}` })),
    ...packs.filter((p) => p.name.toLowerCase().includes(ql) || (p.tags ?? []).some((t) => t.includes(ql))).slice(0, 5)
      .map((p) => ({ kind: "qa" as const, title: p.name, detail: `QA pack · ${words(p.status)}${p.domain_name ? ` · ${p.domain_name}` : ""}`, href: `/dashboard/qa-store?pack=${p.pack_id}` })),
    ...dq.slice(0, 5).map((t) => ({ kind: "dq" as const, title: t.table, detail: `Data quality · ${t.checks} checks`, href: `/dashboard/dq-store?table=${encodeURIComponent(t.table)}` })),
    ...dbt.slice(0, 4).map((g) => ({ kind: "dbt" as const, title: g.target_model ?? g.run_name ?? "dbt project", detail: `dbt v${g.generation_version} · ${words(g.generation_status)}${g.run_name ? ` · ${g.run_name}` : ""}`, href: `/dashboard/modeling/dbt/${g.generation_id}` })),
    ...tests.slice(0, 4).map((t) => ({ kind: "test" as const, title: t.title, detail: `Run test${t.outcome ? ` · ${words(t.outcome)}` : ""}${t.run_name ? ` · ${t.run_name}` : t.target_fqn ? ` · ${t.target_fqn}` : ""}`, href: "/dashboard/qa-store?view=tests" })),
  ];
  return Response.json({ hits }, { headers: { "Cache-Control": "private, max-age=10" } });
}
