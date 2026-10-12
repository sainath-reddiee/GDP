"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { ExternalLink, GitMerge, GitPullRequest, Loader2, RefreshCw, Workflow, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { DmVersion } from "@/lib/model-types";
import { Button } from "@/components/ui/button";
import { Label, Select, Textarea } from "@/components/ui/input";
import { StatusPill, explain } from "@/components/hub/states";
import { useToast } from "@/components/ui/toast";
import { loadChanges, loadPublishing, mergeChange, publishVersion, rejectChange, syncPrs, type Publishing, type RunChange } from "../model-actions";

const PR_TONE: Record<string, string> = {
  OPEN: "bg-sky-100 text-sky-800", DRAFT: "bg-slate-100 text-slate-700", MERGED: "bg-violet-100 text-violet-800",
  CLOSED: "bg-rose-100 text-rose-700", FAILED: "bg-rose-100 text-rose-700", NO_CHANGES: "bg-slate-100 text-slate-700",
};

/** Approved versions to Git: open a pull request, follow its state (merged makes the version PUBLISHED). */
export function PublishPanel({ modelId, versions, canPublish }: { modelId: string; versions: DmVersion[]; canPublish: boolean }) {
  const router = useRouter();
  const toast = useToast();
  const [pub, setPub] = useState<Publishing | null>(null);
  const [repo, setRepo] = useState("");
  const [pending, start] = useTransition();
  const live = versions.filter((v) => v.status === "APPROVED" || v.status === "PUBLISHED" || v.pr_state);
  const approved = versions.find((v) => v.status === "APPROVED");

  useEffect(() => {
    (async () => {
      const r = await loadPublishing();
      if (r.ok) { setPub(r.data); setRepo(r.data.repos[0]?.repo_id ?? ""); }
      // pull request states are refreshed when the tab opens (throttled to once a minute on the server)
      if (versions.some((v) => v.pr_state === "OPEN" || v.pr_state === "DRAFT")) {
        const s = await syncPrs(modelId);
        if (s.ok && (s.data.merged.length || s.data.closed.length)) router.refresh();
      }
    })();
  }, [modelId]); // eslint-disable-line react-hooks/exhaustive-deps

  const publish = (v: DmVersion) => start(async () => {
    const r = await publishVersion(modelId, v.version, { repo_id: repo });
    if (!r.ok) { toast("error", explain(r.error).message); return; }
    if (r.data.status === "PUBLISHED") toast("ok", `Pull request opened for v${v.version}: ${r.data.head_branch}`);
    else if (r.data.status === "NO_CHANGES") toast("ok", "The repository already has this model; marked published");
    else toast("error", r.data.detail || `Publishing failed (${r.data.status})`);
    router.refresh();
  });
  const sync = () => start(async () => {
    const r = await syncPrs(modelId, true);
    if (r.ok) { toast("ok", r.data.detail || `${r.data.checked} pull request(s) checked`); router.refresh(); } else toast("error", explain(r.error).message);
  });

  return (
    <div className="space-y-4" data-testid="publish">
      <section className="surface space-y-3 p-4">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold"><GitPullRequest className="h-4 w-4" /> Publish to Git</h3>
        <p className="text-sm text-muted-foreground">Only an approved version is published: its domain pack, DDL, data dictionary and dbt tests go to <span className="font-mono">domain/&lt;domain&gt;/</span> on a branch with a pull request. The version becomes published when the pull request is merged.</p>
        {!pub ? <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" />Loading repositories...</p>
          : !pub.publisher_ready ? <p className="text-sm text-amber-700">GitHub publishing is not set up. An admin can set it up in Admin, Integrations (GitHub publishing).</p>
          : !pub.repos.length ? <p className="text-sm text-amber-700">No GitHub repository is connected. Connect one in Admin, Integrations.</p>
          : (
            <div className="flex flex-wrap items-end gap-2">
              <div><Label htmlFor="pub-repo">Repository</Label>
                <Select id="pub-repo" className="h-9 w-72" value={repo} onChange={(e) => setRepo(e.target.value)}>
                  {pub.repos.map((r) => <option key={r.repo_id} value={r.repo_id}>{r.name} ({r.branch})</option>)}</Select></div>
              {approved && canPublish && (!approved.pr_state || ["CLOSED", "FAILED"].includes(approved.pr_state)) && (
                <Button onClick={() => publish(approved)} disabled={pending || !repo} data-testid="publish-version">
                  {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitPullRequest className="h-4 w-4" />}
                  {approved.pr_state ? `Publish v${approved.version} again` : `Publish v${approved.version}`}</Button>
              )}
              <Button variant="outline" onClick={sync} disabled={pending}><RefreshCw className="h-4 w-4" /> Check pull requests</Button>
            </div>
          )}
      </section>
      <section className="surface overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/60 text-[11px] uppercase tracking-[0.06em] text-muted-foreground">
            <tr><th className="px-3 py-2 text-left">Version</th><th className="px-3 py-2 text-left">Status</th><th className="px-3 py-2 text-left">Pull request</th><th className="px-3 py-2 text-left">Branch</th></tr>
          </thead>
          <tbody>
            {live.map((v) => (
              <tr key={v.version} className="border-t border-border/60">
                <td className="px-3 py-2 font-medium">v{v.version}</td>
                <td className="px-3 py-2"><StatusPill status={v.status} /></td>
                <td className="px-3 py-2 text-xs">
                  {v.pr_state && <span className={cn("mr-2 rounded px-1.5 py-0.5 text-[10px] font-semibold", PR_TONE[v.pr_state] ?? "bg-muted")}>{v.pr_state.toLowerCase().replace(/_/g, " ")}</span>}
                  {v.pr_url ? <a className="inline-flex items-center gap-1 text-primary hover:underline" href={v.pr_url} target="_blank" rel="noreferrer">#{v.pr_number} <ExternalLink className="h-3 w-3" /></a> : v.pr_state ? null : <span className="text-muted-foreground">not published</span>}
                </td>
                <td className="px-3 py-2 font-mono text-xs">{v.branch ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!live.length && <p className="p-4 text-sm text-muted-foreground">No approved version yet.</p>}
      </section>
    </div>
  );
}

/** Proposals from runs (what a run's model design would change) and from approved Profiling Store annotations (a
 *  column's documentation, PII or semantic type), to merge into the draft or reject. */
export function ChangesPanel({ modelId, canEdit, openStatus }: { modelId: string; canEdit: boolean; openStatus: string | null }) {
  const router = useRouter();
  const toast = useToast();
  const [changes, setChanges] = useState<RunChange[] | null>(null);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [pending, start] = useTransition();
  const reload = async () => { const r = await loadChanges(modelId); setChanges(r.ok ? r.data.changes : []); };
  useEffect(() => { void reload(); }, [modelId]); // eslint-disable-line react-hooks/exhaustive-deps

  const merge = (c: RunChange) => start(async () => {
    const r = await mergeChange(modelId, c.change_id);
    if (r.ok) { toast("ok", `Merged into draft v${r.data.version}; review and submit it`); await reload(); router.refresh(); }
    else toast("error", explain(r.error).message);
  });
  const reject = (c: RunChange) => start(async () => {
    const r = await rejectChange(modelId, c.change_id, note);
    if (r.ok) { toast("ok", "Proposal rejected"); setRejecting(null); setNote(""); await reload(); } else toast("error", explain(r.error).message);
  });
  if (!changes) return <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" />Loading proposals...</p>;
  if (!changes.length) return <p className="surface p-6 text-center text-sm text-muted-foreground">No proposals. A run proposes its model design from its mapping step; an approved Profiling Store annotation proposes its column fields.</p>;
  return (
    <div className="space-y-2" data-testid="run-changes">
      {openStatus === "IN_REVIEW" && <p className="text-xs text-amber-700">A version is in review; withdraw or decide it before merging proposals.</p>}
      {changes.map((c) => (
        <article key={c.change_id} className="surface space-y-2 p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <Workflow className="h-4 w-4 text-muted-foreground" />
            {c.source === "ANNOTATION"
              ? <Link href="/dashboard/profiling?section=annotations" className="font-medium text-primary hover:underline">Profiling annotation{c.patch ? ` of ${c.patch.column}` : ""}</Link>
              : c.source === "TABLE_MODEL" && c.table_model_id
              ? <Link href={`/dashboard/modeling/tables/${c.table_model_id}?version=${c.table_model_version ?? ""}`} className="font-medium text-primary hover:underline">Table model</Link>
              : c.run_id ? <Link href={`/runs/${c.run_id}/mapping`} className="font-medium text-primary hover:underline">{c.run_name ?? c.run_id.slice(0, 8)}</Link> : <span>run</span>}
            <span className="font-mono text-xs">{c.target_entity}</span>
            <StatusPill status={c.status === "PROPOSED" ? "PENDING" : c.status} />
            <span className="text-xs text-muted-foreground">{c.source === "ANNOTATION" ? "approved column annotation" : c.source === "TABLE_MODEL" ? `table model v${c.table_model_version}` : `design v${c.run_design_version}`} · against model v{c.base_version ?? "none"} · {c.created_at?.slice(0, 16)} by {c.created_by}</span>
            {c.merged_into_version && <span className="text-xs text-muted-foreground">merged into v{c.merged_into_version}</span>}
          </div>
          {c.diff.counts && <p className="text-xs">{c.diff.counts.BREAKING ?? 0} breaking · {c.diff.counts.ADDITIVE ?? 0} additive · {c.diff.counts.NON_FUNCTIONAL ?? 0} documentation</p>}
          <ul className="max-h-32 overflow-auto text-xs text-muted-foreground">{(c.diff.changes ?? []).slice(0, 12).map((x, i) => <li key={i}>{x.message}</li>)}</ul>
          {canEdit && ["PROPOSED", "STALE"].includes(c.status) && (
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" onClick={() => merge(c)} disabled={pending || openStatus === "IN_REVIEW"} data-testid={`merge-${c.change_id}`}><GitMerge className="h-3.5 w-3.5" /> Merge into the draft</Button>
              {rejecting === c.change_id ? (
                <span className="flex flex-1 items-center gap-2">
                  <Textarea aria-label="Why" rows={1} className="min-h-0 flex-1 text-xs" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why it does not belong in the domain model" />
                  <Button size="sm" variant="outline" onClick={() => reject(c)} disabled={pending || note.trim().length < 3}>Reject</Button>
                </span>
              ) : <Button size="sm" variant="ghost" onClick={() => setRejecting(c.change_id)}><X className="h-3.5 w-3.5" /> Reject</Button>}
              {c.status === "STALE" && <span className="text-xs text-amber-700">Made against an older version; merging applies it on top of the current draft.</span>}
            </div>
          )}
        </article>
      ))}
    </div>
  );
}
