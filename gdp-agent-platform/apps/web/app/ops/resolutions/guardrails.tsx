"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition, type ReactNode } from "react";
import { Loader2, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FLOW_MODES } from "@/lib/resolve-labels";
import {
  deleteRoute, deleteScope, saveRoute, savePreference, saveScope, type FlowMode, type Preference, type Route, type Scope,
} from "../resolve-actions";

function Card({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <section className="surface overflow-hidden">
      <header className="border-b px-4 py-3"><h2 className="text-sm font-semibold">{title}</h2><p className="text-xs text-muted-foreground">{hint}</p></header>
      <div className="space-y-3 px-4 py-3">{children}</div>
    </section>
  );
}

function Toggle({ label, hint, checked, disabled, onChange }: { label: string; hint: string; checked: boolean; disabled: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-start gap-3 text-sm">
      <input type="checkbox" className="mt-1 h-4 w-4 accent-primary" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span><span className="font-medium">{label}</span><span className="block text-xs text-muted-foreground">{hint}</span></span>
    </label>
  );
}

/** The guardrails the resolver reads before anything else: the control preference (one active row), the pipeline
 *  scopes (which DAGs and tasks are in, and how) and the fix approvers. */
export function Guardrails({ preference, scopes, routes, canEdit }: { preference: Preference; scopes: Scope[]; routes: Route[]; canEdit: boolean }) {
  const router = useRouter();
  const [pref, setPref] = useState(preference);
  const [error, setError] = useState("");
  const [pending, start] = useTransition();
  const [scope, setScope] = useState({ dag_id: "", task_id: "", flow_mode: "AUTO" as FlowMode });
  const [route, setRoute] = useState({ dag_id: "", task_id: "", approver: "" });

  const run = (fn: () => Promise<{ ok: boolean; error?: string }>, after?: () => void) => start(async () => {
    setError("");
    const r = await fn();
    if (!r.ok) { setError(r.error ?? "The change was not saved."); return; }
    after?.();
    router.refresh();
  });
  const patch = (p: Partial<Preference>) => {
    setPref((v) => ({ ...v, ...p }));
    run(() => savePreference(p));
  };
  const ro = !canEdit || pending;

  return (
    <div className="space-y-4" data-testid="guardrails">
      {!canEdit && <p className="text-xs text-muted-foreground">Read only: changing the guardrails needs the OPS.RESOLVE_ADMIN privilege.</p>}
      {error && <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">{error}</p>}

      <Card title="Control preference" hint="Read first on every claim. With automatic resolution off, failures stay queued and nothing runs.">
        <Toggle label="Automatic resolution" hint="Claim queued failures and resolve them (the ops worker triggers the Snowflake procedures)."
                checked={pref.auto_resolve_enabled} disabled={ro} onChange={(v) => patch({ auto_resolve_enabled: v })} />
        <Toggle label="Clear the Airflow task after a successful fix" hint="Airflow then runs the task again; the incident resolves when it succeeds."
                checked={pref.airflow_clear_enabled} disabled={ro} onChange={(v) => patch({ airflow_clear_enabled: v })} />
        <Toggle label="Fixes need approval unless they say otherwise" hint="Applies to a published fix that does not set its own approval flag."
                checked={pref.default_approval_required} disabled={ro} onChange={(v) => patch({ default_approval_required: v })} />
        <Toggle label="The agent proposes fixes on a miss" hint="Built only from the safe-action catalogue, on objects inside the fix scope below, checked against Snowflake. One person's approval publishes and runs it; the same failure is then fixed automatically."
                checked={!!pref.agent_proposals_enabled} disabled={ro} onChange={(v) => patch({ agent_proposals_enabled: v })} />
        <label className="block space-y-1 text-xs font-medium">Agent fix scope
          <Input defaultValue={pref.agent_fix_scope ?? ""} disabled={ro} className="font-mono text-xs" placeholder="ANALYTICS.STAGING.*, DEV_AI_PLATFORM.CORE.LAB_*"
                 onBlur={(e) => e.target.value.trim() !== (pref.agent_fix_scope ?? "") && patch({ agent_fix_scope: e.target.value.trim() })} />
          <span className="block font-normal text-muted-foreground">Comma-separated DATABASE.SCHEMA.NAME patterns (* wildcards). The agent never touches anything else, and never the platform's own tables.</span>
        </label>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="space-y-1 text-xs font-medium">Minimum match score
            <Input type="number" min={0} max={1} step={0.05} defaultValue={pref.search_score_min} disabled={ro} className="text-xs"
                   onBlur={(e) => Number(e.target.value) !== pref.search_score_min && patch({ search_score_min: Number(e.target.value) })} />
            <span className="block font-normal text-muted-foreground">Below it a search hit is a miss.</span>
          </label>
          <label className="space-y-1 text-xs font-medium">Failures per claim
            <Input type="number" min={1} max={50} defaultValue={pref.max_parallel_tasks} disabled={ro} className="text-xs"
                   onBlur={(e) => Number(e.target.value) !== pref.max_parallel_tasks && patch({ max_parallel_tasks: Number(e.target.value) })} />
          </label>
          <label className="space-y-1 text-xs font-medium">Graph hops for the root cause
            <Input type="number" min={1} max={6} defaultValue={pref.graph_hops} disabled={ro} className="text-xs"
                   onBlur={(e) => Number(e.target.value) !== pref.graph_hops && patch({ graph_hops: Number(e.target.value) })} />
          </label>
          <label className="space-y-1 text-xs font-medium">Root-cause analysis
            <select value={pref.rca_mode} disabled={ro} onChange={(e) => patch({ rca_mode: e.target.value as Preference["rca_mode"] })}
                    className="h-9 w-full rounded-lg border bg-card px-2 text-xs">
              <option value="AGENT">RCA agent (grounded diagnosis if it fails)</option>
              <option value="GROUNDED">Grounded diagnosis only</option>
            </select>
          </label>
        </div>
      </Card>

      <Card title="Pipeline scope" hint="Only DAGs listed here are resolved. A task row wins over its DAG row; anything not listed is out of scope.">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted-foreground"><tr><th className="py-1 font-medium">DAG</th><th className="py-1 font-medium">Task</th><th className="py-1 font-medium">Mode</th><th /></tr></thead>
          <tbody className="divide-y">
            {scopes.length === 0 && <tr><td colSpan={4} className="py-2 text-xs text-muted-foreground">No pipeline is in scope yet.</td></tr>}
            {scopes.map((s) => (
              <tr key={s.scope_id}>
                <td className="py-1.5 font-medium">{s.dag_id}</td>
                <td className="py-1.5 text-xs">{s.task_id ?? <span className="text-muted-foreground">whole DAG</span>}</td>
                <td className="py-1.5">
                  <select value={s.enabled ? s.flow_mode : "DISABLED"} disabled={ro} aria-label={`Mode for ${s.dag_id} ${s.task_id ?? ""}`}
                          onChange={(e) => run(() => saveScope({ scope_id: s.scope_id, dag_id: s.dag_id, task_id: s.task_id,
                            flow_mode: e.target.value as FlowMode, enabled: e.target.value !== "DISABLED" }))}
                          className="h-8 rounded-lg border bg-card px-2 text-xs">
                    {FLOW_MODES.map((m) => <option key={m.value} value={m.value} title={m.hint}>{m.label}</option>)}
                  </select>
                </td>
                <td className="py-1.5 text-right">
                  {canEdit && <Button size="sm" variant="ghost" aria-label={`Remove ${s.dag_id} ${s.task_id ?? ""} from scope`} disabled={pending}
                                      onClick={() => run(() => deleteScope(s.scope_id))}><Trash2 className="h-3.5 w-3.5" /></Button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {canEdit && (
          <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => {
            e.preventDefault();
            if (scope.dag_id.trim()) run(() => saveScope({ dag_id: scope.dag_id.trim(), task_id: scope.task_id.trim() || null, flow_mode: scope.flow_mode,
              enabled: scope.flow_mode !== "DISABLED" }), () => setScope({ dag_id: "", task_id: "", flow_mode: "AUTO" }));
          }}>
            <Input value={scope.dag_id} onChange={(e) => setScope({ ...scope, dag_id: e.target.value })} placeholder="DAG id" aria-label="DAG id" className="w-56 text-xs" />
            <Input value={scope.task_id} onChange={(e) => setScope({ ...scope, task_id: e.target.value })} placeholder="Task id (optional)" aria-label="Task id" className="w-48 text-xs" />
            <select value={scope.flow_mode} onChange={(e) => setScope({ ...scope, flow_mode: e.target.value as FlowMode })} aria-label="Mode"
                    className="h-9 rounded-lg border bg-card px-2 text-xs">
              {FLOW_MODES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
            </select>
            <Button size="sm" type="submit" disabled={pending || !scope.dag_id.trim()}>
              {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}Add to scope
            </Button>
          </form>
        )}
        <p className="text-xs text-muted-foreground">{FLOW_MODES.map((m) => `${m.label}: ${m.hint.toLowerCase()}`).join(". ")}.</p>
      </Card>

      <Card title="Fix approvers" hint="Who approves a fix for a DAG or task. A task row wins over its DAG row; with no row, any holder of OPS.FIX_APPROVE decides.">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted-foreground"><tr><th className="py-1 font-medium">DAG</th><th className="py-1 font-medium">Task</th><th className="py-1 font-medium">Approver</th><th /></tr></thead>
          <tbody className="divide-y">
            {routes.length === 0 && <tr><td colSpan={4} className="py-2 text-xs text-muted-foreground">No approver routes: any fix approver decides.</td></tr>}
            {routes.map((r) => (
              <tr key={r.route_id}>
                <td className="py-1.5 font-medium">{r.dag_id}</td>
                <td className="py-1.5 text-xs">{r.task_id ?? <span className="text-muted-foreground">whole DAG</span>}</td>
                <td className="py-1.5 text-xs">{r.approver}</td>
                <td className="py-1.5 text-right">
                  {canEdit && <Button size="sm" variant="ghost" aria-label={`Remove approver for ${r.dag_id}`} disabled={pending}
                                      onClick={() => run(() => deleteRoute(r.route_id))}><Trash2 className="h-3.5 w-3.5" /></Button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {canEdit && (
          <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => {
            e.preventDefault();
            if (route.dag_id.trim() && route.approver.trim()) run(() => saveRoute({ dag_id: route.dag_id.trim(), task_id: route.task_id.trim() || null,
              approver: route.approver.trim() }), () => setRoute({ dag_id: "", task_id: "", approver: "" }));
          }}>
            <Input value={route.dag_id} onChange={(e) => setRoute({ ...route, dag_id: e.target.value })} placeholder="DAG id" aria-label="Approver DAG id" className="w-56 text-xs" />
            <Input value={route.task_id} onChange={(e) => setRoute({ ...route, task_id: e.target.value })} placeholder="Task id (optional)" aria-label="Approver task id" className="w-48 text-xs" />
            <Input value={route.approver} onChange={(e) => setRoute({ ...route, approver: e.target.value })} placeholder="Approver user name" aria-label="Approver" className="w-56 text-xs" />
            <Button size="sm" type="submit" disabled={pending || !route.dag_id.trim() || !route.approver.trim()}><Plus className="h-3.5 w-3.5" />Add approver</Button>
          </form>
        )}
      </Card>
    </div>
  );
}
