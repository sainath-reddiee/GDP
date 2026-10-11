"use client";

import { useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ExternalLink, FileText, GitBranch, Loader2, RefreshCw, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { explain } from "@/components/hub/states";
import { cn } from "@/lib/utils";
import { syncPullRequests } from "./actions";

export type PullRequestRow = {
  kind: string; label: string; asset_id: string; version: number; asset_name: string | null; domain_id: string | null;
  domain_name: string | null; repo_name: string | null; git_url: string | null; branch: string | null; base_branch: string | null;
  pr_number: number | null; pr_url: string | null; state: string | null; merged_sha: string | null; title: string | null;
  files: string[]; detail: string | null; opened_by: string | null; opened_at: string | null; synced_at: string | null; href: string;
};

const STATE: Record<string, { label: string; tone: string }> = {
  OPEN: { label: "open", tone: "bg-sky-500/10 text-sky-700 ring-sky-500/20 dark:text-sky-300" },
  DRAFT: { label: "draft", tone: "bg-slate-500/10 text-slate-700 ring-slate-500/20 dark:text-slate-300" },
  MERGED: { label: "merged", tone: "bg-violet-500/10 text-violet-700 ring-violet-500/20 dark:text-violet-300" },
  CLOSED: { label: "closed without merging", tone: "bg-amber-500/10 text-amber-700 ring-amber-500/20 dark:text-amber-300" },
  FAILED: { label: "publish failed", tone: "bg-rose-500/10 text-rose-700 ring-rose-500/20 dark:text-rose-300" },
  NO_CHANGES: { label: "already in repository", tone: "bg-emerald-500/10 text-emerald-700 ring-emerald-500/20 dark:text-emerald-300" },
};

const STATES = [["", "Any state"], ["open", "Open"], ["merged", "Merged"], ["attention", "Needs attention"], ["NO_CHANGES", "Already in repository"]];

function StateBadge({ state }: { state: string | null }) {
  const s = STATE[state ?? ""] ?? { label: (state ?? "unknown").toLowerCase(), tone: "bg-muted text-muted-foreground ring-border" };
  return <span className={cn("inline-flex rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset", s.tone)}>{s.label}</span>;
}

function next(row: PullRequestRow): string | null {
  if (row.state === "CLOSED") return "Closed in GitHub without merging: the version is still approved; publish it again from its Store page.";
  if (row.state === "FAILED") return row.detail ? `Publishing failed: ${row.detail}` : "Publishing failed; try again from its Store page.";
  return null;
}

export function SourceControlView({ prs, kinds, publisherReady, filters }: {
  prs: PullRequestRow[]; kinds: Record<string, string>; publisherReady: boolean;
  filters: { kind: string; state: string; q: string; domain: string };
}) {
  const router = useRouter();
  const toast = useToast();
  const [busy, start] = useTransition();
  const go = (patch: Partial<typeof filters>) => {
    const p = new URLSearchParams(Object.entries({ ...filters, ...patch }).filter(([, v]) => !!v) as [string, string][]);
    router.push(`/dashboard/source-control${p.size ? `?${p}` : ""}`);
  };
  const check = () => start(async () => {
    const r = await syncPullRequests(filters.kind || undefined);
    if (!r.ok) { toast("error", explain(r.error).message); return; }
    const d = r.data;
    toast(d.errors.length ? "error" : "ok", d.detail || [
      `${d.checked} pull request(s) checked`, d.merged.length ? `${d.merged.length} merged` : "",
      d.closed.length ? `${d.closed.length} closed` : "", d.errors.length ? `${d.errors.length} could not be read` : ""].filter(Boolean).join(", "));
    router.refresh();
  });
  const domains = Array.from(new Map(prs.filter((p) => p.domain_id).map((p) => [p.domain_id!, p.domain_name ?? p.domain_id!])).entries());

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1" role="tablist" aria-label="Asset kind">
          {[["", "All assets"], ...Object.entries(kinds)].map(([k, label]) => (
            <button key={k || "all"} type="button" role="tab" aria-selected={filters.kind === k} onClick={() => go({ kind: k })}
                    className={cn("rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset transition",
                                  filters.kind === k ? "bg-primary text-primary-foreground ring-primary" : "bg-card text-muted-foreground ring-border hover:text-foreground")}>
              {label}
            </button>
          ))}
        </div>
        <Select aria-label="State" value={filters.state} onChange={(e) => go({ state: e.target.value })} className="h-8 w-auto text-xs">
          {STATES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </Select>
        {domains.length > 1 && (
          <Select aria-label="Domain" value={filters.domain} onChange={(e) => go({ domain: e.target.value })} className="h-8 w-auto text-xs">
            <option value="">Any domain</option>
            {domains.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </Select>
        )}
        <form className="relative" onSubmit={(e) => { e.preventDefault(); go({ q: String(new FormData(e.currentTarget).get("q") ?? "") }); }}>
          <Search className="pointer-events-none absolute left-2 top-2 h-4 w-4 text-muted-foreground" />
          <Input name="q" defaultValue={filters.q} placeholder="Asset, title or branch" aria-label="Search pull requests" className="h-8 w-56 pl-8 text-xs" />
        </form>
        <span className="flex-1" />
        <Button size="sm" variant="outline" disabled={busy || !publisherReady} onClick={check}
                title={publisherReady ? "Read every open pull request's state from GitHub" : "GitHub publishing is not set up"}>
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}Check all now
        </Button>
      </div>
      {!publisherReady && (
        <p className="rounded-lg bg-warning/10 px-3 py-2 text-xs text-warning">
          GitHub publishing is not set up, so pull request state cannot be read. An admin can set it up in Admin, Integrations.
        </p>
      )}
      {!prs.length ? (
        <div className="surface px-6 py-12 text-center text-sm text-muted-foreground" data-testid="pr-empty">
          {Object.values(filters).some(Boolean) ? "No pull request matches these filters."
            : "No pull requests yet. Approve an asset version in its Store, then publish it to Git."}
        </div>
      ) : (
        <ul className="space-y-2" data-testid="pr-list">
          {prs.map((p) => {
            const hint = next(p);
            return (
              <li key={`${p.kind}-${p.asset_id}-${p.version}`} data-testid={`pr-${p.kind}-${p.version}`}
                  className={cn("rounded-xl border border-l-4 bg-card px-4 py-3",
                                p.state === "MERGED" ? "border-l-violet-500" : p.state === "FAILED" || p.state === "CLOSED" ? "border-l-rose-500"
                                  : p.state === "NO_CHANGES" ? "border-l-emerald-500" : "border-l-sky-500")}>
                <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
                  <div className="min-w-0 flex-1">
                    <p className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{p.label}</span>
                      <Link href={p.href} className="font-medium hover:underline">{p.asset_name || p.asset_id}</Link>
                      <span className="text-xs text-muted-foreground">v{p.version}</span>
                      <StateBadge state={p.state} />
                    </p>
                    <p className="mt-1 truncate text-xs text-muted-foreground">{p.title}</p>
                    <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
                      {p.branch && <span className="inline-flex items-center gap-1 font-mono"><GitBranch className="h-3 w-3" />{p.branch}{p.base_branch ? ` → ${p.base_branch}` : ""}</span>}
                      {p.repo_name && <span>{p.repo_name}</span>}
                      {p.domain_name && <span>{p.domain_name}</span>}
                      {p.opened_at && <span>opened {p.opened_at.slice(0, 16)}{p.opened_by ? ` by ${p.opened_by}` : ""}</span>}
                      {p.synced_at && <span>checked {p.synced_at.slice(0, 16)}</span>}
                      {p.merged_sha && <span className="font-mono">merged {p.merged_sha.slice(0, 7)}</span>}
                    </p>
                    {p.files.length > 0 && (
                      <details className="mt-1 text-[11px]">
                        <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                          <FileText className="mr-1 inline h-3 w-3" />{p.files.length} file{p.files.length === 1 ? "" : "s"}
                        </summary>
                        <ul className="mt-1 space-y-0.5 font-mono">{p.files.map((f) => <li key={f}>{f}</li>)}</ul>
                      </details>
                    )}
                    {hint && <p className="mt-1 text-xs text-rose-700 dark:text-rose-300">{hint}</p>}
                  </div>
                  {p.pr_url && (
                    <a href={p.pr_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
                      #{p.pr_number}<ExternalLink className="h-3 w-3" />
                    </a>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
