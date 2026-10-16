import Link from "next/link";
import { Plus, Workflow } from "lucide-react";
import { api, ApiError, whoami } from "@/lib/api";
import { can } from "@/lib/types";
import { cn } from "@/lib/utils";
import { PageHeader } from "@/components/page-header";
import { FixStatusPill } from "@/components/ops/status-pill";
import type { FixSkill, KnownFix } from "../resolve-actions";
import { SkillEditor } from "./fix-form";

const FILTERS = ["", "DRAFT", "PUBLISHED", "RETIRED"];

/** The known-fix catalogue: what the resolver may run, who wrote and published it, and how it has done. */
export default async function FixesPage({ searchParams }: { searchParams?: { status?: string; q?: string } }) {
  const me = await whoami();
  const header = (
    <PageHeader eyebrow="Operations" title="Known fixes"
                description="Fixes written from solved tickets and published by a second person. Only published fixes are ever run, and only on failures they match."
                actions={can(me, "OPS.OPERATE") ? (
                  <Link href="/ops/fixes/new" className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90"><Plus className="h-4 w-4" />New fix</Link>
                ) : undefined} />
  );
  if (!can(me, "OPS.VIEW")) return <div className="space-y-5">{header}<p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">Known fixes need the OPS.VIEW privilege.</p></div>;
  const status = FILTERS.includes(searchParams?.status ?? "") ? searchParams?.status ?? "" : "";
  let fixes: KnownFix[] = [];
  let skills: FixSkill[] = [];
  let error = "";
  try {
    [fixes, skills] = await Promise.all([
      api<KnownFix[]>(`/api/ops/resolve/fixes?${new URLSearchParams({ ...(status ? { status } : {}), ...(searchParams?.q ? { q: searchParams.q } : {}) })}`),
      api<FixSkill[]>("/api/ops/resolve/skills"),
    ]);
  } catch (e) {
    if (!(e instanceof ApiError)) throw e;
    error = e.status === 404 ? "Known fixes need the latest deploy (migration V062)." : e.message;
  }
  return (
    <div className="space-y-5">
      {header}
      {error ? <p role="alert" className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{error}</p> : (
        <>
          <div className="flex flex-wrap gap-1.5" aria-label="Filter by status">
            {FILTERS.map((f) => (
              <Link key={f || "all"} href={f ? `/ops/fixes?status=${f}` : "/ops/fixes"}
                    className={cn("rounded-full border px-2.5 py-1 text-xs font-medium", status === f ? "border-primary bg-primary/10 text-primary" : "hover:bg-muted")}>
                {f ? f.charAt(0) + f.slice(1).toLowerCase() : "All"}
              </Link>
            ))}
          </div>
          {fixes.length === 0 ? (
            <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
              No known fixes yet. When a ticketed failure is solved, open it in Resolutions and choose Write a known fix.
            </p>
          ) : (
            <div className="surface overflow-x-auto">
              <table className="w-full text-sm" data-testid="fix-table">
                <thead className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
                  <tr><th className="px-4 py-2 font-medium">Fix</th><th className="px-3 py-2 font-medium">Applies to</th><th className="px-3 py-2 font-medium">Runs</th>
                    <th className="px-3 py-2 font-medium">Approval</th><th className="px-3 py-2 font-medium">Status</th></tr>
                </thead>
                <tbody className="divide-y">
                  {fixes.map((k) => (
                    <tr key={k.issue_id} className="align-top hover:bg-muted/30">
                      <td className="px-4 py-2.5">
                        <Link href={`/ops/fixes/${encodeURIComponent(k.issue_id)}`} className="font-medium hover:text-primary">{k.title}</Link>
                        {k.proposed_by_agent && <span className="ml-2 rounded-full bg-agent/10 px-2 py-0.5 text-[10px] font-semibold text-agent">Proposed by the agent</span>}
                        <p className="text-xs text-muted-foreground">{k.fix_type === "SKILL" ? `Skill ${k.skill_name ?? ""}` : k.procedure_fqn} · v{k.version_no} · by {k.created_by}{k.published_by ? `, published by ${k.published_by}` : ""}</p>
                      </td>
                      <td className="px-3 py-2.5 text-xs">{k.dag_id ?? "any DAG"}{k.task_id ? ` · ${k.task_id}` : ""}{k.exception_class ? ` · ${k.exception_class}` : ""}</td>
                      <td className="px-3 py-2.5 text-xs tabular-nums">{k.runs_ok ?? 0} ok{k.runs_failed ? `, ${k.runs_failed} failed` : ""}</td>
                      <td className="px-3 py-2.5 text-xs">{k.approval_required === false ? "Runs on its own" : "Asks first"}</td>
                      <td className="px-3 py-2.5"><FixStatusPill status={k.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <section className="surface space-y-3 p-4">
            <h2 className="flex items-center gap-2 text-sm font-semibold"><Workflow className="h-4 w-4" />Multi-step fixes (skills)</h2>
            <p className="text-xs text-muted-foreground">A repair that needs several steps. The resolution agent runs the steps in order through one guarded tool; each step is a reviewed CORE.FIX_* procedure, runs once, and only after the step before it succeeded.</p>
            {skills.map((s) => (
              <details key={s.skill_id} className="rounded-lg border px-3 py-2">
                <summary className="cursor-pointer text-sm font-medium">{s.skill_name} <span className="text-xs font-normal text-muted-foreground">v{s.version_no} · {s.steps?.length ?? 0} steps</span></summary>
                <div className="pt-2"><SkillEditor skill={s} editable={can(me, "OPS.OPERATE")} /></div>
              </details>
            ))}
            {can(me, "OPS.OPERATE") && (
              <details className="rounded-lg border border-dashed px-3 py-2">
                <summary className="cursor-pointer text-sm font-medium text-primary">New skill</summary>
                <div className="pt-2"><SkillEditor editable /></div>
              </details>
            )}
          </section>
        </>
      )}
    </div>
  );
}
