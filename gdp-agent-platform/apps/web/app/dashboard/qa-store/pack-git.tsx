"use client";

import { useEffect, useState, useTransition } from "react";
import { AlertTriangle, ExternalLink, FolderGit2, GitPullRequest, Loader2, RefreshCw } from "lucide-react";
import type { PackConflict, PackDetail } from "@/lib/store-types";
import { Button } from "@/components/ui/button";
import { Label, Select } from "@/components/ui/input";
import { StatusPill, explain } from "@/components/hub/states";
import { useToast } from "@/components/ui/toast";
import { cn } from "@/lib/utils";
import { loadGitRepos, publishPack, resolvePackSync, syncPackPrs } from "./actions";

const PR_TONE: Record<string, string> = {
  OPEN: "bg-sky-100 text-sky-800", MERGED: "bg-blue-100 text-blue-800", CLOSED: "bg-rose-100 text-rose-700",
  FAILED: "bg-rose-100 text-rose-700", NO_CHANGES: "bg-slate-100 text-slate-700",
};

/** The repository changed since the pack was edited in the app: show both and let someone decide. */
export function ConflictBanner({ packId, conflict, canEdit, onResolved }: {
  packId: string; conflict: PackConflict; canEdit: boolean; onResolved: (message: string) => void;
}) {
  const toast = useToast();
  const [pending, start] = useTransition();
  const d = conflict.diff;
  const resolve = (how: "take-repo" | "keep-app") => start(async () => {
    const r = await resolvePackSync(packId, conflict.sync.sync_id, how);
    if (!r.ok) { toast("error", explain(r.error).message); return; }
    onResolved(how === "take-repo" ? `The repository version is now draft v${r.data.draft_version}; submit it for approval`
      : "Kept the app version; publish it to Git to update the repository file");
  });
  return (
    <div className="space-y-2 rounded-xl border border-amber-300 bg-amber-50/80 p-3 text-sm text-amber-950 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100" data-testid="pack-conflict">
      <p className="flex items-center gap-1.5 font-medium"><AlertTriangle className="h-4 w-4" /> The repository file changed since this pack was edited in the app</p>
      <p className="text-xs">
        <span className="font-mono">{conflict.sync.repo_path}</span> no longer matches v{conflict.app.version}. The deploy did not overwrite the app&apos;s version.
        {" "}Repository compared with the app: {d.added.length} quer{d.added.length === 1 ? "y" : "ies"} only in the repository, {d.removed.length} only in the app, {d.changed.length} changed
        {d.changed.length ? ` (${d.changed.map((c) => c.query_name).join(", ")})` : ""}.
      </p>
      {canEdit && (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" disabled={pending} onClick={() => resolve("take-repo")} data-testid="take-repo">Take the repository version as a draft</Button>
          <Button size="sm" variant="outline" disabled={pending} onClick={() => resolve("keep-app")} data-testid="keep-app">Keep the app version</Button>
        </div>
      )}
    </div>
  );
}

/** Publish approved versions to Git and follow their pull requests. */
export function GitTab({ packId, detail, canEdit }: { packId: string; detail: PackDetail; canEdit: boolean }) {
  const toast = useToast();
  const [repos, setRepos] = useState<{ repo_id: string; name: string; branch: string }[] | null>(null);
  const [ready, setReady] = useState(true);
  const [repo, setRepo] = useState("");
  const [pending, start] = useTransition();
  const [versions, setVersions] = useState(detail.versions);
  useEffect(() => setVersions(detail.versions), [detail.versions]);
  useEffect(() => {
    void loadGitRepos().then((r) => { if (r.ok) { setRepos(r.data.repos); setReady(r.data.publisher_ready); setRepo(r.data.repos[0]?.repo_id ?? ""); } else setRepos([]); });
  }, []);
  const approved = versions.find((v) => v.status === "APPROVED");
  const publish = () => start(async () => {
    if (!approved) return;
    const r = await publishPack(packId, approved.version, repo);
    if (!r.ok) { toast("error", explain(r.error).message); return; }
    if (r.data.status === "PUBLISHED") {
      toast("ok", `Pull request opened for v${approved.version}: ${r.data.head_branch}`);
      setVersions((vs) => vs.map((v) => (v.version === approved.version ? { ...v, pr_state: "OPEN", branch: r.data.head_branch ?? null, repo_path: r.data.path ?? v.repo_path } : v)));
    } else toast(r.data.status === "NO_CHANGES" ? "ok" : "error", r.data.detail || (r.data.status === "NO_CHANGES" ? "The repository already has this version" : `Publishing failed (${r.data.status})`));
  });
  const check = () => start(async () => {
    const r = await syncPackPrs(packId, true);
    if (r.ok) toast("ok", r.data.detail || `${r.data.checked} pull request(s) checked${r.data.merged.length ? `, ${r.data.merged.length} merged` : ""}`);
    else toast("error", explain(r.error).message);
  });
  const path = versions.find((v) => v.repo_path)?.repo_path;
  return (
    <div className="space-y-4" data-testid="pack-git">
      <section className="surface space-y-3 p-4">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold"><GitPullRequest className="h-4 w-4" /> Publish to Git</h3>
        <p className="text-sm text-muted-foreground">
          The approved version goes to {path ? <span className="font-mono">{path}</span> : <span className="font-mono">domain/&lt;domain&gt;/qa/&lt;pack&gt;.sql</span>} on a branch with a pull request.
          Once merged and deployed, the pack is in step with its file; if the file changes again while the pack is edited here, the QA Store shows the conflict instead of overwriting either side.
        </p>
        {!repos ? <p className="text-sm text-muted-foreground"><Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" />Loading repositories...</p>
          : !ready ? <p className="text-sm text-amber-700">GitHub publishing is not set up. An admin can set it up in Admin, Integrations (GitHub publishing).</p>
          : !repos.length ? <p className="text-sm text-amber-700">No GitHub repository is connected. Connect one in Admin, Integrations.</p>
          : (
            <div className="flex flex-wrap items-end gap-2">
              <div><Label htmlFor="qa-repo">Repository</Label>
                <Select id="qa-repo" className="h-9 w-72" value={repo} onChange={(e) => setRepo(e.target.value)}>
                  {repos.map((r) => <option key={r.repo_id} value={r.repo_id}>{r.name} ({r.branch})</option>)}</Select></div>
              {canEdit && approved && (!approved.pr_state || ["CLOSED", "FAILED"].includes(approved.pr_state)) && (
                <Button onClick={publish} disabled={pending || !repo} data-testid="publish-pack">
                  {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitPullRequest className="h-4 w-4" />} Publish v{approved.version}
                </Button>
              )}
              <Button variant="outline" onClick={check} disabled={pending}><RefreshCw className="h-4 w-4" /> Check pull requests</Button>
            </div>
          )}
        {!approved && <p className="text-sm text-muted-foreground">Approve a version first.</p>}
      </section>
      <section className="surface overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/60 text-[11px] uppercase tracking-[0.06em] text-muted-foreground">
            <tr><th className="px-3 py-2 text-left">Version</th><th className="px-3 py-2 text-left">Status</th><th className="px-3 py-2 text-left">Origin</th><th className="px-3 py-2 text-left">Pull request</th></tr>
          </thead>
          <tbody>
            {versions.map((v) => (
              <tr key={v.version} className="border-t border-border/60">
                <td className="px-3 py-2 font-medium">v{v.version}</td>
                <td className="px-3 py-2"><StatusPill status={v.status} /></td>
                <td className="px-3 py-2 text-xs text-muted-foreground">{v.source === "REPO" ? <span className="inline-flex items-center gap-1"><FolderGit2 className="h-3.5 w-3.5" /> repository</span> : "app"}</td>
                <td className="px-3 py-2 text-xs">
                  {v.pr_state && <span className={cn("mr-2 rounded px-1.5 py-0.5 text-[10px] font-semibold", PR_TONE[v.pr_state] ?? "bg-muted")}>{v.pr_state.toLowerCase().replace(/_/g, " ")}</span>}
                  {v.pr_url ? <a className="inline-flex items-center gap-1 text-primary hover:underline" href={v.pr_url} target="_blank" rel="noreferrer">#{v.pr_number} <ExternalLink className="h-3 w-3" /></a> : v.branch ? <span className="font-mono">{v.branch}</span> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
