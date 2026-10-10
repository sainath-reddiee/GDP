"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  CalendarClock, Check, ChevronDown, CircleCheck, CircleDashed, ExternalLink, FileCode2, FolderGit2, GitBranch, GitPullRequest,
  Github, History, KeyRound, Loader2, Plus, RefreshCw, Search, Settings2, Siren, SlidersHorizontal, Trash2, TriangleAlert, Unplug, Workflow, X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { useAccess } from "@/components/access";
import { useScrollLock } from "@/components/use-scroll-lock";
import { cn } from "@/lib/utils";
import { ago } from "../skills/types";
import {
  checkPublishing, connectRepo, indexRuns, loadSetup, refreshRepo, removeRepo, repoBranches, rotatePublishingToken,
  scheduleRepo, setCredentials, setupPublishing, updateRepo,
  type CodeRepo, type CodeSetup, type IndexRun, type PublishingStatus, type RepoBranch,
} from "../code/actions";
import type { JiraStatus } from "../jira/actions";
import type { AirflowEnv } from "../ops/actions";
import { AirflowTab } from "./airflow-tab";
import { IncidentsTab } from "./incidents-tab";
import { JiraTab } from "./jira-tab";

type Domain = { domain_id: string; domain_name: string };
export type IntegrationView = "repos" | "jira" | "airflow" | "incidents";
const PRESETS = [
  { label: "Every hour", cron: "0 * * * * UTC" }, { label: "Daily 06:00 UTC", cron: "0 6 * * * UTC" },
  { label: "Weekdays 06:00 UTC", cron: "0 6 * * MON-FRI UTC" }, { label: "Weekly, Monday 06:00 UTC", cron: "0 6 * * MON UTC" },
];
const STATUS_TONE: Record<string, string> = {
  READY: "bg-emerald-50 text-emerald-700 ring-emerald-100", INDEXING: "bg-indigo-50 text-indigo-700 ring-indigo-100",
  FAILED: "bg-rose-50 text-rose-700 ring-rose-100", NEW: "bg-slate-100 text-slate-600 ring-slate-200",
  CANCELLED: "bg-amber-50 text-amber-700 ring-amber-100",
};
const POLL_MS = 5000;
const POLL_LIMIT_MS = 20 * 60 * 1000; // stop polling a run that never reports back; Refresh still works
type Msg = { tone: "ok" | "info" | "error"; text: string } | null;
const toneOf = (e: string): "info" | "error" => (/approval|request/i.test(e) ? "info" : "error");
/** "Branch 'x' is not in ... Available: a, b" from the API, as a list to click. */
const suggestedBranches = (error: string) => (error.match(/Available: (.+)$/)?.[1] ?? "").split(", ").filter((b) => b && b !== "none");

let inflight = 0; // server actions still waiting; polling holds off so a refresh never aborts one

/** useTransition whose actions the list polling waits for. */
function useTrackedTransition(): [boolean, (fn: () => Promise<void>) => void] {
  const [pending, startTransition] = useTransition();
  return [pending, (fn) => {
    inflight += 1;
    startTransition(async () => {
      try { await fn(); } finally { inflight = Math.max(0, inflight - 1); }
    });
  }];
}

function Badge({ tone, children }: { tone: "good" | "warn" | "idle" | "bad"; children: React.ReactNode }) {
  return (
    <span className={cn("shrink-0 whitespace-nowrap rounded-full px-1.5 py-0.5 text-[10px] font-medium ring-1 ring-inset",
      tone === "good" ? "bg-emerald-50 text-emerald-700 ring-emerald-100" : tone === "warn" ? "bg-amber-50 text-amber-700 ring-amber-100"
        : tone === "bad" ? "bg-rose-50 text-rose-700 ring-rose-100" : "bg-slate-100 text-slate-600 ring-slate-200")}>{children}</span>
  );
}

/** Admin, Integrations: one tab per integration. Code repositories (configured once, used by the dbt workspace and every
 *  AI step), dbt publishing to GitHub, Jira, and Airflow (Amazon MWAA) for Pipelines. */
export function IntegrationsSection({ repos, domains, publishing, jira, airflow, airflowError, view }: {
  repos: CodeRepo[]; domains: Domain[]; publishing: PublishingStatus; jira: JiraStatus | null; airflow: AirflowEnv[] | null;
  airflowError: string | null; view: IntegrationView;
}) {
  const router = useRouter();
  const { can, canAct } = useAccess();
  const [msg, setMsg] = useState<Msg>(null);
  const indexing = repos.some((r) => r.refreshing || r.status === "INDEXING");
  const pollStart = useRef<number | null>(null);
  useEffect(() => {
    if (!indexing) { pollStart.current = null; return; }
    pollStart.current ??= Date.now();
    const t = setInterval(() => {
      if (Date.now() - (pollStart.current ?? 0) > POLL_LIMIT_MS) { clearInterval(t); return; }
      if (inflight === 0) router.refresh();
    }, POLL_MS);
    return () => clearInterval(t);
  }, [indexing, router]);
  const failing = repos.filter((r) => r.status === "FAILED").length;
  const airflowFailing = (airflow ?? []).filter((e) => e.enabled && e.last_error).length;
  const tabs: { id: IntegrationView; label: string; icon: typeof FolderGit2; badge: React.ReactNode; hint: string }[] = [
    { id: "repos", label: "Code repositories", icon: FolderGit2, hint: "dbt and SQL repositories, and pull requests to GitHub",
      badge: failing ? <Badge tone="bad">{failing} failing</Badge> : <Badge tone={repos.length ? "good" : "idle"}>{repos.length}</Badge> },
    { id: "jira", label: "Jira", icon: Unplug, hint: "QA issues, reproduction and results posted back",
      badge: <Badge tone={jira?.ready ? "good" : "idle"}>{!jira?.installed ? "not installed" : jira.ready ? `${jira.users_connected ?? 0} connected` : "setup needed"}</Badge> },
    { id: "airflow", label: "Airflow", icon: Workflow, hint: "Amazon MWAA DAGs, runs and logs for Pipelines",
      badge: airflow === null ? <Badge tone="idle">not installed</Badge>
        : airflowFailing ? <Badge tone="bad">{airflowFailing} failing</Badge>
          : <Badge tone={airflow.length ? "good" : "idle"}>{airflow.length}</Badge> },
    { id: "incidents", label: "Incidents", icon: Siren, hint: "Teams, routing rules, Teams channels and escalation",
      badge: airflow === null ? <Badge tone="idle">not installed</Badge> : null },
  ];
  return (
    <div className="space-y-4">
      <nav aria-label="Integrations" className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
        {tabs.map((t) => (
          <Link key={t.id} href={`/admin?section=integrations&view=${t.id}`} aria-current={view === t.id ? "page" : undefined}
                className={cn("flex items-start gap-3 rounded-xl border p-3 transition",
                  view === t.id ? "border-primary/40 bg-primary/5 shadow-card" : "bg-card hover:border-primary/30 hover:shadow-card")}>
            <span className={cn("grid h-9 w-9 shrink-0 place-items-center rounded-lg", view === t.id ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>
              <t.icon className="h-4 w-4" /></span>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-semibold"><span className="whitespace-nowrap">{t.label}</span>{t.badge}</span>
              <span className="block truncate text-xs text-muted-foreground">{t.hint}</span>
            </span>
          </Link>
        ))}
      </nav>
      {msg && (
        <p role={msg.tone === "error" ? "alert" : "status"} className={cn("flex items-start gap-2 rounded-lg px-3 py-2 text-xs",
          msg.tone === "error" ? "bg-destructive/10 text-destructive" : msg.tone === "info" ? "bg-sky-50 text-sky-800" : "bg-success/10 text-success")}>
          <span className="flex-1">{msg.text}</span>
          <button type="button" aria-label="Dismiss" onClick={() => setMsg(null)}><X className="h-3.5 w-3.5" /></button>
        </p>
      )}
      {view === "repos" && <ReposTab repos={repos} domains={domains} may={canAct("INTEGRATION.MANAGE")} onMsg={setMsg}
                                     publishing={<PublishingStrip status={publishing} githubRepos={repos.filter((r) => r.provider === "GITHUB")} may={canAct("ADMIN.DEPLOY")} onMsg={setMsg} />} />}
      {view === "jira" && <JiraTab status={jira} may={canAct("INTEGRATION.MANAGE")} onMsg={setMsg} />}
      {view === "incidents" && <IncidentsTab envs={airflow ?? []} may={canAct("INTEGRATION.MANAGE")} onMsg={setMsg} />}
      {view === "airflow" && <AirflowTab envs={airflow} error={airflowError} may={canAct("INTEGRATION.MANAGE")} canPoll={can("OPS.OPERATE")} onMsg={setMsg} />}
    </div>
  );
}

function ReposTab({ repos, domains, may, onMsg, publishing }: {
  repos: CodeRepo[]; domains: Domain[]; may: boolean; onMsg: (m: Msg) => void; publishing: React.ReactNode;
}) {
  const router = useRouter();
  const [connecting, setConnecting] = useState(false);
  const [open, setOpen] = useState<{ id: string; tab: DrawerTab } | null>(null);
  const [filter, setFilter] = useState("");
  const shown = repos.filter((r) => !filter || `${r.name} ${r.git_url} ${r.branch}`.toLowerCase().includes(filter.toLowerCase()));
  const files = repos.reduce((n, r) => n + (r.stats?.files ?? 0), 0);
  const selected = repos.find((r) => r.repo_id === open?.id);
  return (
    <section className="surface overflow-hidden">
      <header className="flex flex-wrap items-center gap-3 border-b px-5 py-4">
        <div className="min-w-[16rem] flex-1">
          <h3 className="text-base font-semibold">Code repositories</h3>
          <p className="text-xs text-muted-foreground">{repos.length} connected · {files.toLocaleString()} files indexed · used by the dbt workspace and every AI step.
            {" "}<Link href="/code" className="text-primary hover:underline">Browse code</Link></p>
        </div>
        {repos.length > 3 && (
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter" className="h-9 w-48 pl-8 text-xs" aria-label="Filter repositories" />
          </div>
        )}
        {may && <Button onClick={() => setConnecting(true)}><Plus className="h-4 w-4" />Connect repository</Button>}
      </header>
      {publishing}
      {repos.length ? (
        <ul className="divide-y">{shown.map((r) => <RepoRow key={r.repo_id} repo={r} domains={domains} may={may} onMsg={onMsg} onOpen={(tab) => setOpen({ id: r.repo_id, tab })} />)}</ul>
      ) : (
        <div className="px-6 py-12 text-center">
          <span className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-indigo-50 text-indigo-600 ring-1 ring-inset ring-indigo-100"><FolderGit2 className="h-6 w-6" /></span>
          <p className="mt-3 text-sm font-semibold">No repositories connected yet</p>
          <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground">GitHub, GitLab, Bitbucket and Azure DevOps work through Snowflake&apos;s Git integration.
            Credentials stay in a Snowflake secret; the platform never keeps a token.</p>
          {may && <Button className="mt-4" onClick={() => setConnecting(true)}><Plus className="h-4 w-4" />Connect repository</Button>}
        </div>
      )}
      {connecting && <ConnectDrawer domains={domains} onClose={() => setConnecting(false)}
                                    onDone={(text) => { setConnecting(false); onMsg({ tone: "ok", text }); router.refresh(); }} />}
      {selected && open && <RepoDrawer repo={selected} domains={domains} may={may} tab={open.tab} onTab={(tab) => setOpen({ id: selected.repo_id, tab })}
                                       onClose={() => setOpen(null)} onMsg={onMsg} />}
    </section>
  );
}

function RepoRow({ repo: r, domains, may, onMsg, onOpen }: {
  repo: CodeRepo; domains: Domain[]; may: boolean; onMsg: (m: Msg) => void; onOpen: (tab: DrawerTab) => void;
}) {
  const router = useRouter();
  const [pending, start] = useTrackedTransition();
  const busy = r.refreshing || r.status === "INDEXING";
  const refresh = () => start(async () => {
    const res = await refreshRepo(r.repo_id);
    onMsg(res.ok ? { tone: "ok", text: `${r.name}: refresh started; only changed files are read.` } : { tone: toneOf(res.error), text: res.error });
    if (res.ok) router.refresh();
  });
  const models = r.stats?.by_kind?.DBT_MODEL ?? 0;
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-3 px-5 py-4">
      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-700">{r.provider === "GITHUB" ? <Github className="h-5 w-5" /> : <GitBranch className="h-5 w-5" />}</span>
      <div className="min-w-[16rem] flex-1">
        <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">{r.name}
          <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ring-inset", STATUS_TONE[busy ? "INDEXING" : r.status] ?? STATUS_TONE.NEW)}>
            {busy ? "indexing" : r.status.toLowerCase()}</span>
          {!r.enabled && <Badge tone="idle">disabled</Badge>}
          {r.use_for_dbt !== false && (r.stats?.dbt_projects ?? []).length > 0 && <Badge tone="good">dbt workspace</Badge>}
        </p>
        <a href={r.git_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono text-[11px] text-muted-foreground hover:text-primary">
          {r.git_url.replace(/^https:\/\//, "")}<ExternalLink className="h-3 w-3" /></a>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          <button type="button" disabled={!may} onClick={() => onOpen("branch")} title={may ? "Switch branch" : undefined}
                  className={cn("inline-flex max-w-[22rem] items-center gap-1 rounded-md border bg-card px-1.5 py-0.5 font-mono text-[11px] text-foreground", may && "hover:border-primary/40 hover:bg-primary/5")}>
            <GitBranch className="h-3 w-3 shrink-0" /><span className="truncate">{r.branch}</span>{may && <ChevronDown className="h-3 w-3 shrink-0" />}
          </button>
          {r.last_commit && <span className="font-mono">{r.last_commit.slice(0, 8)}</span>}
          <span>{r.last_indexed ? `indexed ${ago(r.last_indexed)}` : "not indexed yet"}</span>
          <span className="inline-flex items-center gap-1"><CalendarClock className="h-3 w-3" />{r.schedule_cron ?? "on demand"}</span>
          <span>{r.domain_ids.length ? r.domain_ids.map((d) => domains.find((x) => x.domain_id === d)?.domain_name ?? d).join(", ") : "all domains"}</span>
        </div>
        {r.status === "FAILED" && r.error && <p className="mt-1.5 flex items-start gap-1 text-xs text-destructive"><TriangleAlert className="mt-0.5 h-3 w-3 shrink-0" />{r.error.slice(0, 300)}</p>}
        {!!r.stats?.pending_files && <p className="mt-1 text-xs text-amber-700">{r.stats.pending_files.toLocaleString()} changed files wait for the next refresh.</p>}
      </div>
      <dl className="grid grid-cols-3 gap-2 text-center text-xs">
        {[["files", r.stats?.files], ["models", models], ["links", r.stats?.edges]].map(([k, v]) => (
          <div key={String(k)} className="min-w-[4rem] rounded-lg bg-muted/50 px-2.5 py-1.5"><dd className="text-sm font-semibold tabular-nums">{Number(v ?? 0).toLocaleString()}</dd><dt className="text-[10px] text-muted-foreground">{k}</dt></div>
        ))}
      </dl>
      <div className="flex shrink-0 items-center gap-1.5">
        <Button size="sm" variant="outline" disabled={pending || busy} onClick={refresh}>
          {busy || pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}{busy ? "Indexing" : "Refresh"}</Button>
        <Link href={`/code?repo=${r.repo_id}`} className="inline-flex h-8 items-center gap-1.5 rounded-lg border bg-card px-3 text-xs font-medium hover:bg-muted"><FileCode2 className="h-3.5 w-3.5" />Browse</Link>
        <Button size="sm" variant="ghost" onClick={() => onOpen(may ? "indexing" : "history")} aria-label={`Settings for ${r.name}`}><Settings2 className="h-4 w-4" />{may ? "Settings" : "History"}</Button>
      </div>
    </li>
  );
}

type DrawerTab = "branch" | "indexing" | "schedule" | "dbt" | "credentials" | "history" | "disconnect";
const DRAWER_TABS: { id: DrawerTab; label: string; icon: typeof GitBranch; manage?: boolean }[] = [
  { id: "branch", label: "Branch", icon: GitBranch, manage: true }, { id: "indexing", label: "Indexing", icon: SlidersHorizontal, manage: true },
  { id: "schedule", label: "Schedule", icon: CalendarClock, manage: true }, { id: "dbt", label: "dbt workspace", icon: GitPullRequest, manage: true },
  { id: "credentials", label: "Credentials", icon: KeyRound, manage: true }, { id: "history", label: "History", icon: History },
  { id: "disconnect", label: "Disconnect", icon: Trash2, manage: true },
];

/** Everything about one repository, one tab at a time: branch, what is indexed, schedule, dbt workspace, credentials,
 *  refresh history and disconnect. */
function RepoDrawer({ repo: r, domains, may, tab, onTab, onClose, onMsg }: {
  repo: CodeRepo; domains: Domain[]; may: boolean; tab: DrawerTab; onTab: (t: DrawerTab) => void; onClose: () => void; onMsg: (m: Msg) => void;
}) {
  useScrollLock();
  const router = useRouter();
  const [pending, start] = useTrackedTransition();
  const [runs, setRuns] = useState<IndexRun[] | null>(null);
  const [cron, setCron] = useState(r.schedule_cron ?? PRESETS[1].cron);
  const [doms, setDoms] = useState<string[]>(r.domain_ids);
  const [include, setInclude] = useState(r.include_globs.join(", "));
  const [exclude, setExclude] = useState(r.exclude_globs.join(", "));
  const saved = `${r.domain_ids.join()}|${r.include_globs.join()}|${r.exclude_globs.join()}|${r.schedule_cron}`;
  useEffect(() => {
    setDoms(r.domain_ids); setInclude(r.include_globs.join(", ")); setExclude(r.exclude_globs.join(", "));
    setCron(r.schedule_cron ?? PRESETS[1].cron);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saved]);
  useEffect(() => {
    if (tab === "history") indexRuns(r.repo_id).then((x) => setRuns(x.ok ? x.data.runs : []));
  }, [tab, r.repo_id, r.last_indexed]);
  const act = (fn: () => Promise<{ ok: boolean; error?: string }>, ok: string, after?: () => void) => start(async () => {
    const res = await fn();
    onMsg(res.ok ? { tone: "ok", text: ok } : { tone: toneOf(res.error ?? ""), text: res.error ?? "Failed" });
    if (res.ok) { after?.(); router.refresh(); }
  });
  const split = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
  const tabs = DRAWER_TABS.filter((t) => may || !t.manage);
  const current = tabs.some((t) => t.id === tab) ? tab : tabs[0].id;
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={`${r.name} settings`}>
      <button type="button" aria-label="Close" className="absolute inset-0 bg-black/30" onClick={onClose} />
      <aside className="relative flex h-full w-[720px] max-w-full flex-col border-l bg-background shadow-2xl">
        <header className="flex items-center gap-3 border-b px-5 py-4">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-slate-100 text-slate-700">{r.provider === "GITHUB" ? <Github className="h-4 w-4" /> : <GitBranch className="h-4 w-4" />}</span>
          <div className="min-w-0 flex-1">
            <h3 className="flex items-center gap-2 text-base font-semibold">{r.name}
              <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ring-inset", STATUS_TONE[r.status] ?? STATUS_TONE.NEW)}>{r.status.toLowerCase()}</span></h3>
            <p className="truncate font-mono text-[11px] text-muted-foreground">{r.git_url.replace(/^https:\/\//, "")} · {r.branch}</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded-md p-1 hover:bg-muted"><X className="h-4 w-4" /></button>
        </header>
        <nav className="flex gap-1 overflow-x-auto border-b px-3" aria-label="Repository settings">
          {tabs.map((t) => (
            <button key={t.id} type="button" onClick={() => onTab(t.id)} aria-current={current === t.id ? "page" : undefined}
                    className={cn("-mb-px inline-flex shrink-0 items-center gap-1.5 border-b-2 px-2.5 py-2.5 text-xs",
                      current === t.id ? "border-primary font-medium text-primary" : "border-transparent text-muted-foreground hover:text-foreground",
                      t.id === "disconnect" && current !== t.id && "hover:text-destructive")}>
              <t.icon className="h-3.5 w-3.5" />{t.label}
            </button>
          ))}
        </nav>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain [&>div]:border-t-0 [&>div]:bg-transparent [&>div]:px-5 [&>div]:py-4">
          {current === "branch" && (
            <BranchPicker repo={r} pending={pending}
                          onPick={(b) => act(() => updateRepo(r.repo_id, { branch: b }), `Switched ${r.name} to ${b}. Re-indexing now; AI steps use the new branch when it finishes.`)} />
          )}
          {current === "indexing" && (
            <div className="space-y-4 text-xs">
              <div className="space-y-1.5"><p className="font-medium">Domains it serves <span className="font-normal text-muted-foreground">(none selected means every domain)</span></p>
                <div className="flex flex-wrap gap-1">{domains.map((d) => {
                  const on = doms.includes(d.domain_id);
                  return <button key={d.domain_id} type="button" onClick={() => setDoms(on ? doms.filter((x) => x !== d.domain_id) : [...doms, d.domain_id])}
                                 className={cn("rounded-full border px-2.5 py-0.5", on ? "border-primary bg-primary/10 text-primary" : "bg-card text-muted-foreground")}>{d.domain_name}</button>;
                })}</div>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="space-y-1 font-medium">Only these paths<Input value={include} onChange={(e) => setInclude(e.target.value)} placeholder="models/**, macros/**" className="h-9 font-mono text-xs" />
                  <span className="block font-normal text-muted-foreground">Globs, comma separated. Empty means every file.</span></label>
                <label className="space-y-1 font-medium">Skip these paths<Input value={exclude} onChange={(e) => setExclude(e.target.value)} placeholder="models/legacy/**" className="h-9 font-mono text-xs" />
                  <span className="block font-normal text-muted-foreground">Credential files, target/ and dbt_packages/ are always skipped.</span></label>
              </div>
              <label className="flex items-center gap-2"><input type="checkbox" checked={r.enabled} disabled={pending}
                     onChange={() => act(() => updateRepo(r.repo_id, { enabled: !r.enabled }), r.enabled ? `${r.name} disabled: no longer used in prompts or offered to dbt` : `${r.name} enabled`)} />
                Used in AI prompts and offered to the dbt workspace</label>
              <div className="flex items-center gap-2 border-t pt-3">
                <span className="text-muted-foreground">Changing the paths re-indexes; changing domains does not.</span>
                <Button size="sm" className="ml-auto" disabled={pending}
                        onClick={() => start(async () => {
                          const res = await updateRepo(r.repo_id, { domain_ids: doms, include_globs: split(include), exclude_globs: split(exclude) });
                          onMsg(res.ok ? { tone: "ok", text: res.data.reindexing ? "Saved. The paths changed, so the index is being rebuilt." : "Saved." }
                                       : { tone: toneOf(res.error), text: res.error });
                          if (res.ok) router.refresh();
                        })}>{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Save</Button>
              </div>
            </div>
          )}
          {current === "schedule" && (
            <div className="space-y-3 text-xs">
              <p className="text-muted-foreground">Refreshes run as a Snowflake task; only files that changed since the last refresh are read, and a run already in progress is never doubled.</p>
              <div className="flex flex-wrap gap-1.5">
                {PRESETS.map((p) => <button key={p.cron} type="button" onClick={() => setCron(p.cron)}
                                            className={cn("rounded-full border px-2.5 py-1", cron === p.cron ? "border-primary bg-primary/10 text-primary" : "bg-card hover:border-primary/40")}>{p.label}</button>)}
              </div>
              <label className="block space-y-1 font-medium">Cron (five fields and a time zone)
                <Input value={cron} onChange={(e) => setCron(e.target.value)} className="h-9 w-72 font-mono text-xs" /></label>
              <div className="flex items-center gap-2">
                <Button size="sm" disabled={pending} onClick={() => act(() => scheduleRepo(r.repo_id, cron), `Scheduled ${r.name}: ${cron}`)}><Check className="h-3.5 w-3.5" />Save schedule</Button>
                {r.schedule_cron && <Button size="sm" variant="ghost" disabled={pending} onClick={() => act(() => scheduleRepo(r.repo_id, null), "Schedule removed")}>Remove schedule</Button>}
                <span className="ml-auto text-muted-foreground">Now: {r.schedule_cron ?? "on demand only"}</span>
              </div>
            </div>
          )}
          {current === "dbt" && <DbtPanel repo={r} pending={pending} onSave={(body) => act(() => updateRepo(r.repo_id, body), "dbt settings saved; runs use them from now on.")} />}
          {current === "credentials" && <CredentialsPanel repo={r} pending={pending} onSave={(body, ok) => act(() => setCredentials(r.repo_id, body), ok)} />}
          {current === "history" && (
            <div className="text-xs">
              {!runs ? <p className="flex items-center gap-2 text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Loading…</p> : runs.length ? (
                <ol className="space-y-2">
                  {runs.map((x) => {
                    const ok = x.status === "SUCCEEDED";
                    return (
                      <li key={x.index_run_id} className="flex gap-3 rounded-lg border px-3 py-2">
                        {ok ? <CircleCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" /> : x.status === "RUNNING" ? <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-indigo-600" />
                          : x.status === "CANCELLED" ? <CircleDashed className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" /> : <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-rose-600" />}
                        <div className="min-w-0 flex-1">
                          <p className="font-medium">{x.status.toLowerCase()} · {ago(x.started_at)}<span className="font-normal text-muted-foreground"> by {x.triggered_by === "SYSTEM" ? "the schedule" : x.triggered_by}</span></p>
                          <p className="text-muted-foreground">{x.files_changed ?? 0} changed{x.files_removed ? `, ${x.files_removed} removed` : ""} · {x.chunks ?? 0} chunks
                            {x.commit_sha ? <> · <span className="font-mono">{x.commit_sha.slice(0, 8)}</span></> : ""}{x.duration_ms ? ` · ${Math.round(x.duration_ms / 1000)} s` : ""}</p>
                          {x.error && <p className="mt-0.5 break-words text-destructive">{x.error.slice(0, 300)}</p>}
                        </div>
                      </li>
                    );
                  })}
                </ol>
              ) : <p className="text-muted-foreground">No refreshes yet.</p>}
            </div>
          )}
          {current === "disconnect" && <DisconnectPanel repo={r} pending={pending} onCancel={() => onTab("indexing")}
                                                         onConfirm={(drop) => start(async () => {
                                                           const res = await removeRepo(r.repo_id, drop);
                                                           onMsg(res.ok ? { tone: "ok", text: `${r.name} disconnected${res.data.dropped.length ? `; dropped ${res.data.dropped.join(", ")}` : ""}${res.data.kept.length ? `; kept ${res.data.kept.join(", ")}` : ""}.` }
                                                                        : { tone: toneOf(res.error), text: res.error });
                                                           if (res.ok) { onClose(); router.refresh(); }
                                                         })} />}
        </div>
      </aside>
    </div>
  );
}

function DbtPanel({ repo, pending, onSave }: {
  repo: CodeRepo; pending: boolean; onSave: (body: { use_for_dbt: boolean; dbt_project_dir: string; open_pr: boolean; draft_pr: boolean }) => void;
}) {
  const roots = repo.stats?.dbt_project_roots ?? [];
  const [use, setUse] = useState(repo.use_for_dbt !== false);
  const [dir, setDir] = useState(repo.dbt_project_dir ?? (roots.length === 1 ? roots[0].root : ""));
  const [openPr, setOpenPr] = useState(repo.open_pr !== false);
  const [draft, setDraft] = useState(repo.draft_pr === true);
  const known = roots.some((p) => p.root === dir);
  return (
    <div className="space-y-3 border-t bg-muted/20 px-4 py-3 text-xs">
      <p className="text-muted-foreground">How the dbt workspace of every run in {repo.domain_ids.length ? "these domains" : "every domain"} uses this repository:
        new branches are cut from <span className="font-mono">{repo.branch}</span> (change it on the Branch tab), generated models are written into the
        project folder, and the PR goes to {repo.git_url.replace(/^https:\/\//, "")}.</p>
      <label className="flex items-center gap-1.5"><input type="checkbox" checked={use} onChange={() => setUse(!use)} />Offer this repository to the dbt workspace</label>
      <div className={cn("grid gap-3 md:grid-cols-2", !use && "pointer-events-none opacity-50")}>
        <label className="space-y-1">dbt project folder
          {roots.length ? (
            <Select value={known ? dir : "__other"} onChange={(e) => setDir(e.target.value === "__other" ? dir : e.target.value)} className="h-8 text-xs">
              {roots.map((p) => <option key={p.root || "root"} value={p.root}>{p.root ? `${p.root}/` : "repository root"} ({p.name})</option>)}
              {!known && <option value="__other">{dir || "repository root"} (no dbt_project.yml found)</option>}
            </Select>
          ) : (
            <Input value={dir} onChange={(e) => setDir(e.target.value)} placeholder="empty for the repository root, or analytics" className="h-8 font-mono text-xs" />
          )}
          <span className="block text-[11px] text-muted-foreground">{roots.length ? "Found by the last index." : "No dbt_project.yml found yet; refresh the index after connecting."}</span>
        </label>
        <div className="space-y-1.5 pt-4">
          <label className="flex items-center gap-1.5"><input type="checkbox" checked={openPr} onChange={() => setOpenPr(!openPr)} />Open the PR right after generating (runs can untick it)</label>
          <label className="flex items-center gap-1.5"><input type="checkbox" checked={draft} onChange={() => setDraft(!draft)} />Open PRs as drafts</label>
          {repo.provider !== "GITHUB" && <p className="text-amber-700">Pull requests from the platform are GitHub only; runs on this repository stay stage only.</p>}
        </div>
      </div>
      <Button size="sm" disabled={pending} onClick={() => onSave({ use_for_dbt: use, dbt_project_dir: dir, open_pr: openPr, draft_pr: draft })}>
        {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Save dbt settings</Button>
    </div>
  );
}

function BranchPicker({ repo, pending, onPick, onClose }: { repo: CodeRepo; pending: boolean; onPick: (b: string) => void; onClose?: () => void }) {
  const [list, setList] = useState<RepoBranch[] | null>(null);
  const [error, setError] = useState("");
  const [q, setQ] = useState("");
  const [loading, startLoad] = useTransition();
  const load = (fetch: boolean) => startLoad(async () => {
    const res = await repoBranches(repo.repo_id, fetch);
    if (!res.ok) { setError(res.error); return; }
    setList(res.data.branches);
    setError(res.data.error ? `Could not fetch from the remote, showing the last fetched branches: ${res.data.error}` : "");
  });
  useEffect(() => { load(true); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);
  const shown = useMemo(() => (list ?? []).filter((b) => b.name.toLowerCase().includes(q.trim().toLowerCase())), [list, q]);
  const missing = list && !list.some((b) => b.name === repo.branch);
  return (
    <div className="space-y-2 border-t bg-muted/20 px-4 py-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[14rem] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a branch" className="h-8 pl-8 font-mono text-xs" aria-label="Find a branch" autoFocus />
        </div>
        <Button size="sm" variant="ghost" disabled={loading} onClick={() => load(true)}>{loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}Fetch</Button>
        {onClose && <Button size="sm" variant="ghost" onClick={onClose}>Close</Button>}
      </div>
      {error && <p className="text-amber-700">{error}</p>}
      {missing && <p className="flex items-center gap-1 text-destructive"><TriangleAlert className="h-3 w-3" />{repo.branch} is no longer on the remote. Pick another branch.</p>}
      {!list ? <p className="flex items-center gap-2 text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Fetching branches…</p> : (
        <ul className="max-h-64 divide-y overflow-y-auto overscroll-contain rounded-lg border bg-card">
          {shown.map((b) => {
            const current = b.name === repo.branch;
            return (
              <li key={b.name} className="flex min-w-0 items-center gap-2 px-3 py-1.5">
                <GitBranch className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate font-mono">{b.name}</span>
                <span className="hidden shrink-0 font-mono text-[10px] text-muted-foreground sm:inline">{b.commit.slice(0, 8)}</span>
                {current ? <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">current</span>
                  : <Button size="sm" variant="outline" className="h-6 shrink-0 px-2 text-[11px]" disabled={pending} onClick={() => onPick(b.name)}>Switch</Button>}
              </li>
            );
          })}
          {!shown.length && <li className="px-3 py-3 text-center text-muted-foreground">No branch matches.</li>}
        </ul>
      )}
      <p className="text-[11px] text-muted-foreground">Switching re-indexes the repository from the new branch. A run in progress on the old branch stops without writing.</p>
    </div>
  );
}

function CredentialsPanel({ repo, pending, onSave }: {
  repo: CodeRepo; pending: boolean;
  onSave: (body: { mode: "token" | "secret" | "public"; username?: string | null; token?: string | null; secret_name?: string | null }, ok: string) => void;
}) {
  const [mode, setMode] = useState<"token" | "secret" | "public">("token");
  const [username, setUsername] = useState("");
  const [token, setToken] = useState("");
  const [secret, setSecret] = useState(repo.secret_name ?? "");
  if (repo.owns_git_repository === false) {
    return <p className="border-t bg-muted/20 px-4 py-3 text-xs text-muted-foreground">This repository reuses <span className="font-mono">{repo.git_repository}</span>, which the platform did not create.
      Change its GIT_CREDENTIALS in Snowflake, or connect the repository again with a new clone.</p>;
  }
  return (
    <div className="space-y-2 border-t bg-muted/20 px-4 py-3 text-xs">
      <p>Signed in with: <span className="font-mono">{repo.secret_name || "no credentials (public)"}</span></p>
      <div className="flex max-w-md rounded-lg border bg-card p-0.5">
        {([["token", "New token"], ["secret", "Existing secret"], ["public", "Public"]] as const).map(([k, l]) => (
          <button key={k} type="button" onClick={() => setMode(k)} className={cn("flex-1 rounded-md px-2 py-1", mode === k ? "bg-primary text-primary-foreground" : "text-muted-foreground")}>{l}</button>
        ))}
      </div>
      {mode === "token" && (
        <div className="grid max-w-xl gap-2 sm:grid-cols-2">
          <Input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="Git username (any value for GitHub)" className="h-8 text-xs" />
          <Input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder="New read-only token" className="h-8 text-xs" />
        </div>
      )}
      {mode === "secret" && <Input value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="DB.SCHEMA.SECRET_NAME (TYPE = PASSWORD)" className="h-8 max-w-md font-mono text-xs" />}
      <div className="flex items-center gap-2">
        <Button size="sm" disabled={pending || (mode === "token" && !token.trim()) || (mode === "secret" && !secret.trim())}
                onClick={() => { onSave({ mode, username: username || null, token: mode === "token" ? token : null, secret_name: mode === "secret" ? secret : null },
                                        "Credentials updated and tested with a fetch."); setToken(""); }}>
          {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Save and test</Button>
        <span className="text-[11px] text-muted-foreground">A token goes straight into a Snowflake secret; the platform never keeps it.</span>
      </div>
    </div>
  );
}

function DisconnectPanel({ repo, pending, onCancel, onConfirm }: { repo: CodeRepo; pending: boolean; onCancel: () => void; onConfirm: (drop: boolean) => void }) {
  const [drop, setDrop] = useState(false);
  return (
    <div className="space-y-2 border-t bg-rose-50/40 px-4 py-3 text-xs">
      <p className="font-medium">Disconnect {repo.name}?</p>
      <p className="text-muted-foreground">Its index, refresh history and schedule are removed, and AI steps and the dbt workspace stop using it. Runs that already used it keep their citations.</p>
      {repo.owns_git_repository !== false ? (
        <label className="flex items-center gap-1.5"><input type="checkbox" checked={drop} onChange={() => setDrop(!drop)} />
          Also drop the Snowflake Git repository and secret the platform created for it</label>
      ) : <p className="text-muted-foreground">The reused Git repository <span className="font-mono">{repo.git_repository}</span> is kept.</p>}
      <div className="flex gap-2">
        <Button size="sm" variant="destructive" disabled={pending} onClick={() => onConfirm(drop)}>{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}Disconnect</Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

/** Pull requests from the dbt workspace: one GitHub token for every run, kept in a Snowflake secret. A single line of
 *  status and actions; the token field, access check and setup log open below it only when needed. */
function PublishingStrip({ status, githubRepos, may, onMsg }: { status: PublishingStatus; githubRepos: CodeRepo[]; may: boolean; onMsg: (m: Msg) => void }) {
  const router = useRouter();
  const [pending, start] = useTrackedTransition();
  const [token, setToken] = useState("");
  const [editing, setEditing] = useState(false);
  const [origin, setOrigin] = useState(githubRepos[0]?.git_url ?? "");
  const [check, setCheck] = useState<{ status: string; detail?: string; repository?: string; push?: boolean | null } | null>(null);
  const [log, setLog] = useState<{ sql: string; ok: boolean; error?: string }[]>([]);
  const test = () => start(async () => {
    const res = await checkPublishing(origin);
    if (!res.ok) { onMsg({ tone: toneOf(res.error), text: res.error }); return; }
    setCheck(res.data);
  });
  const saveToken = () => start(async () => {
    const res = status.ready ? await rotatePublishingToken(token.trim()) : await setupPublishing(token.trim());
    if (!res.ok) { onMsg({ tone: toneOf(res.error), text: res.error }); return; }
    setToken("");
    if ("log" in res.data) setLog(res.data.log);
    const ready = !("ready" in res.data) || res.data.ready;
    onMsg({ tone: ready ? "ok" : "error", text: status.ready ? "GitHub token updated in the Snowflake secret." : ready ? "Pull requests to GitHub are ready." : ("detail" in res.data && res.data.detail) || "Setup did not finish." });
    setEditing(false);
    router.refresh();
  });
  const showToken = may && (editing || !status.ready);
  return (
    <div className="border-b bg-muted/20 px-5 py-3 text-xs">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="inline-flex items-center gap-2 text-sm font-medium"><GitPullRequest className="h-4 w-4 text-emerald-600" />Pull requests to GitHub</span>
        <Badge tone={status.ready ? "good" : "idle"}>{status.ready ? "ready" : "not set up"}</Badge>
        <span className="min-w-[12rem] flex-1 text-muted-foreground">
          {status.ready ? <>The dbt workspace pushes branches and opens PRs with one token{status.config?.secret ? <> in <span className="font-mono">{status.config.secret}</span></> : ""}.</>
            : "Set a GitHub token once so runs can push branches and open pull requests. It is stored as a Snowflake secret."}
        </span>
        {may && status.ready && !editing && (
          <span className="flex flex-wrap items-center gap-1.5">
            {githubRepos.length > 1 && (
              <Select value={origin} onChange={(e) => setOrigin(e.target.value)} className="h-8 w-auto max-w-[14rem] text-xs" aria-label="Repository to test">
                {githubRepos.map((r) => <option key={r.repo_id} value={r.git_url}>{r.name}</option>)}
              </Select>
            )}
            <Button size="sm" variant="outline" disabled={pending || !/github\.com\//.test(origin)} onClick={test}
                    title={githubRepos.length === 1 ? `Check the token can push to ${githubRepos[0].name}` : undefined}>
              {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}Test access</Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)}><KeyRound className="h-3.5 w-3.5" />Rotate token</Button>
          </span>
        )}
      </div>
      {showToken && (
        <div className="mt-2.5 flex max-w-2xl flex-wrap items-center gap-2">
          <Input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder="GitHub token with Contents and Pull requests write"
                 className="h-9 min-w-[16rem] flex-1 text-xs" aria-label="GitHub token" />
          <Button size="sm" disabled={pending || !token.trim()} onClick={saveToken}>{pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <KeyRound className="h-3.5 w-3.5" />}{status.ready ? "Save token" : "Set up"}</Button>
          {editing && <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setToken(""); }}>Cancel</Button>}
          <span className="w-full text-[11px] text-muted-foreground">A fine-grained token limited to the client&apos;s repositories, with Contents and Pull requests: read and write.</span>
        </div>
      )}
      {!may && !status.ready && <p className="mt-1.5 text-muted-foreground">A platform admin (ADMIN.DEPLOY) sets this up.</p>}
      {check && (
        <p className={cn("mt-2 flex items-center gap-2 rounded-md px-2.5 py-1.5", check.status === "OK" && check.push !== false ? "bg-success/10 text-success" : "bg-amber-50 text-amber-800")}>
          {check.status === "OK" ? `Connected to ${check.repository}; ${check.push === false ? "this token cannot push to it" : "push allowed"}.` : check.detail || check.status}
          <button type="button" className="ml-auto" aria-label="Dismiss" onClick={() => setCheck(null)}><X className="h-3.5 w-3.5" /></button>
        </p>
      )}
      {log.length > 0 && (
        <ol className="mt-2 space-y-1 text-[11px]">
          {log.map((s) => <li key={s.sql} className={cn("rounded border px-2 py-1 font-mono", s.ok ? "border-emerald-200 bg-emerald-50" : "border-red-200 bg-red-50")}>
            <span className="block whitespace-pre-wrap break-all">{s.sql}</span>{s.error && <span className="mt-0.5 block font-sans text-red-700">{s.error}</span>}</li>)}
        </ol>
      )}
    </div>
  );
}

function ConnectDrawer({ domains, onClose, onDone }: { domains: Domain[]; onClose: () => void; onDone: (text: string) => void }) {
  useScrollLock();
  const [setup, setSetup] = useState<CodeSetup | null>(null);
  const [error, setError] = useState("");
  const [pending, start] = useTrackedTransition();
  const [f, setF] = useState({ name: "", git_url: "", branch: "main", api_integration: "", existing_git_repository: "",
                                auth: "token" as "token" | "secret" | "public", secret_name: "", username: "", token: "",
                                domain_ids: [] as string[], include: "", exclude: "", kind: "DBT", index_now: true });
  useEffect(() => { loadSetup().then((r) => (r.ok ? setSetup(r.data) : setError(r.error))); }, []);
  const host = (() => { try { return new URL(f.git_url).origin; } catch { return ""; } })();
  const integrations = setup?.integrations.filter((i) => i.usable !== false) ?? [];
  const fits = integrations.filter((i) => !host || !(i.allowed_prefixes ?? []).length || (i.allowed_prefixes ?? []).some((p) => f.git_url.startsWith(p.replace(/\/$/, "")) || host.startsWith(p.replace(/\/$/, ""))));
  const set = (patch: Partial<typeof f>) => setF((prev) => ({ ...prev, ...patch }));
  const nameFromUrl = (url: string) => (url.split("/").pop() ?? "").replace(/\.git$/, "").replace(/[^A-Za-z0-9]+/g, "_").toUpperCase().replace(/^_+|_+$/g, "");
  const submit = (override?: Partial<typeof f>) => start(async () => {
    const v = { ...f, ...override };
    setError("");
    const r = await connectRepo({
      name: v.name, git_url: v.git_url, branch: v.branch.trim(), api_integration: v.api_integration,
      existing_git_repository: v.existing_git_repository || null,
      secret_name: v.auth === "secret" ? v.secret_name : null, username: v.auth === "token" ? v.username || null : null,
      token: v.auth === "token" ? v.token || null : null, domain_ids: v.domain_ids,
      include_globs: v.include.split(",").map((s) => s.trim()).filter(Boolean), exclude_globs: v.exclude.split(",").map((s) => s.trim()).filter(Boolean),
      kind: v.kind, index_now: v.index_now,
    });
    if (r.ok) onDone(`${r.data.name} connected on ${r.data.branch}${v.index_now ? "; indexing started" : ""}.`);
    else setError(r.error);
  });
  const branchChoices = suggestedBranches(error);
  const valid = f.name.trim().length >= 2 && /^https:\/\//.test(f.git_url) && f.api_integration && f.branch.trim()
    && (f.auth !== "token" || f.token.trim()) && (f.auth !== "secret" || f.secret_name);
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Connect repository">
      <button type="button" aria-label="Close" className="absolute inset-0 bg-black/30" onClick={onClose} />
      <aside className="relative flex h-full w-[620px] max-w-full flex-col border-l bg-background shadow-2xl">
        <header className="flex items-center gap-3 border-b px-5 py-4">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-indigo-50 text-indigo-600 ring-1 ring-inset ring-indigo-100"><GitBranch className="h-4 w-4" /></span>
          <div className="flex-1"><h3 className="text-base font-semibold">Connect a code repository</h3>
            <p className="text-xs text-muted-foreground">Snowflake clones it with a Git integration; the token goes straight into a Snowflake secret. You can switch branches any time later.</p></div>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded-md p-1 hover:bg-muted"><X className="h-4 w-4" /></button>
        </header>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain px-5 py-4 text-sm">
          {!setup && !error && <p className="flex items-center gap-2 text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Reading Git integrations…</p>}
          <label className="block space-y-1 text-xs font-medium">Repository URL (https)
            <Input value={f.git_url} onChange={(e) => set({ git_url: e.target.value.trim(), name: f.name || nameFromUrl(e.target.value.trim()) })}
                   placeholder="https://github.com/acme/analytics-dbt" className="font-mono" /></label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-xs font-medium">Name<Input value={f.name} onChange={(e) => set({ name: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_") })} className="font-mono" /></label>
            <label className="space-y-1 text-xs font-medium">Branch<Input value={f.branch} onChange={(e) => set({ branch: e.target.value })} className="font-mono" /></label>
          </div>
          <label className="block space-y-1 text-xs font-medium">Snowflake Git integration
            <Select value={f.api_integration} onChange={(e) => set({ api_integration: e.target.value })}>
              <option value="">Choose…</option>
              {f.api_integration && !fits.some((i) => i.name === f.api_integration) && <option value={f.api_integration}>{f.api_integration}</option>}
              {fits.map((i) => <option key={i.name} value={i.name}>{i.name}{i.allowed_prefixes?.length ? ` (${i.allowed_prefixes.join(", ")})` : ""}</option>)}
            </Select>
          </label>
          {setup && !fits.length && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
              <p className="font-medium">No usable Git integration allows {host || "this host"}.</p>
              <p className="mt-1">An account admin can create one, then grant USAGE to your role:</p>
              <pre className="mt-2 overflow-x-auto rounded bg-white/70 p-2 font-mono text-[11px]">{`CREATE API INTEGRATION GIT_CODE_CONTEXT
  API_PROVIDER = GIT_HTTPS_API
  API_ALLOWED_PREFIXES = ('${host || "https://github.com/<org>"}')
  ALLOWED_AUTHENTICATION_SECRETS = ALL
  ENABLED = TRUE;`}</pre>
            </div>
          )}
          {!!setup?.repositories.length && (
            <label className="block space-y-1 text-xs font-medium">Or reuse a Git repository object already in Snowflake (optional)
              <Select value={f.existing_git_repository} onChange={(e) => {
                const repo = setup.repositories.find((x) => x.fqn === e.target.value);
                set({ existing_git_repository: e.target.value, api_integration: repo?.api_integration ?? f.api_integration, git_url: repo?.origin || f.git_url,
                      name: f.name || nameFromUrl(repo?.origin ?? ""), auth: e.target.value ? "public" : f.auth });
              }}>
                <option value="">Create a new one</option>
                {setup.repositories.map((x) => <option key={x.fqn} value={x.fqn}>{x.fqn}{x.origin ? ` (${x.origin})` : ""}</option>)}
              </Select>
              {f.existing_git_repository && <span className="block font-normal text-muted-foreground">A reused object keeps its own credentials; the platform never alters or drops it.</span>}
            </label>
          )}
          {!f.existing_git_repository && (
            <div className="space-y-2">
              <p className="text-xs font-medium">Credentials</p>
              <div className="flex rounded-lg border p-0.5 text-xs">
                {([["token", "New read-only token"], ["secret", "Existing secret"], ["public", "Public repository"]] as const).map(([k, l]) => (
                  <button key={k} type="button" onClick={() => set({ auth: k })} className={cn("flex-1 rounded-md px-2 py-1", f.auth === k ? "bg-primary text-primary-foreground" : "text-muted-foreground")}>{l}</button>
                ))}
              </div>
              {f.auth === "token" && (
                <div className="grid gap-2 sm:grid-cols-2">
                  <Input value={f.username} onChange={(e) => set({ username: e.target.value })} placeholder="Git username (any value for GitHub tokens)" className="text-xs" />
                  <Input type="password" value={f.token} onChange={(e) => set({ token: e.target.value })} placeholder="Personal access token (read-only)" className="text-xs" autoComplete="off" />
                  <p className="text-[11px] text-muted-foreground sm:col-span-2">Stored only as a Snowflake secret; use a fine-grained, read-only token limited to this repository.</p>
                </div>
              )}
              {f.auth === "secret" && (
                <Select value={f.secret_name} onChange={(e) => set({ secret_name: e.target.value })} className="text-xs">
                  <option value="">Choose a PASSWORD secret…</option>
                  {(setup?.secrets ?? []).map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
                </Select>
              )}
            </div>
          )}
          <div className="space-y-1.5">
            <p className="text-xs font-medium">Domains it serves <span className="font-normal text-muted-foreground">(none selected means every domain)</span></p>
            <div className="flex flex-wrap gap-1.5">{domains.map((d) => {
              const on = f.domain_ids.includes(d.domain_id);
              return <button key={d.domain_id} type="button" onClick={() => set({ domain_ids: on ? f.domain_ids.filter((x) => x !== d.domain_id) : [...f.domain_ids, d.domain_id] })}
                             className={cn("rounded-full border px-2.5 py-0.5 text-xs", on ? "border-primary bg-primary/10 text-primary" : "text-muted-foreground")}>{d.domain_name}</button>;
            })}</div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-xs font-medium">Only these paths<Input value={f.include} onChange={(e) => set({ include: e.target.value })} placeholder="models/**, macros/**, tests/**" className="font-mono text-xs" /></label>
            <label className="space-y-1 text-xs font-medium">Skip these paths<Input value={f.exclude} onChange={(e) => set({ exclude: e.target.value })} placeholder="models/legacy/**" className="font-mono text-xs" /></label>
          </div>
          <p className="text-[11px] text-muted-foreground">Never indexed: profiles.yml, .env files, keys and certificates, target/, dbt_packages/. Passwords, tokens, keys and credentials in URLs inside files are redacted.</p>
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={f.index_now} onChange={() => set({ index_now: !f.index_now })} />Index now</label>
          {error && (
            <div role="alert" className="space-y-2 rounded-lg bg-destructive/10 px-3 py-2 text-xs text-destructive">
              <p>{branchChoices.length ? error.split(" Available:")[0] : error}</p>
              {branchChoices.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5 text-foreground">
                  <span className="text-muted-foreground">Connect with:</span>
                  {branchChoices.map((b) => (
                    <button key={b} type="button" disabled={pending} onClick={() => { set({ branch: b }); submit({ branch: b }); }}
                            className="inline-flex items-center gap-1 rounded-md border bg-card px-2 py-0.5 font-mono hover:border-primary"><GitBranch className="h-3 w-3" />{b}</button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
        <footer className="flex items-center gap-2 border-t px-5 py-3">
          <span className="ml-auto" />
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button disabled={pending || !valid} onClick={() => submit()}>{pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}Connect</Button>
        </footer>
      </aside>
    </div>
  );
}
