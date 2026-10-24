"use client";

import { useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ExternalLink, GitPullRequest, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label, Select } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { StatusPill, explain } from "@/components/hub/states";
import type { PullRequestRow } from "../../source-control/source-control-view";
import { syncPullRequests } from "../../source-control/actions";
import { publishSttm } from "../actions";

/** Publish an approved mapping version to Git (sttm/<domain>/<target>.csv and its table design) and follow its pull
 * requests; every pull request is also listed on the Source control page. */
export function SttmGit({ catalogId, version, status, canEdit, prs, repos, publisherReady }: {
  catalogId: string; version: number; status: string; canEdit: boolean; prs: PullRequestRow[];
  repos: { repo_id: string; name: string; branch: string | null }[]; publisherReady: boolean;
}) {
  const router = useRouter();
  const toast = useToast();
  const [repo, setRepo] = useState(repos[0]?.repo_id ?? "");
  const [busy, start] = useTransition();
  const approved = status === "APPROVED";
  const mine = prs.find((p) => p.version === version);
  const publish = () => start(async () => {
    const r = await publishSttm(catalogId, version, repo);
    if (!r.ok) { toast("error", explain(r.error).message); return; }
    if (r.data.status === "PUBLISHED") toast("ok", `Pull request opened for v${version}: ${r.data.head_branch}`);
    else toast(r.data.status === "NO_CHANGES" ? "ok" : "error",
               r.data.detail || (r.data.status === "NO_CHANGES" ? "The repository already has this version" : `Publishing failed (${r.data.status})`));
    router.refresh();
  });
  const check = () => start(async () => {
    const r = await syncPullRequests("STTM");
    if (r.ok) toast("ok", r.data.detail || `${r.data.checked} pull request(s) checked${r.data.merged.length ? `, ${r.data.merged.length} merged` : ""}`);
    else toast("error", explain(r.error).message);
    router.refresh();
  });
  return (
    <section className="surface space-y-3 p-4" data-testid="sttm-git">
      <div className="flex flex-wrap items-center gap-2">
        <GitPullRequest className="h-4 w-4 text-blue-600" />
        <h2 className="text-sm font-semibold">Git</h2>
        <span className="text-xs text-muted-foreground">An approved version is published as a pull request; the repository&apos;s owners merge it.</span>
        <span className="flex-1" />
        <Link href={`/os/release/source-control?kind=STTM`} className="text-xs text-primary hover:underline">Source control</Link>
      </div>
      {canEdit && approved && (
        <div className="flex flex-wrap items-end gap-2">
          <div className="space-y-1">
            <Label htmlFor="sttm-repo">Repository</Label>
            <Select id="sttm-repo" value={repo} onChange={(e) => setRepo(e.target.value)} className="h-8 w-56 text-xs" disabled={!repos.length}>
              {!repos.length && <option value="">No GitHub repository connected</option>}
              {repos.map((r) => <option key={r.repo_id} value={r.repo_id}>{r.name}{r.branch ? ` (${r.branch})` : ""}</option>)}
            </Select>
          </div>
          <Button size="sm" disabled={busy || !repo || !publisherReady} onClick={publish} data-testid="publish-sttm">
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <GitPullRequest className="h-3.5 w-3.5" />}
            {mine && mine.state !== "FAILED" && mine.state !== "CLOSED" ? `Publish v${version} again` : `Publish v${version} to Git`}
          </Button>
          {prs.some((p) => p.state === "OPEN" || p.state === "DRAFT") && (
            <Button size="sm" variant="outline" disabled={busy || !publisherReady} onClick={check}>
              <RefreshCw className="h-3.5 w-3.5" />Check pull requests
            </Button>
          )}
          {!publisherReady && <p className="text-xs text-warning">GitHub publishing is not set up (Admin, Integrations).</p>}
        </div>
      )}
      {!approved && <p className="text-xs text-muted-foreground">v{version} is {status.toLowerCase()}; only an approved version is published.</p>}
      {prs.length > 0 ? (
        <ul className="divide-y rounded-lg border text-xs">
          {prs.map((p) => (
            <li key={p.version} className="flex flex-wrap items-center gap-2 px-3 py-2">
              <span className="font-medium">v{p.version}</span>
              <StatusPill status={p.state ?? "UNKNOWN"} />
              <span className="font-mono text-muted-foreground">{p.branch}</span>
              {p.state === "FAILED" && p.detail && <span className="text-destructive">{p.detail}</span>}
              <span className="flex-1" />
              {p.pr_url && <a href={p.pr_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-primary hover:underline">#{p.pr_number}<ExternalLink className="h-3 w-3" /></a>}
            </li>
          ))}
        </ul>
      ) : <p className="text-xs text-muted-foreground">No pull request yet for this mapping.</p>}
    </section>
  );
}
